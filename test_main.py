"""Self-check for main.py post-processing. Run: python test_main.py"""

import main


def test_math_delimiters() -> None:
    f = main.normalize_ocr_markdown
    assert f(r"\[E = mc^2\]") == "$$E = mc^2$$"
    assert f(r"see \(x + y\) inline") == "see $x + y$ inline"
    # display math may span lines
    assert f("\\[\na = b\n\\]") == "$$\na = b\n$$"
    # row breaks inside a formula survive (a blanket replace of backslashes destroys them)
    assert (
        f(r"\[\begin{pmatrix} a \\ b \end{pmatrix}\]")
        == r"$$\begin{pmatrix} a \\ b \end{pmatrix}$$"
    )
    # pdf-craft double-escapes equation-asset bodies — those get collapsed
    assert (
        f(r"\[F(l,m) = \\sum_{n = 1}^{N} a_{mn} \\cdot e^{-i2\\pi}\]")
        == r"$$F(l,m) = \sum_{n = 1}^{N} a_{mn} \cdot e^{-i2\pi}$$"
    )
    # escaped literals are not delimiters; prose unescaping restores \[
    assert f(r"array\\[3\\]") == r"array\[3\]"
    # dollar math passes through untouched
    assert f(r"$a \\ b$ and $$c d$$") == r"$a \\ b$ and $$c d$$"
    # inline math never spans lines (and prose has no pairs to collapse here)
    assert f("\\(a\nb\\)") == "\\(a\nb\\)"


def test_prose_unescaping() -> None:
    f = main.normalize_ocr_markdown
    # pdf-craft doubles the OCR's own markdown escapes in prose
    assert f(r"Liu Shilong\\* Shandong University") == r"Liu Shilong\* Shandong University"
    # prose collapse never reaches into dollar-math spans
    assert f(r"$$a \\ b$$ and \\* prose") == r"$$a \\ b$$ and \* prose"


ACM_SAMPLE = """Latest updates: https://dl.acm.org/doi/10.1145/3746059.3747603

RESEARCH- ARTICLE

## Assessing Dynamic Flow Experience

LIU SHILONG, Shandong University, Weihai, Weihai, China

View all

Open Access Support provided by:

Shandong University

# Assessing Dynamic Flow Experience

A Processing- based approach.

- first bullet
- second bullet

The challenge - skill balance holds.
"""


def test_acm_cleanup() -> None:
    cleaned, applied = main.postprocess_markdown(ACM_SAMPLE, "auto")
    assert applied == ["acm"]
    assert cleaned.startswith("# Assessing Dynamic Flow Experience")
    assert "View all" not in cleaned
    assert "RESEARCH" not in cleaned
    # hyphenation artifact fixed; bullets and spaced dashes untouched
    assert "A Processing-based approach." in cleaned
    assert "- first bullet" in cleaned
    assert "challenge - skill" in cleaned
    assert "\n\n\n" not in cleaned


def test_hyphenation_runs() -> None:
    cleaned, _ = main.postprocess_markdown("the Whac- A- Mole game", "none")
    assert "the Whac-A-Mole game" in cleaned


def test_ieee_cleanup() -> None:
    text = (
        "# Paper\n\nBody text.\n\n"
        "979-8-3503-1234-5/24/\\$31.00 ©2024 IEEE\n\n"
        "Authorized licensed use limited to: Some University. "
        "Downloaded on May 05,2025 from IEEE Xplore. Restrictions apply.\n"
    )
    cleaned, applied = main.postprocess_markdown(text, "auto")
    assert applied == ["ieee"]
    assert "IEEE" not in cleaned
    assert "Authorized licensed use" not in cleaned
    assert "Body text." in cleaned


def test_iclr_and_arxiv_cleanup() -> None:
    text = (
        "Published as a conference paper at ICLR 2024\n\n"
        "# Title\n\nBody.\n\n"
        "arXiv:2401.12345v2 [cs.LG] 21 Jan 2024\n"
    )
    cleaned, applied = main.postprocess_markdown(text, "auto")
    assert set(applied) == {"arxiv", "iclr"}
    assert "conference paper at ICLR" not in cleaned
    assert "arXiv:2401" not in cleaned
    assert cleaned.startswith("# Title")


def test_venue_none_keeps_boilerplate() -> None:
    cleaned, applied = main.postprocess_markdown(ACM_SAMPLE, "none")
    assert applied == []
    assert "View all" in cleaned


def test_forced_venue_skips_detection() -> None:
    cleaned, applied = main.postprocess_markdown("# T\n\nView all\n", "acm")
    assert applied == ["acm"]
    assert "View all" not in cleaned


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("All checks passed.")
