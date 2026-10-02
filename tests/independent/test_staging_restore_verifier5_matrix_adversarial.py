"""Verifier attempt 5 matrix probes (V1 identity script, V2 S1.1 create script).

Local only: every subprocess is a fake, /proc and filesystem are faked. Never
SSH/PostgreSQL/systemd. Each (subcommand, mutation) pair is driven through the
FULL main() flow of the real script, so the assertions are about behavior.

Oracle for an abnormal tool result at a read-only/precreate step: main returns 1,
no later subprocess runs, no success marker, canary never printed.
"""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import MagicMock, patch


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ID = load("v5_identity", "staging_restore_identity_readonly.py")
CR = load("v5_create", "staging_restore_create_temp.py")
CANARY = "V5_FAKE_SECRET_CANARY"
WARN = "WARNING: unexpected diagnostic " + CANARY

SYSTEMCTL = "ActiveState=active\nSubState=running\nMainPID=101\nUser=deploy"
LISTENER = 'LISTEN 0 200 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=3993835,fd=7))'
VERSION = "createdb (PostgreSQL) 16.15"

# Abnormal mutations: (name, function(valid_stdout) -> new stdout or None if N/A)
def _multi(text):
    return "\n" in text


ABNORMAL_STDOUT = [
    ("warning_suffix_line", lambda s: s + "\n" + WARN),
    ("warning_prefix_line", lambda s: WARN + "\n" + s),
    ("warning_inline_suffix", lambda s: s + " " + WARN),
    ("warning_inline_prefix", lambda s: WARN + " " + s),
    ("warning_middle", lambda s: s.replace("\n", "\n" + WARN + "\n", 1) if _multi(s) else None),
    ("blank_line_middle", lambda s: s.replace("\n", "\n\n", 1) if _multi(s) else None),
    ("blank_line_with_spaces_middle", lambda s: s.replace("\n", "\n   \n", 1) if _multi(s) else None),
    ("lower_warning_line", lambda s: s + "\nwarning: " + CANARY),
    ("notice_line", lambda s: s + "\nNOTICE:  " + CANARY),
    ("plain_garbage_line", lambda s: s + "\nsome unexpected text " + CANARY),
    ("plain_garbage_prefix", lambda s: "some unexpected text " + CANARY + "\n" + s),
    ("ansi_suffix", lambda s: s + "\x1b[31m"),
    ("ansi_prefix", lambda s: "\x1b[31m" + s),
    ("nul_suffix", lambda s: s + "\x00"),
    ("bell_prefix", lambda s: "\x07" + s),
    ("del_suffix", lambda s: s + "\x7f"),
    ("bom_prefix", lambda s: "﻿" + s),
    ("zero_width_suffix", lambda s: s + "​"),
    ("ctrl_inside", lambda s: s[:3] + "\x01" + s[3:]),
    ("cr_overwrite_line", lambda s: s + "\r" + WARN),
    ("unicode_lineSep_warning", lambda s: s + " " + WARN),
    ("vt_sep_warning", lambda s: s + "\x0b" + WARN),
    ("ff_sep_warning", lambda s: s + "\x0c" + WARN),
    ("nel_sep_warning", lambda s: s + "\x85" + WARN),
    ("empty_stdout", lambda s: ""),
    ("only_warning", lambda s: WARN),
    ("duplicate_output", lambda s: s + "\n" + s),
]
# Mutations that only normalise whitespace: reported, NOT counted as anomalies.
BENIGN_STDOUT = [
    ("leading_newline", lambda s: "\n" + s),
    ("trailing_newline", lambda s: s + "\n"),
    ("trailing_spaces", lambda s: s + "   \n"),
    ("crlf", lambda s: s.replace("\n", "\r\n")),
]
ABNORMAL_STDERR = [
    ("stderr_warning", WARN + "\n"),
    ("stderr_ansi", "\x1b[31m"),
    ("stderr_nul", "\x00"),
    ("stderr_garbage", "unexpected " + CANARY),
    ("stderr_notice", "NOTICE: " + CANARY + "\n"),
]


def fake_run_factory(valid_for, gate_fn, inject, trace, kind_counter):
    def run(args, **kwargs):
        gate = gate_fn(args, kind_counter)
        if re.search(r"(?i)\b(drop|terminate|pg_restore|--force|stop|restart)\b", " ".join(args)):
            raise AssertionError("forbidden command attempted: " + gate)
        trace.append(gate)
        stdout, stderr, code = valid_for(args, gate)
        target = inject.get("gate")
        if target is not None and gate == target:
            stdout, stderr, code = inject["apply"](stdout, stderr, code)
        if kwargs.get("stderr") == subprocess.DEVNULL:
            stderr = None
        return subprocess.CompletedProcess(args, code, stdout, stderr)
    return run


