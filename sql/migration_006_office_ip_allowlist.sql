-- Cho phép IP/CIDR văn phòng (bổ sung cho biến OFFICE_IP_ALLOWLIST)
CREATE TABLE IF NOT EXISTS office_ip_allowlist (
    id         SERIAL PRIMARY KEY,
    cidr       TEXT NOT NULL,
    label      TEXT,
    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_office_ip_cidr ON office_ip_allowlist (cidr);
