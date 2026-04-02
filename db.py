import os

import psycopg2
from psycopg2.extensions import connection as PgConnection


def get_connection() -> PgConnection:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Thiếu biến môi trường DATABASE_URL. "
            "Ví dụ: postgresql://user:pass@localhost:5432/ten_db"
        )
    return psycopg2.connect(dsn)
