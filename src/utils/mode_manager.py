"""Utility helpers for toggling between audit and MVP modes."""

from __future__ import annotations

import os


def mode() -> str:
    """Return the active deployment mode, defaults to legacy 'audit'."""
    return os.getenv("MODE", "audit").strip().lower()


def is_mvp() -> bool:
    """True when running in lightweight MVP mode."""
    return mode() == "mvp"


def is_audit() -> bool:
    """True when running in the default audit mode."""
    return mode() == "audit"


__all__ = ["mode", "is_mvp", "is_audit"]
