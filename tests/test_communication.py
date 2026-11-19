import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from test_runtime import FakeHerdr, prepared, runtime
import create_framework as runner


class CommunicationTests(unittest.TestCase):
    def test_notice_nonzero_exit_is_classified_not_replied_to(self):
        communication = importlib.import_module('communicate')
        result = communication.classify_result(subprocess.CompletedProcess([], 2, '{"decision":"notice"}', ''))
        self.assertEqual(result['decision'], 'notice')
        with self.assertRaisesRegex(RuntimeError, 'decision'):
            communication.classify_result(subprocess.CompletedProcess([], 0, '{}', ''))

    def test_message_binds_sender_and_target_and_persists_before_delivery(self):
        communication = importlib.import_module('communicate')
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            transport = FakeHerdr(project, 1)
            state = runner.execute(runtime(transport), state)
            calls = []
            def peer_run(argv, timeout):
                outbox = list((Path(state['plan_dir']) / 'workers').glob('main.outgoing-*.json'))
                self.assertEqual(len(outbox), 1)
                self.assertEqual(json.loads(outbox[0].read_text())['state'], 'delivery_unknown')
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, '01TESTMESSAGEID\n', '')
            result = communication.send(runtime(transport), state, 'main', 'worker1', 'notify', 'TASK-1: read only', peer_run)
            self.assertEqual(result['state'], 'submitted')
            self.assertIn('local:w1:p2', calls[0])
            self.assertEqual(calls[0][calls[0].index('--pane') + 1], 'w1:p1')
            self.assertEqual(calls[0][calls[0].index('--wait-blocked') + 1], '0')
            self.assertIn(state['plan_id'], calls[0][-1])

    def test_changed_target_prevents_delivery(self):
        communication = importlib.import_module('communicate')
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            transport = FakeHerdr(project, 1)
            state = runner.execute(runtime(transport), state)
            transport.agents['w1:p2']['_pid'] = 999
            calls = []
            with self.assertRaisesRegex(RuntimeError, 'process'):
                communication.send(runtime(transport), state, 'main', 'worker1', 'notify', 'hello', lambda *a, **k: calls.append(a))
            self.assertEqual(calls, [])

    def test_old_plan_message_is_rejected_before_protocol_side_effects(self):
        communication = importlib.import_module('communicate')
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            transport = FakeHerdr(project, 1)
            state = runner.execute(runtime(transport), state)
            message = Path(project) / 'received.txt'
            message.write_text('PLAN_ID=other-plan\nQuestion?\n\n[herdr-peers] protocol=1 from=local:w1:p2 reply=yes depth=0 id=01TEST\n')
            calls = []
            with self.assertRaisesRegex(RuntimeError, 'PLAN_ID'):
                communication.check(runtime(transport), state, 'main', message, lambda *a, **k: calls.append(a))
            self.assertEqual(calls, [])
