"""Bounded, resumable batches: one inference process and isolated CPU exporters."""

from __future__ import annotations

import json
import multiprocessing
import os
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path

from .model import ReadingProfile, atomic_json, digest


def _worker_setup():
    # Keep native libraries from multiplying their thread pools in every process.
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[key] = "2"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def _prepare(source, work, options):
    from .extract import prepare_extraction
    return str(prepare_extraction(source, work, force=options.get("reextract", False),
                                  ocr=options.get("ocr", "auto"), device=options.get("device", "auto")))


def _render(source, output_root, work, profile, options, epubcheck):
    from .pipeline import convert
    for attempt in range(2):
        try:
            folder, report = convert(source, output_root, work, profile, **options, allow_inference=False)
            break
        except RuntimeError as exception:
            if attempt or "PDF typesetting failed" not in str(exception):
                raise
    if epubcheck and "epub" in report["outputs"]:
        import subprocess
        check_path = folder / "epubcheck.json"
        epub = folder / report["outputs"]["epub"]["filename"]
        result = subprocess.run(["java", "-jar", str(epubcheck), str(epub), "--json", str(check_path)],
            capture_output=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        validation = json.loads(check_path.read_text(encoding="utf-8"))
        if result.returncode or any(m.get("severity") in {"ERROR", "FATAL"} for m in validation.get("messages", [])):
            raise ValueError("EPUBCheck rejected the export: " + result.stdout[-3000:] + result.stderr[-1000:])
        report["epubcheck"] = {"valid": True, "report": str(check_path),
                               "warnings": [m for m in validation.get("messages", []) if m.get("severity") == "WARNING"]}
        atomic_json(folder / "conversion-report.json", report)
    return str(folder), report


def run_batch(sources: list[Path], output_root: Path, work: Path, profile: ReadingProfile,
              *, workers: int = 3, options: dict | None = None, metadata: dict | None = None,
              checkpoint: Path | None = None, epubcheck: Path | None = None,
              on_complete=None, extraction_timeout: float = 300) -> dict:
    """Resume via validated conversion receipts, never by the existence of a filename.

    Failed papers are isolated. A crashed worker pool remains a batch error so it
    cannot silently drop pending work. Canonical extraction is reused after retry.
    """
    if not 1 <= workers <= 16:
        raise ValueError("CPU workers must be between 1 and 16.")
    sources = [p.resolve(strict=True) for p in sources]
    if len({digest(p) for p in sources}) != len(sources):
        raise ValueError("Batch inputs must be deduplicated before scheduling.")
    output_root, work = output_root.resolve(), work.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoint or output_root / "batch-progress.json"
    options, metadata = dict(options or {}), metadata or {}
    state = {"started": time.time(), "total": len(sources), "workers": workers,
             "inference_workers": 1, "documents": {}, "complete": False}
    for path in sources:
        state["documents"][str(path)] = {"status": "pending"}

    def save():
        state["updated"] = time.time()
        values = list(state["documents"].values())
        state["finished"] = sum(v["status"] in {"ready", "needs_review"} for v in values)
        state["failed"] = sum(v["status"] == "failed" for v in values)
        atomic_json(checkpoint, state)

    def error(path, stage, exception):
        state["documents"][str(path)] = {"status": "failed", "stage": stage,
            "error": str(exception), "traceback": traceback.format_exc()}
        print(f"Failed ({stage}): {path.name}: {exception}", flush=True)
        save()

    save()
    context = multiprocessing.get_context("spawn")
    with ExitStack() as stack:
        def new_inference():
            return stack.enter_context(ProcessPoolExecutor(1, mp_context=context, initializer=_worker_setup))
        inference = new_inference()
        cpu = stack.enter_context(ProcessPoolExecutor(workers, mp_context=context, initializer=_worker_setup))
        pending, index = {}, 0
        extracting = None
        retry_source = None
        while index < len(sources) or pending or extracting or retry_source:
            if extracting is None and (index < len(sources) or retry_source) and len(pending) < workers * 2:
                path = retry_source or sources[index]
                if retry_source:
                    retry_source = None
                else:
                    index += 1
                extracting = inference.submit(_prepare, path, work, options)
                extracting_path = path
                extraction_started = time.monotonic()
                state["documents"][str(path)] = {"status": "extracting"}
                print(f"Extracting [{index}/{len(sources)}]: {path.name}", flush=True)
                save()
            active = set(pending) | ({extracting} if extracting else set())
            completed, _ = wait(active, timeout=20, return_when=FIRST_COMPLETED)
            if extracting and extracting not in completed and time.monotonic() - extraction_started > extraction_timeout:
                # A native parser can stall below Python. Canceling a Future
                # cannot stop it; terminate only this isolated inference worker.
                for process in list(inference._processes.values()):
                    process.terminate()
                    process.join(timeout=5)
                inference.shutdown(wait=False, cancel_futures=True)
                inference = new_inference()
                from .extract import cache_location
                cache = cache_location(extracting_path, work, options.get("ocr", "auto"), options.get("device", "auto"))
                fallback = cache / "parser-fallback.json"
                if not fallback.exists():
                    atomic_json(fallback, {"backend": "pdfium", "reason": "Native parser exceeded its time limit"})
                    retry_source = extracting_path
                    state["documents"][str(extracting_path)] = {"status": "retrying", "backend": "pdfium"}
                    print(f"Retrying with alternate parser: {extracting_path.name}", flush=True)
                else:
                    error(extracting_path, "extraction", TimeoutError("Both parsers exceeded the per-paper time limit."))
                extracting = None
            if not completed:
                save()
                continue
            if extracting in completed:
                path = extracting_path
                try:
                    extracting.result()
                    overrides = metadata.get(digest(path), {})
                    if not isinstance(overrides, dict) or set(overrides) - {"title", "authors", "name"}:
                        raise ValueError("Metadata overrides allow only title, authors, and name.")
                    job_options = {**options, **overrides}
                    # In CPU workers, reextract forces normalization of the newly
                    # prepared raw data without loading another inference model.
                    pending[cpu.submit(_render, path, output_root, work, profile,
                                       job_options, epubcheck)] = path
                    state["documents"][str(path)] = {"status": "formatting"}
                except Exception as exception:
                    error(path, "extraction", exception)
                completed.remove(extracting)
                extracting = None
            for future in completed:
                path = pending.pop(future)
                try:
                    folder, report = future.result()
                    state["documents"][str(path)] = {"status": report["status"], "folder": folder,
                        "title": report["title"], "source_pages": report["source_pages"],
                        "review_items": len(report["review"]), "outputs": report["outputs"],
                        "epubcheck_valid": report.get("epubcheck", {}).get("valid")}
                    save()
                    if on_complete:
                        on_complete(path, Path(folder), report)
                    print(f"Completed [{state['finished']}/{len(sources)}]: {path.name} ({report['status']})", flush=True)
                except Exception as exception:
                    error(path, "formatting", exception)
            save()
    state["complete"] = state["finished"] + state["failed"] == len(sources)
    save()
    return state
