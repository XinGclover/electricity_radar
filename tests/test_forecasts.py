"""Synthetic offline baseline, timing, evaluation and persistence tests."""

import json
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from electricity_radar.forecasts import (
    ForecastUnavailable, cutoff_for, cutoff_time, daily_input, evaluate,
    live_target, make_forecast, performance,
)
from electricity_radar.forecast_db import (
    evaluated_results, generate_live, load_forecasts, run_backtest_day, save_forecast,
)
from electricity_radar.prices import UTC, day_bounds
from test_prices import fixture


def records(day, price='1', retrieved=None):
    retrieved = retrieved or datetime(2026, 11, 1, tzinfo=UTC)
    return [(r.start, r.end, Decimal(price), retrieved) for r in fixture(day)]


class MemoryConnection:
    """Test double for insert-conflict/read behavior; not a PostgreSQL integration test."""
    def __init__(self):
        self.tables = {'live_forecasts': {}, 'backtests': {}}
        self.queries = []

    def transaction(self):
        return nullcontext()

    def execute(self, query, params):
        text = query if isinstance(query, str) else query.as_string()
        self.queries.append(text)
        if 'pg_advisory_xact_lock' in text:
            return self
        table = 'live_forecasts' if '"live_forecasts"' in text else 'backtests'
        if text.startswith('INSERT'):
            assert 'ON CONFLICT DO NOTHING' in text
            assert 'UPDATE' not in text
            row = tuple(params[:11]) + (json.loads(params[11]),) + tuple(params[12:])
            key = row[:4] if table == 'live_forecasts' else row[:4] + (row[10],)
            self.tables[table].setdefault(key, row)
            self.rows = []
        else:
            self.rows = [r for r in self.tables[table].values()
                         if r[0] == params[0] and r[3] == params[1] and (len(params) == 2 or r[1] == params[2])]
            self.rows.sort(key=lambda r: (r[1], r[2], -r[5].timestamp(), r[10]))
        return self

    def fetchall(self):
        return self.rows


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.target = date(2026, 11, 9)
        self.now = datetime(2026, 11, 8, 8, tzinfo=UTC)
        self.cutoff = cutoff_time('10:00')
        self.origin = cutoff_for(self.target, self.cutoff)

    def forecast(self, method='persistence', **kwargs):
        source = self.target - timedelta(days=1 if method == 'persistence' else 7)
        return make_forecast(self.target, method, records(source), self.now, self.origin, **kwargs)

    def test_calendar_source_dates(self):
        self.assertEqual(self.forecast().source_date, date(2026, 11, 8))
        self.assertEqual(self.forecast('weekly_seasonal').source_date, date(2026, 11, 2))
        target = date(2025, 10, 27)
        result = make_forecast(target, 'persistence', records(date(2025, 10, 26), retrieved=self.now),
                               self.now, self.origin, historical=True, target_records=records(target))
        self.assertEqual(result.source_date, date(2025, 10, 26))

    def test_dst_weighting_and_fingerprint(self):
        for day in (date(2025, 10, 26), date(2026, 3, 29)):
            data = records(day, '0')
            # Only one hour at price 4. A 23/25-hour day gives a different daily mean.
            data = [(a, b, Decimal(4) if i < 4 else p, r) for i, (a, b, p, r) in enumerate(data)]
            avg, _, snapshot = daily_input(data, day)
            hours = Decimal(str((day_bounds(day)[1] - day_bounds(day)[0]).total_seconds())) / 3600
            self.assertEqual(avg, Decimal(4) / hours)
            self.assertEqual(snapshot, daily_input(list(reversed(data)), day)[2])
            changed = list(data)
            a, b, p, r = changed[0]
            changed[0] = (a, b, p + 1, r)
            self.assertNotEqual(snapshot, daily_input(changed, day)[2])

    def test_missing_incomplete_and_late_retrievals(self):
        source = self.target - timedelta(days=1)
        for data in ([], records(source)[:-1], records(source, retrieved=self.now + timedelta(seconds=1))):
            with self.assertRaises(ValueError):
                make_forecast(self.target, 'persistence', data, self.now, self.origin)
        mixed = records(source)
        a, b, p, _ = mixed[-1]
        mixed[-1] = (a, b, p, self.now + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, 'Missing UTC interval'):
            make_forecast(self.target, 'persistence', mixed, self.now, self.origin)
        # The exact generation timestamp is eligible.
        self.assertEqual(daily_input(records(source, retrieved=self.now), source, self.now)[0], Decimal(1))

    def test_cutoff_boundary_and_timezone(self):
        self.assertEqual(live_target(self.origin, self.cutoff), (self.target, self.origin))
        live_target(self.origin - timedelta(microseconds=1), self.cutoff)
        with self.assertRaises(ForecastUnavailable):
            live_target(self.origin + timedelta(microseconds=1), self.cutoff)
        # Spring/autumn local 10:00 maps to different UTC clock times.
        self.assertEqual(cutoff_for(date(2026, 3, 30), self.cutoff).hour, 8)
        self.assertEqual(cutoff_for(date(2025, 10, 27), self.cutoff).hour, 9)
        # UTC date differs from Stockholm date near midnight.
        target, _ = live_target(datetime(2026, 11, 8, 23, 30, tzinfo=UTC), self.cutoff)
        self.assertEqual(target, date(2026, 11, 10))
        for value in ('25:00', '10:00+01:00', '9:00'):
            with self.assertRaises(ForecastUnavailable):
                cutoff_time(value)

    def test_custom_cutoff_dst_policy(self):
        cutoff = cutoff_time('02:30')
        with self.assertRaisesRegex(ForecastUnavailable, 'nonexistent'):
            cutoff_for(date(2026, 3, 30), cutoff)
        self.assertEqual(cutoff_for(date(2025, 10, 27), cutoff),
                         datetime(2025, 10, 26, 0, 30, tzinfo=UTC))

    def test_snapshot_keeps_exact_decimal_and_retrieval_version(self):
        day = self.target - timedelta(days=1)
        data = records(day, '0.123456789012345678901234567890123456')
        _, _, snapshot = daily_input(data, day)
        self.assertIn('0.123456789012345678901234567890123456', snapshot)
        self.assertEqual(daily_input(records(day, '1.0'), day)[2], daily_input(records(day, '1.00'), day)[2])
        changed = records(day, retrieved=self.now)
        first = self.forecast()
        later_version = make_forecast(self.target, 'persistence', changed, self.now, self.origin)
        self.assertNotEqual(first.input_fingerprint, later_version.input_fingerprint)

    def test_actual_errors_and_pending(self):
        forecast = self.forecast()
        for prediction, actual, error in [('-1', '0', '-1'), ('0', '-2', '2'), ('0', '0', '0')]:
            result = evaluate(replace(forecast, prediction=Decimal(prediction)), records(self.target, actual))
            self.assertEqual(result['signed_error'], Decimal(error))
            self.assertEqual(result['absolute_error'], abs(Decimal(error)))
        self.assertEqual(evaluate(forecast, [])['status'], 'Pending')
        self.assertEqual(evaluate(forecast, records(self.target)[:-1])['status'], 'Pending')
        self.assertEqual(forecast.prediction, Decimal(1))

    def test_fair_mae_common_dates(self):
        rows = [
            {'method': 'persistence', 'target_date': date(2026, 1, 1), 'absolute_error': Decimal(1)},
            {'method': 'persistence', 'target_date': date(2026, 1, 2), 'absolute_error': Decimal(9)},
            {'method': 'weekly_seasonal', 'target_date': date(2026, 1, 1), 'absolute_error': Decimal(2)},
            {'method': 'weekly_seasonal', 'target_date': date(2026, 1, 3), 'absolute_error': None},
        ]
        summary = performance(rows)
        self.assertEqual(summary[0]['mae'], Decimal(5))
        self.assertEqual(summary[0]['common_mae'], Decimal(1))
        self.assertEqual(summary[1]['common_mae'], Decimal(2))
        self.assertEqual(summary[0]['common_sample_size'], 1)
        self.assertIsNone(performance(rows[:2])[0]['common_mae'])

    def test_immutable_idempotent_save_and_kind_separation(self):
        connection = MemoryConnection()
        first = self.forecast()
        saved = save_forecast(connection, first, 'live')
        changed = replace(first, prediction=Decimal(5), source_average=Decimal(5), generated_at=self.now + timedelta(minutes=1),
                          input_fingerprint='f' * 64)
        rerun = save_forecast(connection, changed, 'live')
        self.assertEqual(rerun, saved)
        self.assertEqual(len(load_forecasts(connection, 'live')), 1)
        historical = replace(first, target_fingerprint='a' * 64)
        save_forecast(connection, historical, 'backtest')
        save_forecast(connection, historical, 'backtest')
        self.assertEqual(len(load_forecasts(connection, 'backtest')), 1)
        save_forecast(connection, replace(historical, input_fingerprint='b' * 64), 'backtest')
        self.assertEqual(len(load_forecasts(connection, 'backtest')), 2)
        self.assertEqual(len(load_forecasts(connection, 'live')), 1)

    def test_generate_refuses_stored_target_and_after_cutoff(self):
        connection = MemoryConnection()
        with patch('electricity_radar.forecast_db.read_prices', return_value=records(self.target)):
            with self.assertRaisesRegex(ForecastUnavailable, 'target-day prices'):
                generate_live(connection, self.cutoff, lambda: self.now)
        with patch('electricity_radar.forecast_db.read_prices', return_value=[]):
            with self.assertRaisesRegex(ForecastUnavailable, 'after cutoff'):
                generate_live(connection, self.cutoff, lambda: self.origin + timedelta(seconds=1))
        self.assertFalse(connection.tables['live_forecasts'])

    def test_generate_uses_current_time_and_returns_existing(self):
        connection = MemoryConnection()
        def read(connection, area, first, last):
            return [] if first == self.target else records(first)
        with patch('electricity_radar.forecast_db.read_prices', side_effect=read):
            saved, unavailable = generate_live(connection, self.cutoff, lambda: self.origin)
        self.assertEqual(len(saved), 2)
        self.assertFalse(unavailable)
        self.assertTrue(all(r.generated_at == self.origin for r in saved))
        with patch('electricity_radar.forecast_db.read_prices', side_effect=AssertionError('Must not recompute')):
            rerun, _ = generate_live(connection, self.cutoff, lambda: self.origin + timedelta(hours=1))
        self.assertEqual(set(r.generated_at for r in rerun), {self.origin})

    def test_unavailable_baseline_is_not_substituted(self):
        connection = MemoryConnection()
        def read(connection, area, first, last):
            return records(first) if first == self.target - timedelta(days=1) else []
        with patch('electricity_radar.forecast_db.read_prices', side_effect=read):
            saved, unavailable = generate_live(connection, self.cutoff, lambda: self.now)
        self.assertEqual([r.method for r in saved], ['persistence'])
        self.assertEqual(len(unavailable), 1)
        self.assertIn('weekly_seasonal', unavailable[0])

    def test_historical_assumptions_missing_target_and_changed_inputs(self):
        connection = MemoryConnection()
        # Today's retrieval time is later than the simulated origin; backtests allow this.
        retrieved = self.now + timedelta(days=1)
        with patch('electricity_radar.forecast_db.read_prices', side_effect=lambda c, a, f, l: records(f, retrieved=retrieved)):
            saved, skipped = run_backtest_day(connection, self.target, self.cutoff, lambda: retrieved)
            rerun, _ = run_backtest_day(connection, self.target, self.cutoff, lambda: retrieved + timedelta(minutes=1))
        self.assertEqual(len(saved), 2)
        self.assertFalse(skipped)
        self.assertEqual(saved, rerun)
        self.assertEqual(saved[0].generated_at, retrieved)
        self.assertEqual(saved[0].cutoff_at, self.origin)
        self.assertFalse(connection.tables['live_forecasts'])
        with patch('electricity_radar.forecast_db.read_prices', side_effect=lambda c, a, f, l: [] if f == self.target else records(f)):
            saved, skipped = run_backtest_day(connection, self.target, self.cutoff, lambda: retrieved)
        self.assertFalse(saved)
        self.assertEqual(len(skipped), 2)
        first = self.forecast(historical=True, target_records=records(self.target))
        changed = self.forecast(historical=True, target_records=records(self.target, '2'))
        self.assertNotEqual(first.input_fingerprint, changed.input_fingerprint)
        self.assertNotEqual(first.target_fingerprint, changed.target_fingerprint)

    def test_evaluation_deduplicates_backtest_versions_and_flags_revisions(self):
        connection = MemoryConnection()
        first = self.forecast(historical=True, target_records=records(self.target))
        changed = replace(first, generated_at=self.now + timedelta(minutes=1), input_fingerprint='c' * 64)
        save_forecast(connection, first, 'backtest')
        save_forecast(connection, changed, 'backtest')
        with patch('electricity_radar.forecast_db.read_prices', return_value=records(self.target, '2')):
            result = evaluated_results(connection, 'backtest')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['generated_at'], changed.generated_at)
        self.assertIn('revised', result[0]['status'])
