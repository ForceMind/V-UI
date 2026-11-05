"""Manual, gated promotion of exact-head accepted artifacts; never rebuild during publication."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO='ForceMind/V-UI'
TARGETS=('x86_64-gnu','aarch64-gnu','x86_64-musl','aarch64-musl')
REQUIRED={'test.yml','toclash.yml','loopback.yml','release.yml','acme.yml',
          'oneclick.yml','portable.yml','docs.yml'}
REQUIRED_ACCEPTANCE_STEPS = {
    'test.yml': {'Browser authentication smoke', 'Browser routing and subscription workflow',
                 'Browser proxy import, drafts and runtime UI lifecycle',
                 'Browser per-inbound direct activation through actual API and core',
                 'Browser two-instance TCP upstream export and import',
                 'Browser managed-certificate editor transitions and restore'},
    'release.yml': {'Verify non-root install HTTPS browser and recovery'},
    'acme.yml': {'Test graphical certificate lifecycle against Pebble'},
}
INSTALLER_ONLY_BASELINE = '586d8a2dbd0be6d7ba8d5c5823df8fe9a1f73c95'
INSTALLER_ONLY_VERSION = '0.4.11'
INSTALLER_ONLY_FILES = {
    'VERSION', 'main.py', 'setup.sh', 'install.sh',
    'scripts/install_system.py', 'scripts/install_ops_control.py', 'scripts/publish_release.py',
    'scripts/installer_certificate_state.py', 'scripts/build_bundle.py',
    'README.md', 'CHANGELOG.md', 'AGENTS.md', 'ROADMAP.md', 'CONTRIBUTING.md',
    '.github/workflows/publish.yml', '.github/workflows/short-install.yml',
    '.github/workflows/oneclick.yml',
    '.github/workflows/online-release-acceptance.yml',
}
CA_TRUST_BASELINE = '4f24e4ce12a5dbe4fbd2da745a936734f82121f6'
CA_TRUST_VERSION = '0.4.12'
CA_TRUST_FILES = {
    'VERSION', 'main.py', 'install.sh', 'setup.sh', 'app/certificates/material.py',
    'scripts/publish_release.py', '.github/workflows/publish.yml',
    '.github/workflows/short-install.yml',
    'README.md', 'CHANGELOG.md', 'AGENTS.md',
    'tests/test_certificate_system_trust.py', 'tests/installed_system_ca_helpers.py',
    'tests/test_acme_real.py', 'tests/test_oneclick_system.py',
    'tests/test_release_promotion.py', 'tests/test_setup_shell.py',
    'tests/test_short_install_contract.py',
}
INSTALL_INTERACTION_BASELINE = '4fc66de1dbb9d9b9a9edec6845609fd32e99e9cc'
INSTALL_INTERACTION_VERSION = '0.4.13'
INSTALL_INTERACTION_FILES = {
    'VERSION', 'main.py', 'install.sh', 'setup.sh', 'app/admin.py',
    'scripts/install_system.py', 'scripts/firewall_support.py', 'scripts/deploy.py',
    'scripts/publish_release.py', '.github/workflows/publish.yml',
    '.github/workflows/short-install.yml',
    'README.md', 'CHANGELOG.md', 'AGENTS.md',
    'tests/test_installer.py', 'tests/test_installer_interactions.py',
    'tests/test_installer_localization.py', 'tests/test_existing_install.py',
    'tests/test_hysteria2_installation.py', 'tests/test_tuic_installation.py',
    'tests/test_linux_installation_regressions.py',
    'tests/test_firewall_support.py', 'tests/test_admin_bootstrap.py',
    'tests/test_oneclick_system.py', 'tests/online_install_helpers.py',
    'tests/test_oneclick_readiness.py', 'tests/test_release_promotion.py',
    'tests/test_setup_shell.py', 'tests/test_short_install_contract.py',
}
STARTUP_BASELINE = 'f81ce5e04aadee8f8e8d53d8b1f4cfc53e1b0f3f'
STARTUP_VERSION = '0.4.14'
STARTUP_FILES = {
    'VERSION', 'main.py', 'install.sh', 'setup.sh',
    'scripts/install_system.py', 'scripts/service_support.py', 'scripts/publish_release.py',
    '.github/workflows/publish.yml', '.github/workflows/short-install.yml',
    'README.md', 'CHANGELOG.md', 'AGENTS.md',
    'tests/test_installer_startup.py', 'tests/test_existing_install.py',
    'tests/test_installer_interactions.py', 'tests/test_oneclick_system.py',
    'tests/test_oneclick_readiness.py', 'tests/test_service_support.py',
    'tests/test_release_promotion.py', 'tests/test_setup_shell.py',
    'tests/test_short_install_contract.py',
}
UI_REDESIGN_BASELINE = '7726c44d16fd88a8dfc7a4ffd404de88942da4ad'
UI_REDESIGN_VERSION = '0.4.15'
# The reviewed UI/firewall implementation is frozen at this candidate. Only
# the listed release metadata, gate and published-upgrade acceptance may follow.
UI_REDESIGN_FILES = {
    'VERSION', 'main.py', 'scripts/publish_release.py',
    '.github/workflows/publish.yml', '.github/workflows/oneclick.yml',
    'tests/test_release_promotion.py', 'tests/test_oneclick_system.py',
    'tests/test_short_install_contract.py',
    'tests/published_upgrade_helpers.py', 'tests/test_published_upgrade_helpers.py',
    'AGENTS.md', 'install.sh', 'docs/README.md',
    'docs/RELEASE_NOTES.md', 'docs/INSTALLATION.md', 'docs/OPERATIONS.md',
    'README.md', 'CHANGELOG.md', 'docs/RELEASING.md',
    'docs/UI_REDESIGN_PROGRESS.md', 'docs/DEVELOPMENT_HANDOFF.md',
}
UI_REDESIGN_UPGRADE_STEP = 'Test published v0.4.14 to exact candidate upgrade on ephemeral systemd runner'
TLS_CERTIFICATE_BASELINE = '26e7bdd7f258f6ddf025291d5b6d86b71efebbe5'
TLS_CERTIFICATE_VERSION = '0.4.16'
# The reviewed TLS binding/API/UI/readiness implementation is frozen. Only this
# explicit release-preparation and published-upgrade scope may follow it.
TLS_CERTIFICATE_FILES = {
    'VERSION', 'main.py', 'install.sh', 'scripts/publish_release.py',
    '.github/workflows/publish.yml', '.github/workflows/oneclick.yml',
    'tests/test_release_promotion.py', 'tests/test_oneclick_system.py',
    'tests/test_short_install_contract.py', 'tests/published_upgrade_helpers.py',
    'tests/test_published_upgrade_helpers.py',
    'README.md', 'CHANGELOG.md', 'AGENTS.md', 'docs/RELEASING.md',
    'docs/CERTIFICATES.md', 'docs/README.md', 'docs/INSTALLATION.md',
    'docs/RELEASE_NOTES.md', 'docs/DEVELOPMENT_HANDOFF.md',
    'docs/TLS_CERTIFICATE_BINDING_FIX.md',
}
TLS_CERTIFICATE_UPGRADE_STEP = 'Test published v0.4.15 to exact candidate upgrade on ephemeral systemd runner'
TLS_CERTIFICATE_CORE_STEP = 'Validate with pinned real cores'
REMOTE_MAINTENANCE_BASELINE = '629d44c7e0acf2f9aebdfdac765522cc93f9af22'
REMOTE_MAINTENANCE_VERSION = '0.4.17'
# The approved remote-maintenance product is frozen. Following changes are
# limited to release metadata/gates, real published upgrades, documentation and
# the post-publication bootstrap pointer; application/root/runtime logic stays frozen.
REMOTE_MAINTENANCE_FILES = {
    'VERSION', 'main.py', 'install.sh', 'scripts/publish_release.py',
    '.github/workflows/publish.yml', '.github/workflows/oneclick.yml',
    '.github/workflows/short-install.yml', 'setup.sh',
    'tests/test_release_promotion.py',
    'tests/test_short_install_contract.py', 'tests/test_setup_shell.py',
    'tests/published_upgrade_helpers.py', 'tests/test_published_upgrade_helpers.py',
    'README.md', 'CHANGELOG.md', 'AGENTS.md', 'docs/RELEASING.md',
    'docs/README.md', 'docs/INSTALLATION.md', 'docs/OPERATIONS.md',
    'docs/RELEASE_NOTES.md', 'docs/DEVELOPMENT_HANDOFF.md',
    'docs/MANAGED_OPERATIONS.md', 'docs/REMOTE_MAINTENANCE_PROGRESS.md',
}
REMOTE_MAINTENANCE_UPGRADE_STEP = 'Test published v0.4.16 to exact candidate upgrade on ephemeral systemd runner'
REMOTE_MAINTENANCE_LOOPBACK_STEPS = {
    'Fetch checksum-pinned real binaries',
    'Exercise actual TLS proxy and local DNS paths',
    'Exercise dual-switch policy and real double-hop transitions',
    'Exercise two-instance TCP upstream export import and real relay',
    'Exercise standard-client per-inbound TCP routes and independent host switch',
}
SIMPLE_NODE_BASELINE = '85bc22d92696bb51a5eb71c1827838d0e0a05a54'
SIMPLE_NODE_VERSION = '0.4.18'
# The approved simple-node product is frozen. Following changes are
# limited to release metadata/gates, real published upgrades, documentation and
# the post-publication bootstrap pointer; application/root/runtime logic stays frozen.
SIMPLE_NODE_FILES = {
    'VERSION', 'main.py', 'install.sh', 'scripts/publish_release.py',
    '.github/workflows/publish.yml', '.github/workflows/oneclick.yml',
    '.github/workflows/short-install.yml', 'setup.sh',
    'tests/test_release_promotion.py',
    'tests/test_short_install_contract.py', 'tests/test_setup_shell.py',
    'tests/published_upgrade_helpers.py', 'tests/test_published_upgrade_helpers.py',
    'README.md', 'CHANGELOG.md', 'AGENTS.md', 'docs/RELEASING.md',
    'docs/README.md', 'docs/INSTALLATION.md', 'docs/OPERATIONS.md',
    'docs/RELEASE_NOTES.md', 'docs/DEVELOPMENT_HANDOFF.md',
    'docs/MANAGED_OPERATIONS.md', 'docs/SIMPLE_NODE_WIZARD_PROGRESS.md',
}
SIMPLE_NODE_CI_FIX_HASHES = {
    'scripts/system_maintenance_smoke.py': '8b0f8d030ee0d5dd8f825bb27407d4650464b9bfe0f7c26a9e2f12d2ce486ec8',
    'tests/test_system_maintenance.py': '6fc9d4d59a1c69367a73f4a96ac25ebb44da9688b159b7920cf4263457daf6bc',
}
SIMPLE_NODE_FILES |= set(SIMPLE_NODE_CI_FIX_HASHES)
SIMPLE_NODE_UPGRADE_STEP = 'Test published v0.4.17 to exact candidate upgrade on ephemeral systemd runner'
SIMPLE_NODE_LOOPBACK_STEPS = {
    'Fetch checksum-pinned real binaries',
    'Exercise actual TLS proxy and local DNS paths',
    'Exercise dual-switch policy and real double-hop transitions',
    'Exercise two-instance TCP upstream export import and real relay',
    'Exercise standard-client per-inbound TCP routes and independent host switch',
}
SIMPLE_NODE_TEMPLATE_STEP = 'Compile shipped node templates in Node VM'
BROWSER_SKIP_STEPS = {
    name: steps | {'Install explicit test browser'} for name, steps in REQUIRED_ACCEPTANCE_STEPS.items()
}
BROWSER_SKIP_STEPS['test.yml'] = REQUIRED_ACCEPTANCE_STEPS['test.yml'] | {
    'Install test browser', 'Prepare checksum-pinned browser frontend'}
COMMON_ASSETS={'install.sh','install_system.py','platform_support.py','firewall_support.py',
               'service_support.py','install_ops_control.py','system_launcher.py',
               'system_ops.py','ops_worker.py','release_tools.py','v-ui',
               'vui-source.zip','RELEASE.json','RELEASE_NOTES.md'}
BUNDLE_ASSETS={'vui-linux-'+target+'.zip' for target in TARGETS}
ASSETS=COMMON_ASSETS|BUNDLE_ASSETS|{'SHA256SUMS'}
KIT_ASSETS=COMMON_ASSETS|{'vui-linux-x86_64-gnu.zip','SHA256SUMS'}


class ReleaseGateError(RuntimeError):
    pass


def installation_command(files, version, *, installer_args=(), interactive=True):
    """One physical line: root-private bootstrap and independently pinned kit.

    Extra arguments are for existing installer options, such as the temporary
    provided-certificate fixture used by the published Linux acceptance gate.
    No credential is generated or passed by this formatter.
    """
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ReleaseGateError('Stable installation version required')
    if '--dry-run' in installer_args:
        raise ReleaseGateError('The managed installation command cannot be a dry run')
    script_sha = hashlib.sha256(files['install.sh']).hexdigest()
    kit_sha = hashlib.sha256(files['SHA256SUMS']).hexdigest()
    url = 'https://github.com/' + REPO + '/releases/download/v' + version + '/install.sh'
    prerequisites = (
        'if ! command -v curl >/dev/null 2>&1 || ! command -v bash >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then '
        'if command -v apt-get >/dev/null 2>&1; then apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl bash coreutils; '
        'elif command -v apk >/dev/null 2>&1; then apk add --no-cache ca-certificates curl bash coreutils; '
        'elif command -v dnf >/dev/null 2>&1; then dnf install -y ca-certificates curl bash coreutils; '
        'elif command -v yum >/dev/null 2>&1; then yum install -y ca-certificates curl bash coreutils; '
        'elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive install ca-certificates curl bash coreutils; '
        'elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm --needed ca-certificates curl bash coreutils; '
        'elif command -v xbps-install >/dev/null 2>&1; then xbps-install -Sy ca-certificates curl bash coreutils; '
        'else printf "%s\\n" "Install curl, Bash and sha256sum, then rerun this command." >&2; exit 1; fi; fi'
    )
    options = ['--version', 'v' + version, '--with-global-cli', '--kit-sha256', kit_sha]
    if interactive:
        options.append('--interactive')
    arguments = shlex.join([*options, *installer_args])
    script = '; '.join([
        'set -eu', 'umask 077', prerequisites,
        'setup_dir=$(mktemp -d /root/vui-bootstrap.XXXXXXXX)',
        'curl --proto =https --proto-redir =https --tlsv1.2 --fail --location --retry 2 --connect-timeout 15 --max-time 300 '
        + shlex.quote(url) + ' -o "$setup_dir/install.sh"',
        'printf "%s  %s\\n" ' + script_sha + ' "$setup_dir/install.sh" | sha256sum -c -',
        'exec /bin/bash "$setup_dir/install.sh" ' + arguments,
    ])
    dispatch = ('PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH; '
                'if [ "$(id -u)" -eq 0 ]; then exec "$@"; else exec sudo -- "$@"; fi')
    return shlex.join(['/bin/sh', '-c', dispatch, 'v-ui-setup', '/usr/bin/env', '-i',
                       'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'HOME=/root', 'LANG=C.UTF-8',
                       'PYTHONDONTWRITEBYTECODE=1', '/bin/sh', '-c', script])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(path,method='GET',data=None,binary=False):
    url=path if path.startswith('https://uploads.github.com/') else \
        'https://api.github.com/repos/'+REPO+('/'+path if path else '')
    body=data if binary else None if data is None else json.dumps(data).encode()
    headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'User-Agent':'V-UI-release-gate',
             'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'}
    if body is not None:
        headers['Content-Type']='application/octet-stream' if binary else 'application/json'
    req=urllib.request.Request(url,method=method,data=body,headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=120) as response:
        return json.load(response)


def select_runs(runs,commit):
    latest={}
    for run in runs:
        if run.get('head_sha')!=commit:
            continue
        path=run.get('path','').split('@')[0].rsplit('/',1)[-1]
        if path in REQUIRED and (path not in latest or run['id']>latest[path]['id']):
            latest[path]=run
    for path in sorted(REQUIRED):
        run=latest.get(path)
        if not run or run.get('status')!='completed' or run.get('conclusion')!='success':
            raise ReleaseGateError('Latest exact-commit check is not successful: '+path)
    return latest


def validate_installer_only_scope(commit, version):
    """One explicitly approved patch; no web/app/dependency/core changes allowed."""
    if version != INSTALLER_ONLY_VERSION:
        raise ReleaseGateError('Non-browser installer exception is only approved for 0.4.11')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',INSTALLER_ONLY_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(['git','diff','--name-status',INSTALLER_ONLY_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Installer-only scope refuses deletion, rename or type change')
            name=fields[1]
            allowed=(name in INSTALLER_ONLY_FILES or
                     (name.startswith('tests/') and name.endswith('.py')) or
                     (name.startswith('docs/') and name.endswith('.md')))
            if not allowed:
                raise ReleaseGateError('Non-installer change requires full acceptance: '+name)
            mode=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True).split()[0]
            if mode not in ('100644','100755'):
                raise ReleaseGateError('Installer-only exception requires regular source files')
        previous=subprocess.check_output(['git','show',INSTALLER_ONLY_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.10'") != 1 or current != previous.replace(b"version='0.4.10'",b"version='0.4.11'",1):
            raise ReleaseGateError('Application changes exceed the installer-only version bump')
        if subprocess.check_output(['git','show',commit+':VERSION']).strip()!=b'0.4.11':
            raise ReleaseGateError('Installer-only version identity differs')
        # Packaging may add this one installer helper, but must not change
        # application/dependency/core selection or rewrite any payload bytes.
        old_builder=subprocess.check_output(['git','show',INSTALLER_ONLY_BASELINE+':scripts/build_bundle.py'])
        new_builder=subprocess.check_output(['git','show',commit+':scripts/build_bundle.py'])
        old_line=b"'scripts/deploy.py','scripts/install_system.py'}"
        new_line=b"'scripts/deploy.py','scripts/install_system.py',\n            'scripts/installer_certificate_state.py'}"
        if old_builder.count(old_line)!=1 or new_builder!=old_builder.replace(old_line,new_line,1):
            raise ReleaseGateError('Builder changes exceed the single installer helper addition')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove installer-only source ancestry/scope') from exc


def validate_ca_trust_scope(commit, version):
    """One approved CA-source fix; never a reusable non-browser release switch."""
    if version != CA_TRUST_VERSION:
        raise ReleaseGateError('Non-browser CA exception is only approved for 0.4.12')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',CA_TRUST_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(['git','diff','--name-status',CA_TRUST_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('CA trust scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in CA_TRUST_FILES and not (name.startswith('docs/') and name.endswith('.md')):
                raise ReleaseGateError('Changes exceed the approved CA trust fix: '+name)
            mode=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True).split()[0]
            if mode not in ('100644','100755'):
                raise ReleaseGateError('CA trust exception requires regular source files')
        previous=subprocess.check_output(['git','show',CA_TRUST_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.11'")!=1 or current!=previous.replace(b"version='0.4.11'",b"version='0.4.12'",1):
            raise ReleaseGateError('Application changes exceed the CA fix version bump')
        if subprocess.check_output(['git','show',commit+':VERSION']).strip()!=b'0.4.12':
            raise ReleaseGateError('CA trust version identity differs')
        old=subprocess.check_output(['git','show',CA_TRUST_BASELINE+':install.sh'])
        new=subprocess.check_output(['git','show',commit+':install.sh'])
        if old.count(b'--version v0.4.11')!=1 or new!=old.replace(b'--version v0.4.11',b'--version v0.4.12',1):
            raise ReleaseGateError('Installer changes exceed the CA fix version documentation')
        old=subprocess.check_output(['git','show',CA_TRUST_BASELINE+':scripts/build_bundle.py'])
        new=subprocess.check_output(['git','show',commit+':scripts/build_bundle.py'])
        if old!=new:
            raise ReleaseGateError('CA trust fix must not change builder/dependencies/core selection')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove CA trust source ancestry/scope') from exc


def validate_install_interaction_scope(commit, version):
    """Version-bound automatic credentials and firewall UX patch only."""
    if version != INSTALL_INTERACTION_VERSION:
        raise ReleaseGateError('Non-browser install interaction exception is only approved for 0.4.13')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',INSTALL_INTERACTION_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(['git','diff','--name-status',INSTALL_INTERACTION_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Install interaction scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in INSTALL_INTERACTION_FILES and not (name.startswith('docs/') and name.endswith('.md')):
                raise ReleaseGateError('Changes exceed the approved install interaction patch: '+name)
            mode=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True).split()[0]
            if mode not in ('100644','100755'):
                raise ReleaseGateError('Install interaction exception requires regular source files')
        previous=subprocess.check_output(['git','show',INSTALL_INTERACTION_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.12'")!=1 or current!=previous.replace(b"version='0.4.12'",b"version='0.4.13'",1):
            raise ReleaseGateError('Application changes exceed the install interaction version bump')
        if subprocess.check_output(['git','show',commit+':VERSION']).strip()!=b'0.4.13':
            raise ReleaseGateError('Install interaction version identity differs')
        old=subprocess.check_output(['git','show',INSTALL_INTERACTION_BASELINE+':install.sh'])
        new=subprocess.check_output(['git','show',commit+':install.sh'])
        if old.count(b'--version v0.4.12')!=1 or new!=old.replace(b'--version v0.4.12',b'--version v0.4.13',1):
            raise ReleaseGateError('Shell installer changes exceed the version documentation')
        old=subprocess.check_output(['git','show',INSTALL_INTERACTION_BASELINE+':scripts/build_bundle.py'])
        if subprocess.check_output(['git','show',commit+':scripts/build_bundle.py'])!=old:
            raise ReleaseGateError('Install interaction patch must not change builder/dependencies/core selection')
        old=subprocess.check_output(['git','show',INSTALL_INTERACTION_BASELINE+':scripts/deploy.py'])
        new=subprocess.check_output(['git','show',commit+':scripts/deploy.py'])
        replacements=(('Activated verified release; start explicitly after checking configuration',
                       '已激活通过校验的版本；核对配置后再启动'), ('Backup SHA-256: ', '备份 SHA-256：'))
        for previous, current in replacements:
            if old.count(previous.encode())!=1:
                raise ReleaseGateError('Controller message baseline differs')
            old=old.replace(previous.encode(),current.encode(),1)
        if new!=old:
            raise ReleaseGateError('Controller changes exceed the two translated messages')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove install interaction source ancestry/scope') from exc


def validate_startup_scope(commit, version):
    """One approved bounded-startup fix; never a reusable non-browser release switch."""
    if version != STARTUP_VERSION:
        raise ReleaseGateError('Non-browser startup exception is only approved for 0.4.14')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',STARTUP_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(['git','diff','--name-status',STARTUP_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Startup readiness scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in STARTUP_FILES and not (name.startswith('docs/') and name.endswith('.md')):
                raise ReleaseGateError('Changes exceed the approved Startup readiness fix: '+name)
            mode=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True).split()[0]
            if mode not in ('100644','100755'):
                raise ReleaseGateError('Startup readiness exception requires regular source files')
        previous=subprocess.check_output(['git','show',STARTUP_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.13'")!=1 or current!=previous.replace(b"version='0.4.13'",b"version='0.4.14'",1):
            raise ReleaseGateError('Application changes exceed the startup fix version bump')
        if subprocess.check_output(['git','show',commit+':VERSION']).strip()!=b'0.4.14':
            raise ReleaseGateError('Startup readiness version identity differs')
        old=subprocess.check_output(['git','show',STARTUP_BASELINE+':install.sh'])
        new=subprocess.check_output(['git','show',commit+':install.sh'])
        if old.count(b'--version v0.4.13')!=1 or new!=old.replace(b'--version v0.4.13',b'--version v0.4.14',1):
            raise ReleaseGateError('Installer changes exceed the startup fix version documentation')
        old=subprocess.check_output(['git','show',STARTUP_BASELINE+':scripts/build_bundle.py'])
        new=subprocess.check_output(['git','show',commit+':scripts/build_bundle.py'])
        if old!=new:
            raise ReleaseGateError('Startup readiness fix must not change builder/dependencies/core selection')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove Startup readiness source ancestry/scope') from exc


def validate_ui_redesign_scope(commit, version):
    """Only the approved 0.4.15 UI candidate plus bounded release preparation."""
    if version != UI_REDESIGN_VERSION:
        raise ReleaseGateError('Non-browser UI exception is only approved for 0.4.15')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',UI_REDESIGN_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(
            ['git','diff','--name-status','--no-renames',UI_REDESIGN_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('UI release scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in UI_REDESIGN_FILES:
                raise ReleaseGateError('Changes exceed the approved UI release preparation: '+name)
            # Preserve the executable installer and non-executable source modes;
            # reject links, gitlinks and mode changes even without a content edit.
            mode = '100755' if name == 'install.sh' else '100644'
            entry=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True)
            if not re.fullmatch(mode+r' blob [a-f0-9]{40}\t'+re.escape(name)+r'\n',entry):
                raise ReleaseGateError('UI release exception refuses nonregular files or mode changes')
        previous=subprocess.check_output(['git','show',UI_REDESIGN_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.14'")!=1 or current!=previous.replace(b"version='0.4.14'",b"version='0.4.15'",1):
            raise ReleaseGateError('Application changes exceed the UI release version bump')
        if subprocess.check_output(['git','show',commit+':VERSION'])!=b'0.4.15\n':
            raise ReleaseGateError('UI release version identity differs')
        previous=subprocess.check_output(['git','show',UI_REDESIGN_BASELINE+':install.sh'])
        current=subprocess.check_output(['git','show',commit+':install.sh'])
        if previous.count(b'--version v0.4.14')!=1 or current!=previous.replace(b'--version v0.4.14',b'--version v0.4.15',1):
            raise ReleaseGateError('Installer changes exceed the UI release version documentation')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove UI release source ancestry/scope') from exc


def validate_tls_certificate_scope(commit, version):
    """Only the approved 0.4.16 TLS certificate candidate plus bounded release preparation."""
    if version != TLS_CERTIFICATE_VERSION:
        raise ReleaseGateError('Non-browser TLS certificate exception is only approved for 0.4.16')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',TLS_CERTIFICATE_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(
            ['git','diff','--name-status','--no-renames',TLS_CERTIFICATE_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('TLS certificate release scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in TLS_CERTIFICATE_FILES:
                raise ReleaseGateError('Changes exceed the approved TLS certificate release preparation: '+name)
            # Preserve the executable installer and non-executable source modes;
            # reject links, gitlinks and mode changes even without a content edit.
            mode = '100755' if name == 'install.sh' else '100644'
            entry=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True)
            if not re.fullmatch(mode+r' blob [a-f0-9]{40}\t'+re.escape(name)+r'\n',entry):
                raise ReleaseGateError('TLS certificate release exception refuses nonregular files or mode changes')
        previous=subprocess.check_output(['git','show',TLS_CERTIFICATE_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.15'")!=1 or current!=previous.replace(b"version='0.4.15'",b"version='0.4.16'",1):
            raise ReleaseGateError('Application changes exceed the TLS certificate release version bump')
        if subprocess.check_output(['git','show',commit+':VERSION'])!=b'0.4.16\n':
            raise ReleaseGateError('TLS certificate release version identity differs')
        previous=subprocess.check_output(['git','show',TLS_CERTIFICATE_BASELINE+':install.sh'])
        current=subprocess.check_output(['git','show',commit+':install.sh'])
        if previous.count(b'--version v0.4.15')!=1 or current!=previous.replace(b'--version v0.4.15',b'--version v0.4.16',1):
            raise ReleaseGateError('Installer changes exceed the TLS certificate release version documentation')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove TLS certificate release source ancestry/scope') from exc


def validate_remote_maintenance_scope(commit, version):
    """Only the approved 0.4.17 remote-maintenance candidate plus bounded release preparation."""
    if version != REMOTE_MAINTENANCE_VERSION:
        raise ReleaseGateError('Non-browser remote maintenance exception is only approved for 0.4.17')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',REMOTE_MAINTENANCE_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(
            ['git','diff','--name-status','--no-renames',REMOTE_MAINTENANCE_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Remote maintenance release scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in REMOTE_MAINTENANCE_FILES:
                raise ReleaseGateError('Changes exceed the approved remote maintenance release preparation: '+name)
            # Preserve the executable installer and non-executable source modes;
            # reject links, gitlinks and mode changes even without a content edit.
            mode = '100755' if name in ('install.sh', 'setup.sh') else '100644'
            entry=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True)
            if not re.fullmatch(mode+r' blob [a-f0-9]{40}\t'+re.escape(name)+r'\n',entry):
                raise ReleaseGateError('Remote maintenance release exception refuses nonregular files or mode changes')
        previous=subprocess.check_output(['git','show',REMOTE_MAINTENANCE_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.16'")!=1 or current!=previous.replace(b"version='0.4.16'",b"version='0.4.17'",1):
            raise ReleaseGateError('Application changes exceed the remote maintenance release version bump')
        if subprocess.check_output(['git','show',commit+':VERSION'])!=b'0.4.17\n':
            raise ReleaseGateError('Remote maintenance release version identity differs')
        previous=subprocess.check_output(['git','show',REMOTE_MAINTENANCE_BASELINE+':install.sh'])
        current=subprocess.check_output(['git','show',commit+':install.sh'])
        if previous.count(b'--version v0.4.16')!=1 or current!=previous.replace(b'--version v0.4.16',b'--version v0.4.17',1):
            raise ReleaseGateError('Installer changes exceed the remote maintenance release version documentation')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove Remote maintenance release source ancestry/scope') from exc


def validate_simple_node_scope(commit, version):
    """Only the approved 0.4.18 simple-node candidate plus bounded release preparation."""
    if version != SIMPLE_NODE_VERSION:
        raise ReleaseGateError('Non-browser simple node exception is only approved for 0.4.18')
    try:
        subprocess.run(['git','merge-base','--is-ancestor',SIMPLE_NODE_BASELINE,commit],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        changes=subprocess.check_output(
            ['git','diff','--name-status','--no-renames',SIMPLE_NODE_BASELINE,commit],text=True)
        for line in changes.splitlines():
            fields=line.split('\t')
            if len(fields)!=2 or fields[0] not in ('A','M'):
                raise ReleaseGateError('Simple node release scope refuses deletion, rename or type change')
            name=fields[1]
            if name not in SIMPLE_NODE_FILES:
                raise ReleaseGateError('Changes exceed the approved simple node release preparation: '+name)
            # Preserve the executable installer and non-executable source modes;
            # reject links, gitlinks and mode changes even without a content edit.
            mode = '100755' if name in ('install.sh', 'setup.sh') else '100644'
            entry=subprocess.check_output(['git','ls-tree',commit,'--',name],text=True)
            if not re.fullmatch(mode+r' blob [a-f0-9]{40}\t'+re.escape(name)+r'\n',entry):
                raise ReleaseGateError('Simple node release exception refuses nonregular files or mode changes')
            if name in SIMPLE_NODE_CI_FIX_HASHES:
                content = subprocess.check_output(['git','show',commit+':'+name])
                if hashlib.sha256(content).hexdigest() != SIMPLE_NODE_CI_FIX_HASHES[name]:
                    raise ReleaseGateError('Simple node CI synchronization fix bytes differ from the reviewed fixture')
        previous=subprocess.check_output(['git','show',SIMPLE_NODE_BASELINE+':main.py'])
        current=subprocess.check_output(['git','show',commit+':main.py'])
        if previous.count(b"version='0.4.17'")!=1 or current!=previous.replace(b"version='0.4.17'",b"version='0.4.18'",1):
            raise ReleaseGateError('Application changes exceed the simple node release version bump')
        if subprocess.check_output(['git','show',commit+':VERSION'])!=b'0.4.18\n':
            raise ReleaseGateError('Simple node release version identity differs')
        previous=subprocess.check_output(['git','show',SIMPLE_NODE_BASELINE+':install.sh'])
        current=subprocess.check_output(['git','show',commit+':install.sh'])
        if previous.count(b'--version v0.4.17')!=1 or current!=previous.replace(b'--version v0.4.17',b'--version v0.4.18',1):
            raise ReleaseGateError('Installer changes exceed the simple node release version documentation')
    except subprocess.CalledProcessError as exc:
        raise ReleaseGateError('Cannot prove Simple node release source ancestry/scope') from exc


def validate_run_jobs(checks, commit, *, installer_only_no_browser=False, ca_trust_no_browser=False, install_interaction_no_browser=False, startup_no_browser=False, ui_redesign_no_browser=False, tls_certificate_no_browser=False, remote_maintenance_no_browser=False, simple_node_no_browser=False):
    """A green workflow with skipped acceptance is not release qualification."""
    if sum((installer_only_no_browser, ca_trust_no_browser, install_interaction_no_browser,
            startup_no_browser, ui_redesign_no_browser, tls_certificate_no_browser, remote_maintenance_no_browser, simple_node_no_browser)) > 1:
        raise ReleaseGateError('Non-browser scope exceptions are mutually exclusive')
    for path, run in sorted(checks.items()):
        attempt = run.get('run_attempt')
        if type(attempt) is not int or attempt < 1:
            raise ReleaseGateError('Exact workflow attempt required: '+path)
        jobs=[]
        for page in range(1,11):
            batch=request('actions/runs/'+str(run['id'])+'/attempts/'+str(attempt)+
                          '/jobs?per_page=100&page='+str(page))['jobs']
            jobs.extend(batch)
            if len(batch)<100:
                break
        else:
            raise ReleaseGateError('Workflow job pagination exceeds bound: '+path)
        if not jobs or len({job['id'] for job in jobs}) != len(jobs):
            raise ReleaseGateError('Missing or duplicated exact-attempt jobs: '+path)
        names={step.get('name') for job in jobs for step in job.get('steps', [])}
        required_steps = REQUIRED_ACCEPTANCE_STEPS.get(path,set())
        if ui_redesign_no_browser or tls_certificate_no_browser or remote_maintenance_no_browser or simple_node_no_browser:
            required_steps = required_steps | BROWSER_SKIP_STEPS.get(path,set())
            if path == 'oneclick.yml':
                required_steps = required_steps | {UI_REDESIGN_UPGRADE_STEP}
        if tls_certificate_no_browser or remote_maintenance_no_browser or simple_node_no_browser:
            if path == 'oneclick.yml':
                required_steps = required_steps | {TLS_CERTIFICATE_UPGRADE_STEP}
            if path == 'test.yml':
                required_steps = required_steps | {TLS_CERTIFICATE_CORE_STEP}
        if remote_maintenance_no_browser or simple_node_no_browser:
            if path == 'oneclick.yml':
                required_steps = required_steps | {REMOTE_MAINTENANCE_UPGRADE_STEP}
            if path == 'loopback.yml':
                required_steps = required_steps | REMOTE_MAINTENANCE_LOOPBACK_STEPS
        if simple_node_no_browser:
            if path == 'oneclick.yml':
                required_steps = required_steps | {SIMPLE_NODE_UPGRADE_STEP}
            if path == 'test.yml':
                required_steps = required_steps | {SIMPLE_NODE_TEMPLATE_STEP}
        if not required_steps <= names:
            raise ReleaseGateError('Missing required acceptance steps: '+path)
        for job in jobs:
            if (job.get('run_id') != run['id'] or job.get('head_sha') != commit
                    or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
                raise ReleaseGateError('Exact workflow job is not successful: '+path)
            steps=job.get('steps', [])
            def accepted(step):
                return (step.get('status') == 'completed' and
                        (step.get('conclusion') == 'success' or
                         ((installer_only_no_browser or ca_trust_no_browser or install_interaction_no_browser or startup_no_browser or ui_redesign_no_browser or tls_certificate_no_browser or remote_maintenance_no_browser or simple_node_no_browser) and step.get('conclusion') == 'skipped'
                          and step.get('name') in BROWSER_SKIP_STEPS.get(path,set()))))
            if not steps or not all(accepted(step) for step in steps):
                raise ReleaseGateError('Workflow has unverified, skipped or failed steps: '+path)
        # Refuse a retry or state transition that started while evidence was read.
        current=request('actions/runs/'+str(run['id']))
        if (current.get('head_sha') != commit or current.get('run_attempt') != attempt
                or current.get('status') != 'completed' or current.get('conclusion') != 'success'):
            raise ReleaseGateError('Workflow changed during qualification: '+path)


def _archive_files(raw: bytes, expected: set[str], limit: int) -> dict[str,bytes]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if set(names)!=expected or len(names)!=len(expected):
            raise ReleaseGateError('Unexpected artifact entries')
        if sum(item.file_size for item in archive.infolist())>limit:
            raise ReleaseGateError('Artifact exceeds size limit')
        if any(item.is_dir() or item.flag_bits & 1 or
               stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
               for item in archive.infolist()):
            raise ReleaseGateError('Artifact contains a link, directory or encrypted entry')
        return {name:archive.read(name) for name in names}


def _parse_checksums(raw: bytes) -> dict[str,str]:
    values={}
    for line in raw.decode().splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)',line)
        if not match or match[2] in values:
            raise ReleaseGateError('Invalid release checksums')
        values[match[2]]=match[1]
    return values


def validate_bundle(raw: bytes,commit: str,version: str,target: str) -> dict:
    if target not in TARGETS:
        raise ReleaseGateError('Unknown release target')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if 'MANIFEST.json' not in archive.namelist():
            raise ReleaseGateError('Target bundle has no manifest')
        manifest=json.loads(archive.read('MANIFEST.json'))
    if (manifest.get('source_commit')!=commit or manifest.get('version')!=version
            or manifest.get('platform')!='linux-multi-cpython312'
            or manifest.get('targets')!=[target]):
        raise ReleaseGateError('Target bundle metadata mismatch: '+target)
    return manifest


def validate_kit(raw,commit,version):
    files=_archive_files(raw,KIT_ASSETS,900_000_000)
    hashes=_parse_checksums(files['SHA256SUMS'])
    if set(hashes)!=KIT_ASSETS-{'SHA256SUMS'}:
        raise ReleaseGateError('Missing kit asset checksum')
    for name,expected in hashes.items():
        if hashlib.sha256(files[name]).hexdigest()!=expected:
            raise ReleaseGateError('Kit asset checksum mismatch: '+name)
    record=json.loads(files['RELEASE.json'])
    if record.get('source_commit')!=commit or record.get('version')!=version:
        raise ReleaseGateError('Artifact metadata does not match requested release')
    validate_bundle(files['vui-linux-x86_64-gnu.zip'],commit,version,'x86_64-gnu')
    return files


def validate_target_artifact(raw,commit,version,target):
    bundle='vui-linux-'+target+'.zip'
    checksum=bundle+'.sha256'
    files=_archive_files(raw,{bundle,checksum},900_000_000)
    line=files[checksum].decode().strip()
    match=re.fullmatch(r'([a-f0-9]{64})  '+re.escape(bundle),line)
    if not match or hashlib.sha256(files[bundle]).hexdigest()!=match.group(1):
        raise ReleaseGateError('Portable target checksum mismatch: '+target)
    validate_bundle(files[bundle],commit,version,target)
    return files[bundle]


def download_artifact(artifact,max_bytes=900_000_000):
    try:
        request('actions/artifacts/'+str(artifact['id'])+'/zip')
    except urllib.error.HTTPError as redirect:
        if redirect.code!=302:
            raise
        location=redirect.headers['Location']
        if urllib.parse.urlsplit(location).scheme!='https':
            raise ReleaseGateError('Insecure artifact redirect')
    else:
        raise ReleaseGateError('Expected signed artifact redirect')
    with urllib.request.urlopen(location,timeout=120) as response:
        raw=response.read(max_bytes+1)
    if len(raw)>max_bytes or artifact.get('digest')!='sha256:'+hashlib.sha256(raw).hexdigest():
        raise ReleaseGateError('Artifact transport digest mismatch')
    return raw


def unique_artifact(items,name):
    selected=[a for a in items if a.get('name')==name and not a.get('expired')]
    if len(selected)!=1:
        raise ReleaseGateError('No unique accepted artifact: '+name)
    return selected[0]


def finalize_assets(files: dict[str,bytes],commit: str,version: str) -> dict[str,bytes]:
    record=json.loads(files['RELEASE.json'])
    record.update(source_commit=commit,version=version,targets=list(TARGETS))
    files['RELEASE.json']=json.dumps(record,indent=2).encode()
    if set(files)-{'SHA256SUMS'}!=ASSETS-{'SHA256SUMS'}:
        raise ReleaseGateError('Final release asset set is incomplete')
    lines=[
        hashlib.sha256(files[name]).hexdigest()+'  '+name
        for name in sorted(files) if name!='SHA256SUMS'
    ]
    files['SHA256SUMS']=('\n'.join(lines)+'\n').encode()
    if set(files)!=ASSETS:
        raise ReleaseGateError('Unexpected final release asset set')
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commit',required=True);p.add_argument('--version',required=True)
    p.add_argument('--confirm',required=True);p.add_argument('--publish',action='store_true')
    p.add_argument('--simple-node-no-browser',action='store_true',
                   help='Approved frozen 0.4.18 simple-node product; browsers remain unverified')
    p.add_argument('--remote-maintenance-no-browser',action='store_true',
                   help='Approved frozen 0.4.17 remote-maintenance product; browsers remain unverified')
    p.add_argument('--tls-certificate-no-browser',action='store_true',
                   help='Approved frozen 0.4.16 TLS certificate candidate; browsers remain unverified')
    p.add_argument('--ui-redesign-no-browser',action='store_true',
                   help='Approved frozen 0.4.15 UI candidate; browsers remain unverified')
    p.add_argument('--startup-no-browser',action='store_true',
                   help='Explicitly approved 0.4.14 bounded startup and diagnostic fix; browsers unverified')
    p.add_argument('--install-interaction-no-browser',action='store_true',
                   help='Explicitly approved 0.4.13 credentials/firewall patch; browsers remain unverified')
    p.add_argument('--ca-trust-no-browser',action='store_true',
                   help='Explicitly approved 0.4.12 system CA fix; browsers remain unverified')
    p.add_argument('--installer-only-no-browser',action='store_true',
                   help='Explicitly approved 0.4.11 installer-only exception; browser evidence stays unverified')
    args=p.parse_args()
    if not re.fullmatch(r'[a-f0-9]{40}',args.commit) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',args.version):
        raise ReleaseGateError('Exact commit and stable version required')
    if args.confirm!='RELEASE '+args.version+' '+args.commit:
        raise ReleaseGateError('Explicit release confirmation does not match')
    if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':
        raise ReleaseGateError('Run only from the trusted manual repository workflow')

    default=request('')['default_branch']
    current=request('git/ref/heads/'+urllib.parse.quote(default,safe=''))['object']['sha']
    local=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if args.commit!=current or local!=current:
        raise ReleaseGateError('Merge and test the exact current default-branch commit first')
    if Path('VERSION').read_text().strip()!=args.version:
        raise ReleaseGateError('VERSION differs')

    runs=[]
    for page in range(1,11):
        batch=request('actions/runs?head_sha='+args.commit+'&per_page=100&page='+str(page))['workflow_runs']
        runs.extend(batch)
        if len(batch)<100:
            break
    checks=select_runs(runs,args.commit)
    if sum((args.installer_only_no_browser, args.ca_trust_no_browser, args.install_interaction_no_browser, args.startup_no_browser, args.ui_redesign_no_browser, args.tls_certificate_no_browser, args.remote_maintenance_no_browser, args.simple_node_no_browser)) > 1:
        raise ReleaseGateError('Non-browser scope exceptions are mutually exclusive')
    if args.simple_node_no_browser:
        validate_simple_node_scope(args.commit,args.version)
    if args.remote_maintenance_no_browser:
        validate_remote_maintenance_scope(args.commit,args.version)
    if args.tls_certificate_no_browser:
        validate_tls_certificate_scope(args.commit,args.version)
    if args.ui_redesign_no_browser:
        validate_ui_redesign_scope(args.commit,args.version)
    if args.startup_no_browser:
        validate_startup_scope(args.commit,args.version)
    if args.install_interaction_no_browser:
        validate_install_interaction_scope(args.commit,args.version)
    if args.ca_trust_no_browser:
        validate_ca_trust_scope(args.commit,args.version)
    if args.installer_only_no_browser:
        validate_installer_only_scope(args.commit,args.version)
    validate_run_jobs(checks,args.commit,installer_only_no_browser=args.installer_only_no_browser,
                      ca_trust_no_browser=args.ca_trust_no_browser,
                      install_interaction_no_browser=args.install_interaction_no_browser, startup_no_browser=args.startup_no_browser,
                      ui_redesign_no_browser=args.ui_redesign_no_browser,
                      tls_certificate_no_browser=args.tls_certificate_no_browser,
                      remote_maintenance_no_browser=args.remote_maintenance_no_browser,
                      simple_node_no_browser=args.simple_node_no_browser)

    tag='v'+args.version
    for path in ('git/ref/tags/'+tag,'releases/tags/'+tag):
        try:
            request(path)
        except urllib.error.HTTPError as error:
            if error.code!=404:
                raise
        else:
            raise ReleaseGateError('Refusing to overwrite an existing tag or release')

    oneclick=request('actions/runs/'+str(checks['oneclick.yml']['id'])+'/artifacts?per_page=100')['artifacts']
    kit=unique_artifact(oneclick,'vui-release-kit-'+args.commit)
    files=validate_kit(download_artifact(kit),args.commit,args.version)

    portable=request('actions/runs/'+str(checks['portable.yml']['id'])+'/artifacts?per_page=100')['artifacts']
    for target in TARGETS[1:]:
        name='vui-linux-'+target+'-'+args.commit
        artifact=unique_artifact(portable,name)
        files['vui-linux-'+target+'.zip']=validate_target_artifact(
            download_artifact(artifact),args.commit,args.version,target
        )

    files=finalize_assets(files,args.commit,args.version)

    evidence='\n'.join('- '+path+': '+str(run['html_url']) for path,run in sorted(checks.items()))
    body=files['RELEASE_NOTES.md'].decode()+'\n\nVerified commit: `'+args.commit+'`\n\n'+evidence
    if args.installer_only_no_browser:
        body+='\n\n验收范围：用户明确批准本次 0.4.11 纯安装交互修复采用非浏览器验收。浏览器未验证；'+\
              '仅跳过明列的浏览器步骤，其余 jobs/steps、四目标制品及实际安装门禁保持。'+\
              '已检查相对 `'+INSTALLER_ONLY_BASELINE+'` 无前端、应用逻辑、运行依赖或固定核心变更；此例外不适用于其他版本。\n'
    if args.ca_trust_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.12系统CA兼容修复采用非浏览器验收。浏览器未验证；'+\
              '只跳过明列浏览器步骤，其余准确提交/attempt/jobs/steps、四目标与实际安装门禁保持。'+\
              '相对 `'+CA_TRUST_BASELINE+'` 仅允许证书材料系统根加载、固定错误分类及对应测试/版本/发布说明；'+\
              '前端、其他应用逻辑、运行依赖、builder和固定核心变更均拒绝，此例外仅适用0.4.12。\n'
    if args.install_interaction_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.13账号自动初始化与防火墙交互修复采用非浏览器验收。浏览器未验证；'+\
              '只跳过明列浏览器步骤，其余准确提交/attempt/jobs/steps、四目标与真实安装门禁保持。'+\
              '相对 `'+INSTALL_INTERACTION_BASELINE+'` 仅允许安装器、只读防火墙检查、本地管理员初始化及对应测试/版本/文档；'+\
              '前端、Web认证API、运行依赖、builder、固定核心和证书验证逻辑变更均拒绝，此例外仅适用0.4.13。\n'
    if args.startup_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.14安装启动等待与诊断修复采用非浏览器验收。浏览器未验证；'+\
              '只跳过明列浏览器步骤，其余准确提交/attempt/jobs/steps、四目标与真实慢启动/失败恢复安装门禁保持。'+\
              '相对 `'+STARTUP_BASELINE+'` 仅允许安装器、服务控制、对应测试/文档和版本；'+\
              '应用逻辑、前端、证书校验、运行依赖、builder和固定核心变更均拒绝，此例外只适用0.4.14。\n'
    if args.ui_redesign_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.15统一UI与防火墙整理采用非浏览器验收。浏览器未验证；'+\
              '仅允许12个明列浏览器步骤跳过，准确提交/attempt/全部jobs与其他steps、四目标、真实安装与旧0.4.14升级门禁保持。'+\
              '批准UI候选固定为 `'+UI_REDESIGN_BASELINE+'`；此后仅允许列明版本、发布门禁、升级验收和文档；'+\
              'main.py仅版本替换，其他生产UI、后端、依赖、核心与短入口保持，安装器仅版本注释替换。此例外只适用0.4.15，不覆盖旧资产。\n'
    if args.tls_certificate_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.16 TLS节点托管证书绑定修复采用非浏览器验收。浏览器未验证；'+\
              '仅允许12个明列浏览器步骤跳过且必须存在，其余准确提交/attempt/全部jobs与steps、四目标、真实安装、旧0.4.14和0.4.15升级门禁保持。'+\
              '固定核心真实Xray TLS/SNI、证书轮换与失败恢复测试必须成功；批准候选冻结为 `'+TLS_CERTIFICATE_BASELINE+'`；'+\
              '此后仅允许精确列明版本、发布门禁、升级验收和文档，main.py仅15→16版本替换，install.sh仅同一版本注释替换。'+\
              '其他UI、API、CoreRuntime、依赖、固定核心与短入口保持，不覆盖旧资产；此例外只适用0.4.16。\n'
    if args.simple_node_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.18简单节点向导采用非浏览器验收。浏览器未验证；'+\
              '要求全部非浏览器jobs/steps、固定Vue诊断编译、固定核心、完整真实链路与四目标、真实安装/维护及公开14/15/16/17保数据升级成功。'+\
              '产品冻结于 `'+SIMPLE_NODE_BASELINE+'`；其后仅允许严格列明版本、发布门禁、升级验收、文档及发布后短入口指针。'+\
              '旧配置、凭据、节点、订阅、证书和旧资产保留；构建与真实安装仅GitHub Actions，未操作用户VPS。此例外仅适用0.4.18。\n'
    if args.remote_maintenance_no_browser:
        body+='\n\n验收范围：用户明确批准本次0.4.17远程维护采用非浏览器验收。浏览器未验证；'+\
              '仅允许12个明列浏览器步骤跳过且必须存在，其余准确提交/attempt/全部jobs与steps、四目标及真实安装保持。'+\
              '已发布0.4.14、0.4.15和0.4.16到准确候选的真实升级、固定核心和完整loopback必须成功；'+\
              '产品冻结于 `'+REMOTE_MAINTENANCE_BASELINE+'`，此后仅允许列明版本、发布门禁、升级验收、文档和发布后短入口指针。'+\
              'main.py仅16→17版本替换，install.sh仅同一版本注释替换；其他应用、UI、root/runtime逻辑、依赖和固定核心保持。'+\
              '构建与真实安装仅在GitHub Actions临时runner执行，未操作用户真实VPS；不覆盖旧资产，此例外仅适用0.4.17。\n'
    body+='\n\n## 一行交互安装并注册 v-ui\n\n```sh\n'+installation_command(files,args.version)+'\n```\n'
    body+='\n安装成功后输入 `v-ui`。普通管理员按已有 sudo 策略认证；不增加免密 sudoers。\n'
    release=request('releases','POST',{'tag_name':tag,'target_commitish':args.commit,'name':'V-UI '+tag,
                                      'body':body,'draft':True,'prerelease':False})
    upload=release['upload_url'].split('{')[0]
    if not upload.startswith('https://uploads.github.com/repos/'+REPO+'/releases/'):
        raise ReleaseGateError('Unexpected upload endpoint; draft retained')
    for name in sorted(files):
        asset=request(upload+'?name='+urllib.parse.quote(name),'POST',files[name],True)
        if asset.get('digest')!='sha256:'+hashlib.sha256(files[name]).hexdigest():
            raise ReleaseGateError('Uploaded asset digest mismatch; release remains draft')
    if args.publish:
        release=request('releases/'+str(release['id']),'PATCH',{'draft':False,'make_latest':'true'})
    print(json.dumps({'release':release['html_url'],'draft':release['draft'],
                      'commit':args.commit,'targets':list(TARGETS)}))


if __name__=='__main__':
    main()
