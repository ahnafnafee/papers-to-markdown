"""High-contrast typographic covers that stay legible on an e-ink library shelf."""

from io import BytesIO
from pathlib import Path


def cover_png(title: str, authors: list[str], fonts: dict[str, Path]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1600, 2560), "white")
    draw = ImageDraw.Draw(image)
    left, width = 150, 1300

    def wrap(text, font):
        lines, line = [], ""
        for word in text.split():
            # Split very long identifiers without dropping any characters.
            pieces = [word]
            if draw.textlength(word, font=font) > width:
                pieces, part = [], ""
                for char in word:
                    if part and draw.textlength(part + char, font=font) > width:
                        pieces.append(part)
                        part = ""
                    part += char
                pieces.append(part)
            for piece in pieces:
                candidate = (line + " " + piece).strip()
                if line and draw.textlength(candidate, font=font) > width:
                    lines.append(line)
                    line = piece
                else:
                    line = candidate
        return lines + ([line] if line else [])

    def fitted(text, path, start, max_height):
        for size in range(start, 19, -2):
            font = ImageFont.truetype(str(path), size)
            lines = wrap(text, font)
            leading = int(size * 1.4)
            if len(lines) * leading <= max_height:
                return font, lines, leading
        raise ValueError("Cover metadata is too long to fit legibly.")

    small = ImageFont.truetype(str(fonts["regular"]), 36)
    draw.text((left, 170), "RESEARCH PAPER", fill="black", font=small)
    draw.line((left, 270, left + width, 270), fill="black", width=5)
    font, lines, leading = fitted(title, fonts["bold"], 100, 1360)
    y = 390
    for line in lines:
        draw.text((left, y), line, fill="black", font=font)
        y += leading
    if authors:
        font, lines, leading = fitted(", ".join(authors), fonts["regular"], 44, 420)
        y = max(1830, y + 100)
        for line in lines:
            draw.text((left, y), line, fill="black", font=font)
            y += leading
    draw.line((left, 2360, left + width, 2360), fill="black", width=2)
    draw.text((left, 2400), "Verdana reading edition", fill="black", font=small)
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
