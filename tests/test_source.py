"""Offline checks for unavailable dates, rejected requests and bounded retries."""

import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from electricity_radar.source import FetchError, UnavailableError, fetch_day


class SourceTests(unittest.TestCase):
    @patch('electricity_radar.source.time.sleep')
    def test_unavailable(self, sleep):
        with patch('electricity_radar.source.urlopen', side_effect=HTTPError('url', 404, '', {}, None)) as request:
            with self.assertRaises(UnavailableError):
                fetch_day(date(2025, 10, 26), 'SE3')
            self.assertEqual(request.call_count, 1)

    @patch('electricity_radar.source.time.sleep')
    def test_forbidden_is_failure(self, sleep):
        with patch('electricity_radar.source.urlopen', side_effect=HTTPError('url', 403, '', {}, None)) as request:
            with self.assertRaisesRegex(FetchError, '403'):
                fetch_day(date(2025, 10, 26), 'SE3')
            self.assertEqual(request.call_count, 1)

    @patch('electricity_radar.source.time.sleep')
    def test_bounded_network_retries(self, sleep):
        with patch('electricity_radar.source.urlopen', side_effect=URLError('offline')) as request:
            with self.assertRaises(FetchError):
                fetch_day(date(2025, 10, 26), 'SE3')
            self.assertEqual(request.call_count, 3)
            self.assertEqual(request.call_args.kwargs['timeout'], 20)

    @patch('electricity_radar.source.time.sleep')
    def test_malformed_provider_interval_is_reported_as_source_data_error(self, sleep):
        payload = [{'time_start': '2025-10-26T02:45:00+02:00',
                    'time_end': '2025-10-26T03:00:00+01:00', 'SEK_per_kWh': 1}]
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(payload).encode()
        with patch('electricity_radar.source.urlopen') as request:
            request.return_value.__enter__.return_value = response
            with self.assertRaises(FetchError) as error:
                fetch_day(date(2025, 10, 26), 'SE3')
        self.assertIn('Source-data error from elprisetjustnu (2025-10-26 SE3)', str(error.exception))
        self.assertIn('4500 seconds; expected 900', str(error.exception))
        self.assertEqual(request.call_count, 1)
