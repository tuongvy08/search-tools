"""Verifier attempt 6: hard-pinned constants vs REAL data shapes, real-filesystem archive
guard (no patching of archive_guard), full-flow command whitelist, SQL static checks,
stdin/argv wiring via real `python -I -B -`, DSN variants for the S1.1 runtime guard.

Local only. Every external tool (systemctl/git/ss/psql/createdb/locale) is a fake; /proc is
faked; nothing touches SSH, PostgreSQL, systemd, production or the repository files.
Oracle for an abnormal condition: main returns 1, no later gate runs, no success marker,
canary never printed, HOLD only after CREATE was attempted.
"""
import calendar
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ID = load("v6_identity", ROOT / "scripts" / "staging_restore_identity_readonly.py")
CR = load("v6_create", ROOT / "scripts" / "staging_restore_create_temp.py")
MX = load("v6_matrix_helpers", HERE / "test_staging_restore_verifier5_matrix_adversarial.py")
OPS = (ROOT / "specs" / "staging-restore-rehearsal" / "OPERATIONS.md").read_text(encoding="utf-8")
SOURCE_CHECK = (ROOT / "specs" / "staging-restore-rehearsal" / "SOURCE_CHECK.sql").read_text(encoding="utf-8")
CANARY = "V6_FAKE_SECRET_CANARY"
EVIDENCE_BYTES = 1228176407          # PO output S0.5 (database_bytes)
EVIDENCE_FREE = 60691943424          # PO output S0.6 (df -B1 available)


def ops_line(prefix):
    for line in OPS.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError("OPERATIONS.md has no line starting with " + repr(prefix))


