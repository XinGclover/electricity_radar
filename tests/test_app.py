"""Exercise UI states with synthetic rows and mocked database access only."""

import unittest
from contextlib import nullcontext
from pathlib import Path
from datetime import date, datetime, timezone
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from electricity_radar.db import DatabaseError
from test_prices import fixture


class AppTests(unittest.TestCase):
    def setUp(self):
        bounds = patch('electricity_radar.db.available_dates', return_value=(date(2025, 10, 26), date(2025, 10, 26)))
        self.bounds = bounds.start()
        self.addCleanup(bounds.stop)
        results = patch('electricity_radar.forecast_db.evaluated_results', return_value=[])
        self.results = results.start()
        self.addCleanup(results.stop)

    def test_missing_configuration(self):
        with patch('electricity_radar.db.connect', side_effect=DatabaseError('Configure PG variables using .env.example.')):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox[0].value, 'SE3')
        self.assertIn('Configure PG variables', app.info[0].value)

    def test_empty_database(self):
        self.bounds.return_value = (None, None)
        with patch('electricity_radar.db.connect', return_value=nullcontext(None)), \
                patch('electricity_radar.db.read_prices', return_value=[]):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertIn('No stored prices', app.info[0].value)

    def test_complete_and_incomplete_days(self):
        day = date(2025, 10, 26)
        raw = [(r.start, r.end, r.price, datetime.now(timezone.utc)) for r in fixture(day)]
        with patch('electricity_radar.db.connect', side_effect=lambda: nullcontext(None)), \
                patch('electricity_radar.db.read_prices', return_value=raw) as read:
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
            app.date_input[0].set_value(day)
            app.date_input[1].set_value((day, day))
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.metric), 3)
            self.assertIn('-25.00', app.metric[0].value)
            read.return_value = raw[:-1]
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.metric), 0)
            self.assertIn('Incomplete', app.warning[0].value)

    def test_date_defaults_and_area_switch(self):
        self.bounds.side_effect = lambda c, area: (None, None) if area == 'SE1' else (date(2025, 10, 20), date(2025, 11, 5))
        with patch('electricity_radar.db.connect', side_effect=lambda: nullcontext(None)), \
                patch('electricity_radar.db.read_prices', return_value=[]):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
            self.assertEqual(app.date_input[0].value, date(2025, 11, 5))
            self.assertEqual(app.date_input[1].value, (date(2025, 10, 30), date(2025, 11, 5)))
            app.selectbox[0].set_value('SE1').run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.date_input), 0)
            self.assertTrue(any('No stored prices for this area' in item.value for item in app.info))
            app.selectbox[0].set_value('SE3').run()
            self.assertFalse(app.exception)
            self.assertEqual(app.date_input[0].value, date(2025, 11, 5))

    def test_forecast_views_and_errors(self):
        from decimal import Decimal
        row = {'target_date': date(2025, 11, 5), 'method': 'persistence', 'version': '1',
               'prediction': Decimal('0'), 'actual': Decimal('-1'), 'signed_error': Decimal('1'),
               'absolute_error': Decimal('1'), 'status': 'Evaluated',
               'generated_at': datetime(2025, 11, 4, 8, tzinfo=timezone.utc),
               'cutoff_at': datetime(2025, 11, 4, 9, tzinfo=timezone.utc)}
        self.results.return_value = [row, dict(row, method='weekly_seasonal')]
        with patch('electricity_radar.db.connect', side_effect=lambda: nullcontext(None)), \
                patch('electricity_radar.db.read_prices', return_value=[]):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual(self.results.call_args.args[1], 'live')
            self.assertEqual(app.dataframe[0].value['Signed error (öre/kWh)'][0], 100)
            self.assertIn('09:00:00+01:00', app.dataframe[0].value['Generation time'][0])
            app.selectbox[1].set_value('Historical backtest').run()
            self.assertFalse(app.exception)
            self.assertEqual(self.results.call_args.args[1], 'backtest')
            self.assertTrue(any('not forecasts issued in the past' in item.value for item in app.info))
