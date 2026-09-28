#!/usr/bin/env python3
"""Read-only comparison of ordinary files for the RK3576 2026-09-28 case."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ORIGINAL_SHA = 'faebf16428deb61867f57f17f79b7d27ba0faf9503853284a697aa83e882abec'
DAMAGED_SHA = '641383a1418518d6a9eeac4547deca37ce30df7879096c1a54571c54df923bc8'
POSITIONS = (0x15074, 0x264c274)


def replay_bad_edits(original, positions):
    """In-memory forensic replay, NOT a way to enable PCIe or create a firmware."""
    if not positions or len(set(positions)) != len(positions):
        raise ValueError('Positions must be nonempty and distinct')
    work = bytearray(original)
    for position in sorted(positions, reverse=True):
        if position < 12 or position + 12 > len(original):
            raise ValueError('Property position outside input')
        if original[position:position + 9] != b'disabled\0':
            raise ValueError('Original status bytes do not match')
        if struct.unpack_from('>I', original, position - 8)[0] != 9:
            raise ValueError('Original property length is not 9')
        struct.pack_into('>I', work, position - 8, 5)
        work[position:position + 9] = b'okay\0\0\0\0'
    work.extend(original[-len(positions):])
    return bytes(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('damaged', type=Path)
    args = parser.parse_args()
    for path in (args.original, args.damaged):
        if not path.is_file() or path.stat().st_size != 67108864:
            parser.error('Inputs must be ordinary, 67108864-byte local files, never block devices')
    original = args.original.read_bytes()
    damaged = args.damaged.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    damaged_sha = hashlib.sha256(damaged).hexdigest()
    if original_sha != ORIGINAL_SHA or damaged_sha != DAMAGED_SHA:
        parser.error('These are not the two verified images from this specific case; no guess is made')
    predicted = replay_bad_edits(original, POSITIONS)
    same = predicted == damaged
    print(json.dumps({'original_sha256': original_sha, 'damaged_sha256': damaged_sha,
        'predicted_sha256': hashlib.sha256(predicted).hexdigest(),
        'exact_full_partition_match_after_two_bad_edits': same,
        'original_status_offsets': [hex(p) for p in POSITIONS],
        'files_or_devices_written': False}, indent=2))
    return 0 if same else 1


if __name__ == '__main__':
    raise SystemExit(main())