# ----------------------------------------------------------------------------------
class PinnedConstantsAgainstRealData(unittest.TestCase):
    """Each hard pin must be usable with real data: right length, format, unit."""

    def test_archive_sha_is_a_real_sha256_hexdigest_shape_and_matches_po_output(self):
        self.assertRegex(CR.ARCHIVE_SHA, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(len(CR.ARCHIVE_SHA), len(hashlib.sha256(b"").hexdigest()))
        po = re.search(r"SHA256 `([0-9a-f]+)`", ops_line("- S0.3 archive hash/TOC server"))
        self.assertIsNotNone(po)
        self.assertEqual(CR.ARCHIVE_SHA, po.group(1), "pin differs from the hash PO printed")
        self.assertIn(CR.ARCHIVE_SHA, CR.LABEL)
        self.assertTrue(CR.LABEL.endswith("archive=" + CR.ARCHIVE_SHA))

    def test_archive_bytes_matches_po_stat_output_and_is_int_bytes(self):
        self.assertIs(type(CR.ARCHIVE_BYTES), int)
        po = re.search(r"backup_bytes=([0-9]+)", ops_line("- S0.2 metadata archive"))
        self.assertEqual(CR.ARCHIVE_BYTES, int(po.group(1)))

    def test_mtime_ns_pin_unit_and_value_match_po_stat_output(self):
        self.assertIs(type(CR.ARCHIVE_MTIME_NS), int)
        line = ops_line("- S0.2 metadata archive")
        m = re.search(r"backup_modified=(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{9}) \+0000", line)
        self.assertIsNotNone(m)
        y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
        expected = calendar.timegm((y, mo, d, h, mi, s)) * 10**9 + int(m.group(7))
        self.assertEqual(CR.ARCHIVE_MTIME_NS, expected)
        # nanosecond magnitude (not seconds / micro / milli)
        self.assertTrue(10**18 < CR.ARCHIVE_MTIME_NS < 10**19)

    def test_mtime_pin_survives_a_real_filesystem_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="v6-mtime-") as folder:
            path = Path(folder) / "f"
            path.write_bytes(b"x")
            os.utime(path, ns=(CR.ARCHIVE_MTIME_NS, CR.ARCHIVE_MTIME_NS))
            seen = path.stat().st_mtime_ns
            if seen != CR.ARCHIVE_MTIME_NS:
                self.skipTest("local filesystem lacks ns mtime precision: %r" % seen)
            self.assertEqual(seen, CR.ARCHIVE_MTIME_NS)

    def test_commit_pin_shape_and_cross_file_agreement(self):
        self.assertRegex(CR.COMMIT, r"\A[0-9a-f]{40}\Z")
        self.assertEqual(CR.COMMIT, ID.TARGET)
        self.assertIn(CR.COMMIT[:7], CR.ARCHIVE.name)
        self.assertIn(CR.COMMIT, ops_line("- S0.4 runtime identity server"))

    def test_temp_name_and_label_are_wellformed_sql_safe_and_agree(self):
        self.assertRegex(CR.TEMP, r"\Asearch_tools_restore_test_[0-9]{8}_[0-9]{6}\Z")
        self.assertLessEqual(len(CR.TEMP.encode()), 63)
        self.assertNotEqual(CR.TEMP, CR.SOURCE)
        date, clock = CR.TEMP.rsplit("_", 2)[-2:]
        import datetime as dt
        dt.datetime.strptime(date + clock, "%Y%m%d%H%M%S")  # real calendar date/time
        self.assertIn("snapshot=%sT%sZ" % (date, clock), CR.LABEL)
        for forbidden in ("'", '"', "\\", ";", "\n", "$", "\0"):
            self.assertNotIn(forbidden, CR.LABEL)
            self.assertNotIn(forbidden, CR.TEMP)
        self.assertRegex(CR.LABEL, r"\A[A-Za-z0-9_=|-]+\Z")
        # the snapshot instant is the PO-observed S0.7 time, not an invented one
        self.assertIn("2026-10-01 06:45:30", ops_line("- S0.7 source server"))
        self.assertEqual((date, clock), ("20261001", "064530"))

    def test_paths_and_service_names_agree_across_scripts_and_po_evidence(self):
        for path in (CR.LIVE, CR.DATA, CR.BASE, CR.ARCHIVE, ID.LIVE):
            self.assertTrue(path.is_absolute())
            self.assertEqual(str(path), os.path.normpath(str(path)), path)
        self.assertEqual(CR.BASE, CR.DATA / "base")
        self.assertEqual(CR.LIVE, ID.LIVE)
        self.assertEqual(CR.WEB, ID.WEB)
        self.assertEqual(CR.WORKER, ID.WORKER)
        self.assertEqual(CR.SOURCE, ID.DATABASE)
        self.assertIn("data_directory `%s`" % CR.DATA, ops_line("- S0.5 metadata DB server"))
        self.assertIn(str(CR.ARCHIVE), OPS)
        for command_marker in ("p=%s;" % CR.ARCHIVE,):
            self.assertGreaterEqual(OPS.count(command_marker), 2)  # S0.2 and S0.3 use the same path
        self.assertIn("df -B1 -- %s " % CR.DATA, OPS)
        for unit in (CR.WEB, CR.WORKER):
            self.assertIn("systemctl is-active " + unit, OPS)

    def test_pg_version_pin_is_the_number_PostgreSQL_reports_for_16_15(self):
        major, minor = 16, 15
        self.assertEqual(160015, major * 10000 + minor)
        self.assertIn("PostgreSQL 16.15", ops_line("- S0.5 metadata DB server"))
        src = (ROOT / "scripts" / "staging_restore_create_temp.py").read_text(encoding="utf-8")
        self.assertIn("160015", src)

    def test_free_space_formula_reproduces_po_planning_number_with_po_inputs(self):
        free, required = self.disk(EVIDENCE_BYTES, EVIDENCE_FREE)
        self.assertEqual((free, required), (EVIDENCE_FREE, 5368709120))
        self.assertIn("5368709120", OPS)

    # -- helpers
    def disk(self, source_bytes, free_bytes):
        with tempfile.TemporaryDirectory(prefix="v6-disk-") as folder:
            base = Path(folder).resolve()
            canned = {"SOURCE_LOCALE_AVAILABLE": "C\nC.utf8\nen_US.utf8\nPOSIX",
                      "CREATEDB_VERSION": "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)"}
            with patch.object(CR, "BASE", base), \
                    patch.object(CR.shutil, "disk_usage", return_value=SimpleNamespace(free=free_bytes)), \
                    patch.object(CR, "checked", side_effect=lambda args, gate, timeout=20: canned[gate]):
                return CR.disk_locale_guard({"bytes": source_bytes, "collate": "en_US.UTF-8"})

    def test_disk_gate_boundaries_and_units(self):
        gib5 = 5 * 1024**3
        self.assertEqual(self.disk(1, gib5), (gib5, gib5))                      # free == required passes
        with self.assertRaises(CR.GateFailure) as ctx:
            self.disk(1, gib5 - 1)                                               # one byte short fails
        self.assertEqual(str(ctx.exception), "DEFAULT_TABLESPACE_FREE_SPACE")
        big = 2_000_000_000                                                      # 4x > 5GiB => 8e9
        self.assertEqual(self.disk(big, 8_000_000_000), (8_000_000_000, 8_000_000_000))
        with self.assertRaises(CR.GateFailure):
            self.disk(big, 8_000_000_000 - 1)
        # 4x source (not 4x dump size, not 1x source) is what dominates above 5 GiB / 4
        threshold = (gib5 + 3) // 4
        self.assertEqual(self.disk(threshold, 4 * threshold)[1], 4 * threshold)
        with self.assertRaises(CR.GateFailure):
            self.disk(threshold + 1, 4 * (threshold + 1) - 1)

    def test_disk_gate_fails_on_missing_locale_or_wrong_createdb_major(self):
        for canned_override, expected in (
                ({"SOURCE_LOCALE_AVAILABLE": "C\nPOSIX"}, "SOURCE_LOCALE_AVAILABLE"),
                ({"CREATEDB_VERSION": "createdb (PostgreSQL) 15.9"}, "CREATEDB_VERSION")):
            canned = {"SOURCE_LOCALE_AVAILABLE": "C\nen_US.utf8", "CREATEDB_VERSION": "createdb (PostgreSQL) 16.15"}
            canned.update(canned_override)
            with tempfile.TemporaryDirectory(prefix="v6-disk-") as folder:
                with patch.object(CR, "BASE", Path(folder).resolve()), \
                        patch.object(CR.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)), \
                        patch.object(CR, "checked", side_effect=lambda a, g, timeout=20, c=canned: c[g]):
                    with self.assertRaises(CR.GateFailure) as ctx:
                        CR.disk_locale_guard({"bytes": 1, "collate": "en_US.UTF-8"})
            self.assertEqual(str(ctx.exception), expected)

    def test_disk_gate_rejects_symlinked_or_missing_base(self):
        with tempfile.TemporaryDirectory(prefix="v6-base-") as folder:
            root = Path(folder).resolve()
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            for candidate in (root / "link", root / "absent"):
                with patch.object(CR, "BASE", candidate), \
                        patch.object(CR.shutil, "disk_usage", return_value=SimpleNamespace(free=10**12)):
                    with self.assertRaises(CR.GateFailure) as ctx:
                        CR.disk_locale_guard({"bytes": 1, "collate": "en_US.UTF-8"})
                    self.assertEqual(str(ctx.exception), "DEFAULT_TABLESPACE_DIRECTORY")


# ----------------------------------------------------------------------------------
def make_archive(root, size=None, ns=None):
    path = Path(root) / "backups" / "pinned.dump"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(CR.ARCHIVE_BYTES if size is None else size)
    stamp = CR.ARCHIVE_MTIME_NS if ns is None else ns
    os.utime(path, ns=(stamp, stamp))
    return path


def digest_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class StubDigest:
    def update(self, chunk):
        pass

    def hexdigest(self):
        return CR.ARCHIVE_SHA


