"""Read-only price history, daily baseline forecasts and evaluation."""

from collections import defaultdict
from datetime import timedelta

import streamlit as st

from electricity_radar.db import DatabaseError, available_dates, connect, read_prices
from electricity_radar.forecast_db import evaluated_results
from electricity_radar.forecasts import performance
from electricity_radar.prices import (
    AREAS, STOCKHOLM, Interval, hourly_averages, ore, validate_day, weighted_average,
)


def show_history(connection, area):
    earliest, latest = available_dates(connection, area)
    if latest is None:
        st.info('No stored prices for this area. Run the ingestion command described in README.md.')
        return
    selected = st.date_input('Delivery date', latest, min_value=earliest, max_value=latest, key=f'delivery_{area}')
    selected_range = st.date_input('Daily-average date range', (max(earliest, latest - timedelta(days=6)), latest),
                                 min_value=earliest, max_value=latest, key=f'range_{area}')
    if len(selected_range) != 2:
        st.info('Select both a start and an end date for the trend.')
        return
    first, last = selected_range
    raw = read_prices(connection, area, min(first, selected), max(last, selected))
    if not raw:
        st.info('No stored prices for these dates. Run the ingestion command described in README.md.')
        return
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


def show_forecasts(connection):
    st.header('SE3 daily-average baselines and evaluation')
    view = st.selectbox('Forecast view', ['Live forecasts', 'Historical backtest'])
    kind = 'live' if view == 'Live forecasts' else 'backtest'
    st.caption('Read-only view. Prediction and error units: öre/kWh. Signed error = prediction − published actual.')
    if kind == 'live':
        st.info('Live forecasts use the project-chosen cutoff on the preceding day (default 10:00 Stockholm). '
                'No target prices in our database does not prove that prices are unpublished externally.')
    else:
        st.info('Historical backtest — not forecasts issued in the past. Assumes prior-day and prior-week '
                'published prices were available at the simulated origin. Today’s retrieval times do not prove past availability.')
        st.caption('Latest calculation per target date and method is shown; all input versions remain stored. '
                   'Actuals are recalculated from current stored prices; revisions are flagged.')
    try:
        results = evaluated_results(connection, kind)
    except DatabaseError as exc:
        st.info(str(exc))
        return
    if not results:
        st.info(f'No {view.lower()} records. Use the manual commands in README.md.')
        return
    table = []
    for row in results:
        item = {'Target date': row['target_date'].isoformat(), 'Method': row['method'], 'Version': row['version'],
                'Status': row['status'],
                'Generation time' if kind == 'live' else 'Calculation time': row['generated_at'].astimezone(STOCKHOLM).isoformat(),
                'Cutoff' if kind == 'live' else 'Simulated origin': row['cutoff_at'].astimezone(STOCKHOLM).isoformat()}
        for field, label in [('prediction', 'Predicted average'), ('actual', 'Published actual average'),
                             ('signed_error', 'Signed error'), ('absolute_error', 'Absolute error')]:
            item[label + ' (öre/kWh)'] = float(ore(row[field])) if row[field] is not None else None
        table.append(item)
    st.dataframe(table, hide_index=True, width='stretch')
    summaries = performance(results)
    st.subheader('Mean absolute error')
    st.dataframe([{'Method': r['method'], 'Evaluated n': r['sample_size'],
                   'MAE (öre/kWh)': float(ore(r['mae'])) if r['mae'] is not None else None}
                  for r in summaries], hide_index=True)
    common_n = summaries[0]['common_sample_size']
    if common_n:
        ranking = sorted(summaries, key=lambda r: r['common_mae'])
        st.caption(f'Fair comparison: both methods evaluated on the same {common_n} target date(s).')
        st.dataframe([{'Method': r['method'], 'Common-date MAE (öre/kWh)': float(ore(r['common_mae'])),
                       'Common evaluated n': common_n} for r in ranking], hide_index=True)
    else:
        st.info('No common evaluated target dates for both methods; no performance ranking is available.')
    if common_n < 30:
        st.info('The common evaluated sample is small (fewer than 30 dates). These descriptive results are too limited for firm conclusions.')
    values, actual_dates = [], set()
    for row in results:
        if row['actual'] is None:
            continue
        day = row['target_date'].isoformat()
        values.append({'date': day, 'series': row['method'], 'price': float(ore(row['prediction']))})
        if day not in actual_dates:
            actual_dates.add(day)
            values.append({'date': day, 'series': 'Published actual', 'price': float(ore(row['actual']))})
    if values:
        st.subheader('Daily-average comparison')
        st.vega_lite_chart({'data': {'values': values}, 'mark': {'type': 'line', 'point': True},
                           'encoding': {'x': {'field': 'date', 'type': 'ordinal', 'title': 'Target delivery date'},
                                        'y': {'field': 'price', 'type': 'quantitative', 'title': 'Daily average (öre/kWh)'},
                                        'color': {'field': 'series', 'type': 'nominal'}}}, width='stretch')
    else:
        st.info('Forecasts are pending until complete target-day published prices are stored.')


st.set_page_config(page_title='Electricity Radar', page_icon='⚡')
st.title('Electricity Radar')
st.write('Explore published Swedish day-ahead prices and SE3 next-day daily-average baseline forecasts.')
st.caption('Prices in öre/kWh, excluding VAT, energy tax, grid fees and retailer markups. Times: Europe/Stockholm.')
st.markdown('Prices provided by [Elpriset just nu.se](https://www.elprisetjustnu.se/elpris-api). '
            'Currency conversion can cause differences from official prices.')
area = st.selectbox('Bidding zone', AREAS, index=2)
try:
    with connect() as connection:
        try:
            show_history(connection, area)
        except DatabaseError as exc:
            st.info(str(exc))
        show_forecasts(connection)
except DatabaseError as exc:
    st.info(str(exc))
