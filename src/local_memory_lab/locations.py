"""Filesystem locations shared by the application's interfaces and services."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "files"
CONTROL = ROOT / "live_control"
PARTS_BIN = ROOT / ".parts-bin" / "project-mapping-guts"
