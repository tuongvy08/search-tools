"""Phase 6C0 lifecycle gates on a throwaway real PostgreSQL database only."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote_plus, urlparse

import psycopg2
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import admin_lifecycle  # noqa: E402
import search  # noqa: E402
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = (ROOT / "sql" / "migration_020_admin_lifecycle.sql").read_text(encoding="utf-8")


@unittest.skipUnless(probe_postgres_reachable(), "local PostgreSQL is required")
class AdminLifecyclePgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()

    @classmethod
    def tearDownClass(cls):
        drop_temp_db(cls.db_name)

    def setUp(self):
        self.env = mock.patch.dict(
            os.environ,
            {"DATABASE_URL": self.dsn, "DISABLE_IP_ALLOWLIST": "1"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        search.app.testing = True
        self.client = search.app.test_client()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "TRUNCATE team_lifecycle_previews, team_permission_previews, "
                "login_audit_events, team_brands, app_users, teams, products "
                "RESTART IDENTITY CASCADE"
            )
        self.active_team = self.insert_team("Team Active")
        self.replacement_team = self.insert_team("Team Replacement")
        self.admin_id = self.insert_user("admin", is_admin=True, password="admin-pass")

    def connect(self):
        return psycopg2.connect(self.dsn)

    def insert_team(self, name, status="ACTIVE"):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (name, lifecycle_status) VALUES (%s, %s) RETURNING id",
                (name, status),
            )
            return cur.fetchone()[0]

    def insert_user(self, username, *, is_admin=False, team_id=None, password="pw"):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_users (username, password_hash, team_id, is_admin, "
                "auth_provider, account_status, auth_version) "
                "VALUES (%s, %s, %s, %s, 'LOCAL', 'ACTIVE', 1) RETURNING id",
                (username, generate_password_hash(password), team_id, is_admin),
            )
            return cur.fetchone()[0]

    def login_admin(self, client=None):
        client = client or self.client
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(
                authenticated=True,
                user_id=self.admin_id,
                auth_version=1,
                is_admin=True,
                username="admin",
                role="admin",
                auth_provider="LOCAL",
                csrf_token="csrf",
            )

    def login_as_admin(self, client, user_id, username):
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(
                authenticated=True, user_id=user_id, auth_version=1,
                is_admin=True, username=username, role="admin",
                auth_provider="LOCAL", csrf_token="csrf",
            )

    def test_migration_is_idempotent_and_preserves_existing_rows(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(MIGRATION_SQL)
            cur.execute(MIGRATION_SQL)
            cur.execute(
                "SELECT archived_at, archived_by FROM app_users WHERE id = %s",
                (self.admin_id,),
            )
            self.assertEqual(cur.fetchone(), (None, None))
            cur.execute(
                "SELECT lifecycle_status, archived_at, archived_by FROM teams WHERE id = %s",
                (self.active_team,),
            )
            self.assertEqual(cur.fetchone(), ("ACTIVE", None, None))

    def test_local_archive_revokes_session_blocks_login_and_restores(self):
        target = self.insert_user("local-user", team_id=self.active_team, password="secret-pass")
        stale_client = search.app.test_client()
        with stale_client.session_transaction() as sess:
            sess.update(
                authenticated=True, user_id=target, auth_version=1,
                is_admin=False, team_id=self.active_team, username="local-user",
                auth_provider="LOCAL",
            )
        self.login_admin()
        response = self.client.post(
            "/admin/users/local/archive",
            data={"csrf_token": "csrf", "user_id": str(target)},
        )
        self.assertEqual(response.status_code, 302)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT account_status, archived_at IS NOT NULL, archived_by, auth_version "
                "FROM app_users WHERE id = %s",
                (target,),
            )
            self.assertEqual(cur.fetchone(), ("SUSPENDED", True, self.admin_id, 2))
            cur.execute(
                "SELECT actor_user_id, user_id, reason_code FROM login_audit_events "
                "WHERE reason_code = 'LOCAL_USER_ARCHIVED'"
            )
            self.assertEqual(cur.fetchone(), (self.admin_id, target, "LOCAL_USER_ARCHIVED"))

        self.assertEqual(stale_client.get("/").status_code, 302)
        denied = search.app.test_client().post(
            "/login", data={"username": "local-user", "password": "secret-pass"}
        )
        self.assertEqual(denied.status_code, 401)

        self.login_admin()
        restored = self.client.post(
            "/admin/users/local/restore",
            data={"csrf_token": "csrf", "user_id": str(target)},
        )
        self.assertEqual(restored.status_code, 302)
        allowed = search.app.test_client().post(
            "/login", data={"username": "local-user", "password": "secret-pass"}
        )
        self.assertEqual(allowed.status_code, 302)

    def test_cannot_archive_self_and_last_admin_survives(self):
        self.login_admin()
        response = self.client.post(
            "/admin/users/local/archive",
            data={"csrf_token": "csrf", "user_id": str(self.admin_id)},
        )
        self.assertIn("Không thể tự lưu trữ", unquote_plus(response.headers["Location"]))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM app_users WHERE is_admin AND account_status = 'ACTIVE' "
                "AND archived_at IS NULL"
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def test_concurrent_cross_archive_keeps_one_active_admin(self):
        admin_b = self.insert_user("admin-b", is_admin=True)
        client_a = search.app.test_client()
        client_b = search.app.test_client()
        self.login_as_admin(client_a, self.admin_id, "admin")
        self.login_as_admin(client_b, admin_b, "admin-b")
        barrier = threading.Barrier(2)
        responses = []

        def archive(client, target):
            barrier.wait()
            responses.append(client.post(
                "/admin/users/local/archive",
                data={"csrf_token": "csrf", "user_id": str(target)},
            ))

        threads = [
            threading.Thread(target=archive, args=(client_a, admin_b)),
            threading.Thread(target=archive, args=(client_b, self.admin_id)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(responses), 2)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM app_users WHERE is_admin AND account_status = 'ACTIVE' "
                "AND archived_at IS NULL"
            )
            self.assertEqual(cur.fetchone()[0], 1)

    def _preview_archive(self, source, replacement=None):
        self.login_admin()
        data = {"csrf_token": "csrf", "team_id": str(source)}
        if replacement is not None:
            data["replacement_team_id"] = str(replacement)
        response = self.client.post("/admin/teams/archive/preview", data=data)
        self.assertEqual(response.status_code, 302)
        token = parse_qs(urlparse(response.headers["Location"]).query).get("archive_preview", [None])[0]
        self.assertIsNotNone(token, response.headers["Location"])
        return token

    def test_team_archive_reassigns_atomically_preserves_grants_and_restore_is_nonmagical(self):
        members = [
            self.insert_user("staff-a", team_id=self.active_team),
            self.insert_user("staff-b", team_id=self.active_team),
        ]
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO team_brands (team_id, brand) VALUES (%s, 'BrandA')",
                (self.active_team,),
            )
        token = self._preview_archive(self.active_team, self.replacement_team)
        confirmed = self.client.post(
            "/admin/teams/archive/confirm",
            data={"csrf_token": "csrf", "preview_token": token},
        )
        self.assertEqual(confirmed.status_code, 302)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT lifecycle_status, archived_at IS NOT NULL, archived_by "
                "FROM teams WHERE id = %s",
                (self.active_team,),
            )
            self.assertEqual(cur.fetchone(), ("ARCHIVED", True, self.admin_id))
            cur.execute(
                "SELECT id, team_id, auth_version FROM app_users WHERE id = ANY(%s) ORDER BY id",
                (members,),
            )
            self.assertEqual(cur.fetchall(), [(members[0], self.replacement_team, 2), (members[1], self.replacement_team, 2)])
            cur.execute("SELECT brand FROM team_brands WHERE team_id = %s", (self.active_team,))
            self.assertEqual(cur.fetchone(), ("BrandA",))

        self.login_admin()
        self.client.post(
            "/admin/teams/restore",
            data={"csrf_token": "csrf", "team_id": str(self.active_team)},
        )
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT lifecycle_status FROM teams WHERE id = %s", (self.active_team,))
            self.assertEqual(cur.fetchone()[0], "ACTIVE")
            cur.execute("SELECT DISTINCT team_id FROM app_users WHERE id = ANY(%s)", (members,))
            self.assertEqual(cur.fetchall(), [(self.replacement_team,)])

    def test_preview_is_stale_when_membership_changes(self):
        self.insert_user("staff-a", team_id=self.active_team)
        token = self._preview_archive(self.active_team, self.replacement_team)
        self.insert_user("staff-late", team_id=self.active_team)
        response = self.client.post(
            "/admin/teams/archive/confirm",
            data={"csrf_token": "csrf", "preview_token": token},
        )
        self.assertIn("D%E1%BB%AF+li%E1%BB%87u+team", response.headers["Location"])
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT lifecycle_status FROM teams WHERE id = %s", (self.active_team,))
            self.assertEqual(cur.fetchone()[0], "ACTIVE")

    def test_preview_token_consumption_is_atomic_across_connections(self):
        token = self._preview_archive(self.active_team)
        barrier = threading.Barrier(2)
        results = []

        def pop_once():
            conn = self.connect()
            try:
                with conn, conn.cursor() as cur:
                    barrier.wait()
                    results.append(admin_lifecycle._pop_preview(cur, token, self.admin_id))
            finally:
                conn.close()

        threads = [threading.Thread(target=pop_once), threading.Thread(target=pop_once)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(sum(row is not None for row in results), 1)

    def test_preview_is_actor_bound_and_wrong_admin_cannot_burn_it(self):
        token = self._preview_archive(self.active_team)
        admin_b = self.insert_user("admin-b", is_admin=True)
        other_client = search.app.test_client()
        self.login_as_admin(other_client, admin_b, "admin-b")
        denied = other_client.post(
            "/admin/teams/archive/confirm",
            data={"csrf_token": "csrf", "preview_token": token},
        )
        self.assertIn("archive_preview", self.client.get(
            f"/admin/teams?archive_preview={token}"
        ).request.url)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM team_lifecycle_previews WHERE token = %s", (token,))
            self.assertEqual(cur.fetchone()[0], 1)
        self.assertEqual(denied.status_code, 302)

        self.login_admin()
        confirmed = self.client.post(
            "/admin/teams/archive/confirm",
            data={"csrf_token": "csrf", "preview_token": token},
        )
        self.assertEqual(confirmed.status_code, 302)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT lifecycle_status FROM teams WHERE id = %s", (self.active_team,))
            self.assertEqual(cur.fetchone()[0], "ARCHIVED")

    def test_archived_team_fails_closed_and_cannot_receive_new_members(self):
        staff = self.insert_user("staff", team_id=self.active_team)
        token = self._preview_archive(self.active_team, self.replacement_team)
        self.client.post(
            "/admin/teams/archive/confirm",
            data={"csrf_token": "csrf", "preview_token": token},
        )
        with self.connect() as conn, conn.cursor() as cur:
            with self.assertRaises(psycopg2.Error):
                cur.execute(
                    "UPDATE app_users SET team_id = %s WHERE id = %s",
                    (self.active_team, staff),
                )

        stale = search.app.test_client()
        with stale.session_transaction() as sess:
            sess.update(
                authenticated=True, user_id=staff, auth_version=2,
                is_admin=False, team_id=self.active_team, auth_provider="LOCAL",
            )
        # Lifecycle is enforced even when the optional IP allowlist is disabled.
        self.assertEqual(stale.post("/api/quote-assistant/match", json={}).status_code, 401)

    def test_assignment_lock_race_cannot_leave_member_on_archived_team(self):
        staff = self.insert_user("racing-staff", team_id=self.replacement_team)
        token = self._preview_archive(self.active_team)

        assignment_conn = self.connect()
        assignment_cur = assignment_conn.cursor()
        assignment_cur.execute(
            "UPDATE app_users SET team_id = %s WHERE id = %s",
            (self.active_team, staff),
        )
        # The trigger now holds FOR SHARE on the source team until commit.
        result = []

        def confirm_archive():
            result.append(self.client.post(
                "/admin/teams/archive/confirm",
                data={"csrf_token": "csrf", "preview_token": token},
            ))

        thread = threading.Thread(target=confirm_archive)
        thread.start()
        time.sleep(0.2)
        self.assertTrue(thread.is_alive(), "archive should wait for the assignment's team lock")
        assignment_conn.commit()
        assignment_cur.close()
        assignment_conn.close()
        thread.join(timeout=5)
        self.assertEqual(len(result), 1)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT lifecycle_status FROM teams WHERE id = %s", (self.active_team,))
            self.assertEqual(cur.fetchone()[0], "ACTIVE")
            cur.execute("SELECT team_id FROM app_users WHERE id = %s", (staff,))
            self.assertEqual(cur.fetchone()[0], self.active_team)


if __name__ == "__main__":
    unittest.main()
