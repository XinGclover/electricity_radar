"""Offline fixtures are synthetic and never used by the application."""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from electricity_radar.prices import (
    Interval, day_bounds, hourly_averages, ore, parse_day, validate_day,
    weighted_average, STOCKHOLM,
)


def fixture(day):
    start, end = day_bounds(day)
    step = timedelta(minutes=15 if day >= date(2025, 10, 1) else 60)
    rows = []
    while start < end:
        rows.append(Interval(start, start + step, Decimal('-0.25')))
        start += step
    return rows


class PriceTests(unittest.TestCase):
    def test_units_and_negative_prices(self):
        self.assertEqual(ore(Decimal('1.2345')), Decimal('123.4500'))
        self.assertEqual(ore(Decimal('-0.25')), Decimal('-25'))

    def test_dst_and_resolution(self):
        for day, count, hours in [(date(2025, 3, 30), 23, 23),
                                  (date(2025, 10, 26), 100, 25),
                                  (date(2026, 3, 29), 92, 23),
                                  (date(2025, 9, 30), 24, 24),
                                  (date(2025, 10, 1), 96, 24)]:
            with self.subTest(day=day):
                rows = validate_day(fixture(day), day)
                self.assertEqual(len(rows), count)
                self.assertEqual(len(hourly_averages(rows, day)), hours)
        hours = hourly_averages(fixture(date(2025, 10, 26)), date(2025, 10, 26))
        repeated = [r.start.astimezone(STOCKHOLM) for r in hours
                    if r.start.astimezone(STOCKHOLM).hour == 2]
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0].utcoffset(), repeated[1].utcoffset())

    def test_weighted_aggregation(self):
        start, _ = day_bounds(date(2025, 10, 1))
        rows = [Interval(start, start + timedelta(minutes=15), Decimal(0)),
                Interval(start + timedelta(minutes=15), start + timedelta(hours=1), Decimal(4))]
        self.assertEqual(weighted_average(rows), Decimal(3))
        self.assertEqual(hourly_averages(rows, date(2025, 10, 1))[0].price, Decimal(3))

    def test_reject_bad_coverage(self):
        day = date(2025, 10, 26)
        rows = fixture(day)
        for bad in [[], rows[:-1], rows[1:], rows + [rows[0]],
                    [Interval(rows[0].start, rows[0].end + timedelta(minutes=1), Decimal(1))] + rows[1:],
                    [Interval(rows[0].start, rows[0].end, Decimal('NaN'))] + rows[1:]]:
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    validate_day(bad, day)

    def test_json_validation_and_utc(self):
        day = date(2025, 10, 26)
        payload = [{'time_start': r.start.astimezone(STOCKHOLM).isoformat(),
                    'time_end': r.end.astimezone(STOCKHOLM).isoformat(),
                    'SEK_per_kWh': float(r.price)} for r in fixture(day)]
        rows = parse_day(payload, day)
        self.assertEqual(rows[0].start.utcoffset(), timedelta(0))
        for value in [True, '1.2', None, float('inf')]:
            bad = [dict(r) for r in payload]
            bad[0]['SEK_per_kWh'] = value
            with self.assertRaises(ValueError):
                parse_day(bad, day)
        payload[0]['time_start'] = '2025-10-26T00:00:00'
        with self.assertRaises(ValueError):
            parse_day(payload, day)
        with self.assertRaises(ValueError):
            parse_day({'error': 'unavailable'}, day)


if __name__ == '__main__':
    unittest.main()