class ArchiveGuardWithRealPins(unittest.TestCase):
    """archive_guard NOT patched; only the filesystem location is redirected."""

    def guard(self, path, **patches):
        with patch.object(CR, "ARCHIVE", path):
            with ExitStack() as stack:
                for key, value in patches.items():
                    stack.enter_context(patch.object(CR, key, value))
                return CR.archive_guard()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v6-arch-")
        self.root = Path(self.tmp.name).resolve()
        self.path = make_archive(self.root)
        info = self.path.stat()
        if info.st_mtime_ns != CR.ARCHIVE_MTIME_NS:
            self.skipTest("filesystem lacks ns mtime precision")
        self.digest = digest_of(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_size_and_mtime_pins_reach_the_hash_stage_and_only_hash_blocks(self):
        with self.assertRaises(CR.GateFailure) as ctx:
            self.guard(self.path)  # pinned sha is for the real server file, not this fixture
        self.assertEqual(str(ctx.exception), "ARCHIVE_HASH_UNCHANGED")

    def test_pin_of_correct_shape_is_accepted_when_digest_equals_it(self):
        fake_hashlib = SimpleNamespace(sha256=lambda: StubDigest())
        fields = self.guard(self.path, hashlib=fake_hashlib)  # STUB: digest forced to pinned constant
        self.assertEqual(fields[5], CR.ARCHIVE_BYTES)
        self.assertEqual(fields[6], CR.ARCHIVE_MTIME_NS)

    def test_matching_real_digest_passes_and_every_single_pin_perturbation_fails(self):
        self.assertTrue(self.guard(self.path, ARCHIVE_SHA=self.digest))
        flipped = ("0" if self.digest[0] != "0" else "1") + self.digest[1:]
        for label, kwargs in [
                ("sha_first_char", dict(ARCHIVE_SHA=flipped)),
                ("sha_upper", dict(ARCHIVE_SHA=self.digest.upper())),
                ("sha_trailing_space", dict(ARCHIVE_SHA=self.digest + " ")),
                ("sha_63", dict(ARCHIVE_SHA=self.digest[1:])),
                ("sha_65", dict(ARCHIVE_SHA=self.digest + "0")),
                ("bytes_plus1", dict(ARCHIVE_SHA=self.digest, ARCHIVE_BYTES=CR.ARCHIVE_BYTES + 1)),
                ("bytes_minus1", dict(ARCHIVE_SHA=self.digest, ARCHIVE_BYTES=CR.ARCHIVE_BYTES - 1)),
                ("mtime_plus1ns", dict(ARCHIVE_SHA=self.digest, ARCHIVE_MTIME_NS=CR.ARCHIVE_MTIME_NS + 1)),
                ("mtime_minus1ns", dict(ARCHIVE_SHA=self.digest, ARCHIVE_MTIME_NS=CR.ARCHIVE_MTIME_NS - 1)),
                ("mtime_plus1s", dict(ARCHIVE_SHA=self.digest, ARCHIVE_MTIME_NS=CR.ARCHIVE_MTIME_NS + 10**9)),
                ("mtime_seconds_unit", dict(ARCHIVE_SHA=self.digest, ARCHIVE_MTIME_NS=CR.ARCHIVE_MTIME_NS // 10**9)),
        ]:
            with self.subTest(label):
                with self.assertRaises(CR.GateFailure):
                    self.guard(self.path, **kwargs)

    def test_real_file_tampering_each_kind_stops(self):
        cases = {}

        def flip(p):
            with open(p, "r+b") as f:
                f.seek(4096)
                f.write(b"\x01")
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS, CR.ARCHIVE_MTIME_NS))
        cases["flip_byte_mtime_restored"] = (flip, "ARCHIVE_HASH_UNCHANGED")

        def append(p):
            with open(p, "ab") as f:
                f.write(b"\0")
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS, CR.ARCHIVE_MTIME_NS))
        cases["append_byte"] = (append, "ARCHIVE_METADATA_MATCH")

        def touch(p):
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS + 1, CR.ARCHIVE_MTIME_NS + 1))
        cases["touch_1ns"] = (touch, "ARCHIVE_METADATA_MATCH")

        def symlink(p):
            aside = p.with_name("aside.dump")
            p.rename(aside)
            p.symlink_to(aside)
        cases["symlink_swap"] = (symlink, "ARCHIVE_CANONICAL_PATH")

        def directory(p):
            p.unlink()
            p.mkdir()
        cases["directory"] = (directory, "ARCHIVE_METADATA_MATCH")
        digest = self.digest
        for label, (mutate, gate) in cases.items():
            with self.subTest(label):
                tmp = tempfile.TemporaryDirectory(prefix="v6-arch2-")
                try:
                    path = make_archive(Path(tmp.name).resolve())
                    mutate(path)
                    with self.assertRaises(Exception) as ctx:  # directory: IsADirectoryError, caught by main
                        self.guard(path, ARCHIVE_SHA=digest)
                    if label != "directory":
                        self.assertIsInstance(ctx.exception, CR.GateFailure)
                        self.assertEqual(str(ctx.exception), gate)
                finally:
                    tmp.cleanup()

    def test_missing_archive_raises_something_main_will_catch(self):
        with self.assertRaises(Exception):
            self.guard(self.root / "nope.dump", ARCHIVE_SHA=self.digest)


# ----------------------------------------------------------------------------------
class ProcFake:
    def __init__(self, live, env_bytes):
        self.live, self.env_bytes = live, env_bytes

    def resolve(self, strict=False):
        return self.live

    def read_bytes(self):
        return self.env_bytes


def proc_factory(live, env_for_pid):
    def factory(*parts):
        if parts and parts[0] == "/proc":
            pid = parts[1]
            return ProcFake(live, env_for_pid(pid)) if pid in ("101", "202") else Path(*parts)
        return Path(*parts)
    return factory


def web_worker_run(args, **kwargs):
    if args[0] == "systemctl":
        pid = "101" if args[2] == CR.WEB else "202"
        return subprocess.CompletedProcess(
            args, 0, "ActiveState=active\nSubState=running\nMainPID=%s\nUser=deploy\n" % pid, "")
    return subprocess.CompletedProcess(args, 0, CR.COMMIT + "\n", "")


GOOD_DSN = "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging"


