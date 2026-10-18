#!/usr/bin/env python3
"""Reject an OP15 Wild vmlinux that cannot initialize all linked LSMs."""

import argparse
import re
import subprocess
from pathlib import Path


def verify(path):
    with path.open('rb') as stream:
        header = stream.read(20)
    if header[:6] != b'\x7fELF\x02\x01' or header[18:20] != b'\xb7\x00':
        raise ValueError('Expected a little-endian ARM64 ELF vmlinux')
    output = subprocess.check_output(['readelf', '--syms', '--wide', str(path)], text=True)
    symbols = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 8 and re.fullmatch(r'\d+:', fields[0]) and fields[6] != 'UND':
            symbols[fields[7]] = int(fields[2], 16 if fields[2].startswith('0x') else 10)
    descriptors = sorted(name for name in symbols
                         if re.fullmatch(r'__(?:early_)?lsm_\w+', name))
    if '__lsm_baseband_guard' not in descriptors:
        raise ValueError('Baseband Guard has no linked LSM descriptor')
    size = symbols.get('lsm_idlist', 0)
    slots = size // 8
    if not descriptors or size % 8 or slots < len(descriptors):
        raise ValueError(f'LSM capacity too small: {slots} slots for '
                         f'{len(descriptors)} linked LSMs; Baseband Guard can be skipped at boot')
    last_hook = f'__SCK__lsm_static_call_file_permission_{slots - 1}'
    if last_hook not in symbols:
        raise ValueError(f'Missing compiled static hook slot: {last_hook}')
    print(f'LSM capacity verified: {slots} slots for {len(descriptors)} linked LSMs, '
          'including Baseband Guard (runtime activation still needs a device check)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vmlinux', type=Path)
    args = parser.parse_args()
    try:
        verify(args.vmlinux)
    except ValueError as error:
        parser.exit(1, f'{error}\n')
