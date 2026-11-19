import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_relocated_skill_resolves_and_runs_helper_without_external_install(self):
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            package = sandbox / 'relocated skill'
            shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', '.omx', '.git', '.DS_Store'))
            home = sandbox / 'empty-home'
            home.mkdir()
            env = {'HOME': str(home), 'PATH': '/usr/bin:/bin',
                   'PYTHONDONTWRITEBYTECODE': '1', 'HERDR_PEERS_PYTHON': sys.executable}
            probe = ('import json,sys; sys.path.insert(0,sys.argv[1]); '
                     'import create_framework; print(json.dumps(create_framework.peer_helper()))')
            result = subprocess.run([sys.executable, '-B', '-c', probe, str(package / 'scripts')],
                                    env=env, cwd=sandbox, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            helper = Path(json.loads(result.stdout))
            self.assertTrue(helper.is_relative_to(package.resolve()))
            version = subprocess.run([str(helper), '--version'], env=env, cwd=sandbox,
                                     capture_output=True, text=True)
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertEqual(version.stdout.strip(), 'herdr-peers 2.2.1 (protocol 2)')

    def test_bundled_help_and_provenance_keep_one_discoverable_skill(self):
        bundle = ROOT / 'vendor/herdr-peers'
        self.assertEqual(sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob('SKILL.md')), ['SKILL.md'])
        manifest = json.loads((bundle / 'UPSTREAM.json').read_text())
        self.assertEqual(manifest['license'], 'MIT')
        self.assertIn('Copyright (c) 2026 DailybotHQ contributors', (bundle / 'LICENSE').read_text())
        for entry in manifest['files']:
            with self.subTest(path=entry['path']):
                self.assertEqual(hashlib.sha256((bundle / entry['path']).read_bytes()).hexdigest(),
                                 entry['bundled_sha256'])
        helper = bundle / 'scripts/herdr-peers'
        for flag in ('--help', '--skill'):
            result = subprocess.run([str(helper), flag], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('herdr-peers', result.stdout)
        self.assertEqual(result.stdout, (bundle / 'UPSTREAM_GUIDE.md').read_text())

    def test_old_external_helper_override_is_not_accepted(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/create_framework.py'),
                                 'create', '--workers', '1', '--main-agent', 'codex',
                                 '--peer-bin', '/external/herdr-peers'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unrecognized arguments', result.stderr)



if __name__ == '__main__':
    unittest.main()
