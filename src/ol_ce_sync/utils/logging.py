"""Small user-facing logging helpers."""

from __future__ import annotations


def info(message: str) -> None:
    print(f"[ol] {message}")


def warn(message: str) -> None:
    print(f"[ol] warning: {message}")
