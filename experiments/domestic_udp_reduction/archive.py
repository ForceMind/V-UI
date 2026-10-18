#!/usr/bin/env python3
"""Reuse exact no-follow/UTF-8/secret/semantic archive boundaries."""
import adapter
import diagnostic_evidence
if __name__=='__main__':
    adapter.install();diagnostic_evidence.d.install=lambda **kw:None
    diagnostic_evidence.main()
