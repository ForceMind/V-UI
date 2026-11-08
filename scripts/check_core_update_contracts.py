"""Run the explicit synthetic core-update contracts on the native OS Python.

This checks parsers, fake transports and temporary filesystem operations. It
does not download, install, execute or qualify an upstream core or a component
Release. No browser is started. The actual producer/runtime suites remain
separate mandatory acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    'test_core_artifacts.py': 23,
    'test_core_store.py': 28,
    'test_core_github.py': 29,
    'test_core_selection.py': 17,
    'test_core_catalog.py': 24,
    'test_core_selection_files.py': 38,
    'test_core_resolver.py': 8,
    'test_core_manager_artifacts.py': 10,
    'test_core_application.py': 11,
    'test_core_boot.py': 19,
    'test_core_diagnostics_artifact.py': 17,
    'test_core_component_builder.py': 20,
    'test_core_candidate_inputs.py': 18,
    'test_core_maintenance_cycle.py': 18,
    'test_core_archives.py': 14,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', action='store_true',
                        help='Run Linux-only fake-executable runtime binding contracts')
    args = parser.parse_args()
    if args.runtime and not sys.platform.startswith('linux'):
        raise SystemExit('Runtime binding contracts require native Linux acceptance')
    contracts = {'test_core_runtime_artifact.py': 32} if args.runtime else CONTRACTS
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    suite = unittest.TestSuite()
    for pattern, expected in contracts.items():
        part = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=pattern)
        if part.countTestCases() != expected:
            raise SystemExit('Core update contract count differs: ' + pattern)
        suite.addTests(part)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    total = sum(contracts.values())
    if (not result.wasSuccessful() or result.testsRun != total or result.skipped
            or result.expectedFailures or result.unexpectedSuccesses):
        raise SystemExit('Core update contracts must all execute without skips or expected failures')
    print(json.dumps({'scope': ('linux-fake-executable-runtime-binding' if args.runtime
                              else 'synthetic-core-update-contracts-native-os-filesystem'),
        'contracts': contracts, 'tests': total, 'skips': 0,
        'real_core_or_component_qualified': False}))


if __name__ == '__main__':
    main()
