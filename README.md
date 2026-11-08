# Electricity Radar

A Swedish electricity price app, starting with real published day-ahead prices
for SE1–SE4 in local PostgreSQL. The longer-term goal is to forecast next-day
SE3 prices before publication, preserve predictions, and evaluate against
published prices and simple baselines. Persistence and weekly seasonal daily-average
baselines are implemented; machine learning is not.

The current increment provides explicit manual ingestion and a read-only
Streamlit history, live baseline and historical backtest views. It does not create database objects automatically,
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
   .venv/bin/python -m electricity_radar.ingest_prices --start-date 2025-10-27 --end-date 2025-10-27 --areas SE3
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

## Daily-average baseline forecasts

For an existing installation, keep your `.env` and price table. No additional
Python dependencies are needed. Apply the new migration manually:

```sh
psql -h localhost -p 5432 -U your_postgres_user -d electricity_radar -v ON_ERROR_STOP=1 -f sql/002_create_forecast_tables.sql
```

Run `001` first only for a new database. `002` adds separate `market.live_forecasts`
and `market.backtests` tables and an UPDATE/DELETE guard for live forecasts;
it does not change existing price data. The SQL is safe to rerun for initial
setup and is never executed by app startup or commands.

Optionally add this setting to your existing `.env` (default is already 10:00):

```dotenv
# Local clock time on the day before the target, always Europe/Stockholm.
FORECAST_CUTOFF=10:00
```

A cutoff inside a nonexistent spring DST hour is rejected. If a configured local
time repeats in autumn, the first occurrence is used. The default 10:00 is
unambiguous throughout the year.

Persistence uses the complete duration-weighted average for D−1; weekly seasonal
uses D−7, both using Stockholm calendar dates, including DST transitions.
Prices are SEK/kWh in storage, öre/kWh in output, with the same exclusions as
published prices. These are baseline forecasts, not trained models.

### Historical backtest

First ensure the required source and target dates are already stored. For
example, targets 2025-11-03 through 2025-11-05 require source dates 2025-10-27
through 2025-10-29 and 2025-11-02 through 2025-11-04, plus all three target days.
If needed, manually ingest a small covering range after checking source access:

```sh
.venv/bin/python -m electricity_radar.ingest_prices --start-date 2025-10-27 --end-date 2025-11-05 --areas SE3
.venv/bin/python -m electricity_radar.baselines backtest --start-date 2025-11-03 --end-date 2025-11-05
```

Do not re-ingest data unnecessarily. Backtests report skipped target/method pairs
for missing or invalid source/target days and exit unsuccessfully if any are
skipped. Known malformed DST data is rejected without repair. Each target day is
committed independently. Backtests record actual calculation time separately
from a simulated D−1 cutoff, assuming those published source prices were
available then. Retrieval timestamps from later downloads are not evidence of
historical availability and are not filtered against the simulated origin.

Identical input versions and simulated origin produce no duplicate backtest
records. Source/target interval fingerprints include UTC timestamps, exact
prices and retrieval timestamps; changes produce a separate version. The UI
shows the latest saved calculation per target/method, while all versions remain
stored. Backtests are never inserted into the live table or presented as past
issued forecasts.

### Live forecast and later evaluation

1. Before the chosen cutoff, manually ingest today's complete published prices
   and the prices from six local calendar days before today (target D−7), if
   not already stored. Use explicit `--start-date` and `--end-date` with the price
   ingestion command. Complete day-ahead prices can be known before delivery
   finishes: no consumption observations are needed.
2. Generate tomorrow's live forecasts; there is deliberately no historical date
   or timestamp override:

   ```sh
   .venv/bin/python -m electricity_radar.baselines generate
   ```

   The default cutoff is 10:00 Stockholm today, chosen for this project, not an
   exchange publication-time claim. The exact cutoff instant is allowed; later
   generation is refused. The actual UTC generation time is recorded, never
   backdated. Any stored target-day intervals block new generation. Each method
   requires a complete source day with retrieval timestamps no later than actual
   generation time; unavailable methods are reported, never substituted.
3. After target prices are published, manually ingest the target date by replacing
   `YYYY-MM-DD` with the date printed by generation, then evaluate:

   ```sh
   .venv/bin/python -m electricity_radar.ingest_prices --start-date YYYY-MM-DD --end-date YYYY-MM-DD --areas SE3
   .venv/bin/python -m electricity_radar.baselines evaluate
   .venv/bin/python -m streamlit run app.py
   ```

Live uniqueness is area/target/method/version. Repeated runs return the existing
record without changing its prediction, issue time or input snapshot, even after
the cutoff if both methods already exist. A partial rerun must still satisfy all
live checks to create a missing method. The source interval snapshot, daily
average, latest retrieval, deterministic fingerprint, method version and cutoff
are preserved. Application ingestion and forecast generation share a per-area
transaction lock; direct SQL writers bypass this coordination.

Absence of target prices in our database **does not prove** they are unpublished
externally. Our latest-retrieval-only price table is not a publication archive;
re-ingesting source days after the cutoff may make them ineligible for a new
live forecast, even if an earlier version was once available. Previously saved
forecasts retain their original snapshots. No publication-time guarantee is made.

Evaluation is read-only and uses current complete published target prices,
leaving missing/incomplete targets pending. Saved predictions never change.
Signed error = prediction − actual; absolute error and MAE are used, never MAPE.
Revised backtest target inputs are flagged. MAE includes evaluated sample size;
method rankings use only common evaluated target dates. Small samples are
explicitly described as insufficient for firm conclusions. The UI separates live
forecasts from Historical backtest and shows daily averages only, no intraday
forecast curve. History defaults follow the selected area's latest stored day.

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
and cover units, DST, strict daily validation, baseline dates, cutoff boundaries,
late inputs, immutable/idempotent application persistence with a database test
double, live/backtest separation, evaluation and common-date MAE. Streamlit
AppTest checks use mocked database reads; they are not browser visual checks.
Real PostgreSQL migrations (including SQL constraints and the immutable trigger),
transaction/concurrency behavior, ingestion, persisted reruns and populated UI
integration remain to be verified after manual database setup. No database objects were created
or database integration claimed during implementation.
