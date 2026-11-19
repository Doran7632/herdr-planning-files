import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import launch


class LaunchTests(unittest.TestCase):
    def test_codex_skips_startup_update_without_changing_permission_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            role = Path(tmp) / 'role.txt'
            role.write_text('Keep my role instructions.')
            args = launch.native_arguments('codex', role)
            self.assertIn('check_for_update_on_startup=false', args)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
            self.assertNotIn('approval_policy=never', args)

    def test_native_arguments_keep_base_and_do_not_replace_system_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            role = Path(tmp) / "worker.launch.txt"
            role.write_text("用户既有追加规则\n本框架 worker1 规则\n", encoding="utf-8")
            for kind in ("codex", "claude code", "omp"):
                args = launch.native_arguments(kind, role)
                self.assertNotIn("--system-prompt", args)
                self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
                if kind == "codex":
                    self.assertIn("--no-daemon", args)
                    value = args[args.index("-c") + 1].split("=", 1)[1]
                    self.assertEqual(json.loads(value), role.read_text())
                else:
                    self.assertEqual(args[args.index("--append-system-prompt") + 1], role.read_text())

    def test_wrapper_keeps_long_prompt_out_of_terminal_command(self):
        command = launch.entry_command(Path("/tmp/project with space/.planning/test"), "worker1")
        self.assertIn("agent_entry.py", command)
        self.assertNotIn("developer_instructions", command)
        self.assertLess(len(command.encode()), 900)

    def test_existing_append_rules_require_explicit_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            project = Path(tmp) / "project"
            (home / ".codex").mkdir(parents=True)
            project.mkdir()
            (home / ".codex/config.toml").write_text('developer_instructions = "keep me"\n')
            with self.assertRaisesRegex(ValueError, "base-instructions"):
                launch.check_append_conflicts("codex", project, home=home, env={})
            base = home / "reviewed-base.txt"
            base.write_text("keep me")
            self.assertEqual(launch.check_append_conflicts("codex", project, home=home, env={}, base_file=base), "keep me")
            base.write_text("")
            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                launch.check_append_conflicts("codex", project, home=home, env={}, base_file=base)


if __name__ == "__main__":
    unittest.main()
