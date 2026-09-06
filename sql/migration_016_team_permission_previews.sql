-- Phase 6A-UAT: move the team-permission preview/confirm token store out of
-- process memory (admin_teams.py's old in-module `_TEAM_PERMISSION_PREVIEWS`
-- Python dict) and into Postgres.
--
-- Root cause this fixes: in a multi-worker deployment (e.g. gunicorn with
-- more than one worker process), a preview created by whichever worker
-- handled POST /admin/teams/preview lived ONLY in that worker's process
-- memory. A confirm request for the same token landing on a DIFFERENT
-- worker (normal with >1 worker + no session/worker affinity) would never
-- find it and would incorrectly report "preview expired", even though the
-- admin had just clicked "Xem trước thay đổi" seconds earlier. Same failure
-- mode existed for the GET /admin/teams?preview=<token> redisplay.
--
-- Fix is intentionally the smallest thing that removes the process-memory
-- dependency: a plain table, not a new cache/queue system. `token` is the
-- primary key (uuid4 hex, generated in Python same as before) so lookup,
-- insert and the one-time-use "pop" (DELETE ... RETURNING) are all O(1)
-- index operations. Expiry is enforced by comparing `created_at` against
-- NOW() at read/pop time (same 1800s TTL admin_teams.py used in-memory);
-- `admin_teams.index()` opportunistically deletes rows older than the TTL
-- on every page load so the table doesn't grow unbounded, but correctness
-- never depends on that sweep running.
--
-- Additive and idempotent: only CREATE TABLE IF NOT EXISTS / CREATE INDEX
-- IF NOT EXISTS. Never touches existing rows in `teams`, `app_users`,
-- `team_brands`, or any other authorization data. Safe to run more than
-- once. LOCAL ONLY -- not applied to any running application database by
-- this change; must be run explicitly (e.g. by a human, against a real
-- deployment's DB) before this feature can work in a multi-worker setup.

CREATE TABLE IF NOT EXISTS team_permission_previews (
    token TEXT PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    new_brands TEXT[] NOT NULL DEFAULT '{}',
    new_ip_policy TEXT NOT NULL,
    captured_updated_at TIMESTAMPTZ,
    created_by INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_permission_previews_created_at
    ON team_permission_previews (created_at);
