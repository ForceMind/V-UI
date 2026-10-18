#!/usr/bin/env python3
"""Run fixed stages in order; incomplete diagnostics stop subsequent stages."""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import packet_boundary as p
import build_and_test as b

STAGES = ('A', 'B', 'C', 'D')


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_group(proc, grace=3):
    """Signal only the new session we created. Never count zombies as joined."""
    signals = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not group_exists(proc.pid):
            break
        try:
            os.killpg(proc.pid, sig)
            signals.append(sig.name)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + grace
        while group_exists(proc.pid) and time.monotonic() < deadline:
            proc.poll()  # Reap our direct child; descendants must also disappear.
            time.sleep(.025)
    proc.wait(timeout=grace)
    return {'signals': signals, 'leader_joined': proc.returncode is not None,
            'process_group_gone': not group_exists(proc.pid)}


def run_stage(command, timeout=1250):
    proc = subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    row = {'pid': proc.pid, 'process_group': proc.pid, 'timeout': False}
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        row['returncode'] = proc.returncode
    except subprocess.TimeoutExpired:
        row.update(returncode=None, timeout=True)
        row['cleanup'] = stop_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.stdout.close(); proc.stderr.close()
            stdout, stderr = '', 'Owned process group retained pipe handles after cleanup'
            row['cleanup']['process_group_gone'] = False
    except BaseException:
        stop_group(proc)
        raise
    finally:
        if 'cleanup' not in row:
            row['cleanup'] = stop_group(proc)
    row.update(stdout=stdout, stderr=stderr)
    return row


def stage_passed(row, report):
    if row['returncode'] != 0 or row['timeout'] or row['cleanup'] != {
            'signals': [], 'leader_joined': True, 'process_group_gone': True}:
        return False
    if (report.get('diagnostic_complete') is not True or
            report.get('product_qualified') is not False or
            report.get('original_suite_passed') is not False or
            report.get('stage') != row['stage']):
        return False
    if row['stage'] in ('A', 'B'):
        return report.get('stage_capacity_passed') is False and bool(report.get('capacity_failed_leaves'))
    return report.get('stage_capacity_passed') is True


def run_stages(output, manifest_path, runner=run_stage):
    results = []
    for stage in STAGES:
        command = [sys.executable, str(b.HERE/'packet_boundary.py'), '--repository', str(b.ROOT),
                   '--binary-manifest', str(manifest_path), '--stage', stage, '--output', str(output/stage)]
        row = runner(command)
        row['stage'] = stage
        try:
            report = p.json_read(output/stage/'result.json')
            row['stage_gate_passed'] = stage_passed(row, report)
        except (OSError, ValueError, KeyError):
            row['stage_gate_passed'] = False
        results.append(row)
        p.save(output/'stages.json', results)
        if not row['stage_gate_passed']:
            break
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--official', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_id = b.identity()
    provenance = args.build.resolve()/'provenance.json'
    candidate = args.build.resolve()/'candidate-sing-box'
    data = json.loads(provenance.read_text())
    manifest = {'schema_version': 1, 'kind': p.KIND+'-input', 'source_identity': source_id,
                'official': {'path': str(args.official.resolve()), 'sha256': p.sha(args.official), 'version': '1.14.2'},
                'candidate': {'path': str(candidate), 'sha256': p.sha(candidate), 'version': data['version'],
                              'provenance_path': str(provenance), 'provenance_sha256': p.sha(provenance)}}
    p.validate_manifest(manifest, source_id, b.ROOT)
    manifest_path = args.build/'runtime-input.json'
    p.save(manifest_path, manifest)
    results = run_stages(args.output, manifest_path)
    passed = len(results) == len(STAGES) and all(row['stage_gate_passed'] for row in results)
    p.save(args.output/'RUNTIME_RESULT.json', {'source_identity': source_id, 'all_stages_completed': passed,
           'unexecuted_stages': list(STAGES[len(results):]), 'official_original_gate_unchanged': True,
           'product_qualified': False, 'stages': results})
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
