#!/usr/bin/env python3
"""Allowlisted UTF-8 evidence; never traverse source clones or binary outputs."""
import argparse
from pathlib import Path
import harness as h

def owner_files(contract):
    labels = {'gofmt-owner', 'modules', 'modules-after'}
    labels |= {f'patch-{i}-{step}' for i in (5, 6, 7) for step in ('check', 'apply')}
    labels |= {'gofmt-new-' + item['source'].replace('/', '-').removesuffix('.go') for item in contract['files']}
    labels |= {g['label'] + '-' + mode for g in contract['groups'] for mode in h.MODES}
    labels |= {f'owner-cross-{i}-{os}' for i in range(len(h.CROSS_PACKAGES)) for os in ('darwin', 'windows')}
    return {label + suffix for label in labels for suffix in ('.stdout', '.stderr', '.exit.json')} | {
        'base-source-manifest.json', 'final-source-manifest.json', 'post-test-source-manifest.json',
        'normalized-modules.json', 'provenance.json', 'VERIFICATION.json', 'RESULT.json'}

def validate(path, jsonl):
    text = path.read_text(encoding='utf-8')
    h.c.need('\x00' not in text and 'PRIVATE KEY-----' not in text and 'BEGIN CERTIFICATE' not in text,
             'Forbidden binary or key material')
    if path.suffix == '.json':
        data = h.packet_prepare.strict_json(text)
        h.c.need(h.c.life.scrub(data) == data, 'Unsanitized JSON evidence')
    elif jsonl:
        for line in text.splitlines():
            if line.strip():
                data = h.packet_prepare.strict_json(line)
                h.c.need(isinstance(data, dict) and h.c.life.scrub(data) == data, 'Unsanitized Go test JSONL')
    else: h.c.need(h.c.life.clean(text) == text, 'Unsanitized text evidence')
    return text

def prepare(build, output, contract):
    h.c.need(build.is_dir() and not build.is_symlink(), 'No owner build evidence')
    output.mkdir(parents=True, exist_ok=False)
    allowed, old_allowed = owner_files(contract), h.packet_prepare.allowed_build()
    new_jsonl = {g['label'] + '-' + m + '.stdout' for g in contract['groups'] for m in h.MODES}
    entries = []
    for path in sorted(build.rglob('*')):
        h.c.need(not path.is_symlink(), 'Linked evidence member'); relative = path.relative_to(build)
        if path.is_dir():
            h.c.need(relative.as_posix() in ('base', 'regression'), 'Unexpected evidence directory'); continue
        h.c.need(path.is_file(), 'Nonregular evidence')
        if len(relative.parts) == 1:
            h.c.need(path.name in allowed, 'Unexpected owner evidence member'); jsonl = path.name in new_jsonl
        else:
            h.c.need(len(relative.parts) == 2 and relative.parts[0] in ('base', 'regression') and path.name in old_allowed,
                     'Unexpected base/regression evidence'); jsonl = path.name in h.packet_prepare.GO_JSONL
        text = validate(path, jsonl); target = output / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        entries.append({'path': relative.as_posix(), 'bytes': target.stat().st_size, 'sha256': h.c.sha256(target)})
    h.c.need(entries and len({r['path'] for r in entries}) == len(entries), 'Empty/duplicate evidence')
    h.packet.save(output / 'MANIFEST.json', {'files': entries, 'count': len(entries), 'product_qualified': False})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); prepare(args.build, args.output, h.read_contract())
