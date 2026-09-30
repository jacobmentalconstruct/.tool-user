"""Per-project SQLite knowledge store, separate from session event history."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ..locations import CONTROL
from ..workspace.paths import MAX_READ, Workspace
from .code_graph import GraphEdge, GraphNode, build_graph
from .chunking import Chunk


class KnowledgeStore:
    def __init__(self, project_root: Path, control_root: Path = CONTROL):
        self.workspace = Workspace(Path(project_root).resolve(strict=True))
        if self.workspace.root is None or not self.workspace.root.is_dir():
            raise ValueError("Choose an existing project folder.")
        canonical = str(self.workspace.root).casefold().encode("utf-8")
        project_key = hashlib.sha256(canonical).hexdigest()
        directory = Path(control_root) / "knowledge"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{project_key}.sqlite"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS source_files (
                    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES source_files(path) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
                    kind TEXT NOT NULL, text TEXT NOT NULL, UNIQUE(path, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED, path UNINDEXED, kind UNINDEXED, text
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                    model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    key TEXT PRIMARY KEY, path TEXT REFERENCES source_files(path) ON DELETE CASCADE,
                    name TEXT NOT NULL, kind TEXT NOT NULL, line INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
                    source TEXT NOT NULL REFERENCES graph_nodes(key) ON DELETE CASCADE,
                    target TEXT NOT NULL REFERENCES graph_nodes(key) ON DELETE CASCADE,
                    kind TEXT NOT NULL, PRIMARY KEY(source, target, kind)
                );
                CREATE INDEX IF NOT EXISTS chunks_path_idx ON chunks(path, ordinal);
                CREATE INDEX IF NOT EXISTS graph_nodes_path_idx ON graph_nodes(path);
            """)

    def replace_file(self, relative: str, content: str, chunks: Sequence[Chunk],
                     summary: str, *, graph: bool = True) -> str:
        """Replace one allowed file's indexed data atomically; return its content hash."""
        path = self.workspace.path(relative)
        if not path.is_file() or not isinstance(content, str):
            raise ValueError("Index an existing UTF-8 project file.")
        encoded = path.read_bytes()
        if len(encoded) > MAX_READ or b"\x00" in encoded:
            raise ValueError("Project file exceeds the text indexing limit or is binary.")
        try:
            source = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Project file is not UTF-8 text.") from exc
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        if normalized != content:
            raise ValueError("Project file changed while its index was being prepared.")
        digest = hashlib.sha256(encoded).hexdigest()
        stat = path.stat()
        nodes, edges = build_graph(relative, content) if graph and path.suffix.casefold() == ".py" else ([], [])
        if any(chunk.path != relative or chunk.lines[0] < 1 or chunk.lines[1] < chunk.lines[0]
               for chunk in chunks):
            raise ValueError("Chunk paths and line ranges must belong to the indexed file.")
        with self._connect() as db:
            old_ids = [row[0] for row in db.execute("SELECT id FROM chunks WHERE path = ?", (relative,))]
            for chunk_id in old_ids:
                db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
            db.execute("DELETE FROM source_files WHERE path = ?", (relative,))
            db.execute("""INSERT INTO source_files(path, sha256, size, mtime_ns, summary)
                          VALUES (?, ?, ?, ?, ?)""",
                       (relative, digest, stat.st_size, stat.st_mtime_ns, summary))
            for ordinal, chunk in enumerate(chunks):
                cursor = db.execute("""INSERT INTO chunks(path, ordinal, line_start, line_end, kind, text)
                                       VALUES (?, ?, ?, ?, ?, ?)""",
                                    (relative, ordinal, chunk.lines[0], chunk.lines[1],
                                     chunk.kind, chunk.text))
                db.execute("INSERT INTO chunks_fts(rowid, chunk_id, path, kind, text) VALUES (?, ?, ?, ?, ?)",
                           (cursor.lastrowid, str(cursor.lastrowid), relative, chunk.kind, chunk.text))
            node_map = {node.key: node for node in nodes}
            for node in nodes:
                db.execute("INSERT OR IGNORE INTO graph_nodes(key, path, name, kind, line) VALUES (?, ?, ?, ?, ?)",
                           (node.key, node.path or None, node.name, node.kind, node.line))
            for edge in edges:
                if edge.source not in node_map:
                    continue
                target = node_map.get(edge.target)
                if target:
                    db.execute("INSERT OR IGNORE INTO graph_nodes(key, path, name, kind, line) VALUES (?, ?, ?, ?, ?)",
                               (target.key, target.path or None, target.name, target.kind, target.line))
                    db.execute("INSERT OR IGNORE INTO graph_edges(source, target, kind) VALUES (?, ?, ?)",
                               (edge.source, target.key, edge.kind))
        return digest

    def file_record(self, relative: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM source_files WHERE path = ?", (relative,)).fetchone()
        return dict(row) if row else None

    def indexed_paths(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT path FROM source_files ORDER BY path").fetchall()
        return [row["path"] for row in rows]

    def remove_file(self, relative: str) -> None:
        with self._connect() as db:
            ids = [row[0] for row in db.execute(
                "SELECT id FROM chunks WHERE path = ?", (relative,))]
            for chunk_id in ids:
                db.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
            db.execute("DELETE FROM source_files WHERE path = ?", (relative,))

    def list_chunks(self, relative: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM chunks WHERE path = ? ORDER BY ordinal", (relative,)).fetchall()
        return [dict(row) for row in rows]

    def chunks_without_embeddings(self, relative: str, model: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""SELECT c.* FROM chunks c
                                  WHERE c.path = ? AND NOT EXISTS (
                                      SELECT 1 FROM embeddings e
                                      WHERE e.chunk_id = c.id AND e.model = ?)
                                  ORDER BY c.ordinal""", (relative, model)).fetchall()
        return [dict(row) for row in rows]

    def all_chunks(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM chunks ORDER BY path, ordinal").fetchall()
        return [dict(row) for row in rows]

    def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        if not isinstance(query, str) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Search query and a limit from 1 to 100 are required.")
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        match = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        with self._connect() as db:
            rows = db.execute("""SELECT c.id, c.path, c.ordinal, c.line_start, c.line_end,
                                       c.kind, c.text, bm25(chunks_fts) AS score
                                FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                                WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?""",
                              (match, limit)).fetchall()
        return [dict(row) for row in rows]

    def put_embedding(self, chunk_id: int, model: str, vector: Iterable[float]) -> None:
        values = tuple(float(value) for value in vector)
        if not model or not values or any(not math.isfinite(value) for value in values):
            raise ValueError("An embedding needs a model name and values.")
        try:
            blob = struct.pack("<" + "f" * len(values), *values)
        except (OverflowError, struct.error) as exc:
            raise ValueError("Embedding values must be finite float32 numbers.") from exc
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM chunks WHERE id = ?", (chunk_id,)).fetchone():
                raise ValueError("Embedding chunk does not exist.")
            db.execute("""INSERT INTO embeddings(chunk_id, model, dimensions, vector)
                          VALUES (?, ?, ?, ?) ON CONFLICT(chunk_id) DO UPDATE SET
                          model=excluded.model, dimensions=excluded.dimensions, vector=excluded.vector""",
                       (chunk_id, model, len(values), blob))

    def get_embeddings(self, model: str | None = None) -> list[dict]:
        sql = "SELECT chunk_id, model, dimensions, vector FROM embeddings"
        parameters: tuple = ()
        if model is not None:
            sql += " WHERE model = ?"
            parameters = (model,)
        with self._connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [{"chunk_id": row["chunk_id"], "model": row["model"],
                 "vector": struct.unpack("<" + "f" * row["dimensions"], row["vector"])}
                for row in rows]

    def graph(self) -> tuple[list[GraphNode], list[GraphEdge]]:
        with self._connect() as db:
            nodes = [GraphNode(row["key"], row["path"], row["name"], row["kind"], row["line"])
                     for row in db.execute("SELECT * FROM graph_nodes ORDER BY path, line")]
            edges = [GraphEdge(row["source"], row["target"], row["kind"])
                     for row in db.execute("SELECT * FROM graph_edges ORDER BY source, target, kind")]
        return [GraphNode(node.key, node.path or "", node.name, node.kind, node.line)
                for node in nodes], edges
