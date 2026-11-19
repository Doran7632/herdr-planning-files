import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import records
import create_framework as runner


class EntryTests(unittest.TestCase):
    def setup_worker(self, project):
        state = records.prepare(project, 1, ['worker1=codex'], 'codex', 'w1:p1', 'w1')
        worker = state['workers'][0]
        worker['pane_id'] = 'w1:p2'
        worker['launch_state'] = 'submitted_unknown'
        fake = Path(project) / 'fake-native-cli'
        fake.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                        'print(json.dumps({"args":sys.argv[1:], "role":os.environ["HERDR_FRAMEWORK_ROLE"],'
                        '"plan":os.environ["PLAN_ID"], "depth":os.environ.get("HERDR_PEERS_DEPTH"),'
                        '"dwp":os.environ.get("DWP_PLAN")}))\n')
        fake.chmod(0o700)
        worker['executable'] = str(fake)
        records.save(state)
        runner.prepare_launch_files(state, {'worker1': 'Keep existing user instructions.\n' * 500})
        return state

    def invoke(self, state, **overrides):
        env = dict(os.environ, HERDR_ENV='1', HERDR_PANE_ID='w1:p2', HERDR_WORKSPACE_ID='w1', HERDR_PEERS_DEPTH='1', DWP_PLAN='unrelated-plan')
        env.update(overrides)
        return subprocess.run([sys.executable, str(Path(runner.__file__).with_name('agent_entry.py')),
                               '--plan-dir', state['plan_dir'], '--role', 'worker1'], env=env,
                               cwd=state['project_root'], text=True, capture_output=True)

    def test_real_exec_delivers_large_native_argv_and_scoped_env_without_prompt_turn(self):
        with tempfile.TemporaryDirectory() as project:
            state = self.setup_worker(project)
            result = self.invoke(state)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual(observed['role'], 'worker1')
            self.assertEqual(observed['plan'], state['plan_id'])
            self.assertIsNone(observed['depth'])
            self.assertIsNone(observed['dwp'])
            self.assertEqual(observed['args'][:2], ['--no-daemon', '-c'])
            text = json.loads(observed['args'][2].split('=', 1)[1])
            self.assertTrue(text.startswith('Keep existing user instructions.'))
            self.assertGreater(len(text), 10000)
            self.assertIn('SELF_PANE=w1:p2', text)
            self.assertEqual(observed['args'][3:], ['-c', 'check_for_update_on_startup=false'])
            receipt = json.loads((Path(state['plan_dir']) / 'workers/worker1.launch.json').read_text())
            self.assertEqual(receipt['instructions_sha256'], state['workers'][0]['launch_hash'])
            self.assertEqual(receipt['stage'], 'exec_requested')

    def test_wrong_actual_pane_or_edited_rules_never_exec(self):
        with tempfile.TemporaryDirectory() as project:
            state = self.setup_worker(project)
            result = self.invoke(state, HERDR_PANE_ID='w1:p9')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            prompt = Path(state['plan_dir']) / 'rules/worker1.launch.txt'
            prompt.write_text('changed')
            result = self.invoke(state)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('instructions changed', result.stderr)
            self.assertFalse((Path(state['plan_dir']) / 'workers/worker1.launch.json').exists())
