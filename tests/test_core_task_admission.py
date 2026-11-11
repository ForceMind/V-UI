"""Root admission/CAS and final boundary ordering against fake authorities."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_admitted_core_control',ROOT/'deploy/core_maintenance_control.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class CoreTaskAdmissionTests(unittest.TestCase):
    def guard(self,action='update',parameters=None):
        intent={'id':'a'*32,'phase':'preflight','action':action,'parameters':parameters or {'core':'sing-box','release_id':7,'selection_revision':None}}
        authority=SimpleNamespace(admission=Mock(side_effect=lambda **kwargs:deepcopy(intent)),
            enter_mutation=Mock(),advance_epoch=Mock())
        return c._AdmissionGuard(authority,intent),authority
    def test_live_preflight_never_consumes_but_exact_mutation_only_once(self):
        guard,authority=self.guard()
        guard.live();authority.enter_mutation.assert_not_called()
        guard.enter({'actual':'fixed plan'});guard.enter({'actual':'recovery safety closure'})
        authority.enter_mutation.assert_called_once()
        self.assertEqual(len(authority.enter_mutation.call_args.kwargs['plan_sha256']),64)
        authority.advance_epoch.assert_not_called()
    def test_revoked_or_changed_admission_blocks_first_mutation(self):
        guard,authority=self.guard();authority.admission.side_effect=ValueError('revoked')
        with self.assertRaises(ValueError):guard.enter({'actual':1})
        authority.enter_mutation.assert_not_called()
        guard,authority=self.guard();authority.admission.side_effect=lambda **kwargs:{'id':'different'}
        with self.assertRaises(c.CoreControlError):guard.enter({'actual':1})
        authority.enter_mutation.assert_not_called()
    def test_restore_reduction_occurs_after_consumption_and_preserves_safety_closure(self):
        guard,authority=self.guard('restore',{'backup_task_id':'c'*32,'selection_revision':'d'*64})
        events=[];authority.enter_mutation.side_effect=lambda **kwargs:events.append('consume')
        authority.advance_epoch.side_effect=lambda:events.append('reduce')
        guard.enter({'actual':'restore plan'})
        authority.admission.side_effect=ValueError('epoch revoked own grant')
        guard.live();guard.enter({'actual':'same admitted safety closure'})
        self.assertEqual(events,['consume','reduce'])
    def test_pending_recovery_must_match_exact_transaction_sequence_and_revision(self):
        parameters={'recovery_kind':'switch','pending_transaction':'a'*32,'pending_sequence':3,'selection_revision':'b'*64}
        journal={'transaction':'a'*32,'sequence':3,'before':{'revision':'b'*64}}
        for change in ({'transaction':'c'*32},{'sequence':4},{'before':{'revision':'d'*64}}):
            guard,authority=self.guard('recover',parameters)
            with self.assertRaises(c.CoreControlError):guard.enter({'actual':1},journal={**journal,**change})
            authority.enter_mutation.assert_not_called()
        guard,authority=self.guard('recover',parameters);guard.enter({'actual':1},journal=journal)
        authority.enter_mutation.assert_called_once()

    def test_observe_candidate_network_failure_is_not_silent_empty_success(self):
        from app.core_updates import artifacts,github
        client=Mock();hint=SimpleNamespace(release_id=7,tag='core-sing-box-v1.14.3-r1')
        client.discover.return_value=SimpleNamespace(hints=[hint])
        client.inspect.side_effect=github.CoreGitHubError('HTTP_403')
        modules=SimpleNamespace(artifacts=artifacts,github=SimpleNamespace(GitHubCoreClient=lambda:client,CoreGitHubError=github.CoreGitHubError))
        cores={name:{'candidates':[]} for name in ('sing-box','xray')}
        self.assertIn('暂不可用',c._observe_candidates(modules,cores,'a'*40,'x86_64-gnu',__import__('time').monotonic()+10))
        self.assertEqual(client.inspect.call_count,1)
    def test_observe_candidate_tag_drift_is_explicit_and_display_uses_verified_version(self):
        from app.core_updates import artifacts,github
        client=Mock();hint=SimpleNamespace(release_id=7,tag='core-sing-box-v1.14.3-r1')
        client.discover.side_effect=[SimpleNamespace(hints=[hint]),SimpleNamespace(hints=[])]
        candidate=SimpleNamespace(authority=SimpleNamespace(release_id=7,tag='core-sing-box-v1.14.4-r1'),
                                  verified_release=SimpleNamespace(packs=[SimpleNamespace(target='x86_64-gnu')]))
        client.inspect.return_value=candidate
        modules=SimpleNamespace(artifacts=artifacts,github=SimpleNamespace(GitHubCoreClient=lambda:client,CoreGitHubError=github.CoreGitHubError))
        cores={name:{'candidates':[]} for name in ('sing-box','xray')}
        self.assertIn('发生变化',c._observe_candidates(modules,cores,'a'*40,'x86_64-gnu',__import__('time').monotonic()+10))
        self.assertFalse(cores['sing-box']['candidates'])
        client.discover.side_effect=[SimpleNamespace(hints=[hint]),SimpleNamespace(hints=[])]
        candidate.authority.tag=hint.tag
        self.assertIsNone(c._observe_candidates(modules,cores,'a'*40,'x86_64-gnu',__import__('time').monotonic()+10))
        self.assertEqual(cores['sing-box']['candidates'],[{'release_id':7,'version':'1.14.3'}])
