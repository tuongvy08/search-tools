-- MẪU — KHÔNG phải migration và KHÔNG tự chạy. Tài khoản PostgreSQL chỉ-đọc cho thẻ NỘI BỘ (n8n lập báo giá)
-- của cổng API máy. Tách hẳn khỏi `machine_api_ro` (thẻ khách, KHÔNG đọc được giá).
-- Chạy bằng tài khoản quản trị DB. Staging: được tự do. Production: HIGH, cần PO nói "đồng ý bắt đầu".
-- Thay {{DB_NAME}} và {{PASSWORD}} (ngẫu nhiên dài; đặt vào MACHINE_INTERNAL_DATABASE_URL trong
-- /etc/search-tools-machine/machine.env, KHÔNG ghi vào Git).
--
-- Phạm vi (PO 2026-11-21): hàng hóa + giá + pháp chế + tồn kho. Không có bảng người dùng/team/đăng nhập/cấu hình.

CREATE ROLE machine_api_internal_ro LOGIN PASSWORD '{{PASSWORD}}'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 10;
GRANT CONNECT ON DATABASE "{{DB_NAME}}" TO machine_api_internal_ro;
GRANT USAGE ON SCHEMA public TO machine_api_internal_ro;

-- Hàng hóa (kể cả giá gốc, hệ số ship, ghi chú) — theo cột để không mở thêm cột nào khác về sau.
GRANT SELECT (id, name, code, cas, brand, size, ship, price, note, manual_compliance,
              manual_compliance_note, manual_compliance_status_id) ON products TO machine_api_internal_ro;
-- Tồn kho (kể cả giá tồn kho, ghi chú tồn kho).
GRANT SELECT (snapshot_id, code, code_norm, cas_norm, brand, brand_norm, size, size_norm,
              quantity, expiry_date, stock_price_vnd, stock_note) ON stock_items TO machine_api_internal_ro;
GRANT SELECT ON stock_state TO machine_api_internal_ro;
-- Pháp chế.
GRANT SELECT ON brand_compliance_settings, regulatory_rules, regulatory_statuses TO machine_api_internal_ro;
-- Tỷ giá (để quy đổi giá về VND đúng công thức ứng dụng nhân viên).
GRANT SELECT (name, currency_code, is_active) ON brand_master TO machine_api_internal_ro;
GRANT SELECT (currency_code, rate_vnd) ON currency_rates TO machine_api_internal_ro;

ALTER ROLE machine_api_internal_ro SET default_transaction_read_only = on;
ALTER ROLE machine_api_internal_ro SET statement_timeout = '3s';
ALTER ROLE machine_api_internal_ro SET idle_in_transaction_session_timeout = '10s';

-- Gỡ bỏ (khi cần): REVOKE ALL ON ALL TABLES IN SCHEMA public FROM machine_api_internal_ro;
--   REVOKE ALL ON SCHEMA public FROM machine_api_internal_ro;
--   REVOKE CONNECT ON DATABASE "{{DB_NAME}}" FROM machine_api_internal_ro; DROP ROLE machine_api_internal_ro;
