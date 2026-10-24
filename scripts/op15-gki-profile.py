#!/usr/bin/env python3
"""Route and describe the validated OP15 r54 build without resolving moving refs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'op15-gki-r54'
PINS_PATH = ROOT / 'configs/gki/op15-6.12.69-pins.json'


def pins():
    values = json.loads(PINS_PATH.read_text())
    for key, value in values.items():
        if key.endswith('_COMMIT') and not re.fullmatch('[0-9a-f]{40}', value):
            raise ValueError(f'Invalid pinned revision: {key}')
    return values


def emit(destination, values):
    text = ''.join(f'{key}={value}\n' for key, value in values.items())
    if any('\n' in str(value) or '\r' in str(value) for value in values.values()):
        raise ValueError('Multiline action output is not supported')
    with Path(os.environ[destination]).open('a') as stream:
        stream.write(text)


def boolean(value):
    if str(value).lower() not in ('true', 'false'):
        raise ValueError(f'Expected true/false, got {value!r}')
    return str(value).lower() == 'true'


def validate_common(config, ksu_type, ksu_ref='', susfs_ref=''):
    if config.get('model') != 'OP15-6.12.69' or config.get('build_profile') != PROFILE:
        raise ValueError('The r54 profile requires model OP15-6.12.69')
    if (config.get('android_version'), config.get('kernel_version'), config.get('kernel_release'),
            config.get('kmi_generation'), config.get('page_size')) != ('android16', '6.12', '6.12.69', 6, 4096):
        raise ValueError('Expected Android16, Linux 6.12.69, KMI6 and 4KB pages')
    fixed = pins()
    if ksu_type.upper() != 'KSUN':
        raise ValueError('OP15-6.12.69 currently supports the validated KernelSU-Next (KSUN) profile only')
    if ksu_ref not in ('', 'dev', fixed['KSUN_COMMIT']):
        raise ValueError('OP15-6.12.69 uses pinned KernelSU-Next; use hash dev or its validated commit')
    if susfs_ref not in ('', fixed['SUSFS_COMMIT']):
        raise ValueError('OP15-6.12.69 requires the pinned SUSFS commit')
    if os.environ.get('OP15_OPTIMIZE', 'O2') != 'O2' or os.environ.get('OP15_TIMESTAMP', ''):
        raise ValueError('The validated r54 profile requires O2 and an empty custom build timestamp')
    return fixed


def route():
    config = json.loads(os.environ['OP15_CONFIG_JSON'])
    profile = config.get('build_profile', 'legacy')
    if profile == 'legacy':
        if config.get('model') == 'OP15-6.12.69':
            raise ValueError('OP15-6.12.69 must explicitly select build_profile=op15-gki-r54')
        emit('GITHUB_OUTPUT', {'op15': 'false'})
        return
    if profile != PROFILE:
        raise ValueError(f'Unknown build profile: {profile}')
    validate_common(config, os.environ['OP15_KSU_TYPE'], os.environ.get('OP15_KSU_REF', ''),
                    os.environ.get('OP15_SUSFS_REF', ''))
    wild = boolean(config.get('wild_features', True))
    droid = wild and boolean(config.get('droidspaces', False))
    emit('GITHUB_OUTPUT', {'op15': 'true', 'root_mode': 'KSUN-SUSFS' if boolean(config.get('susfs', True)) else 'KSUN',
                           'wild_features': str(wild).lower(), 'droidspaces': str(droid).lower()})


def setup():
    mode = os.environ.get('OP15_ROOT_MODE', 'KSUN-SUSFS')
    style = os.environ.get('OP15_ARTIFACT_STYLE', 'dedicated')
    if mode not in ('KSUN-SUSFS', 'KSUN', 'stock') or style not in ('dedicated', 'common'):
        raise ValueError('Invalid root mode or artifact style')
    if style == 'common' and mode == 'stock':
        raise ValueError('Common release filenames require a KernelSU-Next build')
    wild = boolean(os.environ.get('OP15_WILD_FEATURES', 'true')) and mode != 'stock'
    droid = boolean(os.environ.get('OP15_DROIDSPACES', 'false')) and wild
    emit('GITHUB_ENV', pins() | {'ROOT_MODE': mode, 'WILD_FEATURES': str(wild).lower(),
         'DROIDSPACES': str(droid).lower(), 'FEATURE_SUFFIX': '_Wild' if wild else '',
         'DROIDSPACES_SUFFIX': '_Droidspaces' if droid else '', 'OP15_ARTIFACT_STYLE': style,
         'OP15_BUILD_STARTED': str(int(time.time()))})
    print(f'OP15 r54: root={mode}, Wild={wild}, experimental Droidspaces={droid}; fixed source/toolchain revisions')


def matrix():
    config = json.loads((ROOT / 'configs/a16/OP15-6.12.69.json').read_text())
    options = json.loads(os.environ['OP15_KSU_OPTIONS'])
    if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], dict):
        raise ValueError('Select exactly one KSUN entry for the validated OP15-6.12.69 profile')
    fixed = validate_common(config, options[0].get('type', ''), options[0].get('hash') or 'dev',
                            os.environ.get('OP15_SUSFS_REF', ''))
    config |= {'ksu_type': 'KSUN', 'ksu_hash': fixed['KSUN_COMMIT'],
               'ksu_resolved_hash': fixed['KSUN_COMMIT'], 'susfs_resolved_hash': fixed['SUSFS_COMMIT']}
    Path('matrix.json').write_text(json.dumps([config], indent=2) + '\n')
    compact = lambda value: json.dumps(value, separators=(',', ':'))
    emit('GITHUB_OUTPUT', {'matrix': compact({'include': [config]}), 'count': '1',
         'ksu_options_normalized': compact([{'type': 'KSUN', 'hash': fixed['KSUN_COMMIT']}]),
         'ksu_resolved_hash': fixed['KSUN_COMMIT'], 'active_gki_keys': 'android16-6.12',
         'susfs_hash_android16_6_12': fixed['SUSFS_COMMIT'], 'susfs_base_version': fixed['SUSFS_VERSION']})
    print('Selected OP15-6.12.69: pinned KSUN/SUSFS, KMI6, 4KB; no moving-ref API resolution')


def metadata():
    fixed = pins()
    directory = Path('artifacts')
    checksums = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        checksums[name.lstrip('*')] = digest
    zip_names = [name for name in checksums if name.endswith('.zip')]
    if len(zip_names) != 1 or 'Image' not in checksums:
        raise ValueError('Expected one AnyKernel3 ZIP and one Image checksum')
    # Verify the advertised files instead of forwarding stale build metadata.
    for name, digest in checksums.items():
        if Path(name).name != name:
            raise ValueError('Unexpected artifact path')
        with (directory / name).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise ValueError(f'Checksum mismatch: {name}')
    release = (directory / 'kernel-version.txt').read_text().strip()
    timestamp = re.search(r' #\d+ SMP(?: PREEMPT(?:_DYNAMIC|_RT)?)? (.+)$', release)
    if not timestamp:
        raise ValueError('Missing build timestamp in the verified kernel release')
    mode = os.environ['ROOT_MODE']
    build_log = directory / 'build.log'
    warnings = len(re.findall(r'warning:', build_log.read_text(errors='replace'))) if build_log.exists() else ''
    emit('GITHUB_OUTPUT', {'kernel_version': 'android16-6.12.69',
         'ksu_version': fixed['KSUN_VERSION'] if mode != 'stock' else '',
         'ksu_type': 'KSUN' if mode != 'stock' else 'stock',
         'susfs_version': fixed['SUSFS_VERSION'] if mode == 'KSUN-SUSFS' else '',
         'build_timestamp': timestamp[1], 'image_sha256': checksums['Image'],
         'zip_sha256': checksums[zip_names[0]], 'zip_name': zip_names[0], 'warnings': str(warnings),
         'build_time': str(max(0, int(time.time()) - int(os.environ['OP15_BUILD_STARTED']))),
         'ccache_hit_rate': 'N/A (Kleaf)', 'ccache_direct_rate': 'N/A (Kleaf)'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['route', 'setup', 'matrix', 'metadata'])
    args = parser.parse_args()
    try:
        globals()[args.mode]()
    except (ValueError, KeyError) as error:
        parser.exit(1, f'::error::{error}\n')
