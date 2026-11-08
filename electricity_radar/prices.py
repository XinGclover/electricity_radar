"""Price validation and duration-weighted calculations, independent of I/O."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo('Europe/Stockholm')
UTC = timezone.utc
AREAS = ('SE1', 'SE2', 'SE3', 'SE4')
FIRST_DATE = date(2022, 11, 1)
SOURCE = 'elprisetjustnu'


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    price: Decimal


def day_bounds(day):
    """Return actual local midnight boundaries as UTC instants."""
    return tuple(datetime.combine(d, time(), STOCKHOLM).astimezone(UTC)
                 for d in (day, day + timedelta(days=1)))


def validate_day(intervals, day):
    """Validate native intervals in UTC; never repair source timestamps."""
    start, end = day_bounds(day)
    step = timedelta(minutes=15 if day >= date(2025, 10, 1) else 60)
    rows = []
    for index, row in enumerate(intervals, 1):
        if row.start.utcoffset() is None or row.end.utcoffset() is None:
            raise ValueError(f'Record {index}: timezone offsets are required')
        rows.append(Interval(row.start.astimezone(UTC), row.end.astimezone(UTC), row.price))
    if not rows:
        raise ValueError(f'Empty daily response; missing UTC interval [{start.isoformat()}, {end.isoformat()})')
    rows.sort(key=lambda row: row.start)
    cursor = start
    seen = set()
    errors = []
    for row in rows:
        label = f'UTC interval [{row.start.isoformat()}, {row.end.isoformat()})'
        if row.start in seen:
            errors.append(f'Duplicate interval start: {row.start.isoformat()}')
        seen.add(row.start)
        if not row.price.is_finite():
            errors.append(f'Non-finite price at {label}')
        duration = row.end - row.start
        if duration != step:
            errors.append(f'Invalid elapsed duration at {label}: '
                          f'{duration.total_seconds():g} seconds; expected {step.total_seconds():g}')
        if row.start < start or row.end > end:
            errors.append(f'Outside local delivery day {day}: {label}')
        if row.start > cursor:
            errors.append(f'Missing UTC interval [{cursor.isoformat()}, {row.start.isoformat()})')
        elif row.start < cursor:
            errors.append(f'Overlapping {label}; coverage already extends to {cursor.isoformat()}')
        if row.end > row.start:
            cursor = max(cursor, row.end)
    if cursor < end:
        errors.append(f'Missing UTC interval [{cursor.isoformat()}, {end.isoformat()})')
    if errors:
        raise ValueError('; '.join(errors))
    return rows


def parse_day(payload, day):
    if not isinstance(payload, list):
        raise ValueError('Expected a JSON list')
    rows = []
    try:
        for item in payload:
            value = item['SEK_per_kWh']
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise ValueError('Expected a numeric price')
            start = datetime.fromisoformat(item['time_start'])
            end = datetime.fromisoformat(item['time_end'])
            if start.utcoffset() is None or end.utcoffset() is None:
                raise ValueError('Timezone offsets are required')
            rows.append(Interval(start.astimezone(UTC), end.astimezone(UTC), Decimal(str(value))))
    except (KeyError, TypeError, InvalidOperation, OverflowError) as exc:
        raise ValueError('Malformed price response') from exc
    return validate_day(rows, day)


def ore(value):
    return value * Decimal(100)


def weighted_average(rows):
    durations = [Decimal(str((r.end - r.start).total_seconds())) for r in rows]
    if not durations or sum(durations) <= 0:
        raise ValueError('No positive-duration intervals')
    return sum((r.price * d for r, d in zip(rows, durations)), Decimal(0)) / sum(durations)


def hourly_averages(rows, day):
    """Bucket by UTC instants so repeated Stockholm hours remain distinct."""
    cursor, end = day_bounds(day)
    result = []
    while cursor < end:
        stop = min(cursor + timedelta(hours=1), end)
        pieces = [Interval(max(r.start, cursor), min(r.end, stop), r.price)
                  for r in rows if r.start < stop and r.end > cursor]
        if pieces:
            result.append(Interval(cursor, stop, weighted_average(pieces)))
        cursor = stop
    return result
