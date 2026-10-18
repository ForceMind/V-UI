#!/usr/bin/env python3
"""Finite diagnostic owned-group coordinator; shared original safety bounds."""
import diagnostic as d
import runtime as r
import run_matrix
if __name__=='__main__':
    d.install()
    run_matrix.RUNTIME=r.HERE/'diagnostic_runtime.py'
    raise SystemExit(run_matrix.main())
