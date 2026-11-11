"""Pure metadata projections and inert Python workers; no network/core binaries."""
from copy import deepcopy
import json
import io
import threading
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from app.core_updates.discovery_worker import discover_result,validate_result,MAX_OUTPUT
from app.core_updates.github import Discovery,ReleaseHint
from app.services import core_hints


def payload():
    return {'core':'sing-box','qualification':'unverified','truncated':False,
            'hints':[{'release_id':1,'tag':'core-sing-box-v1.14.3-r1','version':'1.14.3','revision':1,
                      'published_at':'2026-11-09T00:00:00Z'}]}

def encode(value):return json.dumps(value,separators=(',',':')).encode()


class CoreDiscoveryProjectionTests(unittest.TestCase):
    def test_metadata_hints_are_never_compatibility_or_preparation_authority(self):
        row=ReleaseHint(1,'core-sing-box-v1.14.3-r1','sing-box','1.14.3',1,'2026-11-09T00:00:00Z')
        client=Mock();client.discover.return_value=Discovery((row,),False,1,1)
        value=validate_result(discover_result('sing-box',client),'sing-box')
        self.assertEqual(value,payload());self.assertEqual(value['qualification'],'unverified')
        self.assertIn('deadline',client.discover.call_args.kwargs)
        self.assertNotIn('url',repr(value))
    def test_closed_shape_duplicates_limits_and_claims_fail(self):
        good=payload()
        for key,changed in (('qualification','qualified'),('truncated',1),('core','xray'),
                            ('hints',good['hints']*33),('hints',good['hints']*2)):
            value=deepcopy(good);value[key]=changed
            with self.subTest(key=key),self.assertRaises(ValueError):validate_result(encode(value),'sing-box')
        value=deepcopy(good);value['hints'][0]['download_url']='https://private/secret'
        with self.assertRaises(ValueError):validate_result(encode(value),'sing-box')
        with self.assertRaises(ValueError):validate_result(b'{"core":"sing-box","core":"xray"}','sing-box')
        with self.assertRaises(ValueError):validate_result(b' '* (MAX_OUTPUT+1),'sing-box')
    def test_release_id_tag_and_timestamp_must_agree(self):
        for key,changed in (('release_id',True),('version','1.14.2'),('revision',True),
                            ('tag','core-xray-v26.10.21-r1'),('published_at','private-text')):
            value=payload();value['hints'][0][key]=changed
            with self.subTest(key=key),self.assertRaises(ValueError):validate_result(encode(value),'sing-box')
    def test_successful_empty_channel_stays_distinct_from_failure(self):
        client=Mock();client.discover.return_value=Discovery((),False,1,0)
        self.assertEqual(json.loads(discover_result('xray',client))['hints'],[])
        client.discover.side_effect=ValueError('private detail')
        with self.assertRaises(ValueError):discover_result('xray',client)


@unittest.skipUnless(os.name=='posix','POSIX service worker')
class CoreDiscoveryBrokerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='vui-discover-');self.addCleanup(temporary.cleanup)
        self.worker=Path(temporary.name)/'inert_worker.py';self.processes=[]
        self.original=subprocess.Popen
    def run_worker(self,code,deadline=1):
        self.worker.write_text(code)
        def spawn(*args,**kwargs):
            process=self.original(*args,**kwargs);self.processes.append(process);return process
        with patch.object(core_hints,'WORKER',self.worker),patch.object(core_hints,'DEADLINE_SECONDS',deadline),patch.object(core_hints.subprocess,'Popen',side_effect=spawn):
            return core_hints.discover('sing-box')
    def test_success_returns_only_validated_hint_projection(self):
        raw=encode(payload())
        value=self.run_worker('import sys\nsys.stdout.buffer.write('+repr(raw)+')\n')
        self.assertEqual(value,payload());self.assertEqual(self.processes[0].returncode,0)
    def test_deadline_kills_and_reaps_stuck_worker(self):
        start=time.monotonic()
        with self.assertRaises(core_hints.CoreHintsUnavailable) as error:self.run_worker('import time\ntime.sleep(60)\n',.15)
        self.assertEqual(error.exception.code,'DEADLINE');self.assertLess(time.monotonic()-start,3)
        self.assertIsNotNone(self.processes[0].returncode)
    def test_eof_without_exit_is_still_bounded(self):
        with self.assertRaises(core_hints.CoreHintsUnavailable) as error:self.run_worker('import os,time\nos.close(1)\ntime.sleep(60)\n',.15)
        self.assertEqual(error.exception.code,'DEADLINE');self.assertIsNotNone(self.processes[0].returncode)
    def test_output_overflow_or_nonzero_exit_is_not_empty_success(self):
        for code in ('import sys\nsys.stdout.buffer.write(b"x"*100000)\n','raise SystemExit(7)\n'):
            with self.subTest(code=code),self.assertRaises(core_hints.CoreHintsUnavailable):self.run_worker(code)
            self.assertIsNotNone(self.processes[-1].returncode)
    def test_slot_exhaustion_never_spawns_another_worker(self):
        self.assertTrue(core_hints.SLOTS.acquire(False));self.assertTrue(core_hints.SLOTS.acquire(False))
        try:
            with patch.object(core_hints.subprocess,'Popen') as spawn:
                with self.assertRaises(core_hints.CoreHintsUnavailable) as error:core_hints.discover('sing-box')
                self.assertEqual(error.exception.code,'BUSY');spawn.assert_not_called()
        finally:core_hints.SLOTS.release();core_hints.SLOTS.release()

    def test_uncertain_cleanup_closes_pipe_and_quarantines_slot(self):
        for kill_failure in (False,True):
            with self.subTest(kill_failure=kill_failure):
                pipe=io.BytesIO()
                child=Mock(pid=12345,stdout=pipe,returncode=None)
                child.wait.side_effect=subprocess.TimeoutExpired('fixed worker',2)
                slots=threading.BoundedSemaphore(2)
                kill=PermissionError('private exception text') if kill_failure else None
                with patch.object(core_hints,'SLOTS',slots),patch.object(core_hints.subprocess,'Popen',return_value=child),patch.object(core_hints.os,'killpg',side_effect=kill):
                    with self.assertRaises(core_hints.CoreHintsUnavailable) as error:core_hints.discover('sing-box')
                    self.assertEqual(error.exception.code,'CLEANUP')
                self.assertTrue(pipe.closed)
                self.assertTrue(slots.acquire(False));self.assertFalse(slots.acquire(False))
