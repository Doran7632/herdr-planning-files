import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import create_framework as runner
import records
from test_runtime import FakeHerdr, prepared, runtime


class TrustHerdr(FakeHerdr):
    def __init__(self, project):
        super().__init__(project, 1)
        self.displayed_project = self.project
        self.selected_yes = False
        self.changed_dialog = False
        self.change_after_down = False
        self.replace_after_down = False
        self.lose_confirmation = False
        self.dismissed = False
        self.inputs = []

    def screen(self):
        if self.dismissed:
            return 'Claude Code\n❯ Try a task'
        if self.changed_dialog:
            return 'Allow running a command?\n❯ Yes\n  No\nEnter to confirm'
        return ('Accessing workspace:\n\n' + self.displayed_project + '\n\n'
                'Quick safety check: Is this a project you created or one you trust?\n'
                "Claude Code'll be able to read, edit, and execute files here.\n\n"
                + ('  No, exit\n❯ Yes, I trust this folder' if self.selected_yes else
                   '❯ No, exit\n  Yes, I trust this folder')
                + '\n\nEnter to confirm · Esc to cancel\n')

    def __call__(self, argv, timeout):
        if argv[1:3] == ['pane', 'read']:
            self.calls.append(argv)
            return self.screen()
        if argv[1:3] == ['agent', 'send-keys']:
            self.calls.append(argv)
            self.inputs.append(argv[4:])
            if argv[4:] == ['down']:
                self.selected_yes = True
                if self.change_after_down:
                    self.changed_dialog = True
                if self.replace_after_down:
                    self.agents[argv[3]]['_pid'] += 1
            elif argv[4:] == ['enter']:
                if self.lose_confirmation:
                    raise TimeoutError('Confirmation admission unknown')
                self.agents[argv[3]]['agent_status'] = 'idle'
                self.dismissed = True
            else:
                raise AssertionError(argv)
            return ''
        output = super().__call__(argv, timeout)
        if argv[1:3] == ['pane', 'run']:
            self.agents[argv[3]]['agent_status'] = 'blocked'
        return output


class StartupDialogTests(unittest.TestCase):
    def test_trusts_only_matching_project_and_verifies_selection_before_enter(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = TrustHerdr(project)
            final = runner.execute(runtime(fake), state)
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(fake.inputs, [['down'], ['enter']])
            audit = json.loads((Path(state['plan_dir']) / 'workers/worker1.startup-trust.json').read_text())
            self.assertEqual(audit['project_root'], state['project_root'])
            self.assertEqual(audit['state'], 'confirmation_submitted')
            runner.execute(runtime(fake), final)
            self.assertEqual(fake.inputs, [['down'], ['enter']])

    def test_preselected_yes_is_confirmed_without_moving_to_no(self):
        with tempfile.TemporaryDirectory() as project:
            fake = TrustHerdr(project)
            fake.selected_yes = True
            final = runner.execute(runtime(fake), prepared(project, 1))
            self.assertEqual(final['state'], 'ready')
            self.assertEqual(fake.inputs, [['enter']])

    def test_unrelated_prompt_or_wrong_directory_receives_no_input(self):
        for scenario in ('other_directory', 'command_approval', 'unmarked_choice'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as project:
                state = prepared(project, 1)
                fake = TrustHerdr(project)
                if scenario == 'other_directory':
                    fake.displayed_project = str(Path.home())
                elif scenario == 'command_approval':
                    fake.changed_dialog = True
                else:
                    original = fake.screen
                    fake.screen = lambda: original().replace('❯', ' ')
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    runner.execute(runtime(fake), state)
                self.assertEqual(fake.inputs, [])

    def test_changed_dialog_after_navigation_never_gets_enter(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = TrustHerdr(project)
            fake.change_after_down = True
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                runner.execute(runtime(fake), state)
            self.assertEqual(fake.inputs, [['down']])
            saved = records.load(state['plan_dir'])
            self.assertIn('no Enter sent', saved['workers'][0]['last_error'])

    def test_replacement_process_never_gets_confirmation(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = TrustHerdr(project)
            fake.replace_after_down = True
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                runner.execute(runtime(fake), state)
            self.assertEqual(fake.inputs, [['down']])
            self.assertIn('launcher process', records.load(state['plan_dir'])['workers'][0]['last_error'])

    def test_uncertain_confirmation_is_not_replayed_after_resume(self):
        with tempfile.TemporaryDirectory() as project:
            state = prepared(project, 1)
            fake = TrustHerdr(project)
            fake.lose_confirmation = True
            for _ in range(2):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                    runner.execute(runtime(fake), records.load(state['plan_dir']))
            self.assertEqual(fake.inputs, [['down'], ['enter']])
            audit = json.loads((Path(state['plan_dir']) / 'workers/worker1.startup-trust.json').read_text())
            self.assertEqual(audit['state'], 'confirmation_requested')
            self.assertEqual(len([c for c in fake.calls if c[1:3] == ['pane', 'run']]), 1)


if __name__ == '__main__':
    unittest.main()
