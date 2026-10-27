"""No sudo or host writes: test only the hosted-fixture gate and metadata model."""
import os
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import online_install_helpers as helpers


class HostedCommandDirectoryTests(unittest.TestCase):
    environment = {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'ForceMind/V-UI',
                   'RUNNER_ENVIRONMENT':'github-hosted','VUI_SYSTEM_INSTALL_TEST':'1'}

    def test_fixture_refuses_outside_explicit_hosted_gate_before_any_command(self):
        with patch.dict(os.environ,{},clear=True),patch.object(helpers.subprocess,'run') as run:
            with self.assertRaisesRegex(AssertionError,'Hosted empty-runner'):
                with helpers.hosted_command_directory():
                    self.fail('must not enter')
            run.assert_not_called()

    def model(self,uid=1001,gid=1001,mode=0o777):
        value=SimpleNamespace(st_dev=1,st_ino=2,st_uid=uid,st_gid=gid,st_mode=stat.S_IFDIR|mode)
        class Directory:
            def __str__(self):return '/explicit-fixture/bin'
            def lstat(self):return value
        return Directory(),value

    def test_fixture_restores_exact_owned_metadata_without_real_sudo(self):
        directory,value=self.model()
        commands=[]
        def run(args,**kwargs):
            commands.append(args)
            if args[2]=='/usr/bin/chown':
                value.st_uid,value.st_gid=map(int,args[3].split(':'))
            else:value.st_mode=stat.S_IFDIR|int(args[3],8)
        with patch.dict(os.environ,self.environment,clear=True),patch.object(helpers.platform,'system',return_value='Linux'),patch.object(helpers,'COMMAND_DIRECTORY',directory), \
                patch.object(helpers.subprocess,'run',side_effect=run):
            with helpers.hosted_command_directory():
                self.assertEqual((value.st_uid,value.st_gid,value.st_mode&0o777),(0,0,0o755))
        self.assertEqual((value.st_uid,value.st_gid,value.st_mode&0o777),(1001,1001,0o777))
        self.assertEqual(len(commands),4)

    def test_replaced_directory_is_never_restored_or_hidden_by_prior_failure(self):
        directory,value=self.model(0,0,0o755)
        with patch.dict(os.environ,self.environment,clear=True),patch.object(helpers.platform,'system',return_value='Linux'),patch.object(helpers,'COMMAND_DIRECTORY',directory), \
                patch.object(helpers.subprocess,'run') as run:
            with self.assertRaisesRegex(AssertionError,'identity changed'):
                with helpers.hosted_command_directory():value.st_ino=9
            run.assert_not_called()
        directory,value=self.model(0,0,0o755)
        error=RuntimeError('original failure')
        with patch.dict(os.environ,self.environment,clear=True),patch.object(helpers.platform,'system',return_value='Linux'),patch.object(helpers,'COMMAND_DIRECTORY',directory), \
                patch.object(helpers.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'original failure'):
                with helpers.hosted_command_directory():
                    value.st_ino=9
                    raise error
            run.assert_not_called()
        self.assertIn('cleanup was not confirmed',error.__notes__[0])
