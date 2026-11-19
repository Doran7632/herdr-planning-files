import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

class WorkspaceTests(unittest.TestCase):
    def test_new_supervisor_skips_update_only_for_its_process(self):
        from unittest.mock import patch
        import start_workspace as module
        with tempfile.TemporaryDirectory() as root:
            request = {'run_dir': root, 'supervisor_executable': '/fixture/codex'}
            with patch.object(module, 'require_supervisor'), patch.object(module.os, 'execve') as native_exec:
                module.entry(request)
            argv = native_exec.call_args.args[1]
            self.assertEqual(argv[:4], ['/fixture/codex', '--no-daemon', '-c', 'check_for_update_on_startup=false'])
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)

    def test_outside_herdr_cannot_create_workspace(self):
        module = importlib.import_module('start_workspace')
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, module.__file__, 'start', '--project', root],
                                    env=dict(os.environ, HERDR_ENV=''), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('HERDR_ENV', result.stderr)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_supervisor_prompt_uses_new_source_and_explicit_verification(self):
        module = importlib.import_module('start_workspace')
        text = module.supervisor_prompt(Path('/tmp/test project/.herdr-bootstrap'))
        self.assertIn('build', text)
        self.assertIn('adopt', text)
        self.assertIn('verify', text)
        self.assertIn('Session Pin', text)
        self.assertNotIn('--dangerously', text)
        self.assertIn('收到的完整', text)


class WorkspaceFlowTests(unittest.TestCase):
    def preflight(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        import start_workspace as module
        stack = ExitStack()
        stack.enter_context(patch.object(module.runner, 'peer_helper', return_value='/fixture/herdr-peers'))
        stack.enter_context(patch.object(module.runner, 'check_peers', return_value='2.2.1'))
        stack.enter_context(patch.object(module.launch, 'capabilities', return_value={'executable':sys.executable}))
        stack.enter_context(patch.object(module.launch, 'check_append_conflicts', return_value=''))
        return stack

    def test_new_workspace_uses_returned_ids_and_repeat_does_not_duplicate(self):
        import start_workspace as module
        with tempfile.TemporaryDirectory() as root, self.preflight():
            calls=[]
            class Fake:
                def run(self,argv):
                    calls.append(tuple(argv[1:]))
                    return ''
                def api(self,*args):
                    calls.append(args)
                    if args[:2]==('workspace','create'):
                        return {'workspace':{'workspace_id':'wC'}, 'root_pane':{'pane_id':'wC:p7','workspace_id':'wC'}}
                    if args[:2]==('pane','get'):
                        return {'pane':{'agent':None,'cwd':root}}
                    if args[:2]==('pane','process-info'):
                        return {'process_info':{'shell_pid':42,'foreground_process_group_id':42}}
                    if args[:2]==('pane','run'):
                        return {'type':'admitted'}
                    raise AssertionError(args)
            result=module.start(Fake(),root,3,['worker3=omp'],'isolated-test')
            self.assertEqual(result['main_pane'],'wC:p7')
            runs=[c for c in calls if c[:2]==('pane','run')]
            self.assertEqual(len(runs),1)
            self.assertEqual(runs[0][2],'wC:p7')
            self.assertNotIn('--dangerously',runs[0][3])
            with self.assertRaisesRegex(RuntimeError,'Existing test run'):
                module.start(Fake(),root,3,['worker3=omp'],'isolated-test')
            self.assertEqual(len([c for c in calls if c[:2]==('workspace','create')]),1)

    def test_lost_workspace_response_keeps_unknown_checkpoint(self):
        import json
        import start_workspace as module
        with tempfile.TemporaryDirectory() as root, self.preflight():
            class Fake:
                def api(self,*args): raise TimeoutError('workspace mutation response lost')
            with self.assertRaises(TimeoutError): module.start(Fake(),root,1,[],'test')
            request=json.loads((Path(root)/'.herdr-bootstrap/request.json').read_text())
            self.assertEqual(request['state'],'workspace_create_unknown')
            with self.assertRaisesRegex(RuntimeError,'Existing test run'):
                module.start(Fake(),root,1,[],'test')

    def test_verifier_does_not_pass_without_worker_and_receiver_evidence(self):
        import records
        import start_workspace as module
        with tempfile.TemporaryDirectory() as root:
            state=records.prepare(root,1,[],'codex','wC:p1','wC')
            directory=Path(root)/'.herdr-bootstrap'; directory.mkdir()
            request={'run_dir':str(directory),'plan_dir':state['plan_dir'],'nonce':'unique'}
            report=module.verify(request)
            self.assertEqual(report['result'],'incomplete')
            self.assertFalse(report['checks'][0]['main_recorded_notice'])


if __name__ == '__main__':
    unittest.main()