class CreateRuntimeGuardDsn(unittest.TestCase):
    def guard(self, web, worker, run=web_worker_run):
        with tempfile.TemporaryDirectory(prefix="v6-live-") as folder:
            live = Path(folder).resolve() / "search-tools"
            live.mkdir()
            env = {"101": web, "202": worker}
            with patch.object(CR, "LIVE", live), \
                    patch.object(CR, "Path", proc_factory(live, lambda pid: env[pid])), \
                    patch.object(CR.subprocess, "run", side_effect=run):
                CR.runtime_guard()

    @staticmethod
    def entry(value):
        return b"A=b\0DATABASE_URL=" + value.encode() + b"\0Z=y\0"

    def test_positive_controls_accepted(self):
        for dsn in (GOOD_DSN, GOOD_DSN + "?sslmode=require", GOOD_DSN.replace(":5432", ""),
                    GOOD_DSN + "?connect_timeout=5&target_session_attrs=any",
                    GOOD_DSN.replace(CANARY, "p%40ss%3Aw%2Ford")):
            with self.subTest(dsn=dsn.replace(CANARY, "X")):
                self.guard(self.entry(dsn), self.entry(dsn))

    def test_wrong_target_dsns_stop_with_fixed_gate_and_no_secret(self):
        bad = {
            "localhost": GOOD_DSN.replace("127.0.0.1", "localhost"),
            "ipv6": GOOD_DSN.replace("127.0.0.1", "[::1]"),
            "other_loopback": GOOD_DSN.replace("127.0.0.1", "127.0.0.2"),
            "remote": GOOD_DSN.replace("127.0.0.1", "db.internal.example"),
            "port": GOOD_DSN.replace(":5432", ":5433"),
            "prod_db": GOOD_DSN.replace("search_tools_staging", "search_tools_prod"),
            "temp_db": GOOD_DSN.replace("search_tools_staging", CR.TEMP),
            "case": GOOD_DSN.replace("search_tools_staging", "Search_Tools_Staging"),
            "no_user": "postgresql://127.0.0.1:5432/search_tools_staging",
            "host_override": GOOD_DSN + "?host=/var/run/postgresql",
            "hostaddr": GOOD_DSN + "?hostaddr=10.0.0.9",
            "dbname_override": GOOD_DSN + "?dbname=search_tools_prod",
            "blank_option": GOOD_DSN + "?sslmode=",
            "dup_option": GOOD_DSN + "?sslmode=a&sslmode=b",
            "fragment": GOOD_DSN + "#x",
            "scheme": GOOD_DSN.replace("postgresql://", "mysql://"),
            "keyvalue": "host=127.0.0.1 dbname=search_tools_staging user=deploy password=" + CANARY,
            "subpath": GOOD_DSN + "/",
        }
        for label, dsn in bad.items():
            with self.subTest(label):
                with self.assertRaises((CR.GateFailure, ValueError)) as ctx:
                    self.guard(self.entry(dsn), self.entry(dsn))
                self.assertNotIn(CANARY, str(ctx.exception))
                if isinstance(ctx.exception, CR.GateFailure):
                    self.assertRegex(str(ctx.exception), r"\A[A-Z0-9_]+\Z")

    def test_env_shape_problems_stop(self):
        good = self.entry(GOOD_DSN)
        for label, web in {
                "absent": b"A=b\0",
                "empty_value": b"DATABASE_URL=\0",
                "twice": good + b"DATABASE_URL=" + GOOD_DSN.encode() + b"\0",
                "prefix_only_name": b"XDATABASE_URL=" + GOOD_DSN.encode() + b"\0",
                "non_utf8": b"DATABASE_URL=\xff\xfe\0"}.items():
            with self.subTest(label):
                with self.assertRaises(Exception) as ctx:
                    self.guard(web, good)
                self.assertNotIn(CANARY, str(ctx.exception))

    def test_web_and_worker_with_different_dsn_stop(self):
        with self.assertRaises(CR.GateFailure) as ctx:
            self.guard(self.entry(GOOD_DSN), self.entry(GOOD_DSN + "?connect_timeout=5"))
        self.assertEqual(str(ctx.exception), "STAGING_WEB_WORKER_SAME_DSN")

    def test_commit_and_cwd_pin_mismatch_stop(self):
        def run_bad_commit(args, **kwargs):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 0, CR.COMMIT[:-1] + "0\n", "")
            return web_worker_run(args, **kwargs)
        with self.assertRaises(CR.GateFailure) as ctx:
            self.guard(self.entry(GOOD_DSN), self.entry(GOOD_DSN), run=run_bad_commit)
        self.assertEqual(str(ctx.exception), "WEB_STAGING_COMMIT")

        def run_short_commit(args, **kwargs):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 0, CR.COMMIT[:7] + "\n", "")
            return web_worker_run(args, **kwargs)
        with self.assertRaises(CR.GateFailure) as ctx:
            self.guard(self.entry(GOOD_DSN), self.entry(GOOD_DSN), run=run_short_commit)
        self.assertEqual(str(ctx.exception), "WEB_COMMIT_READ_STDOUT_UNEXPECTED")


