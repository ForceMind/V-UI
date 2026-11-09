"""Keep an independent data lease for the entire DNS Certbot subprocess."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    root = Path(args.pop(0))
    if not root.is_absolute() or '..' in root.parts:
        return 2
    from app.data_owner import data_owner
    with data_owner(root.parent):
        from certbot.main import main as certbot_main
        return certbot_main(args)


if __name__ == '__main__':
    raise SystemExit(main())