# ---------------------------------------------------------------- identity
def identity_gate(args, counter):
    kind = "systemctl" if args[0] == "systemctl" else "git"
    counter[kind] = counter.get(kind, 0) + 1
    return "%s#%d" % (kind, counter[kind])


def identity_valid(args, gate):
    if args[0] == "systemctl":
        return SYSTEMCTL + "\n", "", 0
    return ID.TARGET + "\n", "", 0


def run_identity(inject):
    trace, output, counter = [], io.StringIO(), {}
    fake = fake_run_factory(identity_valid, identity_gate, inject, trace, counter)
    with ExitStack() as stack:
        stack.enter_context(patch.object(ID.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(ID.sys, "argv", ["-"]))
        stack.enter_context(patch.object(ID, "expected_cwd", return_value="/srv/search-tools"))
        stack.enter_context(patch.object(ID, "process_cwd", return_value="/srv/search-tools"))
        stack.enter_context(patch.object(ID, "process_dsn", return_value=(
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging").encode()))
        stack.enter_context(patch.object(ID.subprocess, "run", side_effect=fake))
        stack.enter_context(redirect_stdout(output))
        code = ID.main()
    return code, output.getvalue(), trace


IDENTITY_GATES = ["systemctl#1", "git#1", "systemctl#2", "git#2"]


# ------------------------------------------------------------------ create
class FakeProcPath:
    def __init__(self, parts):
        self.parts = parts

    def resolve(self, strict=False):
        return CR.LIVE

    def read_bytes(self):
        return ("A=b\0DATABASE_URL=postgresql://deploy:" + CANARY +
                "@127.0.0.1:5432/search_tools_staging\0").encode()


def path_factory(real_path):
    def factory(*parts):
        if parts and parts[0] == "/proc":
            return FakeProcPath(parts)
        return real_path(*parts)
    return factory


def create_gate(args, counter):
    if args[0] == "systemctl":
        kind = "systemctl"
    elif args[0] == "git":
        kind = "git"
    elif args[0] == "/usr/bin/ss":
        kind = "ss"
    elif args[0] == "/usr/bin/locale":
        kind = "locale"
    elif args[0] == "/usr/lib/postgresql/16/bin/createdb":
        kind = "version"
    elif "/usr/lib/postgresql/16/bin/createdb" in args:
        kind = "create"
    elif "/usr/lib/postgresql/16/bin/psql" in args:
        if args[args.index("-d") + 1] == CR.SOURCE:
            kind = "source"
        elif any("read_only=off" in value for value in args):
            kind = "acl"
        else:
            kind = "temp"
    else:
        raise AssertionError("unexpected executable " + repr(args))
    counter[kind] = counter.get(kind, 0) + 1
    return "%s#%d" % (kind, counter[kind])


def make_create_valid(data_dir):
    source = dict(database=CR.SOURCE, oid=17001, version_num=160015, port=5432,
                  data_directory=str(data_dir), read_only="on", bytes=1228176407,
                  encoding="UTF8", provider="c", collate="en_US.UTF-8",
                  ctype="en_US.UTF-8", tablespace="pg_default", schema033=True,
                  temp_exists=False)
    dest = dict(database=CR.TEMP, oid=17002, owner="postgres", label=CR.LABEL,
                encoding="UTF8", provider="c", collate="en_US.UTF-8",
                ctype="en_US.UTF-8", tablespace="pg_default", public_connect=False,
                user_tables=0)
    table = {"systemctl": SYSTEMCTL, "git": CR.COMMIT, "ss": LISTENER,
             "locale": "C\nen_US.utf8", "version": VERSION, "create": "",
             "source": json.dumps(source), "acl": "REVOKE\nCOMMENT", "temp": json.dumps(dest)}

    def valid(args, gate):
        return table[gate.split("#")[0]] + "\n", "", 0
    return valid, table


# the gates of one clean run, in order
CREATE_GATES = ["systemctl#1", "git#1", "systemctl#2", "git#2", "ss#1", "source#1", "locale#1",
                "version#1", "create#1", "acl#1", "temp#1", "systemctl#3", "git#3",
                "systemctl#4", "git#4", "ss#2"]
PRECREATE = CREATE_GATES[:CREATE_GATES.index("create#1")]
POSTCREATE = CREATE_GATES[CREATE_GATES.index("create#1"):]


def run_create(inject, tmpdir):
    data = Path(tmpdir).resolve()
    (data / "postmaster.pid").write_bytes(b"3993835\nfixture\n")
    valid, _ = make_create_valid(data)
    trace, output, counter = [], io.StringIO(), {}
    fake = fake_run_factory(valid, create_gate, inject, trace, counter)
    live = MagicMock()
    live.is_dir.return_value = True
    live.is_symlink.return_value = False
    live.resolve.return_value = live
    live.__str__ = lambda self: "/srv/search-tools"
    base = MagicMock()
    base.is_dir.return_value = True
    base.is_symlink.return_value = False
    base.resolve.return_value = base
    free = MagicMock()
    free.free = 60_000_000_000
    with ExitStack() as stack:
        stack.enter_context(patch.object(CR.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(CR.sys, "argv", ["-", "--create"]))
        stack.enter_context(patch.object(CR, "LIVE", live))
        stack.enter_context(patch.object(CR, "Path", path_factory(CR.Path)))
        stack.enter_context(patch.object(CR, "DATA", data))
        stack.enter_context(patch.object(CR, "BASE", base))
        stack.enter_context(patch.object(CR, "archive_guard", return_value=("unchanged",)))
        stack.enter_context(patch.object(CR.shutil, "disk_usage", return_value=free))
        stack.enter_context(patch.object(CR.subprocess, "run", side_effect=fake))
        stack.enter_context(redirect_stdout(output))
        code = CR.main()
    return code, output.getvalue(), trace


def normal_apply(stdout, stderr, code):
    return stdout, stderr, code


class Base(unittest.TestCase):
    def assert_stopped(self, label, code, output, trace, gate, order, success):
        self.assertNotIn(CANARY, output, label + ": canary leaked")
        self.assertNotIn(success, output, label + ": success marker printed")
        self.assertEqual(code, 1, "%s: main returned %s, trace=%s" % (label, code, trace))
        self.assertEqual(trace[-1], gate, "%s: ran subprocess after abnormal gate: %s" % (label, trace))
        self.assertEqual(len(trace), order.index(gate) + 1, label + ": trace " + str(trace))


class IdentityMatrix(Base):
    def test_control_clean_run_succeeds_and_prints_no_canary(self):
        code, output, trace = run_identity({})
        self.assertEqual(code, 0, output)
        self.assertEqual(trace, IDENTITY_GATES)
        self.assertNotIn(CANARY, output)
        self.assertIn("STAGING_RUNTIME_IDENTITY_OK", output)

    def test_stdout_mutations_each_gate(self):
        for gate in IDENTITY_GATES:
            for name, fn in ABNORMAL_STDOUT:
                with self.subTest(gate=gate, mutation=name):
                    valid = identity_valid(["systemctl"] if gate.startswith("systemctl") else ["git"], gate)[0]
                    mutated = fn(valid.rstrip("\n"))
                    if mutated is None:
                        continue
                    inject = {"gate": gate, "apply": lambda o, e, c, m=mutated: (m + "\n", e, c)}
                    code, output, trace = run_identity(inject)
                    self.assert_stopped("%s/%s" % (gate, name), code, output, trace, gate,
                                        IDENTITY_GATES, "STAGING_RUNTIME_IDENTITY_OK")

    def test_stderr_and_exitcode_each_gate(self):
        for gate in IDENTITY_GATES:
            for name, text in ABNORMAL_STDERR:
                with self.subTest(gate=gate, mutation=name):
                    inject = {"gate": gate, "apply": lambda o, e, c, t=text: (o, t, c)}
                    code, output, trace = run_identity(inject)
                    self.assert_stopped("%s/%s" % (gate, name), code, output, trace, gate,
                                        IDENTITY_GATES, "STAGING_RUNTIME_IDENTITY_OK")
            for rc in (1, 2, 124, -9):
                with self.subTest(gate=gate, returncode=rc):
                    inject = {"gate": gate, "apply": lambda o, e, c, r=rc: (o, e, r)}
                    code, output, trace = run_identity(inject)
                    self.assert_stopped("%s/rc%s" % (gate, rc), code, output, trace, gate,
                                        IDENTITY_GATES, "STAGING_RUNTIME_IDENTITY_OK")

    def test_systemctl_wrong_values_stop(self):
        bad = ["ActiveState=inactive\nSubState=dead\nMainPID=0\nUser=deploy",
               "ActiveState=active\nSubState=running\nMainPID=101\nUser=root",
               "ActiveState=active\nSubState=running\nMainPID=0\nUser=deploy",
               "ActiveState=active\nSubState=running\nMainPID=\nUser=deploy",
               "ActiveState=active\nSubState=running\nMainPID=1٣\nUser=deploy",
               "ActiveState=active\nSubState=running\nMainPID=101\nUser=deploy\nUser=deploy"]
        for gate in ("systemctl#1", "systemctl#2"):
            for text in bad:
                with self.subTest(gate=gate, text=text):
                    inject = {"gate": gate, "apply": lambda o, e, c, t=text: (t + "\n", e, c)}
                    code, output, trace = run_identity(inject)
                    self.assertEqual(code, 1, output)
                    self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", output)
                    self.assertNotIn(CANARY, output)
                    self.assertEqual(trace[-1], gate, trace)

    def test_benign_whitespace_normalisation_reported(self):
        accepted = []
        for gate in IDENTITY_GATES:
            valid = identity_valid(["systemctl"] if gate.startswith("systemctl") else ["git"], gate)[0].rstrip("\n")
            for name, fn in BENIGN_STDOUT:
                mutated = fn(valid)
                inject = {"gate": gate, "apply": lambda o, e, c, m=mutated: (m, e, c)}
                code, output, trace = run_identity(inject)
                self.assertNotIn(CANARY, output)
                if code == 0:
                    accepted.append((gate, name))
        # informational only: whitespace-only differences carry no diagnostic text
        print("identity benign whitespace accepted:", len(accepted))


class CreateMatrix(Base):
    SUCCESS = "STAGING_TEMP_DATABASE_CREATED"

    def run_case(self, inject):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="v5-data-", dir=HERE) as folder:
            return run_create(inject, folder)

    def test_control_clean_run_succeeds(self):
        code, output, trace = self.run_case({})
        self.assertEqual(code, 0, output)
        self.assertEqual(trace, CREATE_GATES)
        self.assertEqual(trace.count("create#1"), 1)
        self.assertNotIn(CANARY, output)
        self.assertIn(self.SUCCESS, output)

    def probe(self, gate, label, transform):
        """transform(o, e, c) -> (o2, e2, c2) or None to skip when not applicable/equal."""
        state = {}

        def apply(o, e, c):
            result = transform(o.rstrip("\n"), e, c)
            if result is None or result == (o.rstrip("\n"), e, c):
                state["skipped"] = True
                return o, e, c
            state["applied"] = True
            return result[0] + "\n", result[1], result[2]
        code, output, trace = self.run_case({"gate": gate, "apply": apply})
        if state.get("skipped") and not state.get("applied"):
            return
        self.assertNotIn(CANARY, output, label + ": canary leaked")
        self.assertNotIn(self.SUCCESS, output, label + ": success marker printed")
        self.assertEqual(code, 1, "%s: main=%s trace=%s" % (label, code, trace))
        self.assertEqual(trace[-1], gate, "%s: continued after abnormal gate: %s" % (label, trace))
        self.assertEqual(len(trace), CREATE_GATES.index(gate) + 1, label)
        self.assertLessEqual(trace.count("create#1") + trace.count("create#2"), 1)
        if gate in PRECREATE:
            self.assertNotIn("create#1", trace, label + ": CREATE issued after precreate anomaly")
            self.assertNotIn("acl#1", trace, label)
            self.assertNotIn("HOLD", output)
        else:
            self.assertIn("HOLD", output, label + ": no HOLD after create attempted")

    def test_stdout_mutations_each_gate(self):
        skip_dup = {"ss#1", "ss#2", "locale#1"}  # repeated valid lines are legitimately valid there
        for gate in CREATE_GATES:
            for name, fn in ABNORMAL_STDOUT:
                if name == "duplicate_output" and (gate in skip_dup or gate.startswith("create")):
                    continue
                if name == "empty_stdout" and gate in ("create#1", "acl#1"):
                    continue  # empty is a valid shape for these write gates
                with self.subTest(gate=gate, mutation=name):
                    self.probe(gate, "%s/%s" % (gate, name),
                               lambda o, e, c, f=fn: (lambda m: None if m is None else (m, e, c))(f(o)))

    def test_stderr_and_exitcode_each_gate(self):
        for gate in CREATE_GATES:
            for name, text in ABNORMAL_STDERR:
                with self.subTest(gate=gate, mutation=name):
                    self.probe(gate, "%s/%s" % (gate, name), lambda o, e, c, t=text: (o, t, c))
            for rc in (1, 2, 124, -9):
                with self.subTest(gate=gate, returncode=rc):
                    self.probe(gate, "%s/rc%s" % (gate, rc), lambda o, e, c, r=rc: (o, e, r))

    def test_json_variants_each_json_gate(self):
        def variants(base):
            obj = json.loads(base)
            return {
                "json_plus_garbage": base + " garbage " + CANARY,
                "json_then_json": base + "\n" + base,
                "garbage_then_json": WARN + "\n" + base,
                "extra_key": json.dumps(dict(obj, extra=CANARY)),
                "missing_key": json.dumps({k: v for k, v in obj.items() if k != "oid"}),
                "duplicate_key": base[:-1] + ', "oid": 5}',
                "wrapped_list": "[" + base + "]",
                "nan": json.dumps(dict(obj, oid=0)).replace('"oid": 0', '"oid": NaN'),
                "string_ints": json.dumps({k: (str(v) if isinstance(v, int) and not isinstance(v, bool) else v)
                                           for k, v in obj.items()}),
                "bool_ints": json.dumps({k: (True if k == "oid" else v) for k, v in obj.items()}),
                "null_everything": json.dumps({k: None for k in obj}),
                "warning_key": json.dumps(dict(obj, warning=WARN)),
                "trailing_comma_garbage": base + ",",
            }
        for gate in ("source#1", "temp#1"):
            for name in variants('{"a": 1}') if False else [
                    "json_plus_garbage", "json_then_json", "garbage_then_json", "extra_key", "missing_key",
                    "duplicate_key", "wrapped_list", "nan", "string_ints", "bool_ints", "null_everything",
                    "warning_key", "trailing_comma_garbage"]:
                with self.subTest(gate=gate, variant=name):
                    self.probe(gate, "%s/%s" % (gate, name),
                               lambda o, e, c, n=name: (variants(o)[n], e, c))

    def test_semantic_wrong_values_stop(self):
        cases = [("source#1", k, v) for k, v in [
            ("version_num", 150000), ("port", 5433), ("read_only", "off"), ("encoding", "LATIN1"),
            ("temp_exists", True), ("schema033", False), ("bytes", 0), ("oid", 0),
            ("tablespace", "other"), ("data_directory", "/other"), ("database", "other_db"),
            ("provider", "i"), ("collate", "C"), ("ctype", "C")]]
        cases += [("temp#1", k, v) for k, v in [
            ("user_tables", 1), ("public_connect", True), ("owner", "deploy"), ("label", "other"),
            ("oid", 17001), ("database", CR.SOURCE), ("encoding", "SQL_ASCII")]]
        for gate, key, value in cases:
            with self.subTest(gate=gate, key=key):
                self.probe(gate, "%s/%s" % (gate, key),
                           lambda o, e, c, k=key, v=value: (json.dumps(dict(json.loads(o), **{k: v})), e, c))

    def test_float_valued_numbers_reported(self):
        """Informational only (not asserted): equal-valued floats."""
        state = {}
        def apply(o, e, c):
            d = json.loads(o)
            d.update(version_num=160015.0, port=5432.0)
            return json.dumps(d) + "\n", e, c
        code, output, trace = self.run_case({"gate": "source#1", "apply": apply})
        print("float-valued source metadata accepted:", code == 0)

    def test_post_create_anomalies_never_report_success_or_retry(self):
        for gate in POSTCREATE:
            for name, text in (("warn_suffix", WARN), ("garbage", "x " + CANARY)):
                with self.subTest(gate=gate, mutation=name):
                    self.probe(gate, "%s/%s" % (gate, name), lambda o, e, c, t=text: (o + "\n" + t, e, c))

    def test_no_secret_in_any_argv_or_env(self):
        import tempfile
        seen = []
        real_run = subprocess.run
        with tempfile.TemporaryDirectory(prefix="v5-argv-", dir=HERE) as folder:
            data = Path(folder).resolve()
            valid, _ = make_create_valid(data)
            counter = {}
            def spy(args, **kwargs):
                seen.append((list(args), dict(kwargs.get("env") or {})))
                gate = create_gate(args, counter)
                o, e, c = valid(args, gate)
                return subprocess.CompletedProcess(args, c, o, e)
            code, output, trace = self._run_with(spy, data)
        self.assertEqual(code, 0, output)
        self.assertTrue(seen)
        for args, env in seen:
            joined = " ".join(args) + " " + " ".join(env) + " " + " ".join(env.values())
            self.assertNotIn(CANARY, joined)
            self.assertNotIn("DATABASE_URL", joined)
            self.assertNotRegex(joined, r"postgres(ql)?://")
        self.assertNotIn(CANARY, output)
        # write-capable invocations are createdb / psql against the temp DB only
        writes = [a for a, _ in seen if any("read_only=off" in x for x in a)]
        for a in writes:
            self.assertTrue(CR.TEMP in a, a)
            self.assertFalse(any(x.lower() in ("drop", "--force", "-f") or "DROP" in x.upper() for x in a), a)

    def _run_with(self, fake, data):
        output = io.StringIO()
        live = MagicMock()
        live.is_dir.return_value = True
        live.is_symlink.return_value = False
        live.resolve.return_value = live
        live.__str__ = lambda self: "/srv/search-tools"
        base = MagicMock()
        base.is_dir.return_value = True
        base.is_symlink.return_value = False
        base.resolve.return_value = base
        free = MagicMock()
        free.free = 60_000_000_000
        (data / "postmaster.pid").write_bytes(b"3993835\nfixture\n")
        with ExitStack() as stack:
            stack.enter_context(patch.object(CR.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(CR.sys, "argv", ["-", "--create"]))
            stack.enter_context(patch.object(CR, "LIVE", live))
            stack.enter_context(patch.object(CR, "Path", path_factory(CR.Path)))
            stack.enter_context(patch.object(CR, "DATA", data))
            stack.enter_context(patch.object(CR, "BASE", base))
            stack.enter_context(patch.object(CR, "archive_guard", return_value=("unchanged",)))
            stack.enter_context(patch.object(CR.shutil, "disk_usage", return_value=free))
            stack.enter_context(patch.object(CR.subprocess, "run", side_effect=fake))
            stack.enter_context(redirect_stdout(output))
            code = CR.main()
        return code, output.getvalue(), None

    def test_benign_whitespace_normalisation_reported(self):
        accepted, rejected = [], []
        _, table = make_create_valid(Path("/x"))
        for gate in CREATE_GATES:
            base = table[gate.split("#")[0]]
            for name, fn in BENIGN_STDOUT:
                mutated = fn(base)
                inject = {"gate": gate, "apply": lambda o, e, c, m=mutated: (m, e, c)}
                code, output, trace = self.run_case(inject)
                self.assertNotIn(CANARY, output)
                (accepted if code == 0 else rejected).append((gate, name))
        print("create benign whitespace accepted:", len(accepted), "rejected:", rejected)


class ExceptionsAndRealSubprocess(Base):
    """Exceptions from the runner and real local subprocess behavior (python -c only)."""

    def raising(self, exc):
        def apply(o, e, c):
            raise exc
        return apply

    def test_runner_exceptions_stop_identity_each_gate(self):
        excs = [subprocess.TimeoutExpired(["x"], 20), FileNotFoundError(CANARY),
                PermissionError(CANARY), OSError(CANARY), UnicodeDecodeError("utf-8", b"\xff", 0, 1, CANARY),
                RuntimeError("postgresql://u:" + CANARY + "@h/db")]
        for gate in IDENTITY_GATES:
            for exc in excs:
                with self.subTest(gate=gate, exc=type(exc).__name__):
                    code, output, trace = run_identity({"gate": gate, "apply": self.raising(exc)})
                    self.assert_stopped(gate, code, output, trace, gate, IDENTITY_GATES,
                                        "STAGING_RUNTIME_IDENTITY_OK")

    def test_runner_exceptions_stop_create_each_gate(self):
        import tempfile
        excs = [subprocess.TimeoutExpired(["x"], 20), FileNotFoundError(CANARY),
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, CANARY),
                RuntimeError("postgresql://u:" + CANARY + "@h/db")]
        for gate in CREATE_GATES:
            for exc in excs:
                with self.subTest(gate=gate, exc=type(exc).__name__):
                    with tempfile.TemporaryDirectory(prefix="v5-exc-", dir=HERE) as folder:
                        code, output, trace = run_create({"gate": gate, "apply": self.raising(exc)}, folder)
                    self.assertNotIn(CANARY, output)
                    self.assertNotIn("STAGING_TEMP_DATABASE_CREATED", output)
                    self.assertEqual(code, 1, output)
                    self.assertEqual(trace[-1], gate, trace)
                    if gate in PRECREATE:
                        self.assertNotIn("create#1", trace)
                        self.assertNotIn("HOLD", output)
                    else:
                        self.assertIn("HOLD", output)

    def real(self, module, gate, code_text, extra=()):
        import sys
        return module.checked([sys.executable, "-B", "-c", code_text], gate)

    def test_real_subprocess_abnormal_outputs_raise(self):
        sys_stdout = "import sys; sys.stdout.write(%r)"
        valid_service = "ActiveState=active\nSubState=running\nMainPID=1\nUser=deploy\n"
        cases = {
            "stderr_warning": "import sys; sys.stderr.write(%r); print(%r)" % (WARN, valid_service.strip()),
            "stderr_control": "import sys; sys.stderr.write('\\x1b[0m'); print(%r)" % valid_service.strip(),
            "invalid_utf8": "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')",
            "nonzero_with_valid": "import sys; print(%r); sys.exit(3)" % valid_service.strip(),
            "stdout_warning_after": "print(%r); print(%r)" % (valid_service.strip(), WARN),
            "empty": "pass",
        }
        for module, gates in ((ID, ["WEB_SERVICE_READ", "WORKER_COMMIT_READ"]),
                              (CR, ["WEB_SERVICE_READ", "STAGING_LISTENER_READ", "SOURCE_LOCALE_AVAILABLE",
                                    "CREATEDB_VERSION", "STAGING_METADATA_READ"])):
            for gate in gates:
                for name, code_text in cases.items():
                    with self.subTest(module=module.__name__, gate=gate, case=name):
                        with self.assertRaises(module.GateFailure) as ctx:
                            if module is CR:
                                CR.checked(["python"] if False else [__import__("sys").executable, "-B", "-c", code_text],
                                           gate)
                            else:
                                self.real(module, gate, code_text)
                        self.assertNotIn(CANARY, str(ctx.exception))

    def test_real_subprocess_exact_valid_shapes_accepted(self):
        import sys
        code = "print(%r)" % "ActiveState=active\nSubState=running\nMainPID=1\nUser=deploy"
        self.assertTrue(ID.checked([sys.executable, "-B", "-c", code], "WEB_SERVICE_READ"))
        self.assertTrue(CR.checked([sys.executable, "-B", "-c", code], "WEB_SERVICE_READ"))


class IdentityDsnAdversarial(unittest.TestCase):
    BASE = "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging"

    def endpoint(self, dsn):
        return ID.staging_endpoint(dsn.encode())

    def test_dsn_variants_rejected(self):
        bad = [
            self.BASE + "?host=evil.example", self.BASE + "?hostaddr=10.0.0.5",
            self.BASE + "?dbname=search_tools_prod", self.BASE + "?service=other",
            self.BASE + "?options=-c%20search_path=x", self.BASE + "?user=postgres",
            self.BASE + "?sslmode=", self.BASE + "?sslmode=a&sslmode=b", self.BASE + "?passfile=/x",
            self.BASE.replace("postgresql://", "mysql://"),
            "postgresql://127.0.0.1:5432/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@evil.example:5432/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@127.0.0.1.evil.example:5432/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:0/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:99999/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_prod",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_staging/extra",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/Search_Tools_Staging",
            "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/",
            self.BASE + "#frag",
            "postgresql://deploy:" + CANARY + "@/search_tools_staging",
            "postgresql://deploy:" + CANARY + "@[::1:5432/search_tools_staging",
            "postgresql:///search_tools_staging?host=/var/run/postgresql",
        ]
        for dsn in bad:
            with self.subTest(dsn=dsn.replace(CANARY, "CANARY")):
                with self.assertRaises(ID.GateFailure) as ctx:
                    self.endpoint(dsn)
                self.assertNotIn(CANARY, str(ctx.exception))

    def test_main_never_prints_secret_when_dsn_gate_fails(self):
        for bad in (self.BASE + "?host=evil", self.BASE.replace("5432/search_tools_staging", "5432/other")):
            out = io.StringIO()
            with ExitStack() as stack:
                stack.enter_context(patch.object(ID.os, "geteuid", return_value=0))
                stack.enter_context(patch.object(ID.sys, "argv", ["-"]))
                stack.enter_context(patch.object(ID, "expected_cwd", return_value="/srv/search-tools"))
                stack.enter_context(patch.object(ID, "process_cwd", return_value="/srv/search-tools"))
                stack.enter_context(patch.object(ID, "process_dsn", return_value=bad.encode()))
                stack.enter_context(patch.object(ID.subprocess, "run", side_effect=lambda a, **k: subprocess.CompletedProcess(
                    a, 0, (SYSTEMCTL if a[0] == "systemctl" else ID.TARGET) + "\n", "")))
                stack.enter_context(redirect_stdout(out))
                code = ID.main()
            self.assertEqual(code, 1)
            self.assertNotIn(CANARY, out.getvalue())
            self.assertNotIn("STAGING_RUNTIME_IDENTITY_OK", out.getvalue())

    def test_web_worker_different_dsn_stops(self):
        seq = iter([self.BASE.encode(), (self.BASE + "?connect_timeout=5").encode()])
        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ID.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(ID.sys, "argv", ["-"]))
            stack.enter_context(patch.object(ID, "expected_cwd", return_value="/srv/search-tools"))
            stack.enter_context(patch.object(ID, "process_cwd", return_value="/srv/search-tools"))
            stack.enter_context(patch.object(ID, "process_dsn", side_effect=lambda pid: next(seq)))
            stack.enter_context(patch.object(ID.subprocess, "run", side_effect=lambda a, **k: subprocess.CompletedProcess(
                a, 0, (SYSTEMCTL if a[0] == "systemctl" else ID.TARGET) + "\n", "")))
            stack.enter_context(redirect_stdout(out))
            code = ID.main()
        self.assertEqual(code, 1)
        self.assertIn("STAGING_WEB_WORKER_SAME_DSN", out.getvalue())
        self.assertNotIn(CANARY, out.getvalue())

    def test_embedded_whitespace_dsn_reported(self):
        """Informational: urlsplit drops tab/newline; not asserted."""
        dsn = "postgresql://deploy:" + CANARY + "@127.0.0.1:5432/search_tools_stag\ning"
        try:
            self.endpoint(dsn)
            print("DSN with embedded newline accepted")
        except ID.GateFailure:
            print("DSN with embedded newline rejected")


class ArchiveHashShape(unittest.TestCase):
    def test_pinned_archive_hash_is_a_valid_sha256_value(self):
        # A SHA-256 hex digest is exactly 64 lowercase hex characters; a pinned
        # value of any other length can never equal hashlib's hexdigest().
        self.assertRegex(CR.ARCHIVE_SHA, r"[0-9a-f]{64}", "len=%d" % len(CR.ARCHIVE_SHA))

    def test_archive_guard_pin_is_satisfiable_by_a_matching_file(self):
        """Control: guard passes for a fixture whose true digest is pinned; then the
        script's own pin must be a same-shape value or it can never pass in the field."""
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory(prefix="v5-arch-", dir=HERE) as folder:
            path = Path(folder).resolve() / "a.dump"
            path.write_bytes(b"x" * 100)
            info = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(CR, "ARCHIVE", path), patch.object(CR, "ARCHIVE_BYTES", 100), \
                    patch.object(CR, "ARCHIVE_MTIME_NS", info.st_mtime_ns), \
                    patch.object(CR, "ARCHIVE_SHA", digest):
                self.assertTrue(CR.archive_guard())
            with patch.object(CR, "ARCHIVE", path), patch.object(CR, "ARCHIVE_BYTES", 100), \
                    patch.object(CR, "ARCHIVE_MTIME_NS", info.st_mtime_ns), \
                    patch.object(CR, "ARCHIVE_SHA", digest[:-1]):
                with self.assertRaises(CR.GateFailure):
                    CR.archive_guard()
        self.assertEqual(len(CR.ARCHIVE_SHA), 64, "pinned value can never equal a SHA-256 hexdigest")


class InformationalControlCharacterAcceptance(unittest.TestCase):
    def test_report_edge_and_separator_control_characters(self):
        svc = "ActiveState=active\nSubState=running\nMainPID=1\nUser=deploy"
        accepted = []
        for name, text in [("trailing_x1f", svc + "\x1f"), ("leading_x1c", "\x1c" + svc),
                           ("trailing_nbsp", svc + "\xa0"), ("x0b_separator", svc.replace("\nSubState", "\x0bSubState")),
                           ("x85_separator", svc.replace("\nSubState", "\x85SubState")),
                           ("u2028_separator", svc.replace("\nSubState", "\u2028SubState"))]:
            for module in (ID, CR):
                with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess(
                        ["x"], 0, text, "")):
                    try:
                        module.checked(["x"], "WEB_SERVICE_READ")
                        accepted.append((module.__name__, name))
                    except module.GateFailure:
                        pass
        print("control characters silently accepted (informational):", len(accepted), "of 12")


if __name__ == "__main__":
    unittest.main()
