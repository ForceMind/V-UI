#!/usr/bin/env python3
"""Run independent fixed diagnostics only after verified resource cleanup."""
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
import verify_execution_safety as safety
import verify_failed_observations as failures

STAGES = ('C', 'D', 'A', 'B', 'U', 'V')


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
    if row['stage'] in ('U', 'V'):
        red = report.get('capacity_failed_leaves')
        return isinstance(red, list) and report.get('stage_capacity_passed') is (not red)
    return report.get('stage_capacity_passed') is True


def check_failure_observations(directory, stage, report):
    return failures.verify_stage(directory, p.plan(stage), report,
                                 p.json_read(directory/'process-binaries.json'))


def run_stages(output, manifest_path, runner=run_stage, safety_checker=safety.verify,
               failure_checker=check_failure_observations):
    results = []
    expected_input = p.json_read(manifest_path)
    manifest_sha256 = p.sha(manifest_path)
    for stage in STAGES:
        command = [sys.executable, str(b.HERE/'packet_boundary.py'), '--repository', str(b.ROOT),
                   '--binary-manifest', str(manifest_path), '--stage', stage, '--output', str(output/stage)]
        report = {}
        report_error = None
        row = runner(command)
        row['stage'] = stage
        try:
            report = p.json_read(output/stage/'result.json')
            row['stage_gate_passed'] = stage_passed(row, report)
        except (OSError, ValueError, KeyError) as exc:
            report_error = {'type': type(exc).__name__, 'message': str(exc)}
            row['stage_gate_passed'] = False
        try:
            row['execution_safety'] = safety_checker(output/stage, stage, row, expected_input, manifest_sha256)
            row['stage_execution_safe'] = row['execution_safety']['stage_execution_safe'] is True
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            row['stage_execution_safe'] = False
            row['execution_safety_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        if row['stage_execution_safe']:
            try:
                observed = failure_checker(output/stage, stage, report)
                row['failure_observations'] = observed
                if observed['applicable'] and not observed['failed_observations_verified']:
                    row['stage_gate_passed'] = False
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                row['stage_gate_passed'] = False
                row['failure_observation_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        # Preserve a small already-sanitized diagnostic in job logs even if
        # artifact assembly subsequently fails. Do not print packet/config data.
        print(json.dumps(b.c.life.scrub({'stage': stage, 'returncode': row['returncode'],
              'timeout': row['timeout'], 'cleanup': row['cleanup'],
              'stage_gate_passed': row['stage_gate_passed'],
              'stage_execution_safe': row['stage_execution_safe'],
              'execution_safety_error': row.get('execution_safety_error'),
              'failure_observations': row.get('failure_observations'),
              'failure_observation_error': row.get('failure_observation_error'),
              'diagnostic_complete': report.get('diagnostic_complete'),
              'stage_capacity_passed': report.get('stage_capacity_passed'),
              'report_error': report_error,
              'failed_cases': [{'case': case.get('case'), 'status': case.get('status'), 'errors': case.get('errors')}
                               for case in report.get('cases', []) if case.get('status') != 'observed' or case.get('errors')],
              'capacity_failed_leaves': report.get('capacity_failed_leaves', []),
              'errors': report.get('errors', ['No readable stage result'])})), flush=True)
        results.append(row)
        p.save(output/'stages.json', results)
        if not row['stage_execution_safe']:
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
    executed = len(results) == len(STAGES)
    passed = executed and all(row['stage_gate_passed'] and row['stage_execution_safe'] for row in results)
    p.save(args.output/'RUNTIME_RESULT.json', {'source_identity': source_id, 'all_stages_executed': executed,
           'all_gates_passed': passed,
           'unexecuted_stages': list(STAGES[len(results):]), 'official_original_gate_unchanged': True,
           'product_qualified': False, 'stages': results})
    return 0 if passed else 1

if __name__ == '__main__':
    raise SystemExit(main())