# ----------------------------------------------------------------------------------
class RealisticToolOutputs(unittest.TestCase):
    """Outputs shaped like real Ubuntu tool output must be accepted (else S1.1 can never finish)."""

    def accepted(self, module, gate, text):
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(["x"], 0, text, "")):
            return module.checked(["x"], gate)

    def test_systemctl_property_order_is_systemd_not_alphabetical(self):
        for module in (ID, CR):
            for text in ("MainPID=3993800\nUser=deploy\nSubState=running\nActiveState=active\n",
                         "ActiveState=active\nSubState=running\nMainPID=3993800\nUser=deploy"):
                self.assertTrue(self.accepted(module, "WEB_SERVICE_READ", text))

    def test_createdb_version_variants_for_ubuntu(self):
        for text in ("createdb (PostgreSQL) 16.15",
                     "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)",
                     "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-1.pgdg24.04+1)",
                     "createdb (PostgreSQL) 16.15 (Ubuntu 16.15-1.pgdg22.04+1)"):
            self.assertEqual(self.accepted(CR, "CREATEDB_VERSION", text), text)

    def test_locale_listing_real_shape(self):
        text = "C\nC.utf8\nen_US.utf8\nPOSIX\n"
        self.assertTrue(self.accepted(CR, "SOURCE_LOCALE_AVAILABLE", text))

    def test_git_rev_parse_real_output_from_this_repo(self):
        real = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
        self.assertEqual(real.returncode, 0)
        self.assertTrue(self.accepted(CR, "WEB_COMMIT_READ", real.stdout))
        self.assertTrue(self.accepted(ID, "WEB_COMMIT_READ", real.stdout))

    def instance(self, listing, pid_file=b"3993835\n/var/lib/postgresql/16/main\n1790000000\n5432\n/var/run/postgresql\n"):
        with tempfile.TemporaryDirectory(prefix="v6-pm-") as folder:
            data = Path(folder).resolve()
            (data / "postmaster.pid").write_bytes(pid_file)
            with patch.object(CR, "DATA", data), patch.object(
                    CR.subprocess, "run", return_value=subprocess.CompletedProcess(["ss"], 0, listing, "")):
                return CR.instance_guard()

    V4 = 'LISTEN 0      200        127.0.0.1:5432       0.0.0.0:*    users:(("postgres",pid=3993835,fd=6))'
    V6 = 'LISTEN 0      200            [::1]:5432          [::]:*    users:(("postgres",pid=3993835,fd=5))'

    def test_real_ss_listing_ipv4_and_ipv6_same_postmaster(self):
        self.assertEqual(self.instance(self.V4 + "\n" + self.V6 + "\n"), "3993835")
        self.assertEqual(self.instance(self.V6 + "\n" + self.V4), "3993835")
        self.assertEqual(self.instance(self.V4.replace("127.0.0.1", "0.0.0.0")), "3993835")

    def test_instance_guard_negatives(self):
        other = self.V4.replace("3993835", "1234")
        cases = {
            "ipv6_only": self.V6,
            "other_pid": other,
            "pid_is_prefix": self.V4.replace("3993835", "39938350"),
            "pid_is_suffix": self.V4.replace("3993835", "13993835"),
            "wrong_address": self.V4.replace("127.0.0.1", "10.1.2.3"),
            "wrong_port_text": self.V4.replace(":5432 ", ":15432 "),
            "not_listen": self.V4.replace("LISTEN", "ESTAB"),
            "empty": "",
        }
        for label, listing in cases.items():
            with self.subTest(label):
                with self.assertRaises(CR.GateFailure):
                    self.instance(listing)
        with self.assertRaises(CR.GateFailure):
            self.instance(self.V4, pid_file=b"0123\nx\n")
        with self.assertRaises(CR.GateFailure):
            self.instance(self.V4, pid_file=b"3993835 \nx\n")
        with self.assertRaises(CR.GateFailure):
            self.instance(self.V4, pid_file=b"")

    def test_instance_guard_rejects_symlinked_postmaster_pid_and_other_data_dir(self):
        with tempfile.TemporaryDirectory(prefix="v6-pm2-") as folder:
            data = Path(folder).resolve()
            real = data / "elsewhere.pid"
            real.write_bytes(b"3993835\n")
            (data / "postmaster.pid").symlink_to(real)
            with patch.object(CR, "DATA", data), patch.object(
                    CR.subprocess, "run", return_value=subprocess.CompletedProcess(["ss"], 0, self.V4, "")):
                with self.assertRaises(OSError):  # O_NOFOLLOW refuses the symlink
                    CR.instance_guard()
            link = data.parent / (data.name + "-link")
            link.symlink_to(data)
            try:
                with patch.object(CR, "DATA", link):
                    with self.assertRaises(CR.GateFailure):
                        CR.instance_guard()
            finally:
                link.unlink()


