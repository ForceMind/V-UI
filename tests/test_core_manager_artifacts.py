"""Boot-snapshot/caching rules, isolated from ORM and executable processes."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import types
import unittest
import uuid

from app.core_updates.binary import RuntimeArtifact
from app.core_updates.selection import bundle_reference

SOURCE=Path(__file__).resolve().parents[1]/'app/services/core_manager.py'


def artifact(core, target='x86_64-gnu', source='a'*40):
    ref=bundle_reference(core=core, version='1.14.2' if core=='sing-box' else '26.3.27',
        target=target, app_source=source, payload_manifest_sha256='b'*64,
        binary_sha256='c'*64,binary_size=1)
    return RuntimeArtifact(ref,Path('/verified')/core,mode=0o700)


def load_manager():
    class FakeRuntime:
        calls=[]
        def __init__(self,*args,**kwargs):
            time.sleep(0.002)
            self.args,self.kwargs=args,kwargs
            self.calls.append(self)
        def status(self):return {'running':False}
        def close(self):pass
        def confirmation_identity(self):
            return {"core":self.args[0], "marker":getattr(self,"marker",0)}
    tree=ast.parse(SOURCE.read_text(),filename=str(SOURCE))
    wanted={'BaseCoreAdapter','XrayAdapter','SingBoxAdapter','CoreManager'}
    nodes=[node for node in tree.body if isinstance(node,ast.ImportFrom) and node.module=='__future__'
           or isinstance(node,ast.ClassDef) and node.name in wanted]
    namespace=dict(RuntimeArtifact=RuntimeArtifact,CoreRuntime=FakeRuntime,CoreError=RuntimeError,
                   threading=threading,uuid=uuid,Path=Path,BIN_DIR=Path('/legacy'),DATA_DIR=Path('/data'))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),namespace)
    return types.SimpleNamespace(**namespace),FakeRuntime


class CoreManagerArtifactTests(unittest.TestCase):
    def setUp(self):
        self.m,self.runtime=load_manager()
        self.map={core:artifact(core) for core in ('sing-box','xray')}

    def test_legacy_factory_retains_exact_three_positional_arguments(self):
        manager=self.m.CoreManager()
        instance=manager.get('sing-box').runtime
        self.assertEqual(instance.args,('sing-box',Path('/legacy/sing-box'),Path('/data/runtime/sing-box')))
        self.assertEqual(instance.kwargs,{})
        self.assertIsNone(manager.runtime_artifact('sing-box'))
        self.assertIs(instance,manager.get('sing-box').runtime)

    def test_configured_factory_passes_exact_immutable_descriptor(self):
        manager=self.m.CoreManager(artifacts=self.map)
        for core in self.map:
            descriptor=manager.runtime_artifact(core)
            self.assertIs(descriptor,self.map[core])
            self.assertEqual(self.runtime.calls,[])
        for core in self.map:
            instance=manager.get(core).runtime
            self.assertEqual(instance.args,(core,self.map[core].path,Path('/data/runtime')/core))
            self.assertEqual(instance.kwargs,{'artifact':self.map[core]})

    def test_bad_map_never_partially_changes_any_adapter(self):
        for bad in ({},{'sing-box':self.map['sing-box']},
                    {**self.map,'third':self.map['xray']},
                    {**self.map,'sing-box':self.map['xray']},
                    {**self.map,'xray':None},
                    {**self.map,'xray':artifact('xray',target='aarch64-gnu')}):
            manager=self.m.CoreManager()
            with self.subTest(map=bad),self.assertRaisesRegex(RuntimeError,'snapshot is invalid'):
                manager.configure_artifacts(bad)
            self.assertTrue(all(adapter.artifact is None and adapter._runtime is None
                                for adapter in manager.adapters.values()))

    def test_snapshot_freezes_map_and_repeated_equal_snapshot_is_idempotent(self):
        external=dict(self.map);manager=self.m.CoreManager(artifacts=external)
        external.clear()
        self.assertIs(manager.runtime_artifact('sing-box'),self.map['sing-box'])
        manager.configure_artifacts(dict(self.map))
        instance=manager.get('xray').runtime
        manager.configure_artifacts(dict(self.map))
        self.assertIs(manager.get('xray').runtime,instance)

    def test_changed_snapshot_or_late_injection_cannot_hot_swap_cached_runtime(self):
        manager=self.m.CoreManager(artifacts=self.map)
        old=manager.get('sing-box').runtime
        new=dict(self.map,xray=artifact('xray',source='d'*40))
        with self.assertRaisesRegex(RuntimeError,'coordinated restart'):
            manager.configure_artifacts(new)
        self.assertIs(manager.get('sing-box').runtime,old)
        self.assertIs(manager.runtime_artifact('xray'),self.map['xray'])
        legacy=self.m.CoreManager();existing=legacy.get('xray').runtime
        with self.assertRaisesRegex(RuntimeError,'coordinated restart'):
            legacy.configure_artifacts(self.map)
        self.assertIs(legacy.get('xray').runtime,existing)
        self.assertIsNone(legacy.runtime_artifact('sing-box'))

    def test_proxy_coordinator_reference_prevents_late_configuration(self):
        manager=self.m.CoreManager();marker=object();manager._proxy_coordinator=marker
        with self.assertRaisesRegex(RuntimeError,'coordinated restart'):
            manager.configure_artifacts(self.map)
        self.assertIs(manager._proxy_coordinator,marker)
        self.assertFalse(self.runtime.calls)

    def test_stopping_and_clearing_cache_never_reopens_snapshot_configuration(self):
        manager=self.m.CoreManager();old=manager.get('sing-box').runtime
        manager.stop_all()
        self.assertIsNone(manager.get('sing-box')._runtime)
        self.assertTrue(manager.get('sing-box')._runtime_created)
        with self.assertRaisesRegex(RuntimeError,'coordinated restart'):
            manager.configure_artifacts(self.map)
        self.assertIsNone(manager.runtime_artifact('sing-box'))
        # An old holder still has the old runtime. Reopening the manager's
        # factory must retain the same legacy material context after stop.
        new=manager.get('sing-box').runtime
        self.assertIsNot(new,old)
        self.assertEqual(old.args,new.args)
        self.assertEqual(new.kwargs,{})

    def test_parallel_first_get_keeps_one_cached_runtime_under_manager_lock(self):
        manager=self.m.CoreManager(artifacts=self.map)
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(lambda _:manager.get('sing-box').runtime,range(16)))
        self.assertTrue(all(value is values[0] for value in values))
        self.assertEqual(len(self.runtime.calls),1)
        self.assertIs(manager.get('sing-box')._runtime_lock,manager.lock)

    def test_standalone_adapter_rejects_other_core_descriptor_and_invalid_core(self):
        with self.assertRaisesRegex(RuntimeError,'snapshot is invalid'):
            self.m.XrayAdapter(self.map['sing-box'])
        manager=self.m.CoreManager()
        with self.assertRaisesRegex(RuntimeError,'Unsupported core'):
            manager.runtime_artifact('unknown')

    def test_diagnostic_snapshot_never_waits_for_shared_apply_or_recovery_lock(self):
        manager=self.m.CoreManager(artifacts=self.map)
        done=threading.Event();values=[]
        def read():
            values.append(manager.runtime_artifact('sing-box'));done.set()
        with manager.lock:
            thread=threading.Thread(target=read);thread.start()
            completed_under_lock=done.wait(1)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(completed_under_lock)
        self.assertEqual(values,[self.map['sing-box']])
        self.assertFalse(self.runtime.calls)

    def test_confirmation_context_is_stable_binds_both_cores_and_new_manager(self):
        manager=self.m.CoreManager()
        first=manager._yaml_confirmation_context()
        self.assertEqual(first, manager._yaml_confirmation_context())
        self.assertEqual(set(first), {'issuer','runtime_fingerprint'})
        manager.get('xray').runtime.marker=1
        second=manager._yaml_confirmation_context()
        self.assertNotEqual(first['runtime_fingerprint'],second['runtime_fingerprint'])
        self.assertEqual(first['issuer'],second['issuer'])
        other=self.m.CoreManager()._yaml_confirmation_context()
        self.assertNotEqual(first['issuer'],other['issuer'])
        self.assertEqual(first['runtime_fingerprint'],other['runtime_fingerprint'])


if __name__=='__main__':unittest.main()
