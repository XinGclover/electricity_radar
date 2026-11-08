-- Run manually while connected to electricity_radar. No automatic initialization.
BEGIN;
CREATE SCHEMA IF NOT EXISTS market;
CREATE TABLE IF NOT EXISTS market.prices (
    area TEXT NOT NULL CHECK (area IN ('SE1', 'SE2', 'SE3', 'SE4')),
    delivery_start TIMESTAMPTZ NOT NULL,
    delivery_end TIMESTAMPTZ NOT NULL,
    price_sek_kwh NUMERIC NOT NULL CHECK (
        price_sek_kwh NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    ),
    source TEXT NOT NULL CHECK (source = 'elprisetjustnu'),
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (area, source, delivery_start),
    CHECK (isfinite(delivery_start) AND isfinite(delivery_end) AND isfinite(retrieved_at)),
    CHECK (delivery_end > delivery_start),
    EXCLUDE USING gist (
        (int4range(substring(area FROM 3)::integer, substring(area FROM 3)::integer, '[]')) WITH &&,
        tstzrange(delivery_start, delivery_end, '[)') WITH &&
    )
);
COMMENT ON COLUMN market.prices.price_sek_kwh IS
    'SEK/kWh excluding VAT, energy tax, grid fees and retailer markups; native source intervals.';
COMMIT;
