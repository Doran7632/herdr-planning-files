import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import records


class RecordsTests(unittest.TestCase):
    def test_defaults_and_explicit_choices(self):
        self.assertEqual(records.resolve_agents(3, []), ["claude code", "codex", "codex"])
        self.assertEqual(records.resolve_agents(2, ["worker1=codex", "worker2=omp"]), ["codex", "omp"])
        with self.assertRaises(ValueError):
            records.resolve_agents(2, ["worker1=codex", "worker1=omp"])

    def test_prepare_isolated_roles_preserves_other_plan(self):
        with tempfile.TemporaryDirectory() as project:
            root = Path(project)
            (root / ".planning").mkdir()
            pointer = root / ".planning/.active_plan"
            pointer.write_text("other-plan\n")
            state = records.prepare(root, 2, [], "codex", "w1:p1", "w1")
            directory = Path(state["plan_dir"])
            self.assertEqual(pointer.read_text(), "other-plan\n")
            self.assertEqual(state["schema_version"], 3)
            self.assertEqual(state["state"], "prepared")
            self.assertIsNone(state["workers"][0]["pane_id"])
            self.assertIn("worker1", (directory / "rules/worker1.md").read_text())
            self.assertNotIn("worker2.md", (directory / "rules/worker1.md").read_text())
            self.assertIn("herdr-peers", (directory / "rules/common.md").read_text())
            self.assertTrue((directory / "task_plan.md").is_file())
            self.assertTrue((directory / "workers/worker1.md").is_file())
            self.assertNotIn("pin", records.context(state, "worker1").lower())
            context = records.context(state, "worker1")
            script_path = Path(records.__file__).resolve().with_name("communicate.py")
            self.assertIn(f"COMMUNICATION_SCRIPT={script_path}", context)
            self.assertTrue(script_path.is_file())
            self.assertNotIn("lead record", (directory / "task_plan.md").read_text())

    def test_modified_or_missing_role_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as project:
            state = records.prepare(project, 1, [], "codex", "w1:p1", "w1")
            path = Path(state["plan_dir"]) / "rules/worker1.md"
            path.write_text("silently changed role")
            with self.assertRaisesRegex(ValueError, "Role rules changed"):
                records.load(state["plan_dir"])


if __name__ == "__main__":
    unittest.main()
