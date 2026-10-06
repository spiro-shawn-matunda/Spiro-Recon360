"""Start the Spiro dashboard. Connection details come from the root .env."""
import argparse
import sys

from app.db_config import read_database_config


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    exports = [flag for flag in ('--export-unmatched', '--export-review') if flag in argv]
    if len(exports) > 1:
        raise ValueError('Choose one report export at a time.')
    if exports:
        if exports[0] == '--export-unmatched':
            from app.missing_counterparts import main as export_report
        else:
            from app.reconcile import main as export_report
        return export_report([option for option in argv if option != exports[0]])
    if any(option.split('=', 1)[0] in ('--import-config', '--dry-run', '--wallet-master-only', '--file-prefix') for option in argv):
        from tools.batch import run_import
        return run_import([option for option in argv if option != '--import-config'])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765, help='Local dashboard port')
    parser.add_argument('--export-unmatched', action='store_true', help='Export missing-counterpart reports')
    parser.add_argument('--export-review', action='store_true', help='Export reconciliation reports')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise ValueError('Port must be between 1024 and 65535.')
    from app.dashboard import serve
    from app.import_service import prepare_database
    settings = read_database_config()
    print('Preparing the reconciliation database...', flush=True)
    prepare_database(settings)
    print('Database ready.', flush=True)
    serve(settings, args.port)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print(f'Stopped: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('Could not run Spiro. Check PostgreSQL, .env, and the dashboard port.', file=sys.stderr)
        sys.exit(1)
