"""Run the explicit synthetic core-update contracts on the native OS Python.

This checks parsers, fake transports and temporary filesystem operations. It
does not download, install, execute or qualify an upstream core or a component
Release. No browser is started. The actual producer/runtime suites remain
separate mandatory acceptance.
"""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    'test_core_artifacts.py': 23,
    'test_core_store.py': 25,
    'test_core_github.py': 29,
    'test_core_selection.py': 17,
    'test_core_catalog.py': 24,
}


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    suite = unittest.TestSuite()
    for pattern, expected in CONTRACTS.items():
        part = unittest.TestLoader().discover(str(ROOT / 'tests'), pattern=pattern)
        if part.countTestCases() != expected:
            raise SystemExit('Core update contract count differs: ' + pattern)
        suite.addTests(part)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    total = sum(CONTRACTS.values())
    if (not result.wasSuccessful() or result.testsRun != total or result.skipped
            or result.expectedFailures or result.unexpectedSuccesses):
        raise SystemExit('Core update contracts must all execute without skips or expected failures')
    print(json.dumps({'scope': 'synthetic-core-update-contracts-native-os-filesystem',
        'contracts': CONTRACTS, 'tests': total, 'skips': 0,
        'real_core_or_component_qualified': False}))


if __name__ == '__main__':
    main()
