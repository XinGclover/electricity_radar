"""Manual SE3 live generation, historical backtests, and read-only evaluation."""

import argparse
from datetime import date, datetime, timedelta

from electricity_radar.db import DatabaseError, connect
from electricity_radar.forecast_db import (
    evaluated_results, generate_live, run_backtest_day, settings_cutoff,
)
from electricity_radar.forecasts import ForecastUnavailable, performance
from electricity_radar.prices import FIRST_DATE, STOCKHOLM, ore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('generate', help="Save tomorrow's live baselines before the local cutoff")
    backtest = commands.add_parser('backtest', help='Historical backtest, not forecasts issued in the past')
    backtest.add_argument('--start-date', required=True, type=date.fromisoformat)
    backtest.add_argument('--end-date', required=True, type=date.fromisoformat)
    commands.add_parser('evaluate', help='Read-only evaluation of stored live forecasts against current prices')
    args = parser.parse_args()
    if args.command == 'backtest':
        if args.start_date > args.end_date or args.start_date < FIRST_DATE:
            parser.error('Provide an ordered inclusive target-date range within supported price history')
        if args.end_date > datetime.now(STOCKHOLM).date():
            parser.error('Historical backtest target dates cannot be in the future')
    try:
        with connect() as connection:
            if args.command == 'generate':
                saved, unavailable = generate_live(connection, settings_cutoff())
                for row in saved:
                    print(f'{row.target_date} {row.method} v{row.version}: {ore(row.prediction):.4f} öre/kWh; '
                          f'issued {row.generated_at.astimezone(STOCKHOLM).isoformat()} (saved or existing, immutable)')
                for message in unavailable:
                    print(message)
                return 1 if unavailable else 0
            if args.command == 'backtest':
                print('Historical backtest: assumes prior-day and prior-week published prices were available at the simulated origin.')
                day, count, skipped_count = args.start_date, 0, 0
                cutoff = settings_cutoff()
                while day <= args.end_date:
                    saved, skipped = run_backtest_day(connection, day, cutoff)
                    count += len(saved)
                    skipped_count += len(skipped)
                    for message in skipped:
                        print(message)
                    print(f'{day}: {len(saved)} historical backtest result(s) saved or existing')
                    day += timedelta(days=1)
                print(f'Historical backtest summary: results={count}, skipped={skipped_count}')
                return 1 if skipped_count else 0
            results = evaluated_results(connection, 'live')
            if not results:
                print('No stored live forecasts. Generate tomorrow\'s forecasts before the cutoff.')
            for row in results:
                if row['actual'] is None:
                    print(f'{row["target_date"]} {row["method"]}: Pending — complete actual prices unavailable')
                else:
                    print(f'{row["target_date"]} {row["method"]}: prediction={ore(row["prediction"]):.4f}, '
                          f'actual={ore(row["actual"]):.4f}, signed error={ore(row["signed_error"]):.4f}, '
                          f'absolute error={ore(row["absolute_error"]):.4f} öre/kWh')
            for summary in performance(results):
                mae = f'{ore(summary["mae"]):.4f}' if summary['mae'] is not None else 'unavailable'
                common_mae = f'{ore(summary["common_mae"]):.4f}' if summary['common_mae'] is not None else 'unavailable'
                print(f'{summary["method"]}: MAE={mae} öre/kWh, n={summary["sample_size"]}; '
                      f'common-date MAE={common_mae}, common n={summary["common_sample_size"]}')
            return 0
    except (DatabaseError, ForecastUnavailable) as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
