"""Tests for bench_day.py.

Pure functions are tested directly; the CLI is run end to end in a subprocess
against fake_harness.py (a protocol-conformant fake solution), with PATH
scrubbed so a real hyperfine is never used. Tests that need hyperfine put a
fake `hyperfine` script first on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import bench_day as bd  # noqa: E402

BENCH_DAY = SCRIPTS / "bench_day.py"
FAKE = SCRIPTS / "fake_harness.py"
POSIX_ONLY = unittest.skipIf(os.name == "nt", "fake hyperfine is a shebang script")

FAKE_HYPERFINE = """#!{python}
import json, os, shlex, subprocess, sys
args = sys.argv[1:]
with open(os.environ["FAKE_HF_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({{"argv": args, "cwd": os.getcwd()}}) + "\\n")
if os.environ.get("FAKE_HF_FAIL"):
    print("Error: command terminated with non-zero exit code", file=sys.stderr)
    sys.exit(1)
if len(args) != 9 or args[:6] != ["-N", "--warmup", "3", "--style", "none", "--export-json"] or args[7] != "--":
    print("fake hyperfine: unexpected arguments %r" % (args,), file=sys.stderr)
    sys.exit(2)
# -N: hyperfine splits the command string itself; run it once to prove it works.
run = subprocess.run(shlex.split(args[8]), capture_output=True, text=True)
if run.returncode != 0 or run.stdout not in ("3\\n", "9\\n"):
    print("fake hyperfine: command failed %r" % (run,), file=sys.stderr)
    sys.exit(3)
stats = {{"mean": 0.000912, "stddev": 0.000038, "median": 0.0009, "min": 0.0008, "max": 0.0011}}
if os.environ.get("FAKE_HF_SCHEMA") == "2":  # hyperfine 2.x layout
    stats.update(unit="second", count=7)
    doc = {{"schema_version": 2, "primary_metric": "time_wall_clock",
           "results": [{{"command": args[8], "measurements": [], "summary": {{"time_wall_clock": stats}}}}]}}
else:  # hyperfine 1.x layout
    doc = {{"results": [{{"command": args[8], "times": [0.0009] * 7, **stats}}]}}
with open(args[6], "w", encoding="utf-8") as f:
    json.dump(doc, f)
"""

WALL = {"mean_s": 0.000912, "stddev_s": 0.000038, "median_s": 0.0009, "min_s": 0.0008, "max_s": 0.0011, "runs": 7}


def doc(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema": 1, "part": 1, "iterations": 3, "ns": [1, 2, 3], "answer": "3",
        "max_rss_before": 100, "max_rss_after": 200,
    }
    base.update(overrides)
    return base


def line(d: dict[str, object]) -> str:
    return json.dumps(d, separators=(",", ":")) + "\n"


class IterationTest(unittest.TestCase):
    def test_compute_iterations(self) -> None:
        self.assertEqual(bd.compute_iterations(1.0, 1_000_000, 10, 100_000), 1000)
        self.assertEqual(bd.compute_iterations(1.0, 10, 10, 100_000), 100_000)  # clamped high
        self.assertEqual(bd.compute_iterations(1.0, 2e9, 10, 100_000), 10)  # clamped low
        self.assertEqual(bd.compute_iterations(1.0, 0, 10, 100_000), 100_000)  # t_cal = 0
        self.assertEqual(bd.compute_iterations(1e-9, 0, 1, 5), 1)
        self.assertEqual(bd.compute_iterations(0.5, 3e6, 1, 1000), 167)  # rounded

    def test_warmup_rule(self) -> None:
        for n, k in [(1, 1), (10, 1), (19, 1), (20, 2), (100, 10), (999, 99), (1000, 100), (100_000, 100)]:
            with self.subTest(n=n):
                self.assertEqual(bd.warmup_for(n), k)

    def test_calibration_iterations(self) -> None:
        self.assertEqual(bd.calibration_iterations(1_000_000), 100)  # 0.1 s / 1 ms
        self.assertEqual(bd.calibration_iterations(3_000_000), 33)  # rounded
        self.assertEqual(bd.calibration_iterations(50_000_000), 5)  # clamped low
        self.assertEqual(bd.calibration_iterations(99_999_999), 5)
        self.assertEqual(bd.calibration_iterations(1000), 1000)  # clamped high
        self.assertEqual(bd.calibration_iterations(0), 1000)  # t1 = 0

    def test_warm_median(self) -> None:
        self.assertEqual(bd.warm_median([5000, 300, 310, 320, 330]), 315)  # first 20% dropped
        self.assertEqual(bd.warm_median([9, 9] + [1] * 8), 1)
        self.assertEqual(bd.warm_median([7, 1, 2, 3]), 2.5)  # 4 // 5 == 0: nothing dropped

    def test_trim(self) -> None:
        self.assertEqual(bd.trim_warmup([9, 9, 1, 2], 2), [1, 2])
        self.assertEqual(bd.trim_warmup([1, 2], 0), [1, 2])


class StatsTest(unittest.TestCase):
    def test_basic(self) -> None:
        s = bd.summarize([4, 1, 3, 2])
        self.assertEqual((s["min"], s["median"], s["mean"], s["max"]), (1, 2.5, 2.5, 4))
        self.assertAlmostEqual(s["stddev"], 1.2909944, places=6)

    def test_single_sample(self) -> None:
        self.assertEqual(bd.summarize([7]), {"min": 7, "median": 7, "mean": 7.0, "stddev": 0.0, "p99": 7, "max": 7})

    def test_p99_nearest_rank(self) -> None:
        self.assertEqual(bd.summarize(list(range(1, 101)))["p99"], 99)
        self.assertEqual(bd.summarize(list(range(101, 0, -1)))["p99"], 100)
        self.assertEqual(bd.summarize([5, 1])["p99"], 5)

    def test_empty(self) -> None:
        with self.assertRaises(ValueError):
            bd.summarize([])


class FormatTest(unittest.TestCase):
    def test_fmt_ns(self) -> None:
        cases = [
            (0, "0 ns"), (0.47, "0.47 ns"), (5.2, "5.20 ns"), (9.996, "10.0 ns"), (123, "123 ns"),
            (999.4, "999 ns"), (999.6, "1.00 µs"), (1234, "1.23 µs"), (12_345, "12.3 µs"),
            (123_456, "123 µs"), (10_260_000, "10.3 ms"), (1.5e9, "1.50 s"), (5e12, "5000 s"),
        ]
        for value, text in cases:
            with self.subTest(value=value):
                self.assertEqual(bd.fmt_ns(value), text)

    def test_fmt_bytes(self) -> None:
        cases = [
            (0, "0 B"), (512, "512 B"), (1023, "1023 B"), (1024, "1.00 KiB"), (4096, "4.00 KiB"),
            (1_048_575, "1.00 MiB"), (25_690_112, "24.5 MiB"), (128 * 1024**2, "128 MiB"),
            (2 * 1024**3, "2.00 GiB"),
        ]
        for value, text in cases:
            with self.subTest(value=value):
                self.assertEqual(bd.fmt_bytes(value), text)

    def test_render_table_golden(self) -> None:
        data = {
            "parts": [
                {
                    "part": 1, "answer": "SECRET-1", "iterations": 100000, "warmup": 100,
                    "ns": {"min": 110, "median": 120, "mean": 123.0, "stddev": 5.2, "p99": 150, "max": 300},
                    "rss_delta_bytes": 0,
                    "wall": {"mean_s": 0.000912, "stddev_s": 0.000038, "median_s": 0.0009,
                             "min_s": 0.0008, "max_s": 0.001, "runs": 300},
                },
                {
                    "part": 2, "answer": "SECRET-2", "iterations": 97, "warmup": 9,
                    "ns": {"min": 10_100_000, "median": 10_200_000, "mean": 10_300_000.0,
                           "stddev": 120_000.0, "p99": 10_900_000, "max": 11_000_000},
                    "rss_delta_bytes": 25_690_112,
                    "wall": None,
                },
            ]
        }
        table = bd.render_table(data)
        self.assertEqual(
            table,
            "| Part | Min | Median | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
            "| 1 | 110 ns | 120 ns | 123 ns ± 5.20 ns | 150 ns | 100000 | 0 B | 912 µs ± 38.0 µs |\n"
            "| 2 | 10.1 ms | 10.2 ms | 10.3 ms ± 120 µs | 10.9 ms | 97 | 24.5 MiB | n/a |",
        )
        self.assertNotIn("SECRET", table)
        data["parts"][1]["rss_delta_bytes"] = None  # type: ignore[index]
        self.assertTrue(bd.render_table(data).endswith("| 97 | n/a | n/a |"))


class HyperfineJsonTest(unittest.TestCase):
    STATS = {"mean": 0.000912, "stddev": 0.000038, "median": 0.0009, "min": 0.0008, "max": 0.0011}

    def test_v1(self) -> None:
        doc = {"results": [{"command": "x", "times": [0.0009] * 7, **self.STATS}]}
        self.assertEqual(bd.parse_hyperfine_json(doc), WALL)

    def test_v2(self) -> None:
        stats = {**self.STATS, "unit": "second", "count": 7}
        doc = {"schema_version": 2, "results": [{"summary": {"time_wall_clock": stats, "time_cpu": {}}}]}
        self.assertEqual(bd.parse_hyperfine_json(doc), WALL)

    def test_v2_non_second_unit_rejected(self) -> None:
        stats = {**self.STATS, "unit": "millisecond", "count": 7}
        doc = {"schema_version": 2, "results": [{"summary": {"time_wall_clock": stats}}]}
        with self.assertRaisesRegex(ValueError, "unexpected --export-json format .*'millisecond'"):
            bd.parse_hyperfine_json(doc)
        del stats["unit"]  # no unit: seconds
        self.assertEqual(bd.parse_hyperfine_json(doc), WALL)

    def test_null_stddev(self) -> None:
        doc = {"results": [{"times": [0.5], **self.STATS, "stddev": None}]}
        self.assertEqual(bd.parse_hyperfine_json(doc)["stddev_s"], 0.0)

    def test_rejects(self) -> None:
        for doc in ({}, {"results": []}, {"results": [{"times": []}]}, [1], {"results": [{"summary": {}}]},
                    {"results": [{"times": [1], **self.STATS, "mean": "nan"}]}):
            with self.subTest(doc=doc), self.assertRaisesRegex(ValueError, "unexpected --export-json format"):
                bd.parse_hyperfine_json(doc)


class WriteJsonTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_atomic_write_uses_umask_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench.json"
            for umask, mode in ((0o022, 0o644), (0o002, 0o664), (0o077, 0o600)):
                with self.subTest(umask=oct(umask)):
                    old = os.umask(umask)
                    try:
                        bd.write_json_atomic(path, {"schema": 1})
                    finally:
                        os.umask(old)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    self.assertEqual(json.loads(path.read_text()), {"schema": 1})
            self.assertEqual(os.listdir(tmp), ["bench.json"])

    def test_write_text_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "benchmarks.md"
            path.write_text("old\r\n")
            bd.write_text_atomic(path, "# Benchmarks\n\n| µ |\n")
            self.assertEqual(path.read_bytes(), "# Benchmarks\n\n| µ |\n".encode())
            self.assertEqual(os.listdir(tmp), ["benchmarks.md"])
            (Path(tmp) / "dir").mkdir()
            with self.assertRaises(OSError):
                bd.write_text_atomic(Path(tmp) / "dir", "x")  # replace onto a directory fails
            self.assertEqual(sorted(os.listdir(tmp)), ["benchmarks.md", "dir"])  # no temp file left

    def test_sort_keys_and_newlines(self) -> None:
        data = {"solutions": {"b": 1, "a": "µ"}, "schema": 1}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            bd.write_json_atomic(path, data, sort_keys=True)
            raw = path.read_bytes()
            self.assertEqual(raw, '{\n  "schema": 1,\n  "solutions": {\n    "a": "µ",\n    "b": 1\n  }\n}\n'.encode())
            bd.write_json_atomic(path, data)  # default: insertion order
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertLess(raw.index(b'"solutions"'), raw.index(b'"schema"'))
            self.assertLess(raw.index(b'"b"'), raw.index(b'"a"'))


GIT = shutil.which("git")


@unittest.skipIf(GIT is None, "git is not on PATH")
class GitCommitTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        # Never let git find a repo above the scratch directory (e.g. a TMPDIR inside a checkout).
        env = mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)})
        env.start()
        self.addCleanup(env.stop)  # also restores the variables popped below
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(key, None)

    def git(self, repo: Path, *args: str) -> str:
        cmd = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false",
               "-c", "init.defaultBranch=main", *args]
        return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    def repo(self) -> Path:
        repo = self.tmp / "year"
        (repo / "days" / "day-01-rust-v1").mkdir(parents=True)
        self.git(repo, "init", "-q")
        (repo / ".gitignore").write_text("input.txt\n")
        (repo / "days" / "day-01-rust-v1" / "main.rs").write_text("fn main() {}\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "init")
        return repo

    def test_clean(self) -> None:
        repo = self.repo()
        sol = repo / "days" / "day-01-rust-v1"
        head = self.git(repo, "rev-parse", "--short", "HEAD")
        self.assertRegex(head, r"^[0-9a-f]{7,}$")
        self.assertEqual(bd.git_commit(sol), head)
        # Untracked and ignored files never make the tree dirty.
        (sol / "input.txt").write_text("puzzle\n")
        (sol / "bench.json").write_text("{}\n")
        self.assertEqual(bd.git_commit(sol), head)

    def test_dirty(self) -> None:
        repo = self.repo()
        sol = repo / "days" / "day-01-rust-v1"
        (sol / "main.rs").write_text("fn main() { println!(); }\n")
        head = self.git(repo, "rev-parse", "--short", "HEAD")
        self.assertEqual(bd.git_commit(sol), f"{head} (dirty)")

    def test_generated_files_are_not_dirty(self) -> None:
        repo = self.repo()
        sol = repo / "days" / "day-01-rust-v1"
        (repo / "benchmarks.md").write_text("# Benchmarks\n")
        (repo / "benchmarks.json").write_text("{}\n")
        (sol / "benchmarks.md").write_text("not generated\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", "benchmarks")
        head = self.git(repo, "rev-parse", "--short", "HEAD")
        # Rewritten by collect_benches.py after a bench: still clean.
        (repo / "benchmarks.md").write_text("# Benchmarks\n\nnew\n")
        (repo / "benchmarks.json").write_text('{"schema": 1}\n')
        self.assertEqual(bd.git_commit(sol), head)
        self.assertEqual(bd.git_commit(repo), head)
        # The exclusion is anchored at the repo root: a nested benchmarks.md counts.
        (sol / "benchmarks.md").write_text("changed\n")
        self.assertEqual(bd.git_commit(sol), f"{head} (dirty)")
        self.git(repo, "checkout", "--", "days")
        self.assertEqual(bd.git_commit(sol), head)
        # Any other tracked file counts.
        (sol / "main.rs").write_text("fn main() { println!(); }\n")
        self.assertEqual(bd.git_commit(sol), f"{head} (dirty)")

    def test_outside_a_repo(self) -> None:
        outside = self.tmp / "plain"
        outside.mkdir()
        self.assertIsNone(bd.git_commit(outside))

    def test_repo_without_commits(self) -> None:
        repo = self.tmp / "empty"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.assertIsNone(bd.git_commit(repo))


class EnvironmentTest(unittest.TestCase):
    def test_git_missing(self) -> None:
        with mock.patch.object(bd.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(bd.git_commit(Path.cwd()))

    def test_cpu_and_os_names(self) -> None:
        for value in (bd.cpu_name(), bd.os_name()):
            self.assertIsInstance(value, str)
            self.assertTrue(value.strip())
        self.assertRegex(bd.os_name(), r"\(.+\)$")


class ParseTest(unittest.TestCase):
    def parse(self, stdout: str, part: int = 1, iterations: int = 3) -> dict[str, object]:
        return bd.parse_bench_output(stdout, part, iterations)

    def rejects(self, stdout: str, pattern: str, part: int = 1, iterations: int = 3) -> None:
        with self.assertRaisesRegex(bd.ProtocolError, pattern):
            self.parse(stdout, part, iterations)

    def test_accepts(self) -> None:
        self.assertEqual(self.parse(line(doc())), doc())
        self.assertEqual(self.parse(line(doc(max_rss_before=None, max_rss_after=None)))["max_rss_after"], None)
        self.assertEqual(self.parse(line(doc(answer="a \"q\"\n\\")))["answer"], "a \"q\"\n\\")
        self.assertEqual(self.parse(line(doc(ns=[0, 0, 0], max_rss_after=100)))["ns"], [0, 0, 0])

    def test_unicode_line_separators_in_answer(self) -> None:
        answer = "a\u2028b\u0085c\u2029d"
        raw = json.dumps(doc(answer=answer), separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(self.parse(raw + "\n")["answer"], answer)
        self.assertEqual(self.parse(raw + "\r\n")["answer"], answer)  # trailing \r tolerated

    def test_rejects(self) -> None:
        cases = [
            ("", "printed nothing"),
            ("\n  \n", "printed nothing"),
            ("debug\n" + line(doc()), "printed 2 lines"),
            ("not json\n", "not JSON"),
            ("[1, 2]\n", "not a JSON object"),
            (line(doc(extra=1)), "unknown key.*extra"),
            (line({k: v for k, v in doc().items() if k != "answer"}), "missing key.*answer"),
            (line(doc(schema=2)), "unsupported schema 2"),
            (line(doc(schema=True)), "unsupported schema True"),
            (line(doc(part=2)), "part 2, expected 1"),
            (line(doc(iterations=4)), "iterations 4, expected 3"),
            (line(doc(ns=[1, 2])), "ns has 2 samples, expected 3"),
            (line(doc(ns="123")), "ns is not a list"),
            (line(doc(ns=[1, -2, 3])), "non-negative integers"),
            (line(doc(ns=[1, 2.5, 3])), "non-negative integers"),
            (line(doc(ns=[1, True, 3])), "non-negative integers"),
            (line(doc(answer=3)), "answer must be a string"),
            (line(doc(max_rss_before="1")), "max_rss_before must be"),
            (line(doc(max_rss_before=None)), "both be integers or both null"),
            (line(doc(max_rss_before=300)), r"max_rss_after \(200\) is smaller"),
        ]
        for stdout, pattern in cases:
            with self.subTest(stdout=stdout[:40]):
                self.rejects(stdout, pattern)

    def test_rejects_fake_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.txt"
            inp.write_text("x\n")
            cases = {
                "garbage": "not JSON",
                "two-lines": "printed 2 lines",
                "short-ns": "ns has 4 samples, expected 5",
                "wrong-part": "part 2, expected 1",
                "extra-key": "unknown key.*extra",
                "no-stdout": "printed nothing",
            }
            for mode, pattern in cases.items():
                with self.subTest(mode=mode):
                    env = {**os.environ, "FAKE_MODE": mode}
                    r = subprocess.run([sys.executable, str(FAKE), "bench", "1", str(inp), "5"],
                                       capture_output=True, text=True, env=env, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.rejects(r.stdout, pattern, 1, 5)


class DriverTest(unittest.TestCase):
    """bench_day.py end to end against fake_harness.py."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.sol = self.tmp / "day-01-fake-v1"
        self.sol.mkdir()
        (self.sol / "input.txt").write_text("foo\nbar\nbaz\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.counter = self.tmp / "calls.log"
        self.hf_log = self.tmp / "hyperfine.log"

    def run_driver(
        self, *args: str, cmd: list[str] | None = None, **env: str
    ) -> subprocess.CompletedProcess[str]:
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_")}
        clean.update(PATH=str(self.bin), FAKE_COUNTER=str(self.counter), FAKE_HF_LOG=str(self.hf_log),
                     PYTHONIOENCODING="utf-8")
        clean.update(env)
        cmd = [sys.executable, str(FAKE)] if cmd is None else cmd
        return subprocess.run(
            [sys.executable, str(BENCH_DAY), *args, "--", *cmd],
            cwd=self.sol, capture_output=True, text=True, encoding="utf-8", env=clean, timeout=120,
        )

    def calls(self) -> list[list[str]]:
        if not self.counter.exists():
            return []
        return [json.loads(x) for x in self.counter.read_text().splitlines()]

    def hyperfine_calls(self) -> list[dict[str, object]]:
        if not self.hf_log.exists():
            return []
        return [json.loads(x) for x in self.hf_log.read_text().splitlines()]

    def bench_json(self) -> dict[str, object]:
        return json.loads((self.sol / "bench.json").read_text(encoding="utf-8"))

    def install_hyperfine(self) -> None:
        hf = self.bin / "hyperfine"
        hf.write_text(FAKE_HYPERFINE.format(python=sys.executable))
        hf.chmod(hf.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def assert_failed(self, r: subprocess.CompletedProcess[str], code: int, message: str) -> None:
        self.assertEqual(r.returncode, code, r.stderr)
        self.assertIn(f"error: {message}", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.sol / "bench.json").exists())

    def test_bench_json_schema(self) -> None:
        r = self.run_driver("--time", "0.00002", "--no-hyperfine", FAKE_NS="1000")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.bench_json()
        self.assertEqual(
            sorted(data),
            ["benched_at", "command", "commit", "input", "name", "parts", "schema", "settings", "system"],
        )
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["name"], "day-01-fake-v1")  # defaults to the cwd name
        self.assertEqual(data["command"], [sys.executable, str(FAKE)])
        self.assertEqual(data["input"], "input.txt")
        self.assertRegex(data["benched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        # The scrubbed PATH hides git, so commit is normally null here; either way it has this shape.
        commit = data["commit"]
        self.assertTrue(commit is None or re.fullmatch(r"[0-9a-f]{7,}( \(dirty\))?", commit), commit)
        self.assertEqual(sorted(data["system"]), ["cpu", "os"])
        for key in ("cpu", "os"):
            self.assertIsInstance(data["system"][key], str)
            self.assertTrue(data["system"][key].strip(), data["system"])
        self.assertEqual(data["settings"], {"time": 2e-05, "min_iterations": 10, "max_iterations": 100000})
        parts = data["parts"]
        self.assertEqual([p["part"] for p in parts], [1, 2])
        self.assertEqual([p["answer"] for p in parts], ["3", "9"])
        for p in parts:
            self.assertEqual(
                sorted(p), ["answer", "iterations", "ns", "part", "rss_delta_bytes", "wall", "warmup"]
            )
            self.assertEqual((p["iterations"], p["warmup"]), (20, 2))
            self.assertEqual(p["ns"], {"min": 1000, "median": 1000, "mean": 1000.0, "stddev": 0.0,
                                       "p99": 1000, "max": 1000})
            self.assertEqual(p["rss_delta_bytes"], 4096)
            self.assertIsNone(p["wall"])
        inp = str((self.sol / "input.txt").resolve())
        # Per part: run-mode check, calibration stage 1 and 2 (0.1 s / 1 µs, clamped), measured run.
        self.assertEqual(self.calls(), [
            ["1"], ["bench", "1", inp, "1"], ["bench", "1", inp, "1000"], ["bench", "1", inp, "22"],
            ["2"], ["bench", "2", inp, "1"], ["bench", "2", inp, "1000"], ["bench", "2", inp, "22"],
        ])
        self.assertIn("part 1: ~1.00 µs/iter, running 20 (+2 warmup) iterations (~", r.stderr)
        self.assertTrue(r.stdout.startswith("| Part | Min | Median |"), r.stdout)
        self.assertIn("| 1 | 1.00 µs | 1.00 µs | 1.00 µs ± 0 ns | 1.00 µs | 20 | 4.00 KiB | n/a |", r.stdout)
        self.assertTrue(r.stdout.endswith("wrote bench.json\n"))
        self.assertNotIn("hyperfine not found", r.stderr)  # --no-hyperfine: no note
        # Atomic write leaves no temp file behind.
        self.assertEqual(sorted(p.name for p in self.sol.iterdir()), ["bench.json", "input.txt"])

    def test_warmup_trimmed(self) -> None:
        r = self.run_driver("--iterations", "2", "--warmup", "2", "--no-hyperfine", FAKE_NS="9000,9000,100,300")
        self.assertEqual(r.returncode, 0, r.stderr)
        ns = self.bench_json()["parts"][0]["ns"]
        self.assertEqual((ns["min"], ns["max"], ns["mean"]), (100, 300, 200.0))

    def test_calibration_uses_warm_median(self) -> None:
        # Stage 1 sees the cold 5000 ns; stage 2 runs 1000 iterations whose warm median is 300 ns.
        r = self.run_driver("--time", "0.0003", "--no-hyperfine", FAKE_NS="5000,300,300,300,300")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("part 1: ~300 ns/iter, running 1000 (+100 warmup) iterations (~", r.stderr)
        bench_sizes = [c[-1] for c in self.calls() if c[0] == "bench" and c[1] == "1"]
        self.assertEqual(bench_sizes, ["1", "1000", "1100"])

    def test_slow_part_single_calibration_run(self) -> None:
        r = self.run_driver("--no-hyperfine", FAKE_NS="100000000")
        self.assertEqual(r.returncode, 0, r.stderr)
        for part in ("1", "2"):
            bench_sizes = [c[-1] for c in self.calls() if c[0] == "bench" and c[1] == part]
            self.assertEqual(bench_sizes, ["1", "11"])  # one calibration run, then N=10 (+1 warmup)
        self.assertEqual([p["iterations"] for p in self.bench_json()["parts"]], [10, 10])
        self.assertIn("part 1: ~100 ms/iter, running 10 (+1 warmup) iterations (~1.10 s)", r.stderr)

    def test_iterations_skips_calibration(self) -> None:
        r = self.run_driver("--iterations", "7", "--no-hyperfine")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([c for c in self.calls() if c[0] == "bench"]), 2)
        self.assertTrue(all(c[-1] == "8" for c in self.calls() if c[0] == "bench"))  # 7 + warmup 1
        self.assertEqual([(p["iterations"], p["warmup"]) for p in self.bench_json()["parts"]], [(7, 1), (7, 1)])
        self.assertIn("part 1: running 7 (+1 warmup) iterations\n", r.stderr)

    @POSIX_ONLY
    def test_other_input_skips_run_check_and_hyperfine(self) -> None:
        self.install_hyperfine()
        (self.sol / "example1.txt").write_text("a\n")
        r = self.run_driver("--input", "example1.txt", "--iterations", "3", FAKE_RUN_ANSWER="wrong")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all(c[0] == "bench" for c in self.calls()))
        self.assertTrue(all(c[2] == str((self.sol / "example1.txt").resolve()) for c in self.calls()))
        self.assertEqual(self.hyperfine_calls(), [])
        self.assertNotIn("hyperfine", r.stderr)
        self.assertIn("note: --input example1.txt: the result goes to bench.json, but collect_benches.py "
                      "will not merge it into benchmarks.json", r.stderr)
        self.assertEqual(self.bench_json()["input"], "example1.txt")

    def test_missing_or_empty_input(self) -> None:
        (self.sol / "input.txt").unlink()
        self.assert_failed(self.run_driver(), 2, "input.txt missing — paste your puzzle input")
        (self.sol / "input.txt").write_text("")
        self.assert_failed(self.run_driver(), 2, "input.txt is empty")
        self.assertEqual(self.calls(), [])

    def test_solution_failures_exit_2(self) -> None:
        r = self.run_driver("--no-hyperfine", FAKE_MODE="crash")
        self.assert_failed(r, 2, "bench mode `")
        self.assertIn("exit status 101:\nfake harness crashed", r.stderr)
        r = self.run_driver("--no-hyperfine", FAKE_MODE="run-crash")
        self.assert_failed(r, 2, "run mode `")
        self.assertIn("fake run mode crashed", r.stderr)
        r = self.run_driver("--no-hyperfine", cmd=[str(self.tmp / "no-such-solution")])
        self.assert_failed(r, 2, "cannot run ")

    def test_protocol_violations_exit_3(self) -> None:
        cases = {
            "garbage": "part 1: bench mode output is not JSON",
            "two-lines": "part 1: bench mode printed 2 lines",
            "short-ns": "part 1: ns has 0 samples, expected 1",  # calibration stage 1
            "wrong-part": "part 1: bench mode reported part 2, expected 1",
            "extra-key": "part 1: bench mode output has unknown key(s): extra",
            "no-stdout": "part 1: bench mode printed nothing",
        }
        for mode, message in cases.items():
            with self.subTest(mode=mode):
                self.assert_failed(self.run_driver("--no-hyperfine", FAKE_MODE=mode), 3, message)

    def test_answer_mismatch_exit_4(self) -> None:
        r = self.run_driver("--no-hyperfine", FAKE_RUN_ANSWER="4")
        self.assert_failed(r, 4, "answer mismatch: run mode printed '4', bench mode returned '3'")

    def test_calibration_answer_mismatch_exit_4(self) -> None:
        # Bench-mode calls: stage 1, stage 2, measured run.
        r = self.run_driver("--no-hyperfine", FAKE_ANSWER_SEQ="3,3,4")
        self.assert_failed(r, 4, "answer mismatch: calibration returned '3', measured run returned '4'")

    def test_usage_errors_exit_1(self) -> None:
        for args, message in [
            ((), "no command given after `--`"),
            (("--bogus",), "unrecognized arguments: --bogus"),
            (("--iterations", "0"), "argument --iterations: invalid positive integer value: '0'"),
            (("--time", "-1"), "argument --time: invalid positive number value: '-1'"),
            (("--min-iterations", "10", "--max-iterations", "5"), "--min-iterations is larger"),
        ]:
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, str(BENCH_DAY), *args], cwd=self.sol,
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(f"error: {message}", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        r = self.run_driver(cmd=[])
        self.assert_failed(r, 1, "no command given after `--`")

    def test_rss_null_is_na(self) -> None:
        r = self.run_driver("--iterations", "3", "--no-hyperfine", FAKE_RSS="null")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["rss_delta_bytes"] for p in self.bench_json()["parts"]], [None, None])
        self.assertIn("| 3 | n/a | n/a |", r.stdout)

    def test_hyperfine_absent(self) -> None:
        r = self.run_driver("--iterations", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("note: hyperfine not found"), 1)
        self.assertEqual([p["wall"] for p in self.bench_json()["parts"]], [None, None])

    @POSIX_ONLY
    def test_fake_hyperfine(self) -> None:
        self.install_hyperfine()
        spaced = self.tmp / "dir with space"
        spaced.mkdir()
        harness = spaced / "fake_harness.py"
        shutil.copy(FAKE, harness)
        cmd = [sys.executable, str(harness)]
        r = self.run_driver("--iterations", "3", cmd=cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("warning", r.stderr)
        calls = self.hyperfine_calls()
        self.assertEqual(len(calls), 2)
        for part, call in zip((1, 2), calls):
            argv = call["argv"]
            self.assertEqual(argv[:6], ["-N", "--warmup", "3", "--style", "none", "--export-json"])
            self.assertEqual(argv[7:], ["--", shlex.join([*cmd, str(part)])])
            self.assertIn("'", argv[8])  # the space forced shlex quoting
            self.assertEqual(Path(call["cwd"]).resolve(), self.sol.resolve())
            self.assertFalse(Path(argv[6]).exists())  # temp export removed
        self.assertEqual([p["wall"] for p in self.bench_json()["parts"]], [WALL, WALL])
        self.assertIn("| 912 µs ± 38.0 µs |", r.stdout)
        self.assertNotIn("n/a", r.stdout)

    @POSIX_ONLY
    def test_fake_hyperfine_2_schema(self) -> None:
        self.install_hyperfine()
        r = self.run_driver("--iterations", "3", FAKE_HF_SCHEMA="2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("warning", r.stderr)
        self.assertEqual([p["wall"] for p in self.bench_json()["parts"]], [WALL, WALL])

    @POSIX_ONLY
    def test_fake_hyperfine_failing_warns(self) -> None:
        self.install_hyperfine()
        r = self.run_driver("--iterations", "3", FAKE_HF_FAIL="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning: hyperfine failed for part 1 (exit status 1: Error: command terminated", r.stderr)
        self.assertIn("warning: hyperfine failed for part 2", r.stderr)
        self.assertEqual([p["wall"] for p in self.bench_json()["parts"]], [None, None])
        self.assertTrue(r.stdout.splitlines()[2].endswith("| n/a |"))

    def test_multi_word_command(self) -> None:
        # Words after `--` that look like options belong to the command, not the driver.
        cmd = [sys.executable, "-X", "utf8", str(FAKE)]
        r = self.run_driver("--iterations", "2", "--no-hyperfine", "--name", "custom", cmd=cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.bench_json()
        self.assertEqual((data["name"], data["command"]), ("custom", cmd))
        self.assertEqual(self.calls()[0], ["1"])

    def test_json_out(self) -> None:
        r = self.run_driver("--iterations", "2", "--no-hyperfine", "--json-out", "other.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.sol / "other.json").is_file())
        self.assertFalse((self.sol / "bench.json").exists())
        self.assertTrue(r.stdout.endswith("wrote other.json\n"))


if __name__ == "__main__":
    unittest.main()
