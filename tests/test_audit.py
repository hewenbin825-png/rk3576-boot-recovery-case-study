import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from fit_audit import audit, parse_fdt
from compare_case import replay_bad_edits


def word(value):
    return struct.pack('>I', value)


def padded(value):
    return value + b'\0' * (-len(value) % 4)


def make_fit(payload=b'synthetic kernel', wrong_hash=False, outside=False):
    strings = b'type\0compression\0data-size\0data-position\0algo\0value\0'
    def prop(name, value):
        return word(3) + word(len(value)) + word(strings.index(name.encode() + b'\0')) + padded(value)
    def begin(name):
        return word(1) + padded(name.encode() + b'\0')
    digest = hashlib.sha256(payload).digest()
    if wrong_hash:
        digest = b'\0' * 32
    structure = (begin('') + begin('images') + begin('kernel')
        + prop('type', b'kernel\0') + prop('compression', b'none\0')
        + prop('data-size', word(len(payload)))
        + prop('data-position', word(0x100000 if outside else 0x400))
        + begin('hash') + prop('algo', b'sha256\0') + prop('value', digest)
        + word(2)*4 + word(9))
    string_start = 56 + len(structure)
    total = string_start + len(strings)
    header = struct.pack('>10I',0xd00dfeed,total,56,string_start,40,17,16,0,len(strings),len(structure))
    fit = header + b'\0'*16 + structure + strings
    return fit + b'\0' * (0x400-len(fit)) + payload


class FitAuditTests(unittest.TestCase):
    def test_valid_hash(self):
        result = audit(make_fit())
        self.assertTrue(result['images'][0]['hash_checks'][0]['matches'])
        self.assertEqual(result['images'][0]['offset'], 0x400)
        self.assertTrue(result['all_payload_hashes_verified'])

    def test_missing_hash_is_not_success(self):
        image = make_fit().replace(b'hash\0', b'none\0')
        self.assertFalse(audit(image)['all_payload_hashes_verified'])

    def test_unsupported_hash_is_not_success(self):
        image = make_fit().replace(b'sha256\0', b'crc999\0')
        self.assertFalse(audit(image)['all_payload_hashes_verified'])

    def test_unsupported_fdt_version_is_rejected(self):
        image = bytearray(make_fit())
        struct.pack_into('>I', image, 20, 16)
        with self.assertRaises(ValueError):
            parse_fdt(image)

    def test_modified_payload_is_detected(self):
        image = bytearray(make_fit())
        image[-1] ^= 1
        self.assertFalse(audit(image)['images'][0]['hash_checks'][0]['matches'])

    def test_incorrect_stored_hash(self):
        self.assertFalse(audit(make_fit(wrong_hash=True))['images'][0]['hash_checks'][0]['matches'])

    def test_payload_outside_file(self):
        with self.assertRaises(ValueError):
            audit(make_fit(outside=True))

    def test_header_truncated(self):
        with self.assertRaises(ValueError):
            parse_fdt(b'\xd0\x0d\xfe\xed')

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            parse_fdt(b'\0'*64)

    def test_unknown_structure_token(self):
        image = bytearray(make_fit())
        struct.pack_into('>I', image, 56, 0x1234)
        with self.assertRaises(ValueError):
            parse_fdt(image)

    def test_declared_size_exceeds_file(self):
        image = bytearray(make_fit())
        struct.pack_into('>I', image, 4, len(image)+1)
        with self.assertRaises(ValueError):
            parse_fdt(image)


class ReplayTests(unittest.TestCase):
    def original(self):
        prop = word(3) + word(9) + word(0) + b'disabled\0' + b'\0'*3
        return b'\0'*16 + prop + prop + b'\0'*16, (28,52)

    def test_exact_splices_preserve_partition_length(self):
        original, positions = self.original()
        replay = replay_bad_edits(original, positions)
        self.assertEqual(len(replay), len(original))
        self.assertEqual(replay[28:36], b'okay\0\0\0\0')
        self.assertEqual(replay[51:59], b'okay\0\0\0\0')
        self.assertEqual(struct.unpack_from('>I', replay, 20)[0], 5)

    def test_original_not_mutated(self):
        original, positions = self.original()
        before = bytes(original)
        replay_bad_edits(original, positions)
        self.assertEqual(original, before)

    def test_rejects_unexpected_original(self):
        original, positions = self.original()
        with self.assertRaises(ValueError):
            replay_bad_edits(original.replace(b'disabled', b'enabledd'), positions)

    def test_rejects_duplicate_or_outside_positions(self):
        original, positions = self.original()
        for bad in ((28,28), (100000,), (), (4,)):
            with self.assertRaises(ValueError):
                replay_bad_edits(original, bad)


if __name__ == '__main__':
    unittest.main()