# ----------------------------------------------------------------------------------
class RealFilesystemFullFlow(unittest.TestCase):
    """main() with archive_guard UNPATCHED against a real file carrying the real size/mtime pins."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v6-e2e-")
        self.root = Path(self.tmp.name).resolve()
        self.live = self.root / "srv" / "search-tools"
        self.live.mkdir(parents=True)
        self.data = self.root / "var" / "main"
        (self.data / "base").mkdir(parents=True)
        (self.data / "postmaster.pid").write_bytes(b"3993835\nfixture\n")
        self.archive = make_archive(self.root)
        if self.archive.stat().st_mtime_ns != CR.ARCHIVE_MTIME_NS:
            self.skipTest("filesystem lacks ns mtime precision")
        self.digest = digest_of(self.archive)
        self.record = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, inject=None, free=EVIDENCE_FREE):
        valid, _ = MX.make_create_valid(self.data)
        trace, counter = [], {}
        fake = MX.fake_run_factory(valid, MX.create_gate, inject or {}, trace, counter)

        def spy(args, **kwargs):
            self.record.append((list(args), dict(kwargs)))
            return fake(args, **kwargs)
        out = io.StringIO()
        env = b"A=b\0DATABASE_URL=" + GOOD_DSN.encode() + b"\0"
        with ExitStack() as stack:
            stack.enter_context(patch.object(CR.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(CR.sys, "argv", ["-", "--create"]))
            stack.enter_context(patch.object(CR, "LIVE", self.live))
            stack.enter_context(patch.object(CR, "DATA", self.data))
            stack.enter_context(patch.object(CR, "BASE", self.data / "base"))
            stack.enter_context(patch.object(CR, "ARCHIVE", self.archive))
            stack.enter_context(patch.object(CR, "ARCHIVE_SHA", self.digest))  # only way to match a fixture
            stack.enter_context(patch.object(CR, "Path", proc_factory(self.live, lambda pid: env)))
            stack.enter_context(patch.object(CR.shutil, "disk_usage", return_value=SimpleNamespace(free=free)))
            stack.enter_context(patch.object(CR.subprocess, "run", side_effect=spy))
            stack.enter_context(redirect_stdout(out))
            code = CR.main()
        return code, out.getvalue(), trace

    def assert_stopped_before_create(self, label, code, out, trace, last):
        self.assertEqual(code, 1, label + out)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", out, label)
        self.assertEqual(trace[-1], last, label + str(trace))
        self.assertNotIn("create#1", trace, label)
        self.assertNotIn("HOLD", out, label)
        self.assertNotIn(CANARY, out)

    def test_control_clean_flow_with_po_numbers_reaches_success_once(self):
        code, out, trace = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertEqual(trace, MX.CREATE_GATES)
        self.assertEqual(trace.count("create#1"), 1)
        self.assertIn("STAGING_TEMP_DATABASE_CREATED (empty, no restore performed)", out)
        self.assertIn("data_base_free_bytes=%d required_free_bytes=5368709120" % EVIDENCE_FREE, out)
        self.assertNotIn(CANARY, out)
        self.assertNotIn("DATABASE_URL", out)

    def test_archive_defects_before_create_stop_before_any_pg_call(self):
        def size_plus(p):
            with open(p, "ab") as f:
                f.write(b"\0")
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS,) * 2)

        def size_minus(p):
            os.truncate(p, CR.ARCHIVE_BYTES - 1)
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS,) * 2)

        def mtime_plus(p):
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS + 1,) * 2)

        def mtime_minus(p):
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS - 1,) * 2)

        def flip(p):
            with open(p, "r+b") as f:
                f.seek(777)
                f.write(b"\x07")
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS,) * 2)

        def symlink(p):
            aside = p.with_name("aside")
            p.rename(aside)
            p.symlink_to(aside)

        def missing(p):
            p.unlink()

        def directory(p):
            p.unlink()
            p.mkdir()

        for label, prep in dict(size_plus=size_plus, size_minus=size_minus, mtime_plus=mtime_plus,
                                mtime_minus=mtime_minus, flip=flip, symlink=symlink, missing=missing,
                                directory=directory).items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                prep(self.archive)
                code, out, trace = self.run_main()
                self.assert_stopped_before_create(label, code, out, trace, "ss#1")

    def test_archive_tampered_during_or_after_create_gives_hold_not_success(self):
        def flip(p):
            with open(p, "r+b") as f:
                f.seek(1234)
                f.write(b"\x09")
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS,) * 2)

        def touch(p):
            os.utime(p, ns=(CR.ARCHIVE_MTIME_NS + 5,) * 2)

        def symlink(p):
            aside = p.with_name("aside")
            p.rename(aside)
            p.symlink_to(aside)

        for gate in ("create#1", "acl#1", "temp#1", "systemctl#3"):
            for label, mutate in (("flip", flip), ("touch", touch), ("symlink", symlink)):
                with self.subTest(gate=gate, mutation=label):
                    self.tearDown()
                    self.setUp()

                    def apply(o, e, c, m=mutate):
                        m(self.archive)
                        return o, e, c
                    code, out, trace = self.run_main({"gate": gate, "apply": apply})
                    self.assertEqual(code, 1, out)
                    self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", out)
                    self.assertIn("HOLD: CREATE attempted", out)
                    self.assertIn("temp_database=" + CR.TEMP, out)
                    self.assertEqual(trace.count("create#1"), 1)
                    self.assertEqual(trace[-1], "ss#2", trace)
                    self.assertNotIn(CANARY, out)

    def test_disk_below_buffer_with_real_numbers_stops_before_create(self):
        for free in (5368709120 - 1, 0, 1):
            with self.subTest(free=free):
                code, out, trace = self.run_main(free=free)
                self.assert_stopped_before_create("free", code, out, trace, "source#1")
                self.assertIn("DEFAULT_TABLESPACE_FREE_SPACE", out)

    def test_full_flow_command_whitelist_and_no_write_outside_temp(self):
        code, out, trace = self.run_main()
        self.assertEqual(code, 0, out)
        pg_bin = "/usr/lib/postgresql/16/bin/"
        creates = psql_writes = psql_reads = 0
        seen_units = set()
        for args, kwargs in self.record:
            self.assertIsInstance(args, list)
            self.assertFalse(kwargs.get("shell"), args)
            self.assertEqual(kwargs.get("env"), CR.ENV)
            self.assertIs(kwargs.get("check"), False)
            joined = " ".join(args)
            self.assertNotRegex(joined, r"(?i)\b(pg_restore|pg_dump|dropdb|drop|truncate|--clean|--create|"
                                        r"--force|terminate|restart|stop|start|reload|enable|disable|kill)\b")
            self.assertNotRegex(joined, r"postgres(ql)?://")
            self.assertNotIn(CANARY, joined)
            if args[0] == "systemctl":
                self.assertEqual(args[1], "show")
                self.assertIn(args[2], (CR.WEB, CR.WORKER))
                self.assertEqual(args[3:], ["--no-pager", "-p", "ActiveState", "-p", "SubState", "-p",
                                            "MainPID", "-p", "User"])
                seen_units.add(args[2])
            elif args[0] == "git":
                self.assertEqual(args, ["git", "-c", "safe.directory=" + str(self.live), "-C",
                                        str(self.live), "rev-parse", "HEAD"])
            elif args[0] == "/usr/bin/ss":
                self.assertEqual(args, ["/usr/bin/ss", "-H", "-ltnp", "sport = :5432"])
            elif args[0] == "/usr/bin/locale":
                self.assertEqual(args, ["/usr/bin/locale", "-a"])
            elif args[0] == pg_bin + "createdb":
                self.assertEqual(args, [pg_bin + "createdb", "--version"])
            else:
                self.assertEqual(args[:7], ["sudo", "-n", "-u", "postgres", "env", "-i", "LC_ALL=C"])
                self.assertEqual(args[7], "LANG=C")
                self.assertTrue(args[8].startswith("PGOPTIONS=-c default_transaction_read_only="))
                self.assertEqual(args[9], "PGCONNECT_TIMEOUT=10")
                self.assertEqual(sorted(a.split("=")[0] for a in args[4:10] if "=" in a),
                                 ["LANG", "LC_ALL", "PGCONNECT_TIMEOUT", "PGOPTIONS"])
                write = "default_transaction_read_only=off" in args[8]
                tool = args[10]
                if tool == pg_bin + "createdb":
                    self.assertTrue(write)
                    creates += 1
                    self.assertEqual(args[-1], CR.TEMP)
                    self.assertIn("--maintenance-db=" + CR.SOURCE, args)
                    self.assertIn("--template=template0", args)
                    self.assertIn("--owner=postgres", args)
                    self.assertIn("--tablespace=pg_default", args)
                    self.assertEqual(sum(1 for a in args if a == CR.TEMP), 1)
                    self.assertNotIn(CR.SOURCE, [a for a in args if not a.startswith("--maintenance-db=")])
                else:
                    self.assertEqual(tool, pg_bin + "psql")
                    database = args[args.index("-d") + 1]
                    if write:
                        psql_writes += 1
                        self.assertEqual(database, CR.TEMP)
                        sql = args[args.index("-c") + 1]
                        statements = [s.strip() for s in sql.split(";") if s.strip()]
                        self.assertEqual(len(statements), 2, statements)
                        self.assertEqual(statements[0], 'REVOKE CONNECT ON DATABASE "%s" FROM PUBLIC' % CR.TEMP)
                        self.assertEqual(statements[1], 'COMMENT ON DATABASE "%s" IS \'%s\'' % (CR.TEMP, CR.LABEL))
                    else:
                        psql_reads += 1
                        self.assertIn(database, (CR.SOURCE, CR.TEMP))
                        sql = args[args.index("-c") + 1]
                        self.assertRegex(sql.lstrip(), r"\ASELECT ")
                        self.assertNotRegex(sql, r"(?i)\b(insert|update|delete|drop|alter|create|grant|revoke|"
                                                 r"truncate|comment|vacuum|copy|call|pg_read_file|pg_ls_dir|"
                                                 r"pg_terminate_backend|pg_cancel_backend|lo_import|lo_export)\b")
                        self.assertEqual(sql.count(";"), 1)
        self.assertEqual((creates, psql_writes), (1, 1))
        self.assertEqual(psql_reads, 2)
        self.assertEqual(seen_units, {CR.WEB, CR.WORKER})

    def test_inline_schema033_sql_is_the_nine_po_verified_source_check_flags(self):
        code, out, trace = self.run_main()
        sql = next(a[a.index("-c") + 1] for a, _ in self.record
                   if "-c" in a and "-d" in a and a[a.index("-d") + 1] == CR.SOURCE)

        def squash(text):
            return re.sub(r"\s+", "", text)

        def top_level_split(text):
            parts, depth, quote, start = [], 0, False, 0
            for i, ch in enumerate(text):
                if ch == "'":
                    quote = not quote
                elif not quote and ch == "(":
                    depth += 1
                elif not quote and ch == ")":
                    depth -= 1
                elif not quote and depth == 0 and ch == ",":
                    parts.append(text[start:i])
                    start = i + 1
            parts.append(text[start:])
            return parts
        last_select = SOURCE_CHECK.rsplit("SELECT EXISTS", 1)[1]
        body = "EXISTS" + last_select.rstrip().rstrip(";")
        flags = [re.sub(r"\s+AS\s+\w+\s*$", "", part.strip()) for part in top_level_split(body)]
        self.assertEqual(len(flags), 9)
        flat = squash(sql)
        for flag in flags:
            self.assertIn(squash(flag), flat, flag)
        schema = flat[flat.index("'schema033',("):flat.index(")::text")]
        self.assertEqual(schema.count("EXISTS("), 9)
        # the nine EXISTS terms are joined by AND (no OR that could weaken the gate)
        self.assertNotIn("OR", re.sub(r"'[^']*'", "", schema).replace("ORDER", ""))
        # balanced parentheses and quotes outside literals
        depth, quote = 0, False
        for ch in sql:
            if ch == "'":
                quote = not quote
            elif not quote:
                depth += (ch == "(") - (ch == ")")
                self.assertGreaterEqual(depth, 0)
        self.assertEqual((depth, quote), (0, False))


# ----------------------------------------------------------------------------------
class IdentityScriptS04(unittest.TestCase):
    """V1: S0.4 read-only identity script, re-attacked with data shapes."""

    def run_main(self, web, worker, record, systemctl_extra=""):
        dsn_by_pid = {"101": web, "202": worker}
        out = io.StringIO()

        def fake(args, **kwargs):
            record.append((list(args), dict(kwargs)))
            if args[0] == "systemctl":
                pid = "101" if args[2] == ID.WEB else "202"
                return subprocess.CompletedProcess(
                    args, 0, "ActiveState=active\nSubState=running\nMainPID=%s\nUser=deploy\n" % pid, "")
            return subprocess.CompletedProcess(args, 0, ID.TARGET + "\n", "")
        with tempfile.TemporaryDirectory(prefix="v6-id-") as folder:
            live = Path(folder).resolve() / "search-tools"
            live.mkdir()
            with ExitStack() as stack:
                stack.enter_context(patch.object(ID.os, "geteuid", return_value=0))
                stack.enter_context(patch.object(ID.sys, "argv", ["-"]))
                stack.enter_context(patch.object(ID, "LIVE", live))
                stack.enter_context(patch.object(ID, "process_cwd", return_value=str(live)))
                stack.enter_context(patch.object(
                    ID, "process_dsn", side_effect=lambda pid: dsn_by_pid[str(pid)].encode()))
                stack.enter_context(patch.object(ID.subprocess, "run", side_effect=fake))
                stack.enter_context(redirect_stdout(out))
                code = ID.main()
        return code, out.getvalue()

    def test_only_four_readonly_subprocesses_exact_argv_and_no_secret_in_output(self):
        for dsn in (GOOD_DSN, "postgresql://u%40x:" + CANARY + "%2F%3A@127.0.0.1:6432/search_tools_staging?sslmode=require",
                    "postgres://deploy:" + CANARY + "@localhost/search_tools_staging",
                    "postgresql://deploy:" + CANARY + "@[::1]:5432/search_tools_staging"):
            record = []
            code, out = self.run_main(dsn, dsn, record)
            self.assertEqual(code, 0, out)
            self.assertEqual(len(record), 4)
            for args, kwargs in record:
                self.assertEqual(kwargs.get("env"), ID.READ_ENV)
                self.assertFalse(kwargs.get("shell"))
                if args[0] == "systemctl":
                    self.assertEqual(args[1], "show")
                    self.assertEqual(args[3:], ["--no-pager", "-p", "ActiveState", "-p", "SubState",
                                                "-p", "MainPID", "-p", "User"])
                else:
                    self.assertEqual(args[0], "git")
                    self.assertEqual(args[-2:], ["rev-parse", "HEAD"])
            self.assertNotIn(CANARY, out)
            self.assertNotRegex(out, r"postgres(ql)?://")
            lines = out.splitlines()
            allowed = [r"inspected_at_utc=\d{4}-\d\d-\d\dT[0-9:.]+\+00:00",
                       r"web_active=YES worker_active=YES web_worker_same_database=YES",
                       r"live_database=search_tools_staging", r"live_host=(127\.0\.0\.1|localhost|::1)",
                       r"live_port=[0-9]+", r"live_cwd=.+", r"live_commit=[0-9a-f]{40}",
                       r"STAGING_RUNTIME_IDENTITY_OK \(no DB connection, no changes\)"]
            self.assertEqual(len(lines), len(allowed), out)
            for line, pattern in zip(lines, allowed):
                self.assertRegex(line, r"\A" + pattern + r"\Z")

    def test_wrong_identity_variants_stop_with_fixed_gate_and_no_secret(self):
        bad = ["postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_prod",
               "postgresql://deploy:" + CANARY + "@10.0.0.9:5432/search_tools_staging",
               "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging?host=evil",
               "postgresql://deploy:" + CANARY + "@127.0.0.1:70000/search_tools_staging",
               "postgresql://deploy:" + CANARY + "@127.0.0.1:0/search_tools_staging",
               "host=127.0.0.1 password=" + CANARY]
        for dsn in bad:
            record = []
            code, out = self.run_main(dsn, dsn, record)
            self.assertEqual(code, 1, dsn)
            self.assertNotIn(CANARY, out)
            self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", out)
            self.assertRegex(out.splitlines()[0], r"\ADUNG: [A-Z0-9_]+ \(no secret output, no retry\)\Z")

    def test_script_text_contains_no_postgres_or_service_mutation_tokens(self):
        text = (ROOT / "scripts" / "staging_restore_identity_readonly.py").read_text(encoding="utf-8")
        code_only = re.sub(r'"""[\s\S]*?"""', "", text)
        for token in ("psql", "createdb", "pg_restore", "pg_dump", "psycopg", "dropdb", "sudo", "shell=True",
                      "os.system", "Popen", "\"start\"", "\"stop\"", "\"restart\"", "\"reload\"", "\"kill\"",
                      "open(", ".write_text", ".write_bytes", "unlink", "rmtree", ".env", "dotenv"):
            self.assertNotIn(token, code_only, token)
        self.assertEqual(code_only.count("subprocess.run("), 1)


# ----------------------------------------------------------------------------------
class StdinWiringWithRealPython(unittest.TestCase):
    """Runs the scripts exactly as the PO command does (python -I -B - [args] < script),
    non-root and with an euid prelude. /srv/search-tools must not exist locally, so no
    service or DB command can run: the first filesystem gate fires."""

    def run_script(self, script, args, prelude=""):
        source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="v6-wire-") as folder:
            result = subprocess.run([sys.executable, "-I", "-B", "-", *args], input=prelude + source,
                                    cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=30,
                                    env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "LANG": "C"})
        return result

    def setUp(self):
        if Path("/srv/search-tools").exists() or Path("/var/lib/postgresql").exists():
            self.skipTest("local host unexpectedly has staging paths; refusing to execute")

    ROOT_PRELUDE = "import os\nos.geteuid = lambda: 0\n"

    def test_create_script_argument_gate_through_stdin(self):
        r = self.run_script("staging_restore_create_temp.py", ["--create"])
        if os.geteuid() != 0:
            self.assertEqual(r.returncode, 1)
            self.assertIn("DUNG: ROOT_EXPLICIT_CREATE_ONLY", r.stdout)
        for args in ([], ["--create", "extra"], ["extra"], ["--CREATE"], ["--create=1"], ["-"], ["--create", "--create"]):
            with self.subTest(args=args):
                r = self.run_script("staging_restore_create_temp.py", args, self.ROOT_PRELUDE)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("DUNG: ROOT_EXPLICIT_CREATE_ONLY", r.stdout)
                self.assertNotIn("temp_database=", r.stdout)
                self.assertEqual(r.stderr, "")

    def test_create_script_with_exact_po_arguments_reaches_first_filesystem_gate_only(self):
        r = self.run_script("staging_restore_create_temp.py", ["--create"], self.ROOT_PRELUDE)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DUNG: STAGING_LIVE_PATH", r.stdout)
        self.assertIn("temp_database=" + CR.TEMP, r.stdout)
        self.assertNotIn("HOLD", r.stdout)
        self.assertNotIn("PRECREATE_READONLY_GATES_OK", r.stdout)
        self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_identity_script_argument_gate_and_first_gate_through_stdin(self):
        r = self.run_script("staging_restore_identity_readonly.py", [])
        if os.geteuid() != 0:
            self.assertIn("DUNG: ROOT_NO_EXTRA_ARGUMENTS", r.stdout)
        r = self.run_script("staging_restore_identity_readonly.py", ["x"], self.ROOT_PRELUDE)
        self.assertIn("DUNG: ROOT_NO_EXTRA_ARGUMENTS", r.stdout)
        r = self.run_script("staging_restore_identity_readonly.py", [], self.ROOT_PRELUDE)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DUNG: STAGING_LIVE_DIRECTORY", r.stdout)
        self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_operations_commands_use_exactly_this_invocation(self):
        self.assertIn("python3 -I -B -' < \"/Volumes/DATA/Development/Search-tools/scripts/"
                      "staging_restore_identity_readonly.py\"", OPS)
        self.assertIn("python3 -I -B - --create' < \"/Volumes/DATA/Development/Search-tools/scripts/"
                      "staging_restore_create_temp.py\"", OPS)

    def test_pinned_script_hashes_in_operations_equal_actual_files(self):
        """Informational about stale text: reports every distinct hash the doc attaches to S1.1."""
        actual_create = hashlib.sha256((ROOT / "scripts" / "staging_restore_create_temp.py").read_bytes()).hexdigest()
        actual_identity = hashlib.sha256((ROOT / "scripts" / "staging_restore_identity_readonly.py").read_bytes()).hexdigest()
        actual_sql = hashlib.sha256((ROOT / "specs" / "staging-restore-rehearsal" / "SOURCE_CHECK.sql").read_bytes()).hexdigest()
        self.assertIn(actual_create, OPS)
        self.assertIn(actual_identity, OPS)
        self.assertIn(actual_sql, OPS)
        stale = sorted(set(re.findall(r"staging_restore_create_temp\.py`, SHA256 `([0-9a-f]{64})`", OPS)) - {actual_create})
        print("OPERATIONS.md hash(es) attached to create script that differ from actual file:", stale)


if __name__ == "__main__":
    unittest.main()
