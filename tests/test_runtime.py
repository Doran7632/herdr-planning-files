import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import create_framework as runner


class RuntimeTests(unittest.TestCase):
    def test_no_herdr_context_fails_before_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            with self.assertRaisesRegex(RuntimeError, 'HERDR_ENV'):
                runner.Runtime(env={}, transport=lambda *a, **k: calls.append(a))
            self.assertEqual(calls, [])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_cli_prints_stack_before_business_error(self):
        env = dict(os.environ, HERDR_ENV='')
        result = subprocess.run([sys.executable, str(Path(runner.__file__)), 'create',
                                 '--workers', '1', '--main-agent', 'codex'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Traceback (most recent call last)', result.stderr)
        self.assertLess(result.stderr.index('Traceback'), result.stderr.index('框架操作未完成'))

class FakeHerdr:
    """External CLI boundary fixture matching Herdr v0.9.3 response fields."""
    def __init__(self, project, count=2):
        import threading
        self.project = str(Path(project).resolve())
        self.panes = {'w1:p1': self.pane('w1:p1', 'codex')}
        self.agents = {'w1:p1': self.agent('w1:p1', 'codex', 100)}
        self.calls = []
        self.state = None
        self.barrier = threading.Barrier(count)
        self.fail_split = False
        self.fail_after_submit = False
        self.extra_after_split = False

    def pane(self, pane, kind=None):
        return {'pane_id': pane, 'workspace_id': 'w1', 'tab_id': 'w1:t1',
                'terminal_id': 'terminal-' + pane, 'cwd': self.project, 'agent': kind}

    def agent(self, pane, kind, pid):
        return {**self.pane(pane, kind), 'agent_status': 'idle',
                'agent_session': {'kind': 'session_id', 'value': 'session-' + pane}, '_pid': pid}

    def __call__(self, argv, timeout):
        import json
        import shlex
        import records
        self.calls.append(argv)
        args = argv[1:]
        if args[:2] == ['pane', 'read']:
            return 'Ordinary agent input screen'
        if args[:2] == ['agent', 'list']:
            result = {'agents': list(self.agents.values())}
        elif args[:2] == ['pane', 'current']:
            result = {'pane': self.panes['w1:p1']}
        elif args[:2] == ['pane', 'get']:
            result = {'pane': self.panes[args[2]]}
        elif args[:2] == ['pane', 'process-info']:
            pane = args[3]
            pid = self.agents.get(pane, {}).get('_pid', 10)
            result = {'process_info': {'pane_id': pane, 'shell_pid': 10,
                      'foreground_process_group_id': pid, 'foreground_processes': [{'pid': pid}]}}
        elif args[:2] == ['pane', 'layout']:
            result = {'layout': {'tab_id': 'w1:t1', 'panes': [
                {'pane_id': p, 'rect': {'width': 120, 'height': 40}} for p in self.panes]}}
        elif args[:2] == ['pane', 'split']:
            pane = 'w1:p' + str(len(self.panes) + 1)
            self.panes[pane] = self.pane(pane)
            if self.fail_split:
                self.fail_split = False
                raise TimeoutError('Response lost AFTER split')
            result = {'pane': self.panes[pane]}
        elif args[:2] == ['pane', 'run']:
            pane = args[2]
            launch_args = shlex.split(args[3])
            directory = launch_args[launch_args.index('--plan-dir') + 1]
            role = launch_args[-1]
            state = records.load(directory)
            worker = next(w for w in state['workers'] if w['role'] == role)
            pid = 200 + int(role[6:])
            receipt = {'role': role, 'pane_id': pane, 'pid': pid, 'stage': 'exec_requested',
                       'instructions_sha256': worker['launch_hash']}
            records.atomic_text(Path(directory) / 'workers' / f'{role}.launch.json', json.dumps(receipt))
            kind = {'claude code': 'claude'}.get(worker['agent'], worker['agent'])
            self.panes[pane]['agent'] = kind
            self.agents[pane] = self.agent(pane, kind, pid)
            # A serial implementation breaks this barrier: test actual overlap, not elapsed-time guesses.
            self.barrier.wait(timeout=3)
            if self.fail_after_submit:
                raise TimeoutError('Response lost AFTER admission')
            result = {'type': 'pane_run'}
        else:
            raise AssertionError(argv)
        return json.dumps({'id': 'fixture', 'result': result})


def prepared(project, count=2):
    import records
    state = records.prepare(project, count, [], 'codex', 'w1:p1', 'w1')
    state['main']['identity'] = {'pane_id': 'w1:p1', 'terminal_id': 'terminal-w1:p1',
        'agent': 'codex', 'pid': 100, 'agent_session': {'kind': 'session_id', 'value': 'session-w1:p1'}}
    state['peers']['helper'] = '/test/herdr-peers'
    for worker in state['workers']:
        worker['executable'] = sys.executable
    records.save(state)
    return state


def runtime(transport):
    return runner.Runtime(env={'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1', 'HERDR_WORKSPACE_ID': 'w1'},
                          transport=transport, seconds=10)


class LifecycleTests(unittest.TestCase):
    def test_parallel_native_start_and_idempotent_resume(self):
        import records
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project)
            transport = FakeHerdr(project)
            final = runner.execute(runtime(transport), state)
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(len(transport.panes), 3)
            self.assertTrue(all(w['identity'] for w in final['workers']))
            splits = [c for c in transport.calls if c[1:3] == ['pane', 'split']]
            self.assertTrue(all('--no-focus' in c and str(Path(project).resolve()) in c for c in splits))
            starts = [c for c in transport.calls if c[1:3] == ['pane', 'run']]
            self.assertEqual(len(starts), 2)
            self.assertTrue(all('developer_instructions' not in c[4] for c in starts))
            runner.execute(runtime(transport), records.load(final['plan_dir']))
            self.assertEqual(len([c for c in transport.calls if c[1:3] == ['pane', 'run']]), 2)
            self.assertFalse(any(c[1] in ('lead', 'notify', 'ask') for c in transport.calls))

    def test_uncertain_split_is_not_duplicated_and_can_be_explicitly_adopted(self):
        import records
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project)
            transport = FakeHerdr(project)
            transport.fail_split = True
            with self.assertRaises(TimeoutError):
                runner.execute(runtime(transport), state)
            self.assertEqual(len(transport.panes), 2)
            with self.assertRaisesRegex(RuntimeError, 'Uncertain split'):
                runner.execute(runtime(transport), records.load(state['plan_dir']))
            self.assertEqual(len(transport.panes), 2)
            final = runner.execute(runtime(transport), records.load(state['plan_dir']), adoptions={'worker1': 'w1:p2'})
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(len(transport.panes), 3)

    def test_lost_start_response_resumes_by_observation_without_resubmission(self):
        import records
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project)
            transport = FakeHerdr(project)
            transport.fail_after_submit = True
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                runner.execute(runtime(transport), state)
            final = runner.execute(runtime(transport), records.load(state['plan_dir']))
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(len([c for c in transport.calls if c[1:3] == ['pane', 'run']]), 2)

    def test_changed_live_agent_is_rejected(self):
        import records
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project)
            transport = FakeHerdr(project)
            final = runner.execute(runtime(transport), state)
            transport.agents['w1:p2']['agent_session']['value'] = 'replacement-session'
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                runner.execute(runtime(transport), records.load(final['plan_dir']))
            self.assertEqual(len(transport.panes), 3)

