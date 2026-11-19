"""Keep data exclusion across DNS Certbot and the two fixed hook processes."""
from pathlib import Path
from contextlib import contextmanager
import shlex
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))


@contextmanager
def fixed_bootstrap_hooks(args, root, descriptor):
    """Pass the exclusive lease only to our two exact, shell-free callbacks.

    Pinned Certbot normally closes non-standard descriptors in manual hooks.
    This scoped adapter is only used by stopped bootstrap; it refuses every
    command except the two fixed hook strings from the provider invocation.
    """
    from importlib.metadata import version
    if version('certbot') != '5.8.0':
        raise ValueError('DNS_BOOTSTRAP_CERTBOT_VERSION_UNSUPPORTED')
    from certbot.compat import misc
    hook = str(Path(__file__).absolute().with_name('dns_hook.py'))
    permitted = {}
    for action in ('auth', 'cleanup'):
        flag = '--manual-' + action + '-hook'
        if args.count(flag) != 1:
            raise ValueError('DNS_BOOTSTRAP_HOOK_INVALID')
        position = args.index(flag)
        if position + 1 >= len(args):
            raise ValueError('DNS_BOOTSTRAP_HOOK_INVALID')
        command = args[position + 1]
        values = shlex.split(command)
        if (len(values) != 13 or values[:6] != [sys.executable, '-B', '-I', hook, action, str(root)]
                or values[7] != '--deadline' or values[9] != '--propagation-seconds'
                or values[11:] != ['--owner-fd', str(descriptor)]):
            raise ValueError('DNS_BOOTSTRAP_HOOK_INVALID')
        permitted['manual-' + action + '-hook'] = (command, values)
    original = misc.execute_command_status
    def execute(cmd_name, shell_cmd, env=None):
        expected = permitted.get(cmd_name)
        if expected is None or shell_cmd != expected[0]:
            raise ValueError('DNS_BOOTSTRAP_HOOK_REFUSED')
        result = subprocess.run(expected[1], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, check=False, env=env,
                                pass_fds=(descriptor,))
        return result.returncode, result.stderr, result.stdout
    misc.execute_command_status = execute
    try:
        yield
    finally:
        misc.execute_command_status = original


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    root = Path(args.pop(0))
    if not root.is_absolute() or '..' in root.parts:
        return 2
    descriptor = None
    if args[:1] == ['--owner-fd']:
        if len(args) < 3 or not args[1].isdecimal():
            return 2
        descriptor = int(args[1])
        del args[:2]
    from app.data_owner import data_owner, inherited_exclusive_owner
    ownership = (data_owner(root.parent) if descriptor is None
                 else inherited_exclusive_owner(root.parent, descriptor))
    with ownership:
        from certbot.main import main as certbot_main
        if descriptor is None:
            return certbot_main(args)
        with fixed_bootstrap_hooks(args, root, descriptor):
            return certbot_main(args)


if __name__ == '__main__':
    raise SystemExit(main())
