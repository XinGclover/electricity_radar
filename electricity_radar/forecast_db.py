"""Forecast persistence and orchestration; no automatic DDL or external fetching."""

import json
import os
from datetime import datetime, timedelta

import psycopg
from psycopg import sql

from electricity_radar.db import DatabaseError, read_prices
from electricity_radar.forecasts import (
    Forecast, ForecastUnavailable, METHODS, VERSION, cutoff_for, cutoff_time,
    evaluate, live_target, make_forecast,
)
from electricity_radar.prices import SOURCE, STOCKHOLM, UTC


def settings_cutoff():
    # connect() already loads the project-root .env without overriding the environment.
    return cutoff_time(os.environ.get('FORECAST_CUTOFF', '10:00'))


def forecast_message(exc):
    if isinstance(exc, (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName)):
        return 'Forecast tables are missing. Run sql/002_create_forecast_tables.sql manually after 001.'
    return 'Forecast database operation failed. Check connectivity, migration setup and access permissions.'


def table_for(kind):
    if kind not in ('live', 'backtest'):
        raise ValueError('Unknown forecast kind')
    return 'live_forecasts' if kind == 'live' else 'backtests'


def load_forecasts(connection, kind, target=None):
    table = table_for(kind)
    generated, cutoff = ('generated_at', 'cutoff_at') if kind == 'live' else ('calculated_at', 'simulated_origin')
    query = sql.SQL('SELECT area,target_date,method,version,prediction,{},{},source_date,source_average,'
                    'source_latest_retrieval,input_fingerprint,input_snapshot{} '
                    'FROM market.{} WHERE area=%s AND version=%s').format(
        sql.Identifier(generated), sql.Identifier(cutoff),
        sql.SQL(',target_fingerprint' if kind == 'backtest' else ''), sql.Identifier(table))
    params = ['SE3', VERSION]
    if target is not None:
        query += sql.SQL(' AND target_date=%s')
        params.append(target)
    query += sql.SQL(' ORDER BY target_date,method,{} DESC,input_fingerprint').format(sql.Identifier(generated))
    try:
        records = connection.execute(query, params).fetchall()
    except psycopg.Error as exc:
        raise DatabaseError(forecast_message(exc)) from None
    result = []
    for r in records:
        result.append(Forecast(r[1], r[2], r[4], r[5], r[6], r[7], r[8], r[9], r[10],
                               json.dumps(r[11], sort_keys=True, separators=(',', ':')),
                               r[12] if kind == 'backtest' else None, r[0], r[3]))
    return result


def save_forecast(connection, forecast, kind):
    """Insert once, then return the saved row, including on a conflicting rerun."""
    table = table_for(kind)
    generated, cutoff = ('generated_at', 'cutoff_at') if kind == 'live' else ('calculated_at', 'simulated_origin')
    columns = ['area', 'target_date', 'method', 'version', 'prediction', generated, cutoff,
               'source_date', 'source_average', 'source_latest_retrieval', 'input_fingerprint', 'input_snapshot']
    values = [forecast.area, forecast.target_date, forecast.method, forecast.version, forecast.prediction,
              forecast.generated_at, forecast.cutoff_at, forecast.source_date, forecast.source_average,
              forecast.source_latest_retrieval, forecast.input_fingerprint, forecast.input_snapshot]
    if kind == 'backtest':
        columns.append('target_fingerprint')
        values.append(forecast.target_fingerprint)
    try:
        connection.execute(sql.SQL('INSERT INTO market.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(
            sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.SQL(',').join(sql.Placeholder() for _ in values)), values)
        candidates = load_forecasts(connection, kind, forecast.target_date)
        return next(r for r in candidates if r.method == forecast.method and
                    (kind == 'live' or r.input_fingerprint == forecast.input_fingerprint))
    except psycopg.Error as exc:
        raise DatabaseError(forecast_message(exc)) from None


def generate_live(connection, cutoff, clock=None):
    clock = clock or (lambda: datetime.now(UTC))
    try:
        with connection.transaction():
            # Same lock as price ingestion: cooperating writers cannot insert target
            # prices or revise source inputs between the checks and persistence.
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (SOURCE + 'SE3',))
            target = clock().astimezone(STOCKHOLM).date() + timedelta(days=1)
            existing = {r.method: r for r in load_forecasts(connection, 'live', target)}
            if len(existing) == len(METHODS):
                return list(existing.values()), []
            if read_prices(connection, 'SE3', target, target):
                raise ForecastUnavailable('Live generation refused: target-day prices are already stored.')
            inputs = {method: read_prices(connection, 'SE3', target - timedelta(days=lag), target - timedelta(days=lag))
                      for method, lag in METHODS.items() if method not in existing}
            generated = clock().astimezone(UTC)
            checked_target, boundary = live_target(generated, cutoff)
            if checked_target != target:
                raise ForecastUnavailable('Local date changed during generation; rerun the command.')
            saved = list(existing.values())
            unavailable = []
            for method, records in inputs.items():
                try:
                    forecast = make_forecast(target, method, records, generated, boundary)
                except ValueError as exc:
                    unavailable.append(f'{target} {method}: unavailable — {exc}')
                    continue
                saved.append(save_forecast(connection, forecast, 'live'))
            return saved, unavailable
    except psycopg.Error as exc:
        raise DatabaseError(forecast_message(exc)) from None


def run_backtest_day(connection, target, cutoff, clock=None):
    clock = clock or (lambda: datetime.now(UTC))
    saved, skipped = [], []
    try:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (SOURCE + 'SE3',))
            targets = read_prices(connection, 'SE3', target, target)
            for method, lag in METHODS.items():
                source = target - timedelta(days=lag)
                inputs = read_prices(connection, 'SE3', source, source)
                try:
                    forecast = make_forecast(target, method, inputs, clock().astimezone(UTC),
                                             cutoff_for(target, cutoff), historical=True, target_records=targets)
                except ValueError as exc:
                    skipped.append(f'{target} {method}: skipped — {exc}')
                    continue
                saved.append(save_forecast(connection, forecast, 'backtest'))
        return saved, skipped
    except psycopg.Error as exc:
        raise DatabaseError(forecast_message(exc)) from None


def evaluated_results(connection, kind):
    # Latest calculation per target/method only for backtest display. All input
    # versions remain stored; no duplicate versions are counted in MAE.
    saved = load_forecasts(connection, kind)
    latest = {}
    actuals = {}
    for forecast in saved:
        key = (forecast.target_date, forecast.method)
        if key in latest:
            continue
        if forecast.target_date not in actuals:
            actuals[forecast.target_date] = read_prices(connection, 'SE3', forecast.target_date, forecast.target_date)
        latest[key] = evaluate(forecast, actuals[forecast.target_date])
    return list(latest.values())
