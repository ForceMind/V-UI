"""Fixed real core validation; requires explicitly provisioned test binaries."""
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from app.services.core_manager import SingBoxAdapter, XrayAdapter
from app.services.core_runtime import CoreRuntime, CoreError, atomic_write


@unittest.skipUnless(os.getenv('VUI_TEST_CORES'), 'real binary path not provided')
class PinnedCoreTests(unittest.TestCase):
    def test_real_xray_and_singbox_check_positive_and_negative(self):
        with tempfile.TemporaryDirectory(prefix='vui-real-check-') as tmp:
            for name, adapter, settings in (
                ('xray', XrayAdapter(), {'clients': [{'id': '11111111-1111-1111-1111-111111111111'}], 'decryption': 'none'}),
                ('sing-box', SingBoxAdapter(), {'users': [{'uuid': '11111111-1111-1111-1111-111111111111'}]}),
            ):
                with self.subTest(core=name):
                    item = SimpleNamespace(enable=True, protocol='vless', port=18443, tag='test',
                                           settings=settings, stream_settings={})
                    config = adapter.build_config([item])
                    runtime = CoreRuntime(name, Path(os.environ['VUI_TEST_CORES']) / name, Path(tmp) / name)
                    self.addCleanup(runtime.close)
                    self.assertFalse(runtime.apply(config, activate=False)['applied'])
                    path = Path(tmp) / 'bad.json'
                    atomic_write(path, b'{this is not json')
                    with self.assertRaises(CoreError): runtime.check(path)
                    print('Pinned core positive/negative config check:', name)


if __name__ == '__main__': unittest.main()
