#!/usr/bin/env python3
"""Owned-group coordinator for one finite domestic reduction arm."""
import adapter
import run_matrix
if __name__=='__main__':
    adapter.install();run_matrix.RUNTIME=adapter.c.HERE/'run_stage.py'
    raise SystemExit(run_matrix.main())
