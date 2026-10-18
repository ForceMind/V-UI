#!/usr/bin/env python3
"""Produce an ordinary validated product node; never a research-only node map."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.services.validated_export import validated_nodes, share_link

def export(port):
    item = SimpleNamespace(id=1, core='sing-box', protocol='vless', port=port,
        remark='DOMESTIC_ONLY', enable=True, expiry_time=0, relay_mode='inherit',
        settings={'users': [{'uuid': '11111111-1111-4111-8111-111111111111', 'flow': ''}]},
        stream_settings={'tls': {'enabled': True, 'server_name': 'vpn.example.test'}})
    return {'node': validated_nodes([item], '127.0.0.1')[0],
            'uri': share_link(item, '127.0.0.1'),
            'source': 'app.services.validated_export.validated_nodes',
            'scope': 'ordinary-production-exporter-not-http-api'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    print(json.dumps(export(parser.parse_args().port), sort_keys=True))
