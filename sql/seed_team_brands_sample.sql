-- Gán các brand trùng với sql/seed_test.sql cho team 1, 4, 5
-- (mỗi team đều thấy đủ 4 dòng mẫu: Sigma, Merck, CẤM NHẬP, Phụ lục III)
--
-- Chạy SAU: schema + migration RBAC + (tuỳ chọn) seed_test.sql đã có dữ liệu products.
-- Docker:
--   docker compose exec -T db psql -U searchlocal -d products_local < sql/seed_team_brands_sample.sql

INSERT INTO team_brands (team_id, brand) VALUES
  (1, 'Sigma'),
  (1, 'Merck'),
  (1, 'CẤM NHẬP'),
  (1, 'Phụ lục III'),
  (4, 'Sigma'),
  (4, 'Merck'),
  (4, 'CẤM NHẬP'),
  (4, 'Phụ lục III'),
  (5, 'Sigma'),
  (5, 'Merck'),
  (5, 'CẤM NHẬP'),
  (5, 'Phụ lục III')
ON CONFLICT (team_id, brand) DO NOTHING;
