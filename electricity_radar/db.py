"""Explicit PostgreSQL configuration and parameterized data access; never DDL."""

import os
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from electricity_radar.prices import SOURCE, UTC, day_bounds, validate_day


class DatabaseError(Exception):
    pass


def connect():
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
    required = ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'PGSSLMODE')
    if any(not os.environ.get(key) for key in required):
        raise DatabaseError('Configure all PG variables using .env.example in a project-root .env or the environment.')
    try:
        connection = psycopg.connect(connect_timeout=5, autocommit=True)
        connection.execute("SET TIME ZONE 'UTC'")
        return connection
    except psycopg.Error:
        raise DatabaseError('Cannot connect to PostgreSQL. Check PG settings and that the server and database exist.') from None


def database_message(exc):
    if isinstance(exc, (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName)):
        return 'Tables are missing. Run sql/001_create_price_tables.sql manually against electricity_radar.'
    return 'Database operation failed. Check connectivity, table setup and access permissions.'


def write_day(connection, area, day, rows):
    rows = validate_day(rows, day)
    start, end = day_bounds(day)
    retrieved = datetime.now(UTC)
    try:
        with connection.transaction():
            # Serialize cooperating ingestions, including already stored overlap checks.
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (SOURCE + area,))
            existing = connection.execute(
                'SELECT delivery_start, delivery_end FROM market.prices '
                'WHERE area=%s AND source=%s AND delivery_start < %s AND delivery_end > %s',
                (area, SOURCE, end, start)).fetchall()
            wanted = {(r.start, r.end) for r in rows}
            if any(tuple(pair) not in wanted for pair in existing):
                raise DatabaseError('Stored intervals conflict with this response; inspect the affected day manually.')
            with connection.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO market.prices '
                    '(area, delivery_start, delivery_end, price_sek_kwh, source, retrieved_at) '
                    'VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (area,source,delivery_start) '
                    'DO UPDATE SET price_sek_kwh=EXCLUDED.price_sek_kwh, retrieved_at=EXCLUDED.retrieved_at',
                    [(area, r.start, r.end, r.price, SOURCE, retrieved) for r in rows])
        return len(rows)
    except psycopg.Error as exc:
        raise DatabaseError(database_message(exc)) from None


def read_prices(connection, area, first, last):
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    try:
        return connection.execute(
            'SELECT delivery_start, delivery_end, price_sek_kwh, retrieved_at '
            'FROM market.prices WHERE area=%s AND source=%s '
            'AND delivery_start < %s AND delivery_end > %s ORDER BY delivery_start',
            (area, SOURCE, end, start)).fetchall()
    except psycopg.Error as exc:
        raise DatabaseError(database_message(exc)) from None


def available_dates(connection, area):
    """Bounds of stored local delivery dates, including incomplete days."""
    try:
        return connection.execute(
            "SELECT min((delivery_start AT TIME ZONE 'Europe/Stockholm')::date), "
            "max((delivery_start AT TIME ZONE 'Europe/Stockholm')::date) "
            'FROM market.prices WHERE area=%s AND source=%s', (area, SOURCE)).fetchone()
    except psycopg.Error as exc:
        raise DatabaseError(database_message(exc)) from None
