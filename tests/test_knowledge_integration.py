"""Selected-project indexing, patch refresh, idle scheduling, and restart checks."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.knowledge.embedding import EmbeddingUnavailable  # noqa: E402
from local_memory_lab.knowledge.service import KnowledgeService  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)


class _Embedder:
    model = "test-embedder"

    def embed(self, text):
        return (1.0, 0.0)

    def embed_many(self, texts):
        return [(1.0, 0.0) for _ in texts]


class _UnavailableEmbedder(_Embedder):
    available = False

    def embed(self, text):
        if not self.available:
            raise EmbeddingUnavailable("embedding service is down")
        return super().embed(text)

    def embed_many(self, texts):
        if not self.available:
            raise EmbeddingUnavailable("embedding service is down")
        return super().embed_many(texts)


class KnowledgeIntegrationTests(unittest.TestCase):
    def test_indexing_without_embeddings_keeps_keyword_search_available(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "guide.md").write_text("# Guide\nsearchable fallback phrase\n", encoding="utf-8")
            embedder = _UnavailableEmbedder()
            service = KnowledgeService(project, control_root=base / "control", embedder=embedder)
            try:
                self.assertTrue(service.wait_idle())
                result = service.retriever.search("fallback")
                self.assertEqual("keyword_only", result["status"])
                self.assertIn("embedding service is down", result["fallback_reason"])
                self.assertTrue(result["results"])
                self.assertIn("Keyword-only knowledge fallback", service.context_for("fallback"))
                embedder.available = True
                service.begin_activity()
                service.end_activity()
                self.assertTrue(service.wait_idle())
                self.assertTrue(service.store.get_embeddings(model=embedder.model))
            finally:
                service.close()

    def test_session_selection_and_restart_schedule_project_scans(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "guide.md").write_text("# Guide\nselected project phrase\n", encoding="utf-8")
            control = base / "control"

            def service_factory(root, start_worker, embedder=None):
                return KnowledgeService(root, control_root=control, embedder=embedder or _Embedder(),
                                        start_worker=True)

            with patch("local_memory_lab.session.KnowledgeService", side_effect=service_factory):
                first = SharedSession(base / "events.sqlite", load_models=False, start_worker=False)
                first.set_project_root(str(project))
                self.assertTrue(first.knowledge.wait_idle())
                self.assertTrue(first.knowledge.store.search_fts("selected"))
                first.knowledge.close()

                restored = SharedSession(base / "events.sqlite", load_models=False, start_worker=False)
                try:
                    self.assertTrue(restored.knowledge.wait_idle())
                    self.assertEqual(["guide.md"], restored.knowledge.store.indexed_paths())
                finally:
                    restored.knowledge.close()

    def test_startup_scan_is_filtered_and_restart_refreshes_and_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "guide.md").write_text("# First\nfirst searchable text\n", encoding="utf-8")
            (project / "node_modules").mkdir()
            (project / "node_modules" / "ignored.py").write_text("secret = True\n", encoding="utf-8")
            control = base / "control"

            first = KnowledgeService(project, control_root=control, embedder=_Embedder())
            self.assertTrue(first.wait_idle())
            self.assertEqual(["guide.md"], first.store.indexed_paths())
            original_hash = first.store.file_record("guide.md")["sha256"]
            first.close()

            (project / "guide.md").write_text("# Second\nsecond searchable text\n", encoding="utf-8")
            (project / "guide.md").touch()
            (project / "guide.md").rename(project / "renamed.md")
            second = KnowledgeService(project, control_root=control, embedder=_Embedder())
            try:
                self.assertTrue(second.wait_idle())
                self.assertEqual(["renamed.md"], second.store.indexed_paths())
                self.assertNotEqual(original_hash, second.store.file_record("renamed.md")["sha256"])
                self.assertTrue(second.store.search_fts("second"))
            finally:
                second.close()

    def test_an_applied_change_is_retrievable_by_the_next_task_without_a_full_rescan(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            source = project / "guide.md"
            source.write_text("# Guide\nfirst task text\n", encoding="utf-8")
            service = KnowledgeService(project, control_root=base / "control", embedder=_Embedder())
            try:
                self.assertTrue(service.wait_idle())
                service.begin_activity()  # a job is running: the worker must not index
                try:
                    source.write_text("# Guide\napplied by task one\n", encoding="utf-8")
                    service.index_paths_now(["guide.md"])  # what the job does right after an apply
                    self.assertTrue(service.store.search_fts("applied"))
                    self.assertIn("applied by task one", service.context_for("applied"))
                finally:
                    service.end_activity()
                self.assertFalse(service._pending_full)  # ending activity no longer queues a full rescan
            finally:
                service.close()

    def test_next_idle_scan_refreshes_external_changes_before_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            source = project / "guide.md"
            source.write_text("# Guide\nold external text\n", encoding="utf-8")
            service = KnowledgeService(project, control_root=base / "control", embedder=_Embedder())
            try:
                self.assertTrue(service.wait_idle())
                source.write_text("# Guide\nnew external text\n", encoding="utf-8")
                service.begin_activity()
                try:
                    self.assertTrue(service.store.search_fts("external"))
                    self.assertFalse(service.store.search_fts("old"))
                    self.assertTrue(service.store.search_fts("new"))
                finally:
                    service.end_activity()
                self.assertTrue(service.wait_idle())
            finally:
                service.close()

    def test_only_applied_patch_queues_and_completes_index_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            (project / "guide.md").write_text("# Guide\nold phrase\n", encoding="utf-8")
            service = KnowledgeService(project, control_root=base / "control", embedder=_Embedder())
            session = SharedSession(base / "events.sqlite", load_models=False, start_worker=False)
            session.knowledge.close()
            session.knowledge = service
            try:
                self.assertTrue(service.wait_idle())
                self.assertTrue(service.store.search_fts("old"))
                with patch("local_memory_lab.agent.patch_tools.CONTROL", base / "patch_control"):
                    with patch.object(session, "_confirm_patch", return_value=False):
                        denied = session._tools_for(project, "denied")
                        result = denied.call("patch_project_file", {
                            "path": "guide.md", "search_block": "old phrase",
                            "replace_block": "new phrase"})
                        self.assertEqual("cancelled", result["status"])
                        self.assertTrue(service.wait_idle(0.1))
                        self.assertTrue(service.store.search_fts("old"))

                    with patch.object(session, "_confirm_patch", return_value=True):
                        approved = session._tools_for(project, "approved")
                        result = approved.call("patch_project_file", {
                            "path": "guide.md", "search_block": "old phrase",
                            "replace_block": "new phrase"})
                        self.assertEqual("patched", result["status"])
                self.assertTrue(service.wait_idle())
                self.assertFalse(service.store.search_fts("old"))
                self.assertTrue(service.store.search_fts("new"))
            finally:
                service.close()

    def test_refresh_waits_for_active_turn_and_command(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / "project"
            project.mkdir()
            source = project / "guide.md"
            source.write_text("# Guide\ninitial phrase\n", encoding="utf-8")
            service = KnowledgeService(project, control_root=base / "control", embedder=_Embedder())
            session = SharedSession(base / "events.sqlite", load_models=False)
            session.knowledge.close()
            session.knowledge = service
            turn_started = threading.Event()
            enter_command = threading.Event()
            command_started = threading.Event()
            release_command = threading.Event()
            command_finished = threading.Event()

            def fake_turn(prompt, model, turns, notes, tools, on_tool, **kwargs):
                turn_started.set()
                self.assertTrue(enter_command.wait(3))
                tools.request_command("hold")
                return "finished", [{"role": "user", "content": prompt}]

            def blocked_command(_name, _root, _request_id, _job_id=None):
                command_started.set()
                self.assertTrue(release_command.wait(3))
                command_finished.set()
                return {"status": "ok", "message": "command finished"}

            try:
                self.assertTrue(service.wait_idle())
                original_hash = service.store.file_record("guide.md")["sha256"]
                with patch("local_memory_lab.session.run_turn", side_effect=fake_turn), \
                     patch.object(session, "_run_named_command", side_effect=blocked_command):
                    session.submit("inspect the guide", "USER")
                    self.assertTrue(turn_started.wait(3))
                    source.write_text("# Guide\nturn change\n", encoding="utf-8")
                    service.refresh_paths(["guide.md"])
                    self.assertFalse(service.wait_idle(0.15))
                    self.assertEqual(original_hash, service.store.file_record("guide.md")["sha256"])

                    enter_command.set()
                    self.assertTrue(command_started.wait(3))
                    source.write_text("# Guide\ncommand change\n", encoding="utf-8")
                    service.refresh_paths(["guide.md"])
                    self.assertFalse(service.wait_idle(0.15))
                    self.assertEqual(original_hash, service.store.file_record("guide.md")["sha256"])

                    release_command.set()
                    self.assertTrue(command_finished.wait(3))
                    self.assertTrue(service.wait_idle(3))
                    self.assertNotEqual(original_hash, service.store.file_record("guide.md")["sha256"])
                    self.assertTrue(service.store.search_fts("command"))
            finally:
                release_command.set()
                service.close()
                session.knowledge.close()


if __name__ == "__main__":
    unittest.main()
