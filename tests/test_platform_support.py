import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import platform_support as p

class PlatformSupportTests(unittest.TestCase):
    def test_os_release_is_generic_not_distribution_whitelist(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"os-release";path.write_text('ID=somefuturelinux\nNAME="Future Linux"\nVERSION_ID="42"\nID_LIKE="arch"\n')
            value=p.read_os_release(path)
            self.assertEqual(value["ID"],"somefuturelinux")
            self.assertEqual(value["ID_LIKE"],"arch")
    def test_arch_aliases(self):
        for raw,expected in (("x86_64","x86_64"),("amd64","x86_64"),("aarch64","aarch64"),("arm64","aarch64")):
            with self.subTest(raw=raw),patch.object(p.platform,"machine",return_value=raw):
                self.assertEqual(p.architecture(),expected)
    def test_unknown_arch_fails_instead_of_guessing(self):
        with patch.object(p.platform,"machine",return_value="mips64"):
            with self.assertRaises(RuntimeError):p.architecture()
    def test_target_key_combines_arch_and_libc(self):
        with patch.object(p.platform,"system",return_value="Linux"),patch.object(p,"architecture",return_value="aarch64"),patch.object(p,"libc_family",return_value="musl"):
            self.assertEqual(p.target_key(),"aarch64-musl")
    def test_service_manager_detects_systemd_then_openrc(self):
        with patch.object(p.Path,"is_dir",return_value=True),patch.object(p.shutil,"which",side_effect=lambda n:"/bin/"+n if n=="systemctl" else None):
            self.assertEqual(p.init_system(),"systemd")
        with patch.object(p.Path,"is_dir",return_value=False),patch.object(p.shutil,"which",side_effect=lambda n:"/sbin/"+n if n in {"rc-service","rc-update"} else None):
            self.assertEqual(p.init_system(),"openrc")

if __name__=="__main__":unittest.main()
