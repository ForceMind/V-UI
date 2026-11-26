"""Read-only unit tests for the embedded Linux preflight; no host service calls."""
import contextlib
import ast
import copy
import io
import ipaddress
import json
import math
import pathlib
import re
import shutil
import sqlite3
import stat
import sys
import types
import tempfile
import unittest
from unittest.mock import patch


SOURCE = (pathlib.Path(__file__).parent / 'zh-migrate.sh').read_text(encoding='utf-8')
PREFLIGHT = SOURCE.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
APP = '/usr/local/x-ui'
PROPS = {
    'LoadState': 'loaded', 'User': '', 'Group': '', 'WorkingDirectory': APP,
    'ExecStart': '{ path=/usr/local/x-ui/x-ui ; argv[]=/usr/local/x-ui/x-ui ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }',
    'FragmentPath': '/etc/systemd/system/x-ui.service',
    'Environment': 'XRAY_VMESS_AEAD_FORCED=false',
    'EnvironmentFiles': '/etc/default/x-ui (ignore_errors=yes)',
    'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0',
}


class Preflight(unittest.TestCase):
    def run_preflight(self, props=None, files=None, version='3.11.0', modes=None, mode='migrate'):
        properties = copy.deepcopy(PROPS)
        properties.update(props or {})
        contents = {
            '/etc/x-ui/x-ui.db': b'SQLite format 3\0' + b'fixture',
            APP + '/x-ui': b'fixture-binary',
            APP + '/bin/xray-linux-amd64': b'fixture-core',
            APP + '/x-ui.sh': b'fixture-app-cli',
            '/etc/systemd/system/x-ui.service': b'fixture-unit',
            '/usr/bin/x-ui': b'fixture-cli',
        }
        contents.update(files or {})
        directories = {'/', '/usr', '/usr/local', APP, APP+'/bin', '/usr/local/bin', '/usr/bin', '/etc', '/etc/default', '/etc/conf.d', '/etc/sysconfig', '/etc/x-ui', '/etc/systemd', '/etc/systemd/system'}
        modes = modes or {}
        calls = []

        class FakePath(pathlib.PurePosixPath):
            def exists(self):
                return str(self) in contents or str(self) in directories

            def is_symlink(self):
                return stat.S_ISLNK(modes.get(str(self), (0, 0, 1))[0])

            def is_dir(self):
                return str(self) in directories

            def is_file(self):
                return str(self) in contents

            def lstat(self):
                if not self.exists():
                    raise FileNotFoundError(str(self))
                mode, uid, nlink = modes.get(str(self), ((stat.S_IFDIR if self.is_dir() else stat.S_IFREG) | 0o755, 0, 1))
                return types.SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=nlink)

            stat = lstat

            def read_text(self):
                return contents[str(self)].decode()

            def read_bytes(self):
                return contents[str(self)]

            def open(self, mode):
                assert mode == 'rb'
                return io.BytesIO(contents[str(self)])

        def check_output(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[:3], ['systemctl', 'show', 'x-ui.service'])
            return properties.get(args[3].split('=', 1)[1], '')

        def run(args, **kwargs):
            calls.append(args)
            self.assertEqual(args[0], 'setpriv')
            self.assertIn('--no-new-privs', args)
            self.assertEqual(args[-2:], [APP + '/x-ui', '-v'])
            return types.SimpleNamespace(stdout=version)

        output = io.StringIO()
        nobody = types.SimpleNamespace(pw_uid=65534, pw_gid=65534)
        fake_pwd = types.SimpleNamespace(getpwnam=lambda name: nobody)
        with patch.object(pathlib, 'Path', FakePath), patch('subprocess.check_output', check_output), patch('subprocess.run', run), patch.dict(sys.modules, {'pwd': fake_pwd}), patch.object(sys, 'argv', ['-', APP, mode]), contextlib.redirect_stdout(output):
            exec(compile(PREFLIGHT, 'migration-preflight', 'exec'), {})
        return json.loads(output.getvalue()), calls

    def test_stock_sqlite(self):
        state, calls = self.run_preflight()
        self.assertEqual(state['database'], '/etc/x-ui')
        self.assertFalse(state['was_active'])
        self.assertEqual(state['environment'], {})
        self.assertEqual(len([x for x in calls if x[0] == 'setpriv']), 1)

    def test_original_version_prefix(self):
        self.run_preflight(version='v3.11.0\n')

    def test_original_292_has_separate_environment_and_core_contract(self):
        state, _ = self.run_preflight(version='2.9.2')
        self.assertTrue(state['legacy_upgrade'])
        self.assertEqual(state['source_version'], '2.9.2')
        state, _ = self.run_preflight(version='v2.9.2\n', props={'WorkingDirectory': ''})
        self.assertTrue(state['needs_workdir'])
        with self.assertRaises(SystemExit):
            self.run_preflight(version='2.9.2', props={'EnvironmentFiles':''}, files={'/etc/default/x-ui': b'XUI_DB_FOLDER=/etc/x-ui\n'})
        with self.assertRaises(SystemExit):
            self.run_preflight(version='2.9.2', props={'Environment':'XUI_BIN_FOLDER=/custom/bin'})
        with self.assertRaises(SystemExit):
            self.run_preflight(version='2.9.2', props={'WorkingDirectory':''}, files={APP+'/.env':b'XUI_LOG_LEVEL=info\n'})

    def test_known_original_cli_symlink_is_backed_up_as_link(self):
        with patch('os.path.realpath', return_value=APP+'/x-ui.sh'):
            state, _ = self.run_preflight(version='2.9.2', modes={'/usr/bin/x-ui':(stat.S_IFLNK|0o777,0,1)})
        self.assertTrue(state['upstream_cli_existed'])
        with patch('os.path.realpath', return_value='/tmp/untrusted'), self.assertRaises(SystemExit):
            self.run_preflight(version='2.9.2', modes={'/usr/bin/x-ui':(stat.S_IFLNK|0o777,0,1)})

    def test_official_unit_variants(self):
        for path in ('/etc/default/x-ui', '/etc/sysconfig/x-ui', '/etc/conf.d/x-ui'):
            with self.subTest(path=path):
                self.run_preflight(props={'WorkingDirectory': APP+'/', 'EnvironmentFiles': path+' (ignore_errors=yes)'})

    def test_systemctl_environment_files_preserves_repeated_and_ordered_records(self):
        # Ubuntu systemctl serializes repeated unit/drop-in entries as separate lines.
        repeated = '/etc/default/x-ui (ignore_errors=yes)\n/etc/default/x-ui (ignore_errors=yes)'
        state, _ = self.run_preflight(props={'Environment':'XRAY_VMESS_AEAD_FORCED=false XUI_CI_FIXTURE=preserve-env', 'EnvironmentFiles':repeated}, files={'/etc/default/x-ui':b'XUI_DB_FOLDER=/etc/x-ui\nXUI_LOG_FOLDER=/var/log/x-ui\n', APP+'/.env':b'XUI_LOG_LEVEL=warning\n'})
        self.assertEqual(state['environment']['XUI_CI_FIXTURE'],'preserve-env')
        self.assertEqual(state['environment']['XUI_LOG_LEVEL'],'warning')
        self.assertEqual(state['config_files'].count('/etc/default/x-ui'),1)
        for token in ('/etc/second env','"/etc/second env"',"'/etc/second env'"):
            with self.subTest(path_token=token):
                state, _ = self.run_preflight(props={'EnvironmentFiles':token+' (ignore_errors=no)'},files={'/etc/second env':b'XUI_LOG_LEVEL=error\n'})
                self.assertEqual(state['environment']['XUI_LOG_LEVEL'],'error')
        for separator in ('\n', '  ', '\n\t  '):
            with self.subTest(separator=repr(separator)):
                output = separator.join(['/etc/default/x-ui (ignore_errors=yes)', '"/etc/second env"  ( ignore_errors = no )', '/etc/default/x-ui (ignore_errors=no)'])
                state, _ = self.run_preflight(props={'EnvironmentFiles':output}, files={'/etc/default/x-ui':b'XUI_LOG_LEVEL=warning\n','/etc/second env':b'XUI_LOG_LEVEL=error\n'})
                self.assertEqual(state['environment']['XUI_LOG_LEVEL'],'warning')  # final repeated file wins
                state, _ = self.run_preflight(props={'EnvironmentFiles':separator.join(['/etc/default/x-ui (ignore_errors=yes)','"/etc/second env" (ignore_errors=no)'])}, files={'/etc/default/x-ui':b'XUI_LOG_LEVEL=warning\n','/etc/second env':b'XUI_LOG_LEVEL=error\n'})
                self.assertEqual(state['environment']['XUI_LOG_LEVEL'],'error')

    def test_systemctl_environment_files_consumes_every_record_or_rejects(self):
        malformed = ['junk /etc/default/x-ui (ignore_errors=yes)', '/etc/default/x-ui (ignore_errors=yes) trailing-junk', '/etc/default/x-ui (ignore_errors=yes)\n/etc/second (ignore_errors=maybe)', '/etc/default/x-ui (ignore_errors=yes)/etc/second (ignore_errors=no)', '/etc/../root/config (ignore_errors=yes)', '/etc//default/x-ui (ignore_errors=yes)', 'EnvironmentFile=-/etc/default/x-ui', '"/etc/unclosed (ignore_errors=yes)']
        for output in malformed:
            with self.subTest(output=output), self.assertRaises(SystemExit): self.run_preflight(props={'EnvironmentFiles':output})
        with self.assertRaises(SystemExit):
            self.run_preflight(props={'EnvironmentFiles':'"/etc/unsafe env" (ignore_errors=no)'}, files={'/etc/unsafe env':b'XUI_LOG_LEVEL=warning\n'}, modes={'/etc/unsafe env':(stat.S_IFREG|0o666,0,1)})

    def test_runtime_reloads_current_environment_and_ignores_snapshot(self):
        state, calls = self.run_preflight(mode='runtime', props={'Environment': 'XUI_NODE_TOKEN_KEY=current NODE_TOKEN_ENCRYPTION=required'}, files={APP + '/.zh-lite-migration': b'sqlite-preserved-v1\n', APP + '/.zh-lite-version': b'v3.11.0-zh.3\n', APP + '/.zh-lite-migration-env.json': b'{"environment":{"XUI_NODE_TOKEN_KEY":"stale"}}'})
        self.assertEqual(state['environment']['XUI_NODE_TOKEN_KEY'], 'current')
        self.assertEqual(state['environment']['NODE_TOKEN_ENCRYPTION'], 'required')
        self.assertTrue(all(call[0] == 'systemctl' for call in calls))

    def test_runtime_can_use_postgres_after_explicit_admin_conversion(self):
        state, _ = self.run_preflight(mode='runtime', props={'Environment': 'XUI_DB_TYPE=postgres XUI_DB_DSN=postgres://user:pass@host/db'}, files={APP + '/.zh-lite-migration': b'sqlite-preserved-v1\n'})
        self.assertEqual(state['database_kind'], 'postgres')

    def test_management_cli_environment_is_scoped_and_current(self):
        helper = (pathlib.Path(__file__).parent/'zh-helpers.sh').read_text(encoding='utf-8')
        cli = next(code for code in re.findall(r"python3 -I -c '([^']*)'", helper) if 'os.execve' in code)
        current = {'environment': {'XUI_DB_FOLDER': '/srv/panel-db', 'NODE_TOKEN_ENCRYPTION': 'required'}, 'service_environment': {'XUI_DB_FOLDER': '/srv/panel-db', 'NODE_TOKEN_ENCRYPTION': 'required', 'EXTRA_SERVICE_OPTION': 'preserved'}}
        caller = {'PATH': '/usr/bin:/bin', 'CALLER_EXTRA': 'keep', 'XUI_DB_FOLDER': '/wrong-db', 'XUI_NODE_TOKEN_KEY': 'stale-caller-secret', 'NODE_TOKEN_ENCRYPTION': 'off'}
        with patch.dict('os.environ', caller, clear=True), patch.object(sys, 'stdin', io.StringIO(json.dumps(current))), patch.object(sys, 'argv', ['-c', 'setting', '-show', 'true']), patch('os.execve') as execute:
            exec(compile(cli, 'migration-cli', 'exec'), {})
            import os
            self.assertEqual(dict(os.environ), caller)
        executable, argv, environment = execute.call_args.args
        self.assertEqual(executable, APP+'/x-ui')
        self.assertEqual(argv, ['x-ui', 'setting', '-show', 'true'])
        self.assertEqual(environment['XUI_DB_FOLDER'], '/srv/panel-db')
        self.assertEqual(environment['NODE_TOKEN_ENCRYPTION'], 'required')
        self.assertEqual(environment['CALLER_EXTRA'], 'keep')
        self.assertEqual(environment['EXTRA_SERVICE_OPTION'], 'preserved')
        self.assertNotIn('XUI_NODE_TOKEN_KEY', environment)

    def test_reentry_with_checked_marker(self):
        state, _ = self.run_preflight(files={APP + '/.zh-lite-migration': b'sqlite-preserved-v1\n', APP + '/.zh-lite-version': b'v3.11.0-zh.3\n'}, version='3.11.0-zh.3')
        self.assertEqual(state['database'], '/etc/x-ui')

    def test_running_environment_verified(self):
        with patch('os.path.realpath', side_effect=lambda path: APP + ('/x-ui' if path.endswith('/exe') else '')):
            state, _ = self.run_preflight(props={'ActiveState': 'active', 'SubState': 'running', 'MainPID': '12'}, files={'/proc/12/environ': b'XRAY_VMESS_AEAD_FORCED=false\0'})
        self.assertTrue(state['was_active'])

    def test_environment_file_is_data(self):
        state, calls = self.run_preflight(props={'Environment': '"XUI_TEST=$(touch /tmp/untrusted)"'}, files={'/etc/default/x-ui': b'XUI_DB_FOLDER=/etc/x-ui\n'})
        self.assertEqual(state['environment']['XUI_TEST'], '$(touch /tmp/untrusted)')
        self.assertTrue(all(command[0] in ('systemctl', 'setpriv') for command in calls))

    def test_go_env_fallback_and_dotenv_do_not_override_service(self):
        state, _ = self.run_preflight(props={'Environment': 'XUI_DB_FOLDER=/etc/x-ui', 'EnvironmentFiles': ''}, files={'/etc/default/x-ui': b'XUI_DB_FOLDER=/not-real\nNODE_TOKEN_ENCRYPTION=required\n', APP + '/.env': b'XUI_DB_FOLDER=/also-not-real\nXUI_NODE_TOKEN_KEY=literal-key\n'})
        self.assertEqual(state['database'], '/etc/x-ui')
        self.assertEqual(state['environment']['NODE_TOKEN_ENCRYPTION'], 'required')
        self.assertEqual(state['environment']['XUI_NODE_TOKEN_KEY'], 'literal-key')

    def test_go_fallback_postgres_without_systemd_env_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_preflight(props={'EnvironmentFiles': ''}, files={'/etc/conf.d/x-ui': b'XUI_DB_TYPE=postgres\nXUI_DB_DSN=postgres://user:pass@host/db\n'})

    def test_real_sqlite_wal_snapshot_retains_accounts_and_settings(self):
        integrity = SOURCE.split("<<'PY'\n")[3].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory(prefix='x-ui-migration-wal-') as temporary:
            original = pathlib.Path(temporary)/'original'
            original.mkdir()
            database = original/'x-ui.db'
            connection = sqlite3.connect(database)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA wal_autocheckpoint=0')
                connection.execute('CREATE TABLE users (username TEXT, password TEXT)')
                connection.execute('CREATE TABLE settings (key TEXT, value TEXT)')
                connection.execute('INSERT INTO users VALUES (?, ?)', ('existing-admin', 'original-hash'))
                connection.execute('INSERT INTO settings VALUES (?, ?)', ('webBasePath', '/existing-path/'))
                connection.commit()
                before = {p.name:p.read_bytes() for p in original.iterdir()}
                self.assertGreater(len(before['x-ui.db-wal']), 0)
                snapshot = pathlib.Path(temporary)/'snapshot'
                shutil.copytree(original, snapshot)
                with patch.object(sys, 'argv', ['-', str(snapshot/'x-ui.db')]):
                    exec(compile(integrity, 'migration-integrity', 'exec'), {})
                with contextlib.closing(sqlite3.connect(snapshot/'x-ui.db')) as copied:
                    self.assertEqual(copied.execute('SELECT * FROM users').fetchall(), [('existing-admin', 'original-hash')])
                    self.assertEqual(copied.execute('SELECT * FROM settings').fetchall(), [('webBasePath', '/existing-path/')])
                self.assertEqual(before, {p.name:p.read_bytes() for p in original.iterdir()})
            finally:
                connection.close()

    def test_292_database_contract_rejects_client_account_or_configuration_loss(self):
        upgrade = SOURCE.split("<<'PY'\n")[4].split('\nPY\n', 1)[0]
        accepted = {'none','numeric-strings','unset-timestamps','json-number-format'}
        for damage in ('none', 'numeric-strings', 'unset-timestamps', 'json-number-format', 'core-reject', 'password', 'client', 'limit', 'link', 'port', 'traffic', 'setting', 'routing', 'dns', 'outbound', 'json-boolean-type', 'created-at', 'updated-at', 'relative-cert-without-inbounds'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory(prefix='migration-292-') as temporary:
                stage = pathlib.Path(temporary)
                (stage/'backup/database').mkdir(parents=True)
                (stage/'check-database').mkdir()
                (stage/'backup/state.json').write_text(json.dumps({'environment': {}}))
                original = stage/'backup/database/x-ui.db'
                client = {'id':'client-uuid', 'email':'legacy@example.com', 'enable':True, 'subId':'old-sub-id', 'limitIp':2, 'totalGB':500, 'expiryTime':2000000000000, 'tgId':'123', 'flow':'xtls-rprx-vision', 'reset':7, 'created_at':1700000000123, 'updated_at':1700000000456}
                if damage=='numeric-strings': client.update(limitIp='+2',totalGB=' 5 00 ',expiryTime='2000000000000',tgId='1 2 3',reset=' +7 ',created_at='1700000000123',updated_at='1700000000456')
                elif damage=='unset-timestamps': client.update(created_at='0',updated_at=0)
                with contextlib.closing(sqlite3.connect(original)) as db:
                    db.executescript('CREATE TABLE users(id INTEGER,username TEXT,password TEXT); INSERT INTO users VALUES(1,"legacy-admin","$2a$original-hash"); CREATE TABLE history_of_seeders(id INTEGER,seeder_name TEXT); INSERT INTO history_of_seeders VALUES(1,"UserPasswordHash"); CREATE TABLE settings(id INTEGER,key TEXT,value TEXT); INSERT INTO settings VALUES(1,"webBasePath","/legacy-path/"); CREATE TABLE inbounds(id INTEGER,port INTEGER,protocol TEXT,settings TEXT,stream_settings TEXT,sniffing TEXT); CREATE TABLE client_traffics(id INTEGER,email TEXT,up INTEGER,down INTEGER); INSERT INTO client_traffics VALUES(1,"legacy@example.com",17,29);')
                    db.execute('INSERT INTO inbounds VALUES(?,?,?,?,?,?)',(7,12345,'vless',json.dumps({'clients':[client],'decryption':'none'}),'{}','{}'))
                    template = {'routing':{'rules':[{'domain':['example.org'],'outboundTag':'custom','type':'field'}]},'dns':{'servers':['1.1.1.1'],'disableCache':True,'serveStale':5},'outbounds':[{'tag':'custom','protocol':'freedom','settings':{}}]}
                    db.execute('INSERT INTO settings VALUES(?,?,?)',(2,'xrayTemplateConfig',json.dumps(template)))
                    if damage=='relative-cert-without-inbounds':
                        db.execute('DELETE FROM inbounds')
                        db.execute('INSERT INTO settings VALUES(3,"webCertFile","relative/cert.pem")')
                    db.commit()
                before = original.read_bytes()
                shutil.copy2(original, stage/'check-database/x-ui.db')
                def simulate_official_seeders(args, **kwargs):
                    if args[1:3]==['run','-test']:
                        self.assertEqual(args[3],'-c')
                        self.assertEqual(kwargs['env']['XRAY_LOCATION_ASSET'],'/usr/local/x-ui/bin')
                        validation=json.loads(pathlib.Path(args[4]).read_text())
                        self.assertEqual(validation['routing'],template['routing'])
                        return types.SimpleNamespace(returncode=23 if damage=='core-reject' else 0)
                    self.assertEqual(kwargs['env']['XUI_DB_FOLDER'],str(stage/'check-database'))
                    self.assertEqual(args[1:],['setting','-show','true'])
                    with contextlib.closing(sqlite3.connect(stage/'check-database/x-ui.db')) as migrated:
                        migrated.executescript('CREATE TABLE clients(id INTEGER,email TEXT,uuid TEXT,enable INTEGER,sub_id TEXT,limit_ip INTEGER,total_gb INTEGER,expiry_time INTEGER,tg_id INTEGER,reset INTEGER,created_at INTEGER,updated_at INTEGER); CREATE TABLE client_inbounds(client_id INTEGER,inbound_id INTEGER,flow_override TEXT); INSERT INTO clients VALUES(1,"legacy@example.com","client-uuid",1,"old-sub-id",2,500,2000000000000,123,7,1700000000123,1700000000456); INSERT INTO client_inbounds VALUES(1,7,"xtls-rprx-vision");')
                        mutations={'password':'UPDATE users SET password="changed"', 'client':'DELETE FROM clients', 'limit':'UPDATE clients SET limit_ip=0', 'link':'DELETE FROM client_inbounds', 'port':'UPDATE inbounds SET port=9', 'traffic':'UPDATE client_traffics SET up=0', 'setting':'DELETE FROM settings', 'created-at':'UPDATE clients SET created_at=1700000000000', 'updated-at':'UPDATE clients SET updated_at=1700000000000'}
                        if damage in mutations: migrated.execute(mutations[damage])
                        # Reordered/minified JSON remains semantically equal; real configuration loss fails.
                        changed = copy.deepcopy(template)
                        if damage=='routing': changed['routing']['rules']=[]
                        elif damage=='dns': changed['dns']['servers']=['8.8.8.8']
                        elif damage=='outbound': changed['outbounds'][0]['protocol']='blackhole'
                        elif damage=='json-boolean-type': changed['dns']['disableCache']=1
                        elif damage=='json-number-format': changed['dns']['serveStale']=5.0
                        migrated.execute('UPDATE settings SET value=? WHERE key="xrayTemplateConfig"',(json.dumps(changed,sort_keys=True,separators=(',',':')),))
                        migrated.commit()
                    return types.SimpleNamespace(returncode=0)
                with patch.object(sys,'argv',['-',str(stage),'/verified-package/x-ui']), patch('subprocess.run',simulate_official_seeders):
                    if damage in accepted: exec(compile(upgrade,'upgrade-contract','exec'),{})
                    else:
                        with self.assertRaises(SystemExit): exec(compile(upgrade,'upgrade-contract','exec'),{})
                self.assertEqual(original.read_bytes(),before)

    def test_official_template_changes_need_ordered_private_egress_proof(self):
        upgrade=SOURCE.split("<<'PY'\n")[4].split('\nPY\n',1)[0]
        nodes=[node for node in ast.parse(upgrade).body if isinstance(node,ast.FunctionDef) and node.name in ('json_semantics','template_semantics_preserved')]
        namespace={'copy':copy,'ipaddress':ipaddress,'math':math}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'template-proof','exec'),namespace)
        preserved=namespace['template_semantics_preserved']
        # Exact relevant official v2.9.2 defaults, with the systemd fixture's
        # explicit public test destination inserted before the private deny.
        before={'api':{'tag':'api','services':['HandlerService','LoggerService','StatsService']},'inbounds':[{'tag':'api','listen':'127.0.0.1','port':62789,'protocol':'tunnel','settings':{'address':'127.0.0.1'}}],
                'outbounds':[{'tag':'direct','protocol':'freedom','settings':{'domainStrategy':'AsIs','redirect':'','noises':[]}},{'tag':'blocked','protocol':'blackhole','settings':{}},{'tag':'ci-loopback','protocol':'freedom','settings':{'finalRules':[{'action':'allow','ip':['203.0.114.77/32']},{'action':'block'}]}}],
                'routing':{'domainStrategy':'AsIs','rules':[{'type':'field','inboundTag':['api'],'outboundTag':'api'},{'type':'field','ip':['203.0.114.77/32'],'outboundTag':'ci-loopback'},{'type':'field','ip':['geoip:private'],'outboundTag':'blocked'},{'type':'field','outboundTag':'blocked','protocol':['bittorrent']}]}}
        after=copy.deepcopy(before)
        after['outbounds'][0]['settings'].pop('domainStrategy')
        after['outbounds'][0]['settings']['finalRules']=[{'action':'block','ip':['geoip:private']},{'action':'allow'}]
        self.assertTrue(preserved(before,after,[{'protocol':'vless'}]))
        for damage in ('private-allow','domain-exception','missing-deny','filtered-deny','public-api','plaintext','ips-override','dns-change','route-order','custom-final-rules'):
            old,new=copy.deepcopy(before),copy.deepcopy(after)
            rows=[{'protocol':'vless'}]
            if damage=='private-allow':
                rule={'type':'field','ip':['10.0.0.0/8'],'outboundTag':'direct'}
                old['routing']['rules'].insert(1,rule);new['routing']['rules'].insert(1,rule)
            elif damage=='domain-exception':
                rule={'type':'field','domain':['internal.example.com'],'outboundTag':'direct'}
                old['routing']['rules'].insert(1,rule);new['routing']['rules'].insert(1,rule)
            elif damage=='missing-deny':
                del old['routing']['rules'][2];del new['routing']['rules'][2]
            elif damage=='filtered-deny':
                old['routing']['rules'][2]['port']='443';new['routing']['rules'][2]['port']='443'
            elif damage=='public-api':
                old['inbounds'][0]['listen']='0.0.0.0';new['inbounds'][0]['listen']='0.0.0.0'
            elif damage=='plaintext': rows=[{'protocol':'http'}]
            elif damage=='ips-override': old['outbounds'][0]['settings']['ipsBlocked']=[]
            elif damage=='dns-change': new['dns']={'servers':['8.8.8.8']}
            elif damage=='route-order': new['routing']['rules'].reverse()
            elif damage=='custom-final-rules': old['outbounds'][0]['settings']['finalRules']=[{'action':'allow','ip':['10.0.0.0/8']}]
            with self.subTest(damage=damage): self.assertFalse(preserved(old,new,rows))

    def test_rejects_unsupported_or_ambiguous_layouts(self):
        examples = [
            {'version': '3.10.9'},
            {'props': {'User': 'unrelated-account'}},
            {'props': {'Environment': 'XUI_DB_TYPE=postgres XUI_DB_DSN=postgres://user:pass@host/db'}},
            {'props': {'Environment': 'XUI_DB_TYPE=unknown'}},
            {'props': {'Environment': 'XUI_DB_FOLDER=/etc'}},
            {'props': {'Environment': 'XUI_DB_FOLDER=/usr/local/x-ui'}},
            {'props': {'WorkingDirectory': '/opt/x-ui'}},
            {'props': {'ExecStartPre': '{ path=/bin/sh ; argv[]=/bin/sh hook ; }'}},
            {'props': {'RootDirectory': '/sandbox'}},
            {'props': {'EnvironmentFiles': '/etc/path with spaces (ignore_errors=no)'}},
            {'props': {'ActiveState': 'activating'}},
            {'files': {'/etc/x-ui/x-ui.db': b'wrong-database'}},
            {'modes': {'/etc/x-ui/x-ui.db': (stat.S_IFLNK | 0o777, 0, 1)}},
            {'modes': {'/etc/x-ui/x-ui.db': (stat.S_IFREG | 0o600, 0, 2)}},
            {'modes': {APP: (stat.S_IFDIR | 0o777, 0, 1)}},
            {'modes': {APP + '/x-ui': (stat.S_IFREG | 0o755, 0, 2)}},
            {'files': {'/etc/default/x-ui': b'XUI_DB_FOLDER="unterminated\n'}},
            {'files': {APP + '/.zh-lite-version': b'v3.11.0-zh.3\n'}},
        ]
        for example in examples:
            with self.subTest(example=example), self.assertRaises(SystemExit):
                self.run_preflight(**example)


if __name__ == '__main__':
    unittest.main()
