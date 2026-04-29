"""Text and binary detection helpers."""

from __future__ import annotations

from pathlib import Path

TEXT_EXTENSIONS = {".tex", ".bib", ".sty", ".cls", ".bst", ".md", ".txt", ".csv"}
BINARY_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps", ".gif", ".zip"}


def is_probably_binary_bytes(content: bytes) -> bool:
    if b"\x00" in content:
        return True
    if not content:
        return False
    sample = content[:4096]
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def is_text_path(path: str | Path, content: bytes | None = None) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    if suffix in BINARY_EXTENSIONS:
        return False
    if content is None:
        return True
    return not is_probably_binary_bytes(content)
