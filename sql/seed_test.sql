-- Dữ liệu MẪU ĐỂ THỬ — chỉ dùng trên máy / DB test local.
-- Chạy SAU khi đã chạy sql/schema.sql (bảng products đã tồn tại).
--
-- Lệnh:
--   psql "postgresql://searchlocal:searchlocal@127.0.0.1:5432/products_local" -f sql/seed_test.sql
--
-- Cảnh báo: lệnh DELETE xóa toàn bộ sản phẩm trong bảng (chỉ làm trên DB local).

DELETE FROM products;

-- Bốn giá trị brand dưới đây phải khớp team_brands nếu dùng phân quyền theo team:
--   Sigma | Merck | CẤM NHẬP | Phụ lục III
-- Gán nhanh cho team 1,4,5: sql/seed_team_brands_sample.sql

INSERT INTO products (name, code, cas, brand, size, ship, price, note)
VALUES
  ('Acetone thử nghiệm', 'ACE-001', '67-64-1', 'Sigma', '500ml', '1', '45', 'Hàng mẫu test'),
  ('Ethanol 96%', 'ETH-002', '64-17-5', 'Merck', '1L', '2', '120', NULL),
  ('Chất X cấm nhập', 'XXX-003', '111-22-3', 'CẤM NHẬP', '100g', '1', '999', 'Test cảnh báo CAS'),
  ('Chất Y phụ lục', 'YYY-004', '222-33-4', 'Phụ lục III', '250g', '1', '500', 'Test phụ lục');
