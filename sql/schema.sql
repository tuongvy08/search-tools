-- Chạy một lần sau khi tạo database PostgreSQL (tạo bảng products).
-- Ví dụ:
--   psql "postgresql://searchlocal:searchlocal@127.0.0.1:5432/products_local" -f sql/schema.sql

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    name        TEXT,
    code        TEXT,
    cas         TEXT,
    brand       TEXT,
    size        TEXT,
    ship        TEXT,
    price       TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_products_cas ON products (cas);
CREATE INDEX IF NOT EXISTS idx_products_code ON products (code);
