"""Independent UTC boundaries and source-shaped DST regressions; no I/O."""

import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from electricity_radar.prices import (
    Interval, STOCKHOLM, UTC, day_bounds, parse_day, validate_day,
)


class DSTRegressionTests(unittest.TestCase):
    def test_independent_midnights_and_local_zone_intervals(self):
        cases = [
            (date(2025, 10, 26), '2025-10-25T22:00:00+00:00', '2025-10-26T23:00:00+00:00', 100),
            (date(2026, 3, 29), '2026-03-28T23:00:00+00:00', '2026-03-29T22:00:00+00:00', 92),
            (date(2025, 10, 27), '2025-10-26T23:00:00+00:00', '2025-10-27T23:00:00+00:00', 96),
        ]
        for day, first, last, count in cases:
            with self.subTest(day=day):
                start, end = datetime.fromisoformat(first), datetime.fromisoformat(last)
                self.assertEqual(day_bounds(day), (start, end))
                utc_rows = [Interval(start + timedelta(minutes=15*i),
                                     start + timedelta(minutes=15*(i+1)), Decimal(1)) for i in range(count)]
                # Same ZoneInfo on both endpoints exposes wall-time subtraction/fold bugs.
                local_rows = [Interval(r.start.astimezone(STOCKHOLM), r.end.astimezone(STOCKHOLM), r.price)
                              for r in utc_rows]
                self.assertEqual(validate_day(local_rows, day), utc_rows)
                payload = [{'time_start': r.start.isoformat(), 'time_end': r.end.isoformat(),
                            'SEK_per_kWh': 1} for r in local_rows]
                parsed = parse_day(payload, day)
                self.assertEqual(parsed, utc_rows)
                if count == 100:
                    repeated = [r for r in parsed if r.start.astimezone(STOCKHOLM).hour == 2]
                    self.assertEqual(len(repeated), 8)
                    self.assertEqual(len({r.start for r in repeated}), 8)
                missing = utc_rows[count//2]
                with self.assertRaisesRegex(ValueError, 'Missing UTC interval') as error:
                    validate_day(utc_rows[:count//2] + utc_rows[count//2+1:], day)
                self.assertIn(missing.start.isoformat(), str(error.exception))
                self.assertIn(missing.end.isoformat(), str(error.exception))

    def test_observed_source_autumn_end_timestamp_is_rejected(self):
        # Matches the malformed timestamps observed live, not real price values.
        start, end = day_bounds(date(2025, 10, 26))
        payload = []
        cursor = start
        while cursor < end:
            payload.append({'time_start': cursor.astimezone(STOCKHOLM).isoformat(),
                            'time_end': (cursor + timedelta(minutes=15)).astimezone(STOCKHOLM).isoformat(),
                            'SEK_per_kWh': 1})
            cursor += timedelta(minutes=15)
        payload[11]['time_end'] = '2025-10-26T03:00:00+01:00'
        with self.assertRaises(ValueError) as error:
            parse_day(payload, date(2025, 10, 26))
        message = str(error.exception)
        self.assertIn('4500 seconds; expected 900', message)
        self.assertIn('2025-10-26T00:45:00+00:00', message)
        self.assertIn('Overlapping', message)

    def test_exact_live_neighbor_timestamp_conversion(self):
        pairs = [
            ('2025-10-26T02:30:00+02:00', '2025-10-26T02:45:00+02:00',
             '2025-10-26T00:30:00+00:00', '2025-10-26T00:45:00+00:00', 900),
            ('2025-10-26T02:45:00+02:00', '2025-10-26T03:00:00+01:00',
             '2025-10-26T00:45:00+00:00', '2025-10-26T02:00:00+00:00', 4500),
            ('2025-10-26T02:00:00+01:00', '2025-10-26T02:15:00+01:00',
             '2025-10-26T01:00:00+00:00', '2025-10-26T01:15:00+00:00', 900),
        ]
        for first, last, expected_start, expected_end, seconds in pairs:
            with self.subTest(first=first):
                start = datetime.fromisoformat(first).astimezone(UTC)
                end = datetime.fromisoformat(last).astimezone(UTC)
                self.assertEqual(start.isoformat(), expected_start)
                self.assertEqual(end.isoformat(), expected_end)
                self.assertEqual((end - start).total_seconds(), seconds)

    def test_duplicate_and_naive_errors(self):
        day = date(2025, 10, 27)
        start, _ = day_bounds(day)
        row = Interval(start, start + timedelta(minutes=15), Decimal(1))
        with self.assertRaisesRegex(ValueError, 'Duplicate interval start'):
            validate_day([row, row], day)
        naive = Interval(datetime(2025, 10, 27), row.end, Decimal(1))
        with self.assertRaisesRegex(ValueError, 'timezone offsets are required'):
            validate_day([row, naive], day)
