#!/usr/bin/env python3
"""Finite diagnostic owned-group coordinator; shared original safety bounds."""
import sys
import diagnostic as d
import runtime as r
import run_matrix
if __name__=='__main__':
    idle_repair='--idle-repair' in sys.argv
    if idle_repair:sys.argv.remove('--idle-repair')
    d.install(idle_repair=idle_repair)
    run_matrix.RUNTIME=r.HERE/('diagnostic_idle_runtime.py' if idle_repair else 'diagnostic_runtime.py')
    raise SystemExit(run_matrix.main())
