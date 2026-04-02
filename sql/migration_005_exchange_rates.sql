-- Tỷ giá theo brand (ưu tiên hơn static/exchange_rates.json khi có trong bảng)
CREATE TABLE IF NOT EXISTS exchange_rates (
    brand      TEXT PRIMARY KEY,
    rate       NUMERIC NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
