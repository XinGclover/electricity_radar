# Electricity Radar

A Swedish electricity price app, starting with real published day-ahead prices
for SE1–SE4 in local PostgreSQL. The longer-term goal is to forecast next-day
SE3 prices before publication, preserve predictions, and evaluate against
published prices and simple baselines. Forecasting is not implemented yet.

The current increment provides explicit manual ingestion and a read-only
Streamlit history page. It does not create database objects automatically,
schedule jobs, or fetch external data from the UI.

## Local setup

Use Python 3.12 and a running local PostgreSQL server with `psql` available.
From the project root:

```sh
python3.12 -m venv .venv  # Only if .venv does not already exist
.venv/bin/python -m pip install -r requirements.txt
```

1. Create the database manually using your local PostgreSQL user (enter passwords
   at prompts, never put them in commands):

   ```sh
   createdb -h localhost -p 5432 -U your_postgres_user electricity_radar
   ```

2. Apply the schema and table SQL manually, connected to that database:

   ```sh
   psql -h localhost -p 5432 -U your_postgres_user -d electricity_radar -v ON_ERROR_STOP=1 -f sql/001_create_price_tables.sql
   ```

   The SQL is safe to rerun for initial setup; it does not migrate existing table
   definitions. No extension is required. The user needs schema/table privileges.

3. Create your local configuration:

   ```sh
   cp .env.example .env
   ```

   Edit all six PG values in `.env` for your local server. `.env` is ignored by
   Git. Existing environment variables take precedence. Never commit credentials.
   `PGSSLMODE=disable` is intended for local development only.

4. Ingest a small real sample:

   ```sh
   .venv/bin/python -m electricity_radar.ingest_prices --start-date 2025-10-26 --end-date 2025-10-26 --areas SE3
   ```

   Dates are inclusive delivery dates in Europe/Stockholm. Each area-day is
   validated and committed independently. Complete days are upserted without
   duplicates; repeated runs refresh prices and retrieval times. The written
   count includes updated records. Failed or unavailable area-days produce a
   nonzero exit code; completed days remain committed. Requests use 20-second
   timeouts, at most three attempts, and pacing of at least one second.

5. Backfill a supported range explicitly, preferably in small batches:

   ```sh
   .venv/bin/python -m electricity_radar.ingest_prices --start-date 2025-10-01 --end-date 2025-10-07 --areas SE1 SE2 SE3 SE4
   ```

   Increase these dates deliberately after checking the first batch. History is
   documented from 2022-11-01; availability is not guaranteed for every date.
   Future dates beyond tomorrow are rejected. Tomorrow may not be published yet.

6. Launch the app:

   ```sh
   .venv/bin/python -m streamlit run app.py
   ```

   Open the printed local URL. Stop with `Ctrl+C`. No shell activation is needed.

## Source and interpretation

Prices provided by [Elpriset just nu.se](https://www.elprisetjustnu.se), using
its [API documentation](https://www.elprisetjustnu.se/elpris-api), checked
2026-11-08. It documents SE1–SE4 coverage, history starting 2022-11-01,
and hourly data before 2025-10-01, then 15-minute data. Tomorrow's prices
arrive no earlier than 13:00 on the preceding day; this is not an availability
guarantee. DST days have their actual duration, not a fixed interval count.

`SEK_per_kWh` is stored as SEK/kWh excluding VAT, energy tax, grid fees and
retailer markups. The UI multiplies by 100 to show öre/kWh. Source data comes
from ENTSO-E with the provider's currency conversion; it can differ from official
prices. These are published prices, not model or baseline forecasts.

The provider describes the API as free for any use and requests/recommends
public attribution, included in the UI. No numeric rate limits or service-level
guarantee were found in the API documentation. Detailed upstream redistribution
rights and a separate formal license remain unverified. Respect provider changes
and avoid parallel or bulk request bursts. A live SE3 sample for 2025-10-26 was inspected: it contains 100 records,
but the interval starting at 02:45+02:00 ends at 03:00+01:00 (75 elapsed
minutes), overlapping the repeated hour. Strict ingestion rejects that response
with specific UTC duration and overlap errors. No source timestamps are repaired
or repeated hours discarded. Availability and correctness must be checked per day.

Native intervals and UTC-aware timestamps are preserved. Daily averages and
hourly chart averages are duration weighted. Repeated local hours are separate
UTC buckets with distinct offset labels. Minimum/maximum refer to native
interval prices. Incomplete days are flagged and excluded from full-day metrics
and trends. Retrieval timestamps represent the latest successful retrieval,
not publication times or versioned forecast inputs; this history alone must not
be treated as a leakage-safe forecasting dataset.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

Tests use synthetic fixtures only, require neither PostgreSQL nor network access,
and cover units, DST, weighted aggregation and invalid/incomplete daily data.
Database schema execution, ingestion, idempotent reruns and populated UI checks
must be verified after manual database setup. No database objects were created
or database integration claimed during implementation.
