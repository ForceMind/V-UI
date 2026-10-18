import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('packet_verifier', HERE/'verify_evidence.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class TestPacketVerifier(unittest.TestCase):
    def setUp(self):
        self.package = 'github.com/sagernet/sing/common/network'
        self.leaves = ['TestVUICompletePacketA/fit', 'TestVUICompletePacketA/over']
        self.rows = [{'Package':self.package, 'Test':name, 'Action':'pass'}
                     for name in sorted(v.expected_terminals(self.leaves))]
        self.rows += [{'Package':self.package, 'Action':'pass'}]
    def verify(self, rows):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'go.stdout';p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return v.verify_go(p, self.leaves, self.package)
    def test_complete(self):
        self.assertEqual(self.verify(self.rows), {'leaves':2,'terminals':3})
    def test_zero_tests(self):
        with self.assertRaises(ValueError):self.verify(self.rows[-1:])
    def test_missing_leaf(self):
        with self.assertRaises(ValueError):self.verify(self.rows[1:])
    def test_skip(self):
        rows=copy.deepcopy(self.rows);rows[1]['Action']='skip'
        with self.assertRaises(ValueError):self.verify(rows)
    def test_fail(self):
        rows=copy.deepcopy(self.rows);rows[1]['Action']='fail'
        with self.assertRaises(ValueError):self.verify(rows)
    def test_duplicate(self):
        with self.assertRaises(ValueError):self.verify(self.rows+[self.rows[0]])
    def test_unexpected_test(self):
        with self.assertRaises(ValueError):self.verify(self.rows+[{'Package':self.package,'Test':'TestVUICompletePacketExtra','Action':'pass'}])
    def test_no_package_pass(self):
        with self.assertRaises(ValueError):self.verify(self.rows[:-1])
    def test_wrong_package(self):
        rows=copy.deepcopy(self.rows);rows[0]['Package']='wrong'
        with self.assertRaises(ValueError):self.verify(rows)
    def test_frozen_contract(self):
        x=json.loads((HERE/'TEST_CASES.json').read_text())
        self.assertEqual(sum(map(len,x['linux_required_leaf_tests'].values())),58)
        self.assertEqual(x['linux_required_leaf_count'],58)
        self.assertEqual(len(x['files']),4)
        for f in x['files']:
            self.assertTrue((HERE/f['source']).is_file())
            self.assertTrue(Path(f['target']).name.startswith('vui_'))
    def test_patch_paths(self):
        paths={line.split(' b/',1)[1] for line in (HERE/'receive.patch').read_text().splitlines() if line.startswith('diff --git ')}
        self.assertEqual(paths, {'common/network/direct.go','common/network/packet.go','common/bufio/conn.go',
            'common/bufio/packet_complete.go','common/bufio/copy_direct_posix.go','common/bufio/copy_direct_windows.go',
            'common/bufio/packet_batch_mmsg.go','common/bufio/packet_batch_msgx_darwin.go','protocol/socks/handshake.go'})

class TestPacketBuildVerifier(unittest.TestCase):
    def fixture(self, root):
        contract=json.loads((HERE/'TEST_CASES.json').read_text())
        checks=[]
        for package, leaves in contract['linux_required_leaf_tests'].items():
            label=package.removeprefix('github.com/sagernet/sing/').replace('/','-')
            for mode in ('race','debug','darwin-compile','windows-compile'):
                name=label+'-'+mode
                cross=mode.endswith('-compile')
                env={'GOOS':mode.split('-')[0] if cross else 'linux','GOARCH':'amd64','CGO_ENABLED':'0' if cross else '1','GOTOOLCHAIN':'local','GOWORK':'off'}
                args=['go','test','-c',package] if cross else ['go','test','-race',*(['-tags=debug'] if mode=='debug' else []),'-run','^TestVUICompletePacket',package]
                (root/(name+'.exit.json')).write_text(json.dumps({'args':args,'returncode':0,'environment':env}))
                if not cross:
                    rows=[{'Package':package,'Test':test,'Action':'pass'} for test in sorted(v.expected_terminals(leaves))]+[{'Package':package,'Action':'pass'}]
                    (root/(name+'.stdout')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
                checks.append({'label':name,'returncode':0})
        for label in ('build','module-verify'):
            env={'GOOS':'linux','GOARCH':'amd64','CGO_ENABLED':'0' if label=='build' else '1','GOTOOLCHAIN':'local','GOWORK':'off'}
            (root/(label+'.exit.json')).write_text(json.dumps({'args':['go','build'] if label=='build' else ['go','mod','verify'],'returncode':0,'environment':env}))
            checks.append({'label':label,'returncode':0})
        (root/'provenance.json').write_text(json.dumps({'checks':checks,'product_qualified':False}))
        return contract
    def test_full_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);contract=self.fixture(root)
            self.assertEqual(len(v.verify_build(root,contract)['linux']),6)
    def test_missing_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);contract=self.fixture(root);p=root/'provenance.json';x=json.loads(p.read_text());x['checks']=[];p.write_text(json.dumps(x))
            with self.assertRaises(ValueError):v.verify_build(root,contract)
    def test_duplicate_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);contract=self.fixture(root);p=root/'provenance.json';x=json.loads(p.read_text());x['checks'][-1]=x['checks'][-2];p.write_text(json.dumps(x))
            with self.assertRaises(ValueError):v.verify_build(root,contract)
    def test_native_claimed_cross_compile(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);contract=self.fixture(root);p=root/'common-network-windows-compile.exit.json';x=json.loads(p.read_text());x['environment']['GOOS']='linux';p.write_text(json.dumps(x))
            with self.assertRaises(ValueError):v.verify_build(root,contract)

if __name__=='__main__':unittest.main()