class BoundaryTests(unittest.TestCase):
    def test_unknown_worker_cwd_does_not_receive_input(self):
        import records
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            transport = FakeHerdr(project, 1)
            rt = runtime(transport)
            runner.split_workers(rt, state)
            runner.prepare_launch_files(state, {})
            transport.panes['w1:p2'].pop('cwd')
            previous = os.getcwd()
            try:
                os.chdir(state['project_root'])
                with self.assertRaisesRegex(RuntimeError, 'cwd'):
                    runner.start_one(rt, state, state['workers'][0], True)
            finally:
                os.chdir(previous)
            self.assertFalse(any(c[1:3] == ['pane', 'run'] for c in transport.calls))

    def test_missing_peer_dependency_stops_preflight_before_panes(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as project:
            transport = FakeHerdr(project, 1)
            with patch.object(runner, 'peer_helper', side_effect=runner.FrameworkError('missing dependency')):
                previous = os.getcwd()
                try:
                    os.chdir(project)
                    with self.assertRaisesRegex(RuntimeError, 'missing dependency'):
                        runner.preflight(runtime(transport), project, ['codex'], 'codex')
                finally:
                    os.chdir(previous)
            self.assertEqual(transport.calls, [])

    def test_concurrent_resume_lock_rejects_second_runner_before_mutations(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            transport = FakeHerdr(project, 1)
            with runner.locked(state['plan_dir']):
                with self.assertRaises(BlockingIOError):
                    runner.execute(runtime(transport), state)
            self.assertEqual(transport.calls, [])


class CliAdmissionTests(unittest.TestCase):
    def test_pane_run_empty_success_output_is_not_parsed_as_json(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = FakeHerdr(project, 1)
            def transport(argv, timeout):
                output = fake(argv, timeout)
                return '' if argv[1:3] == ['pane', 'run'] else output
            final = runner.execute(runtime(transport), state)
            self.assertEqual(final['state'], 'ready')

    def test_native_idle_without_managed_ready_flag_resumes_without_resubmission(self):
        import records
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = FakeHerdr(project, 1)
            rt = runtime(fake)
            runner.split_workers(rt, state)
            runner.prepare_launch_files(state, {})
            worker = state['workers'][0]
            # Reproduce the live run: native process already launched, admission response was lost.
            rt.run(['herdr', 'pane', 'run', worker['pane_id'],
                    runner.launch.entry_command(state['plan_dir'], worker['role'])])
            worker['launch_state'] = 'submitted_unknown'
            records.save(state)
            self.assertNotIn('interactive_ready', fake.agents[worker['pane_id']])
            resumed = runner.Runtime(env=rt.env, transport=fake, seconds=1)
            final = runner.execute(resumed, state)
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(final['workers'][0]['identity']['pid'], 201)
            self.assertEqual(len([c for c in fake.calls if c[1:3] == ['pane', 'run']]), 1)

    def test_unsettled_or_blocked_native_workers_are_not_ready(self):
        import contextlib
        import io
        import records
        for status, pending in [('blocked', False), ('unknown', False), ('working', False), ('idle', True)]:
            with self.subTest(status=status, pending=pending), tempfile.TemporaryDirectory() as project:
                state = prepared(project, 1)
                fake = FakeHerdr(project, 1)
                def transport(argv, timeout):
                    result = fake(argv, timeout)
                    if argv[1:3] == ['pane', 'run']:
                        fake.agents[argv[3]].update(agent_status=status, launch_pending=pending)
                    return result
                rt = runner.Runtime(env={'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p1',
                                         'HERDR_WORKSPACE_ID': 'w1'}, transport=transport, seconds=1)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    runner.execute(rt, state)
                saved = records.load(state['plan_dir'])
                self.assertEqual(saved['state'], 'incomplete')
                self.assertIsNone(saved['workers'][0]['identity'])
                self.assertIn('blocked' if status == 'blocked' else 'TimeoutError', saved['workers'][0]['last_error'])
                self.assertEqual(len([c for c in fake.calls if c[1:3] == ['pane', 'run']]), 1)


if __name__ == '__main__':
    unittest.main()
