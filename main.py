"""Start the Spiro dashboard. Connection details come from the root .env."""
import argparse
import logging
import sys

from app.db_config import read_database_config
from app.reconciliation_backend import COUNTRIES, STATUSES


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--export-unmatched', action='store_true', help='Export missing-counterpart reports')
    modes.add_argument('--export-review', action='store_true', help='Export reconciliation reports')
    modes.add_argument('--import-config', action='store_true', help='Import CSV files listed in config.json')
    parser.add_argument('--port', type=int, help='Local dashboard port (default: 8765)')
    parser.add_argument('--country', choices=COUNTRIES)
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--status', choices=STATUSES)
    parser.add_argument('--output')
    parser.add_argument('--dry-run', action='store_true', help='Validate configured CSV files')
    parser.add_argument('--wallet-master-only', action='store_true')
    parser.add_argument('--file-prefix')
    args = parser.parse_args(argv)
    export = args.export_unmatched or args.export_review
    importing = args.import_config or args.dry_run or args.wallet_master_only or args.file_prefix is not None
    report_options = any(value is not None for value in (args.country, args.start, args.end, args.status, args.output))
    if export and importing:
        parser.error('Export and import options cannot be combined.')
    if (export or importing) and args.port is not None:
        parser.error('--port applies only to the dashboard.')
    if report_options and not export:
        parser.error('Country, date, status and output options require a report export.')
    if args.export_unmatched and args.status is not None:
        parser.error('--status applies only to --export-review.')
    if export:
        if args.export_unmatched:
            from app.missing_counterparts import main as export_report
        else:
            from app.reconcile import main as export_report
        return export_report([option for option in argv if option not in ('--export-unmatched', '--export-review')])
    if importing:
        from tools.batch import run_import
        return run_import([option for option in argv if option != '--import-config'])
    port = args.port if args.port is not None else 8765
    if not 1024 <= port <= 65535:
        raise ValueError('Port must be between 1024 and 65535.')
    from app.dashboard import serve
    from app.import_service import prepare_database
    settings = read_database_config()
    print('Preparing the reconciliation database...', flush=True)
    prepare_database(settings)
    print('Database ready.', flush=True)
    serve(settings, port)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as exc:
        print(f'Stopped: {exc}', file=sys.stderr)
        sys.exit(1)
    except Exception:
        logging.getLogger(__name__).exception('Spiro startup failed')
        print('Could not run Spiro. Check PostgreSQL, .env, and the dashboard port.', file=sys.stderr)
        sys.exit(1)
