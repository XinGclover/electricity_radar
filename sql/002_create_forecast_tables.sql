-- Execute manually after 001 while connected to electricity_radar.
-- No changes to market.prices. Application commands never run DDL.
BEGIN;
CREATE TABLE IF NOT EXISTS market.live_forecasts (
    area TEXT NOT NULL CHECK (area = 'SE3'),
    target_date DATE NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('persistence', 'weekly_seasonal')),
    version TEXT NOT NULL CHECK (version = '1'),
    prediction NUMERIC NOT NULL CHECK (prediction NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    generated_at TIMESTAMPTZ NOT NULL CHECK (isfinite(generated_at)),
    cutoff_at TIMESTAMPTZ NOT NULL CHECK (isfinite(cutoff_at)),
    source_date DATE NOT NULL,
    source_average NUMERIC NOT NULL CHECK (source_average = prediction),
    source_latest_retrieval TIMESTAMPTZ NOT NULL CHECK (isfinite(source_latest_retrieval)),
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    input_snapshot JSONB NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'array'),
    PRIMARY KEY (area, target_date, method, version),
    CHECK (generated_at <= cutoff_at),
    CHECK (source_latest_retrieval <= generated_at),
    CHECK (source_date = target_date - CASE WHEN method = 'persistence' THEN 1 ELSE 7 END)
);
CREATE TABLE IF NOT EXISTS market.backtests (
    area TEXT NOT NULL CHECK (area = 'SE3'),
    target_date DATE NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('persistence', 'weekly_seasonal')),
    version TEXT NOT NULL CHECK (version = '1'),
    prediction NUMERIC NOT NULL CHECK (prediction NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    calculated_at TIMESTAMPTZ NOT NULL CHECK (isfinite(calculated_at)),
    simulated_origin TIMESTAMPTZ NOT NULL CHECK (isfinite(simulated_origin)),
    source_date DATE NOT NULL,
    source_average NUMERIC NOT NULL CHECK (source_average = prediction),
    source_latest_retrieval TIMESTAMPTZ NOT NULL CHECK (isfinite(source_latest_retrieval)),
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
    input_snapshot JSONB NOT NULL CHECK (jsonb_typeof(input_snapshot) = 'array'),
    target_fingerprint TEXT NOT NULL CHECK (target_fingerprint ~ '^[a-f0-9]{64}$'),
    PRIMARY KEY (area, target_date, method, version, input_fingerprint),
    CHECK (source_date = target_date - CASE WHEN method = 'persistence' THEN 1 ELSE 7 END)
);
-- Protect issued records from UPDATE/DELETE as well as application upserts.
CREATE OR REPLACE FUNCTION market.reject_live_forecast_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Issued live forecasts are immutable';
END;
$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'market.live_forecasts'::regclass
                   AND tgname = 'live_forecasts_immutable') THEN
        CREATE TRIGGER live_forecasts_immutable BEFORE UPDATE OR DELETE ON market.live_forecasts
        FOR EACH ROW EXECUTE FUNCTION market.reject_live_forecast_mutation();
    END IF;
END;
$$;
COMMENT ON TABLE market.live_forecasts IS 'Immutable issued daily-average baselines; application only inserts or reads.';
COMMENT ON TABLE market.backtests IS 'Historical backtests, not forecasts issued in the past; calculated_at is actual calculation time.';
COMMENT ON COLUMN market.live_forecasts.prediction IS 'SEK/kWh excluding VAT, energy tax, grid fees and retailer markups.';
COMMENT ON COLUMN market.backtests.prediction IS 'SEK/kWh excluding VAT, energy tax, grid fees and retailer markups.';
COMMIT;
