"""Read-only historical Swedish electricity prices from local PostgreSQL."""

from collections import defaultdict
from datetime import datetime, timedelta

import streamlit as st

from electricity_radar.db import DatabaseError, connect, read_prices
from electricity_radar.prices import (
    AREAS, FIRST_DATE, STOCKHOLM, Interval, hourly_averages, ore,
    validate_day, weighted_average,
)

st.set_page_config(page_title='Electricity Radar', page_icon='⚡')
st.title('Electricity Radar')
st.write('Explore published Swedish day-ahead prices. Forecasting and evaluation will be added incrementally.')
st.caption('Prices in öre/kWh, excluding VAT, energy tax, grid fees and retailer markups. Times: Europe/Stockholm.')
st.markdown('Prices provided by [Elpriset just nu.se](https://www.elprisetjustnu.se/elpris-api). '
            'Currency conversion can cause differences from official prices.')

area = st.selectbox('Bidding zone', AREAS, index=2)
today = datetime.now(STOCKHOLM).date()
selected = st.date_input('Delivery date', today, min_value=FIRST_DATE, max_value=today + timedelta(days=1))
selected_range = st.date_input('Daily-average date range', (today - timedelta(days=6), today),
                             min_value=FIRST_DATE, max_value=today + timedelta(days=1))
if len(selected_range) != 2:
    st.info('Select both a start and an end date for the trend.')
    st.stop()
first, last = selected_range
try:
    with connect() as connection:
        raw = read_prices(connection, area, min(first, selected), max(last, selected))
except DatabaseError as exc:
    st.info(str(exc))
    st.stop()

if not raw:
    st.info('No stored prices for these dates. Run the ingestion command described in README.md.')
    st.stop()
st.caption('Latest retrieval in the queried data: ' + max(r[3] for r in raw).astimezone(STOCKHOLM).isoformat())
by_day = defaultdict(list)
for start, end, price, retrieved in raw:
    by_day[start.astimezone(STOCKHOLM).date()].append(Interval(start, end, price))
complete = {}
incomplete = []
for day, rows in by_day.items():
    try:
        complete[day] = validate_day(rows, day)
    except ValueError:
        incomplete.append(day.isoformat())
if incomplete:
    st.warning('Incomplete or invalid days (excluded from full-day metrics and trends): ' + ', '.join(incomplete))

st.subheader('Intraday prices — hourly averages')
if selected in complete:
    rows = complete[selected]
    columns = st.columns(3)
    for column, label, value in zip(columns, ['Daily average', 'Minimum native interval', 'Maximum native interval'],
                                    [weighted_average(rows), min(r.price for r in rows), max(r.price for r in rows)]):
        column.metric(label, f'{ore(value):.2f} öre/kWh')
else:
    st.info('No complete day available for the selected delivery date. Full-day metrics are excluded.')
rows = by_day.get(selected, [])
# Only validated full days are charted, so missing intervals cannot be bridged silently.
if selected in complete:
    values = [{'hour': r.start.astimezone(STOCKHOLM).strftime('%H:%M %z'), 'price': float(ore(r.price))}
              for r in hourly_averages(rows, selected)]
    st.vega_lite_chart({'data': {'values': values}, 'mark': {'type': 'line', 'point': True},
                       'encoding': {'x': {'field': 'hour', 'type': 'ordinal', 'sort': None,
                                          'title': 'Hour (Europe/Stockholm, UTC offset)'},
                                    'y': {'field': 'price', 'type': 'quantitative', 'title': 'öre/kWh'}}},
                      width='stretch')

st.subheader('Daily-average trend')
trend = []
day = first
missing = []
while day <= last:
    rows = complete.get(day)
    trend.append({'date': day.isoformat(), 'price': float(ore(weighted_average(rows))) if rows else None})
    if not rows:
        missing.append(day.isoformat())
    day += timedelta(days=1)
if missing:
    st.info('Missing or incomplete trend days: ' + ', '.join(missing))
if any(r['price'] is not None for r in trend):
    st.vega_lite_chart({'data': {'values': trend}, 'mark': {'type': 'line', 'point': True, 'invalid': 'break-paths-show-domains'},
                       'encoding': {'x': {'field': 'date', 'type': 'ordinal', 'title': 'Local delivery date'},
                                    'y': {'field': 'price', 'type': 'quantitative', 'title': 'Daily average (öre/kWh)'}}},
                      width='stretch')
else:
    st.info('No complete days in the selected trend range.')
