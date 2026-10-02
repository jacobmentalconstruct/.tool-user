"""Small immutable backup generations for workspace patch transactions."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass
class Generation:
    id: str
    status: str
    files: list[dict]


class BackupStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    @classmethod
    def for_project(cls, control: Path, project_root: Path) -> "BackupStore":
        """One backup folder per selected project, under the runtime control folder."""
        scope = hashlib.sha256(str(Path(project_root)).casefold().encode("utf-8")).hexdigest()[:16]
        return cls(Path(control) / "backups" / scope)

    def create(self, kind: str, source: str, request_id: str, files: list[tuple[str, bytes | None]]) -> Generation:
        generation_id = uuid4().hex
        stage = self.folder / ("." + generation_id + ".tmp")
        final = self.folder / generation_id
        stage.mkdir(parents=True)
        manifest_files = []
        try:
            for relative, content in files:
                if content is None:  # the file did not exist before; restoring it means removing it
                    manifest_files.append({"path": relative, "absent": True})
                    continue
                target = stage / "files" / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                manifest_files.append({"path": relative, "size": len(content)})
            (stage / "manifest.json").write_text(json.dumps({
                "id": generation_id, "kind": kind, "source": source,
                "request_id": request_id, "status": "ok", "files": manifest_files,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            stage.replace(final)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return Generation(generation_id, "ok", manifest_files)

    def get(self, generation_id: str) -> Generation:
        folder = self.folder / generation_id
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        return Generation(data["id"], data["status"], data["files"])

    def read(self, generation_id: str, relative: str) -> bytes:
        generation = self.get(generation_id)
        if not any(item["path"] == relative for item in generation.files):
            raise ValueError("file is not in this backup generation")
        return (self.folder / generation_id / "files" / Path(relative)).read_bytes()

    def list(self) -> list[Generation]:
        if not self.folder.exists():
            return []
        return [self.get(path.name) for path in sorted(self.folder.iterdir())
                if path.is_dir() and not path.name.startswith(".")]
