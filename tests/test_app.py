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
    def test_missing_configuration(self):
        with patch('electricity_radar.db.connect', side_effect=DatabaseError('Configure PG variables using .env.example.')):
            app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / 'app.py'), default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox[0].value, 'SE3')
        self.assertIn('Configure PG variables', app.info[0].value)

    def test_empty_database(self):
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
