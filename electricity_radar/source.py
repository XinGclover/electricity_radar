"""Small, paced API client. No database side effects."""

import json
import time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from electricity_radar.prices import parse_day


class UnavailableError(Exception):
    pass


class FetchError(Exception):
    pass


def fetch_day(day, area):
    url = f'https://www.elprisetjustnu.se/api/v1/prices/{day:%Y/%m-%d}_{area}.json'
    for attempt in range(3):
        time.sleep(1 if attempt == 0 else 2 ** attempt)
        try:
            request = Request(url, headers={'User-Agent': 'ElectricityRadar/0.1', 'Accept': 'application/json'})
            with urlopen(request, timeout=20) as response:
                if response.status == 204:
                    raise UnavailableError('No published data (HTTP 204)')
                payload = json.loads(response.read(), parse_float=Decimal)
            return parse_day(payload, day)
        except HTTPError as exc:
            if exc.code in (404, 204):
                raise UnavailableError('No published data at the requested URL (HTTP 404/204)') from None
            if exc.code != 429 and exc.code < 500:
                raise FetchError(f'Request rejected (HTTP {exc.code})') from None
        except (URLError, TimeoutError, OSError):
            pass
        except (ValueError, UnicodeError) as exc:
            raise FetchError(f'Source-data error from elprisetjustnu ({day} {area}): {exc}') from None
    raise FetchError('Request failed after 3 attempts (network error, rate limit or server error)')
