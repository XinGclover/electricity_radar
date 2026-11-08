"""Manually ingest complete local delivery days; never create database objects."""

import argparse
from datetime import date, datetime, timedelta

from electricity_radar.db import DatabaseError, connect, write_day
from electricity_radar.prices import AREAS, FIRST_DATE, STOCKHOLM
from electricity_radar.source import FetchError, UnavailableError, fetch_day


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date', required=True, type=date.fromisoformat)
    parser.add_argument('--end-date', required=True, type=date.fromisoformat)
    parser.add_argument('--areas', nargs='+', choices=AREAS, default=['SE3'])
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error('Start date must not follow end date')
    if args.start_date < FIRST_DATE:
        parser.error(f'Source history starts on {FIRST_DATE}')
    if args.end_date > datetime.now(STOCKHOLM).date() + timedelta(days=1):
        parser.error('Dates after tomorrow are not published')
    success = failed = unavailable = written = attempted = 0
    areas = tuple(dict.fromkeys(args.areas))
    total = ((args.end_date - args.start_date).days + 1) * len(areas)
    current = None
    try:
        with connect() as connection:
            day = args.start_date
            while day <= args.end_date:
                for area in areas:
                    current = f'{day} {area}'
                    attempted += 1
                    try:
                        rows = fetch_day(day, area)
                        written += write_day(connection, area, day, rows)
                        success += 1
                        print(f'{day} {area}: complete ({len(rows)} records upserted)')
                    except UnavailableError as exc:
                        unavailable += 1
                        print(f'{day} {area}: unavailable — {exc}')
                    except FetchError as exc:
                        failed += 1
                        print(f'{day} {area}: failed — {exc}')
                day += timedelta(days=1)
    except DatabaseError as exc:
        if current is not None:
            failed += 1
        print(f'{current}: {exc}' if current else str(exc))
    print(f'Area-days: successful={success}, failed={failed}, unavailable={unavailable}, '
          f'not attempted={total - attempted}; records written={written}')
    return 1 if success != total else 0


if __name__ == '__main__':
    raise SystemExit(main())
