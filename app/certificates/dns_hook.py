"""Fixed Certbot DNS hook. No user command, provider URL, or token argument."""
from __future__ import annotations
import argparse
import math
import os
from pathlib import Path
import re
import sys
import time

# Certbot starts a trusted file by absolute path under the verified payload.
# Isolated Python omits the payload directory from sys.path.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))

from app.certificates.dns_journal import DNSChallengeJournal
from app.certificates.dns_store import DNSStoreError
from app.certificates.errors import CertificateError
from app.data_owner import data_owner


def main(argv=None, *, journal_factory=DNSChallengeJournal, wait_seconds=None):
    parser = argparse.ArgumentParser(description='V-UI internal DNS challenge callback')
    parser.add_argument('action', choices=('auth', 'cleanup'))
    parser.add_argument('root')
    parser.add_argument('job_id')
    parser.add_argument('--deadline', type=float, required=True)
    parser.add_argument('--propagation-seconds', type=int, default=60)
    args = parser.parse_args(argv)
    try:
        root = Path(args.root)
        if (not root.is_absolute() or '..' in root.parts or not re.fullmatch('[a-f0-9]{32}', args.job_id)
                or not math.isfinite(args.deadline) or not time.time() < args.deadline <= time.time() + 901
                or type(args.propagation_seconds) is not int or not 30 <= args.propagation_seconds <= 300):
            raise CertificateError('DNS_HOOK_ARGUMENTS_INVALID')
        # wait_seconds is an in-process test seam, never a CLI/API setting.
        wait = args.propagation_seconds if wait_seconds is None else wait_seconds
        if type(wait) is not int or not 0 <= wait <= 300:
            raise CertificateError('DNS_HOOK_ARGUMENTS_INVALID')
        deadline = time.monotonic() + args.deadline - time.time()
        with data_owner(root.parent):
            journal = journal_factory(root)
            if args.action == 'auth':
                identifiers = [os.environ[key] for key in ('CERTBOT_IDENTIFIER', 'CERTBOT_DOMAIN') if key in os.environ]
                if not identifiers or any(value.removeprefix('*.') != identifiers[0].removeprefix('*.') for value in identifiers):
                    raise CertificateError('DNS_HOOK_IDENTIFIER_INVALID')
                journal.present(args.job_id, identifiers[0], os.environ.get('CERTBOT_VALIDATION'), deadline=deadline)
                # A conservative fixed wait works with the pinned legacy and new
                # hook variable names; it does not assert global propagation.
                finish = time.monotonic() + wait
                while time.monotonic() < finish:
                    if time.monotonic() >= deadline:
                        raise CertificateError('DNS_CHALLENGE_BUDGET_EXHAUSTED')
                    time.sleep(min(.2, max(0, finish - time.monotonic())))
            elif not journal.cleanup(args.job_id, deadline=deadline):
                raise CertificateError('DNS_CLEANUP_PENDING')
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, (CertificateError, DNSStoreError)) else 'DNS_HOOK_FAILED'
        if type(code) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}', code):
            code = 'DNS_HOOK_FAILED'
        # Certbot owns its private log; never print exception text, environment,
        # ACME validation values, authorization headers, or credential snapshots.
        sys.stderr.write('V-UI DNS hook: ' + code + '\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
