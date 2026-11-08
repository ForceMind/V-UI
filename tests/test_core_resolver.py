"""Read-only resolver integration with synthetic API/materials and private files."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from app.core_updates import artifacts as a, catalog as c, github as g, resolver as r, selection as s, store as fs
from app.core_updates.binary import RuntimeArtifact
from test_core_artifacts import APP, BUILD, Fixture
from test_core_github import Transport


class CoreResolverTests(unittest.TestCase):
    @contextmanager
    def fixture(self, core='sing-box', target='x86_64-gnu', *, record=True, prepare=True):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();root.chmod(0o700)
            f=Fixture(core,'1.14.3' if core=='sing-box' else '26.10.21')
            transport=Transport(f);client=g.GitHubCoreClient(opener=transport)
            candidate=client.inspect(31,app_source=APP)
            catalog=c.CoreCatalog(fs.Layout(root/'catalog',os.geteuid(),root))
            store=fs.CoreArtifactStore(fs.Layout(root/'store',os.geteuid(),root))
            pack=root/'input.zip';pack.write_bytes(f.pack_bytes[target])
            if record:catalog.record(candidate,app_source=APP,client=client)
            identity=a.verify_pack(pack,candidate.verified_release,target=target)
            if prepare:store.prepare(pack,candidate.manifest,candidate.checksums,
                authority=candidate.authority,expected_tag=f.tag,expected_builder_source=BUILD,
                app_source=APP,target=target)
            descriptors={}
            for name,version in (('sing-box','1.14.2'),('xray','26.3.27')):
                path=root/('bundle-'+name);body=('fake-bundle-'+name).encode()
                path.write_bytes(body);path.chmod(0o700)
                reference=s.bundle_reference(core=name,version=version,target=target,
                    app_source=APP,payload_manifest_sha256='d'*64,
                    binary_sha256=hashlib.sha256(body).hexdigest(),binary_size=len(body))
                descriptors[name]=RuntimeArtifact(reference,path,os.geteuid(),root,0o700)
            resolver=r.CoreResolver(app_source=APP,target=target,bundles=descriptors,catalog=catalog,store=store)
            state=s.initial_selection(app_source=APP,target=target,
                bundles={name:value.reference for name,value in descriptors.items()})
            before_calls=list(transport.calls)
            yield root,transport,resolver,state,descriptors,identity,catalog,store
            self.assertEqual(transport.calls,before_calls,'Resolution must be offline')
            transport.closed(self)

    def test_component_resolution_binds_independent_catalog_store_and_full_reference_without_network(self):
        for core in s.CORES:
            for target in a.TARGETS:
                with self.subTest(core=core,target=target),self.fixture(core,target) as (_,_,resolver,state,_,identity,_,_):
                    selected=s.plan_component(state,core=core,identity=identity,expected_revision=state.revision).after
                    artifact=resolver.current(selected,core)
                    self.assertEqual(artifact.reference,s.component_reference(identity))
                    self.assertEqual(artifact.artifact_id,s.reference_identity(artifact.reference))
                    self.assertEqual(artifact.path.read_bytes(),b'NOT AN ELF: synthetic binary '+target.encode())
                    self.assertEqual(hashlib.sha256(artifact.path.read_bytes()).hexdigest(),identity.binary_sha256)

    def test_bundle_resolution_uses_only_verified_current_context_and_rechecks_file(self):
        with self.fixture() as (_,_,resolver,state,bundles,_,_,_):
            for core in s.CORES:self.assertIs(resolver.current(state,core),bundles[core])
            bundles['sing-box'].path.write_bytes(b'changed bundle')
            with self.assertRaisesRegex(r.CoreResolutionError,'MATERIAL_UNAVAILABLE_OR_CHANGED'):
                resolver.current(state,'sing-box')

    def test_no_catalog_or_no_store_cannot_fallback_to_bundle_or_artifact_trust(self):
        for record,prepare in ((False,True),(True,False)):
            with self.subTest(record=record,prepare=prepare),self.fixture(record=record,prepare=prepare) as (_,_,resolver,state,_,identity,_,_):
                selected=s.plan_component(state,core='sing-box',identity=identity,expected_revision=state.revision).after
                with self.assertRaisesRegex(r.CoreResolutionError,'MATERIAL_UNAVAILABLE_OR_CHANGED'):
                    resolver.current(selected,'sing-box')

    def test_wrong_component_identity_refuses_even_if_another_valid_material_exists(self):
        with self.fixture() as (_,_,resolver,_,_,identity,_,_):
            reference=s.component_reference(identity)
            for field,value in (('release_id',999),('builder_source','e'*40),('binary_sha256','e'*64),
                                ('checksums_sha256','e'*64),('pack_sha256','e'*64),
                                ('pack_manifest_sha256','e'*64)):
                with self.subTest(field=field),self.assertRaises(r.CoreResolutionError):
                    resolver.resolve(replace(reference,**{field:value}))

    def test_damaged_store_or_catalog_refuses_without_automatic_repair_or_network(self):
        for what in ('store','catalog'):
            with self.subTest(what=what),self.fixture() as (_,_,resolver,_,_,identity,catalog,store):
                directory=(store.layout.root/'artifacts' if what=='store' else catalog.layout.root)/identity.release_manifest_sha256
                filename=directory/('TRUST.json' if what=='store' else 'CONTEXT.json')
                filename.write_bytes(b'bad')
                with self.assertRaises(r.CoreResolutionError):resolver.resolve(s.component_reference(identity))
                self.assertEqual(filename.read_bytes(),b'bad')

    def test_state_context_or_checksum_is_not_silently_accepted(self):
        with self.fixture() as (_,_,resolver,state,_,_,_,_):
            for bad in (replace(state,app_source='e'*40),replace(state,target='aarch64-gnu'),
                        replace(state,revision='e'*64)):
                with self.subTest(state=bad),self.assertRaises(r.CoreResolutionError):resolver.current(bad,'sing-box')
            with self.assertRaises(r.CoreResolutionError):resolver.current(state,'unknown')

    def test_archived_bundle_or_unverified_different_bundle_reference_is_not_rebound(self):
        with self.fixture() as (_,_,resolver,state,_,_,_,_):
            reference=state.slot('sing-box').current
            for bad in (replace(reference,app_source='e'*40),replace(reference,binary_sha256='e'*64),
                        replace(reference,target='aarch64-gnu')):
                with self.subTest(reference=bad),self.assertRaises(r.CoreResolutionError):resolver.resolve(bad)

    def test_constructor_freezes_bundle_mapping_and_never_mutates_selector_or_runtime(self):
        with self.fixture() as (root,_,resolver,state,bundles,_,catalog,store):
            external=dict(bundles)
            other=r.CoreResolver(app_source=APP,target=state.target,bundles=external,catalog=catalog,store=store)
            external.clear()
            self.assertIs(other.current(state,'sing-box'),bundles['sing-box'])
            for name in ('selection.json','state.json','CORE_UPDATE_PENDING.json'):
                self.assertFalse((root/name).exists())
            self.assertFalse(hasattr(resolver,'activate'))


if __name__=='__main__':unittest.main()
