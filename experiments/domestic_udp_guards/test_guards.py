"""Pure actual-shape verifier and observer negative controls; never run a core."""
from copy import deepcopy
from pathlib import Path
import os
import unittest
import guard_contract as g
import verify_case as v
import test_fixture

class GuardControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy=Path(os.environ['VUI_STOCK_UDP_LEGACY']).resolve()
        cls.safe,_=g.safe_archive.modules(cls.legacy);g.r.load_framework(cls.legacy)
    def fixture(self,case='upstream-uuid'):return test_fixture.make(case,self.safe)
    def verify(self,values):return v.verify(*values,self.safe)
    def reject(self,values):
        with self.assertRaises((ValueError,KeyError,AssertionError,IndexError,TypeError)):self.verify(values)
    def test_all_fourteen_complete_positive_shapes(self):
        for case in g.IDS:
            with self.subTest(case=case):self.assertTrue(self.verify(self.fixture(case))['passed'])
    def test_packet_missing_duplicate_wrong_tuple_payload(self):
        for field in ('missing','duplicate','tuple','payload','short-window'):
            values=self.fixture();s=next(s for s in values[0]['steps'] if s['id']=='negative')
            if field=='missing':s.pop('sends')
            elif field=='duplicate':s['sends']*=2
            elif field=='tuple':s['requested'][1]+=1
            elif field=='payload':s['sends'][0]['wire_hex']=s['sends'][0]['wire_hex'][:-2]+'ff'
            else:s['completed']=s['sends'][0]['completed']+1.999
            with self.subTest(field=field):self.reject(values)
    def test_success_requires_raw_reply(self):
        for case in ('upstream-uuid','guarded-retarget'):
            values=self.fixture(case);step=next(x for x in values[0]['steps'] if x['kind']=='raw-echo' if case=='guarded-retarget') if case=='guarded-retarget' else values[0]['steps'][1]
            step.pop('receives');self.reject(values)
    def test_guard_rule_connection_and_destination_are_bound(self):
        for change in ('index','conn','dest','tail'):
            values=self.fixture('literal-ports');s=next(x for x in values[0]['steps'] if x['id']=='port-53');fresh=s['sends'][0]['fresh_logs']['domestic'];old=fresh['text']
            if change=='index':fresh['text']=old.replace('match[5]','match[0]')
            elif change=='tail':fresh['text']=old.replace('match[5]','match[7]')
            elif change=='conn':fresh['text']=old.replace('[123 1ms]','[124 1ms]')
            else:fresh['text']=old.replace(':53',':54')
            self.reject(values)
    def test_dns_answers_rcode_source_phase_and_fallback(self):
        for field in ('answers','source','phase','extra','ordinary','short-write'):
            values=self.fixture('dns-all');d=values[0]['dns'];entry=d['wire'][0]
            if field=='answers':entry['response']['answers']=[]
            elif field=='source':entry['peer']='127.0.0.1'
            elif field=='phase':entry['started']=entry['completed']=0
            elif field=='extra':d['wire'].append(deepcopy(entry))
            elif field=='ordinary':d['ordinary'].append('unexpected')
            else:entry['write_return']-=1
            with self.subTest(field=field):self.reject(values)
    def test_rebinding_and_retarget_keep_original_association(self):
        values=self.fixture('same-port-rebind');s=next(x for x in values[0]['steps'] if x['id']=='same');s['sends'][0]['association']=0;self.reject(values)
    def test_roles_owners_build_and_post_identity(self):
        for field in ('role','owner','post','version','pid'):
            values=self.fixture();row,roles,inputs,_=values
            if field=='role':roles[0]['origin']='candidate'
            elif field=='owner':roles[1]['owner_option_absent']=False
            elif field=='post':row.pop('post_identity')
            elif field=='version':inputs['binaries']['candidate']['version']='1.14.2'
            else:roles[1]['launches'][0]['pid']=roles[0]['launches'][0]['pid']
            with self.subTest(field=field):self.reject(values)
    def test_cleanup_fd_and_target_lifetime(self):
        for field in ('fd','missing','thread','late','peer'):
            values=self.fixture();row=values[0]
            if field=='fd':row['closed_associations'][0]['control_fd']=3
            elif field=='missing':row['closed_associations'].pop()
            elif field=='thread':row['cleanup']['new_threads']=1
            elif field=='late':row['target_ledgers'][0]['received'].append(row['target_ledgers'][0]['received'][-1])
            else:row['target_ledgers'][0]['received'][-1]['peer']='127.0.0.1'
            with self.subTest(field=field):self.reject(values)
    def test_ordinary_export_reconfiguration_cannot_change_node(self):
        values=self.fixture('device-sni');values[1][-1]['config_sha256']='f'*64;self.reject(values)
    def test_missing_prerequisite_cannot_claim_later_fault(self):
        values=self.fixture();values[0]['steps'][1]['status']='failed';self.reject(values)
    def test_no_containment_replacement_or_overseas_domain_escape(self):
        values=self.fixture();values[0]['route_contract']['domestic']['rules'][0]={'action':'reject'};self.reject(values)
        values=self.fixture();values[1][0]['route_shape']['rules']=[];self.reject(values)
    def test_missing_full_log_and_forged_fresh_reason(self):
        values=self.fixture();values[0]['process_logs'].clear();self.reject(values)
        values=self.fixture();s=next(x for x in values[0]['steps'] if x['id']=='negative');s['sends'][0]['fresh_logs']['upstream']['text']='timeout\n';self.reject(values)

    def test_empty_checkpoint_cannot_detach_fresh_rejection(self):
        values=self.fixture();s=next(x for x in values[0]['steps'] if x['id']=='negative');s['sends'][0]['log_checkpoint']={};self.reject(values)
    def test_boolean_numeric_and_unknown_fields_fail_closed(self):
        for mutate in (lambda r:r.update(unknown='value'),lambda r:r['cleanup'].update(fixture_removed='false'),lambda r:r['cleanup'].update(new_threads=False),lambda r:r['associations'][0].update(id=False)):
            values=self.fixture();mutate(values[0]);self.reject(values)
    def test_mapped_log_unwrap_but_wire_remains_ipv6(self):
        values=self.fixture('literal-ports');self.assertTrue(self.verify(values)['passed'])
        s=next(x for x in values[0]['steps'] if x['id']=='literal-7');s['requested'][0]='127.0.0.7';self.reject(values)
    def test_red_prefix_still_audits_overseas_lifetime(self):
        values=self.fixture();row,roles,inputs,export=values
        # Protocol-red recovery with all owned processes disposed is not a leak.
        row['status']='failed';row['errors']=[{'type':'AssertionError','message':'synthetic failure'}];row['steps'][-1]['status']='failed'
        self.assertTrue(v.verify(*values,self.safe,safety_only=True));self.assertFalse(self.verify(values)['passed'])
        name=next(n for n in row['process_logs'] if n.endswith('-overseas.log'));row['process_logs'][name]+='INFO[0000] [123 0ms] '+v.PACKET+'169.254.169.254:12345\n';self.reject(values)

if __name__=='__main__':unittest.main()
