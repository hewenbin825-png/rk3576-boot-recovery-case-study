#!/usr/bin/env python3
"""Read-only FIT case-audit for ordinary files; not a complete libfdt/signature validator."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def u32(data, offset=0):
    return struct.unpack_from('>I', data, offset)[0]


def align4(value):
    return (value + 3) & ~3


def parse_fdt(data, base=0):
    if base < 0 or base + 40 > len(data) or u32(data, base) != 0xD00DFEED:
        raise ValueError('FDT magic/header missing')
    total, struct_off, strings_off = struct.unpack_from('>III', data, base + 4)
    version = u32(data, base + 20)
    strings_size, struct_size = struct.unpack_from('>II', data, base + 32)
    if total < 40 or base + total > len(data) or version != 17:
        raise ValueError('Invalid FDT size/version')
    if (struct_off < 40 or struct_off % 4 or struct_size % 4
            or struct_off + struct_size > total or strings_off < 40
            or strings_off + strings_size > total):
        raise ValueError('FDT block outside declared size')
    cursor = base + struct_off
    end = cursor + struct_size
    string_start = base + strings_off
    string_end = string_start + strings_size
    stack, nodes = [], {}
    while cursor + 4 <= end:
        token = u32(data, cursor)
        cursor += 4
        if token == 1:
            name_end = data.find(b'\0', cursor, end)
            if name_end < 0:
                raise ValueError('Unterminated node')
            stack.append(data[cursor:name_end].decode('ascii', 'replace'))
            key = '/' + '/'.join(x for x in stack if x)
            if key in nodes:
                raise ValueError('Duplicate node path')
            nodes[key] = {}
            cursor = base + align4(name_end + 1 - base)
        elif token == 2:
            if not stack:
                raise ValueError('Unbalanced node end')
            stack.pop()
        elif token == 3:
            if not stack or cursor + 8 > end:
                raise ValueError('Invalid property location')
            length, name_off = struct.unpack_from('>II', data, cursor)
            value_start = cursor + 8
            value_end = value_start + length
            if value_end > end or name_off >= strings_size:
                raise ValueError('Invalid property bounds')
            name_end = data.find(b'\0', string_start + name_off, string_end)
            if name_end < 0:
                raise ValueError('Unterminated property name')
            name = data[string_start + name_off:name_end].decode('ascii', 'replace')
            nodes['/' + '/'.join(x for x in stack if x)][name] = {
                'value': data[value_start:value_end], 'offset': value_start,
            }
            cursor = base + align4(value_end - base)
        elif token == 4:
            pass
        elif token == 9:
            if stack:
                raise ValueError('Nodes not closed')
            return {'base': base, 'total': total, 'nodes': nodes}
        else:
            raise ValueError('Unknown FDT token at 0x%x' % (cursor - 4))
    raise ValueError('FDT end token missing')


def text_value(props, key):
    return props.get(key, {}).get('value', b'').rstrip(b'\0').decode('ascii', 'replace')


def audit(data):
    fit = parse_fdt(data)
    nodes = fit['nodes']
    if '/images' not in nodes:
        raise ValueError('FDT is not a FIT with /images')
    result = {
        'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
        'fit_header_size': fit['total'], 'images': [], 'pcie_status': [],
        'note': 'Matching embedded hashes proves consistency, not trusted provenance or signature validity.',
    }
    for path, props in nodes.items():
        if not path.startswith('/images/') or path.count('/') != 2:
            continue
        if 'data' in props:
            offset, size = props['data']['offset'], len(props['data']['value'])
        else:
            size = u32(props['data-size']['value'])
            if 'data-position' in props:
                offset = u32(props['data-position']['value'])
            else:
                offset = align4(fit['total']) + u32(props['data-offset']['value'])
        if offset < 0 or size < 1 or offset + size > len(data):
            raise ValueError('Payload outside input: ' + path)
        payload = data[offset:offset + size]
        image = {'node': path, 'type': text_value(props, 'type'),
                 'compression': text_value(props, 'compression'),
                 'offset': offset, 'size': size,
                 'sha256': hashlib.sha256(payload).hexdigest(), 'hash_checks': []}
        for subpath, subprops in nodes.items():
            if subpath.rsplit('/', 1)[0] != path or not subpath.rsplit('/', 1)[1].startswith('hash'):
                continue
            algorithm = text_value(subprops, 'algo')
            expected = subprops.get('value', {}).get('value', b'').hex()
            actual = hashlib.new(algorithm, payload).hexdigest() if algorithm in ('sha1', 'sha256', 'sha384', 'sha512') else None
            image['hash_checks'].append({'algorithm': algorithm, 'expected': expected,
                                         'actual': actual, 'matches': actual == expected if actual is not None else None})
        result['images'].append(image)
    cursor = 0
    while True:
        base = data.find(b'\xd0\x0d\xfe\xed', cursor)
        if base < 0:
            break
        cursor = base + 4
        try:
            tree = parse_fdt(data, base)
        except (ValueError, struct.error):
            continue
        for path, props in tree['nodes'].items():
            if path.rsplit('/', 1)[-1] == 'pcie@2a210000' and 'status' in props:
                result['pcie_status'].append({'fdt_offset': base, 'fdt_size': tree['total'],
                    'node': path, 'status': text_value(props, 'status'),
                    'value_offset': props['status']['offset'],
                    'property_length': len(props['status']['value'])})
    result['all_payload_hashes_verified'] = bool(result['images']) and all(
        item['hash_checks'] and all(check['matches'] is True for check in item['hash_checks'])
        for item in result['images'])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error('Input must be an ordinary local image file, not a block device')
    if args.image.stat().st_size > 256 * 1024 * 1024:
        parser.error('This case-audit tool limits inputs to 256 MiB')
    try:
        report = audit(args.image.read_bytes())
    except (OSError, ValueError, KeyError, struct.error) as error:
        parser.exit(2, 'Cannot audit image: %s\n' % error)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['all_payload_hashes_verified']:
        raise SystemExit(1)
