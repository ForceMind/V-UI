#!/usr/bin/env python3
"""Child entry for only the reviewed two-case idle fixture repair collection."""
import sys
from diagnostic_runtime import main
if __name__=='__main__':
    sys.argv.append('--idle-repair')
    raise SystemExit(main())
