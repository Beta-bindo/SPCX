"""Writable paths for config, ledger, and exports (dev + PyInstaller)."""


from __future__ import annotations
from pathlib import Path


def user_data_dir() -> Path:
    d = Path.home() / ".xau_assistant"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = user_data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path() -> Path:
    return user_data_dir() / "trade_ledger.json"
