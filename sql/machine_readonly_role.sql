-- MẪU — KHÔNG phải migration và KHÔNG tự chạy. Tạo tài khoản PostgreSQL chỉ-đọc riêng
-- cho cổng API máy, để dù có lỗi code cổng cũng không ghi được vào database.
-- Chạy bằng tài khoản quản trị DB SAU KHI PO duyệt (production là thao tác HIGH).
-- Thay {{DB_NAME}} và {{PASSWORD}} (mật khẩu ngẫu nhiên dài; đặt vào MACHINE_DATABASE_URL
-- trong /etc/search-tools/machine.env, KHÔNG ghi vào Git).

CREATE ROLE machine_api_ro LOGIN PASSWORD '{{PASSWORD}}'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 10;
GRANT CONNECT ON DATABASE "{{DB_NAME}}" TO machine_api_ro;
GRANT USAGE ON SCHEMA public TO machine_api_ro;
-- Quyền theo CỘT: ngay cả khi mã cổng có lỗi, tài khoản này KHÔNG đọc được giá, phí vận chuyển,
-- ghi chú nội bộ của sản phẩm hay giá/ghi chú tồn kho, và không đọc được bảng người dùng/mật khẩu.
GRANT SELECT (id, name, code, cas, brand, size, manual_compliance, manual_compliance_note,
              manual_compliance_status_id) ON products TO machine_api_ro;
GRANT SELECT (id, permission_keys, lifecycle_status) ON teams TO machine_api_ro;
GRANT SELECT (snapshot_id, code, code_norm, cas_norm, brand, brand_norm, size, size_norm,
              quantity, expiry_date) ON stock_items TO machine_api_ro;
GRANT SELECT ON team_brands, brand_compliance_settings, regulatory_rules, regulatory_statuses,
                stock_state TO machine_api_ro;
ALTER ROLE machine_api_ro SET default_transaction_read_only = on;
ALTER ROLE machine_api_ro SET statement_timeout = '3s';
ALTER ROLE machine_api_ro SET idle_in_transaction_session_timeout = '10s';

-- Gỡ bỏ (khi cần): REVOKE ALL ON ALL TABLES IN SCHEMA public FROM machine_api_ro;
--   REVOKE ALL ON SCHEMA public FROM machine_api_ro; REVOKE CONNECT ON DATABASE "{{DB_NAME}}" FROM machine_api_ro;
--   DROP ROLE machine_api_ro;
