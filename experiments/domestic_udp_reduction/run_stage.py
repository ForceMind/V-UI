#!/usr/bin/env python3
"""One exact 1200-byte two-leaf I case for the requested domestic build."""
import adapter
import diagnostic_runtime
if __name__=='__main__':
    adapter.install()
    # diagnostic_runtime only parses/runs after its existing install call.
    diagnostic_runtime.d.install=lambda **kw:None
    raise SystemExit(diagnostic_runtime.main())
