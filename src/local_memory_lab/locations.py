"""Filesystem locations shared by the application's interfaces and services."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "live_control"
