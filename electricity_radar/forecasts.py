"""Daily baseline calculations and evaluation, independent of database access."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from electricity_radar.prices import Interval, STOCKHOLM, UTC, validate_day, weighted_average

METHODS = {'persistence': 1, 'weekly_seasonal': 7}
VERSION = '1'


class ForecastUnavailable(ValueError):
    """A forecast cannot be issued with the available inputs or timing."""


@dataclass(frozen=True)
class Forecast:
    target_date: date
    method: str
    prediction: Decimal
    generated_at: datetime
    cutoff_at: datetime
    source_date: date
    source_average: Decimal
    source_latest_retrieval: datetime
    input_fingerprint: str
    input_snapshot: str
    target_fingerprint: str | None = None
    area: str = 'SE3'
    version: str = VERSION


def cutoff_time(value):
    """Accept a local clock time only; the zone is always Europe/Stockholm."""
    try:
        if len(value) != 5 or value[2] != ':':
            raise ValueError
        return datetime.strptime(value, '%H:%M').time()
    except (ValueError, TypeError):
        raise ForecastUnavailable('FORECAST_CUTOFF must be HH:MM in Europe/Stockholm (default 10:00).') from None


def cutoff_for(target, cutoff):
    local = datetime.combine(target - timedelta(days=1), cutoff, STOCKHOLM)
    boundary = local.astimezone(UTC)
    # fold=0 chooses the first occurrence of an ambiguous autumn clock time.
    if boundary.astimezone(STOCKHOLM).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ForecastUnavailable('Configured cutoff falls in a nonexistent Stockholm spring DST hour; choose another cutoff.')
    return boundary


def live_target(now, cutoff):
    if now.utcoffset() is None:
        raise ForecastUnavailable('Generation time must be timezone-aware')
    target = now.astimezone(STOCKHOLM).date() + timedelta(days=1)
    boundary = cutoff_for(target, cutoff)
    if now.astimezone(UTC) > boundary:
        raise ForecastUnavailable(f'Live generation refused after cutoff {boundary.astimezone(STOCKHOLM).isoformat()}')
    return target, boundary


def price_text(value):
    """Canonical exact decimal text without context-dependent rounding."""
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def fingerprint(snapshot):
    return hashlib.sha256(snapshot.encode()).hexdigest()


def daily_input(records, day, available_at=None):
    """Validate the full day, filtering later retrievals only for live forecasts."""
    selected = [r for r in records if available_at is None
                or r[3].astimezone(UTC) <= available_at.astimezone(UTC)]
    rows = validate_day([Interval(r[0], r[1], r[2]) for r in selected], day)
    # Include every retrieval time so the snapshot is a reproducible input version.
    snapshot = json.dumps([
        {'start': r[0].astimezone(UTC).isoformat(), 'end': r[1].astimezone(UTC).isoformat(),
         'price_sek_kwh': price_text(r[2]),
         'retrieved_at': r[3].astimezone(UTC).isoformat()}
        for r in sorted(selected, key=lambda r: r[0].astimezone(UTC))
    ], sort_keys=True, separators=(',', ':'))
    return weighted_average(rows), max(r[3].astimezone(UTC) for r in selected), snapshot


def make_forecast(target, method, records, generated_at, cutoff_at, *, historical=False, target_records=None):
    source_day = target - timedelta(days=METHODS[method])
    average, latest, snapshot = daily_input(records, source_day, None if historical else generated_at)
    target_fp = None
    if historical:
        _, _, target_snapshot = daily_input(target_records, target)
        target_fp = fingerprint(target_snapshot)
    identity = json.dumps({'source': fingerprint(snapshot), 'target': target_fp,
                           'origin': cutoff_at.isoformat()}, sort_keys=True)
    return Forecast(target, method, average, generated_at, cutoff_at, source_day,
                    average, latest, fingerprint(identity), snapshot, target_fp)


def evaluate(forecast, records):
    result = {'target_date': forecast.target_date, 'method': forecast.method,
              'version': forecast.version, 'prediction': forecast.prediction,
              'generated_at': forecast.generated_at, 'cutoff_at': forecast.cutoff_at,
              'actual': None, 'signed_error': None, 'absolute_error': None, 'status': 'Pending'}
    try:
        actual, _, snapshot = daily_input(records, forecast.target_date)
    except ValueError:
        return result
    error = forecast.prediction - actual
    result.update(actual=actual, signed_error=error, absolute_error=abs(error), status='Evaluated')
    if forecast.target_fingerprint and fingerprint(snapshot) != forecast.target_fingerprint:
        result['status'] = 'Evaluated (target input version revised since backtest)'
    return result


def performance(results):
    """Per-method MAE plus a fair ranking on common evaluated target dates."""
    groups = {method: {} for method in METHODS}
    for row in results:
        if row['absolute_error'] is not None:
            groups[row['method']][row['target_date']] = row['absolute_error']
    summary = []
    common = set.intersection(*(set(values) for values in groups.values()))
    for method, values in groups.items():
        summary.append({'method': method, 'sample_size': len(values),
                        'mae': sum(values.values(), Decimal(0)) / len(values) if values else None,
                        'common_sample_size': len(common),
                        'common_mae': sum((values[d] for d in common), Decimal(0)) / len(common) if common else None})
    return summary
