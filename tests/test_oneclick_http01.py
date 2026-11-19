"""Pure/fake checks for hosted installer fixtures; never bind a real port."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from test_oneclick_system import (http01_responder_arguments, occupied_http01_port,
                                 require_http01_fixture_host)


class HTTP01FixtureContractTests(unittest.TestCase):
    def test_http_responder_flag_comes_from_the_actual_installer_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, frozen = root/'current', root/'frozen'
            for source, declaration in ((current, "p.add_argument('--enable-http01', action='store_true')"),
                                        (frozen, "p.add_argument('--cert')")):
                (source/'scripts').mkdir(parents=True)
                (source/'scripts/install_system.py').write_text(declaration+'\nraise AssertionError("do not execute")\n')
                (source/'VERSION').write_text('0.4.26\n')
            cases = [({}, ['--enable-http01']),
                     ({'VUI_PUBLISHED_INSTALL_VERSION':'0.4.26'}, ['--enable-http01']),
                     ({'VUI_PUBLISHED_UPGRADE_SOURCE':str(frozen)}, []),
                     ({'VUI_PUBLISHED_INSTALL_VERSION':'0.4.26','VUI_SETUP_INSTALL':'1',
                       'VUI_SETUP_RELEASE_SOURCE':str(frozen)}, [])]
            for environ, expected in cases:
                with self.subTest(environ=environ):
                    self.assertEqual(http01_responder_arguments(current,environ),expected)
            self.assertEqual(http01_responder_arguments(frozen,{}),[])
            (frozen/'scripts/install_system.py').write_text(
                '# p.add_argument("--enable-http01")\nexample = \'p.add_argument("--enable-http01")\'\n')
            self.assertEqual(http01_responder_arguments(frozen,{}),[])

    def test_real_port_fixture_requires_all_hosted_runner_gates(self):
        good = {'VUI_SYSTEM_INSTALL_TEST':'1','GITHUB_ACTIONS':'true',
                'RUNNER_ENVIRONMENT':'github-hosted','GITHUB_REPOSITORY':'ForceMind/V-UI'}
        for missing in good:
            environ = {key:value for key,value in good.items() if key != missing}
            with self.subTest(missing=missing),patch.dict(os.environ,environ,clear=True), \
                    patch('test_oneclick_system.os.geteuid',return_value=1000), \
                    patch('test_oneclick_system.subprocess.Popen') as spawn:
                with self.assertRaises(AssertionError):
                    with occupied_http01_port():self.fail('fixture must not start')
                spawn.assert_not_called()
        with patch.dict(os.environ,good,clear=True),patch('test_oneclick_system.os.geteuid',return_value=0):
            with self.assertRaises(AssertionError):require_http01_fixture_host()
        with patch.dict(os.environ,{**good,'RUNNER_ENVIRONMENT':'self-hosted'},clear=True), \
                patch('test_oneclick_system.os.geteuid',return_value=1000):
            with self.assertRaises(AssertionError):require_http01_fixture_host()

    def fixture(self, output=b'READY\n', code=0):
        process = Mock(stdin=io.BytesIO(),stdout=io.BytesIO(output))
        process.poll.return_value = None
        process.wait.return_value = code
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200;response.read.return_value = b'oneclick-owned-port80\n'
        return process,connection

    def test_owned_listener_stays_checked_and_closes_only_its_pipes(self):
        process,connection = self.fixture()
        with patch('test_oneclick_system.require_http01_fixture_host'), \
                patch('test_oneclick_system.subprocess.Popen',return_value=process) as spawn, \
                patch('test_oneclick_system.select.select',return_value=([process.stdout],[],[])), \
                patch('test_oneclick_system.http.client.HTTPConnection',return_value=connection):
            with occupied_http01_port() as check:
                check()
            command = spawn.call_args.args[0]
            self.assertEqual(command[:7],['sudo','-n','/usr/bin/python3','-B','-I','-S','-c'])
            compile(command[7],'<port80-fixture>','exec')
        self.assertEqual(connection.request.call_count,2)
        self.assertEqual(connection.close.call_count,2)
        self.assertTrue(process.stdin.closed and process.stdout.closed)
        process.wait.assert_called_once_with(timeout=10)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_existing_port_owner_is_never_stopped_when_reservation_fails(self):
        for output in (b'Address already in use\n',b'',b'REA'):
            with self.subTest(output=output):
                process,connection = self.fixture(output,1)
                with patch('test_oneclick_system.require_http01_fixture_host'), \
                        patch('test_oneclick_system.subprocess.Popen',return_value=process), \
                        patch('test_oneclick_system.select.select',return_value=([process.stdout],[],[])), \
                        patch('test_oneclick_system.http.client.HTTPConnection',return_value=connection) as probe:
                    with self.assertRaisesRegex(AssertionError,'Could not reserve port 80'):
                        with occupied_http01_port():self.fail('reservation failed or READY was truncated at EOF')
                probe.assert_not_called()
                process.terminate.assert_not_called();process.kill.assert_not_called()
                self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_fragmented_ready_line_uses_one_deadline(self):
        process,connection = self.fixture()
        original_read = process.stdout.read1
        with patch('test_oneclick_system.require_http01_fixture_host'), \
                patch('test_oneclick_system.subprocess.Popen',return_value=process), \
                patch('test_oneclick_system.select.select',return_value=([process.stdout],[],[])) as readable, \
                patch.object(process.stdout,'read1',side_effect=lambda count:original_read(min(count,3))) as read, \
                patch('test_oneclick_system.time.monotonic',side_effect=[0,1,9,9.5]), \
                patch('test_oneclick_system.http.client.HTTPConnection',return_value=connection):
            with occupied_http01_port():pass
        self.assertEqual([call.args[3] for call in readable.call_args_list],[9,1])
        self.assertEqual([call.args[0] for call in read.call_args_list],[6,3])
        self.assertTrue(process.stdin.closed and process.stdout.closed)
        process.terminate.assert_not_called();process.kill.assert_not_called()

    def test_partial_ready_line_times_out_with_a_real_pipe_still_open(self):
        process,connection = self.fixture()
        process.stdout.close()
        read_fd,write_fd = os.pipe()
        process.stdout = os.fdopen(read_fd,'rb')
        release = threading.Event()
        real_monotonic = time.monotonic
        def hold_writer():
            try:release.wait(1)
            finally:os.close(write_fd)
        os.write(write_fd,b'REA')
        writer = threading.Thread(target=hold_writer,daemon=True);writer.start()
        clock = iter([0,9.95])
        started = real_monotonic()
        try:
            with patch('test_oneclick_system.require_http01_fixture_host'), \
                    patch('test_oneclick_system.subprocess.Popen',return_value=process), \
                    patch('test_oneclick_system.time.monotonic',side_effect=lambda:next(clock,9.95)), \
                    patch('test_oneclick_system.http.client.HTTPConnection',return_value=connection) as probe:
                with self.assertRaisesRegex(AssertionError,'Could not reserve port 80'):
                    with occupied_http01_port():self.fail('partial READY line is not readiness')
            self.assertLess(real_monotonic()-started,.5,'Reading the partial line waited for writer EOF')
            self.assertTrue(writer.is_alive(),'The timeout must happen while the pipe writer remains open')
            probe.assert_not_called()
            self.assertTrue(process.stdin.closed and process.stdout.closed)
            process.wait.assert_called_once_with(timeout=10)
            process.terminate.assert_not_called();process.kill.assert_not_called()
        finally:
            release.set();writer.join(timeout=2)
            process.stdout.close()
        self.assertFalse(writer.is_alive())

    def test_unconfirmed_listener_shutdown_fails_the_fixture(self):
        process,connection = self.fixture()
        process.wait.side_effect = [subprocess.TimeoutExpired('owned-listener',10),0]
        with patch('test_oneclick_system.require_http01_fixture_host'), \
                patch('test_oneclick_system.subprocess.Popen',return_value=process), \
                patch('test_oneclick_system.select.select',return_value=([process.stdout],[],[])), \
                patch('test_oneclick_system.http.client.HTTPConnection',return_value=connection):
            with self.assertRaisesRegex(AssertionError,'shutdown was not confirmed'):
                with occupied_http01_port():pass
        process.terminate.assert_called_once_with();process.kill.assert_not_called()
        self.assertTrue(process.stdin.closed and process.stdout.closed)


if __name__ == '__main__':unittest.main()
