"""Run with python3 -m unittest discover -s tests -v."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/op15-gki-profile.py'
CONFIG = json.loads((ROOT / 'configs/a16/OP15-6.12.69.json').read_text())
PINS = json.loads((ROOT / 'configs/gki/op15-6.12.69-pins.json').read_text())


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cwd = Path(self.temporary.name)

    def invoke(self, mode, extra=None, success=True):
        output = self.cwd / 'outputs'
        output.write_text('')
        env = os.environ.copy()
        # Isolate tests from a caller's selected build profile.
        for key in list(env):
            if key.startswith('OP15_'):
                del env[key]
        env.update(GITHUB_OUTPUT=str(output), GITHUB_ENV=str(output),
                   OP15_CONFIG_JSON=json.dumps(CONFIG), OP15_KSU_TYPE='KSUN')
        env.update(extra or {})
        result = subprocess.run([sys.executable, str(HELPER), mode], cwd=self.cwd,
                                env=env, capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), '')
        return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_old_op15_remains_on_legacy_builder(self):
        legacy = json.loads((ROOT / 'configs/a16/OP15.json').read_text())
        self.assertEqual(self.invoke('route', {'OP15_CONFIG_JSON': json.dumps(legacy)}), {'op15': 'false'})

    def test_new_op15_routes_to_pinned_engine(self):
        result = self.invoke('route')
        self.assertEqual(result, {'op15': 'true', 'root_mode': 'KSUN-SUSFS',
                                 'wild_features': 'true', 'droidspaces': 'false'})

    def test_rejects_unvalidated_kmi_and_source_overrides(self):
        cases = [{'OP15_KSU_TYPE': 'KSU'}, {'OP15_KSU_REF': 'a' * 40},
                 {'OP15_SUSFS_REF': 'gki-android16-6.12'}, {'OP15_OPTIMIZE': 'O3'},
                 {'OP15_TIMESTAMP': 'custom timestamp'},
                 {'OP15_CONFIG_JSON': json.dumps(CONFIG | {'kmi_generation': 5})},
                 {'OP15_CONFIG_JSON': json.dumps(CONFIG | {'build_profile': 'legacy'})},
                 {'OP15_CONFIG_JSON': json.dumps(CONFIG | {'build_profile': 'unknown'})}]
        for extra in cases:
            with self.subTest(extra=extra):
                self.invoke('route', extra, success=False)

    def test_matrix_uses_fixed_refs_and_exposes_release_metadata(self):
        result = self.invoke('matrix', {'OP15_KSU_OPTIONS': '[{"type":"ksun","hash":"dev"}]'})
        entry, = json.loads(result['matrix'])['include']
        self.assertEqual(entry['model'], 'OP15-6.12.69')
        self.assertEqual(entry['ksu_resolved_hash'], PINS['KSUN_COMMIT'])
        self.assertEqual(entry['susfs_resolved_hash'], PINS['SUSFS_COMMIT'])
        self.assertEqual(result['susfs_base_version'], 'v2.3.0')
        self.assertEqual(json.loads((self.cwd / 'matrix.json').read_text()), [entry])
        self.assertFalse(entry['droidspaces'])

    def test_matrix_rejects_unsupported_variant_and_multiple_builds(self):
        for options in ['[]', '[{"type":"ksu","hash":"main"}]',
                        '[{"type":"ksun"},{"type":"ksun"}]']:
            with self.subTest(options=options):
                self.invoke('matrix', {'OP15_KSU_OPTIONS': options}, success=False)

    def test_composite_false_strings_do_not_enable_features(self):
        result = self.invoke('setup', {'OP15_WILD_FEATURES': 'false', 'OP15_DROIDSPACES': 'true'})
        self.assertEqual(result['WILD_FEATURES'], 'false')
        self.assertEqual(result['DROIDSPACES'], 'false')
        self.assertEqual(result['FEATURE_SUFFIX'], '')
        result = self.invoke('setup', {'OP15_ROOT_MODE': 'stock', 'OP15_WILD_FEATURES': 'true',
                                       'OP15_DROIDSPACES': 'true'})
        self.assertEqual(result['WILD_FEATURES'], 'false')
        self.assertEqual(result['DROIDSPACES'], 'false')
        self.assertEqual(result['ACK_COMMIT'], '242e1a07f8782987c579929a20a74c0b26e88608')

    def artifact_fixture(self):
        directory = self.cwd / 'artifacts'
        directory.mkdir()
        for name, data in [('Image', b'image'), ('AK3-test.zip', b'zip')]:
            (directory / name).write_bytes(data)
        (directory / 'SHA256SUMS').write_text(''.join(
            f'{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n'
            for name in ['Image', 'AK3-test.zip']))
        (directory / 'kernel-version.txt').write_text(
            'Linux version 6.12.69-android16-6-test #1 SMP PREEMPT Thu Jan 1 00:00:00 UTC 1970\n')
        return directory

    def test_outputs_report_verified_artifacts_and_real_timestamp(self):
        self.artifact_fixture()
        result = self.invoke('metadata', {'ROOT_MODE': 'KSUN-SUSFS',
                                         'OP15_BUILD_STARTED': str(int(time.time()) - 5)})
        self.assertEqual(result['zip_name'], 'AK3-test.zip')
        self.assertEqual(result['kernel_version'], 'android16-6.12.69')
        self.assertEqual(result['ksu_version'], '33326')
        self.assertEqual(result['build_timestamp'], 'Thu Jan 1 00:00:00 UTC 1970')
        self.assertEqual(result['image_sha256'], hashlib.sha256(b'image').hexdigest())
        self.assertGreaterEqual(int(result['build_time']), 5)

    def test_outputs_reject_stale_checksum(self):
        directory = self.artifact_fixture()
        (directory / 'Image').write_bytes(b'changed')
        self.invoke('metadata', {'ROOT_MODE': 'KSUN-SUSFS', 'OP15_BUILD_STARTED': '0'}, success=False)


if __name__ == '__main__':
    unittest.main()
