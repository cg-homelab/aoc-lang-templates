"""Tests for `aoc-toolkit bench` (bench_day.py).

Pure functions are tested directly, the peak-RSS probe against real child
processes; the CLI is run end to end in a subprocess against fake_harness.py
(a protocol-conformant fake solution), with PATH
scrubbed so a real hyperfine is never used. Tests that need hyperfine put a
fake `hyperfine` script first on PATH.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import aoc_toolkit.bench_day as bd

FAKE = Path(__file__).resolve().parent / "fake_harness.py"
# The driver subprocesses import this checkout's package, with or without uv.
SRC = str(Path(__file__).resolve().parents[1] / "src")
BENCH = [sys.executable, "-m", "aoc_toolkit", "bench"]
POSIX_ONLY = unittest.skipIf(os.name == "nt", "fake hyperfine is a shebang script")
MIB = 1024 * 1024
LINUX = sys.platform.startswith("linux")

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


def bench_out(ns: list[int], answer: str | None = "3", newline: str = "\n") -> bytes:
    """A protocol-2 bench-mode stdout: samples, the length-prefixed answer (unless None), `done`."""
    out = "".join(f"{v}{newline}" for v in ns).encode()
    if answer is not None:
        data = answer.encode()
        out += f"answer {len(data)}{newline}".encode() + data + newline.encode()
    return out + f"done{newline}".encode()


class IterationTest(unittest.TestCase):
    def test_warmup_rule(self) -> None:
        for n, k in [(1, 0), (2, 1), (10, 1), (19, 1), (20, 2), (100, 10), (999, 99), (1000, 100),
                     (100_000, 100)]:
            with self.subTest(n=n):
                self.assertEqual(bd.warmup_for(n), k)

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
    def parse(self, stdout: bytes, lo: int = 3, hi: int = 3) -> tuple[list[int], str]:
        return bd.parse_bench_output(stdout, lo, hi)

    def rejects(self, stdout: bytes, pattern: str, lo: int = 3, hi: int = 3) -> None:
        with self.assertRaisesRegex(bd.ProtocolError, pattern):
            self.parse(stdout, lo, hi)

    def test_example(self) -> None:
        self.assertEqual(self.parse(b"1532\n1498\n1501\nanswer 1\n3\ndone\n"), ([1532, 1498, 1501], "3"))

    def test_accepts(self) -> None:
        self.assertEqual(self.parse(bench_out([0, 0, 0])), ([0, 0, 0], "3"))
        self.assertEqual(self.parse(bench_out([1, 2, 3], newline="\r\n")), ([1, 2, 3], "3"))  # CRLF
        self.assertEqual(self.parse(b"1\n2\n3\nanswer 1\n3\ndone"), ([1, 2, 3], "3"))  # no final newline
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="")), ([1, 2, 3], ""))  # empty answer
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="#.\n.#\n")), ([1, 2, 3], "#.\n.#\n"))
        # Newlines and protocol words inside the N bytes are answer, not protocol.
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="a\ndone\nanswer 4\n5")),
                         ([1, 2, 3], "a\ndone\nanswer 4\n5"))
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="  7 ")), ([1, 2, 3], "  7 "))  # verbatim
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="a\r\nb")), ([1, 2, 3], "a\nb"))  # as run mode
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="Δ→✓")), ([1, 2, 3], "Δ→✓"))  # N counts bytes
        # Run mode decodes "abc\r\n" to "abc\n" and strips it: a final "\r" is not part of the answer.
        self.assertEqual(self.parse(b"1\n2\n3\nanswer 4\nabc\r\ndone\n"), ([1, 2, 3], "abc"))
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer="a\r\r")), ([1, 2, 3], "a\n"))
        self.assertEqual(self.parse(bench_out([10**19, 1, 2])), ([10**19, 1, 2], "3"))  # 20 digits
        self.assertEqual(self.parse(bench_out([5] * 2), lo=2, hi=4), ([5, 5], "3"))  # count at both bounds
        self.assertEqual(self.parse(bench_out([5] * 4), lo=2, hi=4), ([5] * 4, "3"))

    def test_unicode_line_separators_in_answer(self) -> None:
        answer = "a\u2028b\u0085c\u2029d"
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer=answer))[1], answer)
        self.assertEqual(self.parse(bench_out([1, 2, 3], answer=answer, newline="\r\n"))[1], answer)

    def test_rejects(self) -> None:
        cases = [
            (b"", "exited without printing `done`$"),
            (b"1\n2\n3\nanswer 1\n3\n", "exited without printing `done`$"),
            (b'{"schema":1,"part":1}\n', r"line 1 is not a sample: '\{.*\}' \(old harness\? regenerate"),
            (b"debug\n" + bench_out([1, 2, 3]), r"line 1 is not a sample: 'debug' \(is process\(\) printing"),
            (b"1\n2.5\n3\nanswer 1\n3\ndone\n", "line 2 is not a sample: '2.5'"),
            (b"1\n-2\n3\nanswer 1\n3\ndone\n", "line 2 is not a sample: '-2'"),
            (b"1\n+2\n3\nanswer 1\n3\ndone\n", "line 2 is not a sample: '\\+2'"),
            (b"1\n\n2\n3\nanswer 1\n3\ndone\n", "line 2 is not a sample: ''"),
            (b"1\nDone\n", "line 2 is not a sample: 'Done'"),
            (b"1\n2\n3\nanswer\n3\ndone\n", r"line 4 is a malformed `answer` header: 'answer' \(expected"),
            (b"1\n2\n3\nanswer x\n3\ndone\n", "line 4 is a malformed `answer` header: 'answer x'"),
            (b"1\n2\n3\nanswer -1\n3\ndone\n", "malformed `answer` header"),
            (b"1\n2\n3\nanswer  1\n3\ndone\n", "malformed `answer` header"),
            (b"1\n2\n3\nanswer 9\n3\n", "answer header says 9 bytes, but stdout ended after 2"),
            (b"1" * 21 + b"\n2\n3\nanswer 1\n3\ndone\n", "line 1 is not a sample: '1{21}'"),
            (b"9" * 5000 + b"\n2\n3\nanswer 1\n3\ndone\n", r"line 1 is not a sample: '9{80}\.\.\.'"),
            (b"1\n2\n3\nanswer " + b"0" * 20 + b"1\n3\ndone\n", "malformed `answer` header"),
            (b"1\n2\n3\nanswer 1\n33\ndone\n", r"answer \(1 bytes\) is not followed by a newline: got '3'"),
            (b"1\n2\n3\nanswer 1\n3", "not followed by a newline: got end of output"),
            (b"1\n2\n3\nanswer 1\n3\n4\ndone\n", "line 6 after the answer is not `done`: '4'"),
            (b"1\nanswer 3\na\nb\n7\ndone\n", "line 5 after the answer is not `done`: '7'"),
            (bench_out([1, 2, 3]) + b"extra\n", "printed output after `done`: 'extra'"),
            (bench_out([1, 2, 3]) + b"\n", "printed output after `done`: ''"),
            (bench_out([1, 2, 3], answer=None), r"printed 3 sample\(s\) but no `answer` block"),
            (bench_out([1, 2]), "printed 2 samples, expected 3$"),
            (bench_out([1, 2, 3, 4]), "printed 4 samples, expected 3$"),
            (bench_out([]), "printed 0 samples, expected 3$"),
        ]
        for stdout, pattern in cases:
            with self.subTest(stdout=stdout[:40]):
                self.rejects(stdout, pattern)
        self.rejects(bench_out([1]), "printed 1 samples, expected between 2 and 4", lo=2, hi=4)
        self.rejects(bench_out([1] * 5), "printed 5 samples, expected between 2 and 4", lo=2, hi=4)

    def test_first_output_line(self) -> None:
        self.assertEqual(bd._first_output_line(b"\ndone\n"), b"")
        self.assertEqual(bd._first_output_line(b"done\r\nx\r\n"), b"x")

    def test_reader_stops_at_done(self) -> None:
        stream = io.BytesIO(bench_out([1, 2]) + b"after\n")
        consumed = bytearray()
        self.assertEqual(bd.read_bench_output(stream, consumed), bd.BenchOutput([1, 2], "3"))
        self.assertEqual(bytes(consumed), bench_out([1, 2]))
        self.assertEqual(stream.read(), b"after\n")  # left for the caller
        self.assertEqual(bd.read_bench_output(io.BytesIO(b"done\n")), bd.BenchOutput([], None))

    def fake(self, *args: str, **env: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run([sys.executable, str(FAKE), "bench", *args], stdin=subprocess.DEVNULL,
                              capture_output=True, env={**os.environ, **env}, timeout=30)

    def test_rejects_fake_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.txt"
            inp.write_text("x\n")
            cases = {
                "garbage": "line 1 is not a sample: 'fast'",
                "debug-line": "line 1 is not a sample: 'debug print from process\\(\\)'",
                "no-answer": "no `answer` block",
                "too-few": "printed 4 samples, expected 5",
                "too-many": "printed 6 samples, expected 5",
                "no-stdout": "exited without printing `done`",
                "no-done": "exited without printing `done`",
                "short-answer": "exited without printing `done`",  # 6 bytes `3\ndone`, then EOF (stdin: EOF)
                "park-before-done": "exited without printing `done`",
                "after-done": "printed output after `done`: 'stray line after done'",
            }
            for mode, pattern in cases.items():
                with self.subTest(mode=mode):
                    r = self.fake("1", str(inp), "0", "5", "5", FAKE_MODE=mode)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.rejects(r.stdout, pattern, 5, 5)

    def test_fake_harness_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "input.txt"
            inp.write_text("x\n")
            r = self.fake("2", str(inp), "0", "3", "3")
            self.assertEqual((r.returncode, r.stdout), (0, b"1000\n1000\n1000\nanswer 1\n9\ndone\n"))
            r = self.fake("1", str(inp), "2500", "1", "10", FAKE_NS="1000")  # budget reached after 3 calls
            self.assertEqual(self.parse(r.stdout, 1, 10), ([1000] * 3, "3"))
            r = self.fake("1", str(inp), "0", "0", "100000")  # baseline: zero iterations, only `done`
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"done\n", b""))
            for args in (("1", str(inp), "0", "6", "5"), ("3", str(inp), "0", "1", "1"), ("1", str(inp), "1", "1"),
                         ("1", str(inp), "-1", "1", "1"), ("1", str(Path(tmp) / "missing"), "0", "1", "1")):
                with self.subTest(args=args):
                    r = self.fake(*args)
                    self.assertEqual((r.returncode, r.stdout), (1, b""))


PARKED_64_MIB = ("import sys; b = bytearray(64 * 1024 * 1024); b[::4096] = b'\\x01' * (len(b) // 4096); "
                 "sys.stdout.write('answer 2\\nok\\ndone\\n'); sys.stdout.flush(); sys.stdin.readline()")


class MemoryProbeTest(unittest.TestCase):
    """_reap, _live_peak and _run_with_peak against real child processes."""

    def popen(self, code: str, **kwargs: object) -> subprocess.Popen[bytes]:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        kwargs.setdefault("stdout", subprocess.DEVNULL)
        return subprocess.Popen([sys.executable, "-c", code], **kwargs)  # type: ignore[call-overload]

    def test_reap_keeps_exit_status(self) -> None:
        proc = self.popen("import sys; sys.exit(3)")
        handle = bd._open_process_handle(proc.pid)
        try:
            peak = bd._reap(proc, handle)
        finally:
            bd._close_process_handle(handle)
        self.assertEqual(proc.returncode, 3)
        if os.name != "nt":
            self.assertIsNotNone(peak)
            assert peak is not None
            self.assertGreater(peak, 0)

    @unittest.skipIf(os.name == "nt", "POSIX: os.wait4")
    def test_reap_after_someone_else_reaped(self) -> None:
        proc = self.popen("import sys; sys.exit(7)")
        proc.wait()
        proc.returncode = None  # as if it were reaped behind Popen's back
        # Restore it, or Popen.__del__ warns "still running" into whatever test is capturing stderr then.
        self.addCleanup(setattr, proc, "returncode", 7)
        with self.assertRaisesRegex(bd.BenchError, r"exit status unavailable \(child reaped elsewhere\)") as caught:
            bd._reap(proc, None)
        self.assertEqual(caught.exception.code, bd.EXIT_SOLUTION)

    @unittest.skipUnless(LINUX, "VmHWM in /proc/<pid>/status")
    def test_live_peak_of_a_parked_child(self) -> None:
        proc = self.popen(PARKED_64_MIB, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            assert proc.stdout is not None and proc.stdin is not None
            self.assertEqual(bd.read_bench_output(proc.stdout), bd.BenchOutput([], "ok"))
            peak = bd._live_peak(proc.pid)
            self.assertIsNotNone(peak)
            assert peak is not None
            self.assertGreaterEqual(peak, 60 * MIB)
            self.assertIsNone(proc.poll())  # still parked
            proc.stdin.write(b"\n")
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=30), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        self.assertIsNone(bd._live_peak(proc.pid))  # reaped: no status

    @unittest.skipUnless(LINUX and shutil.which("sh"), "Linux, sh")
    def test_live_peak_has_no_parent_floor(self) -> None:
        # After exit, a child's ru_maxrss on Linux is at least this process's own RSS; VmHWM is not.
        ballast = bytearray(64 * MIB)
        ballast[::4096] = b"\x01" * (len(ballast) // 4096)
        result = bd._run_with_peak(["sh", "-c", "echo done; read line"])
        self.assertEqual((result.returncode, result.output), (0, bd.BenchOutput([], None)))
        assert result.live is not None
        self.assertLess(result.live, 32 * MIB)
        assert result.reaped is not None
        self.assertGreater(result.reaped, 64 * MIB)  # the after-exit value has this process as a floor
        del ballast

    def test_peak_of_a_64_mib_allocation(self) -> None:
        result = bd._run_with_peak([sys.executable, "-c", PARKED_64_MIB])
        self.assertEqual((result.returncode, result.stderr, result.error, result.trailing), (0, "", None, b""))
        self.assertEqual(result.output, bd.BenchOutput([], "ok"))
        peak = bd._platform_peak(result)
        if peak is None:
            self.skipTest("peak RSS unavailable on this platform")
        self.assertGreaterEqual(peak, 60 * MIB)

    def test_run_with_peak_without_done(self) -> None:
        code = "import sys; sys.stdout.write('1\\n2'); sys.stderr.write('oops\\n'); sys.exit(5)"
        result = bd._run_with_peak([sys.executable, "-c", code])
        self.assertEqual((result.returncode, result.stderr, result.output), (5, "oops\n", None))
        self.assertRegex(str(result.error), "exited without printing `done`")
        self.assertEqual(result.consumed, b"1\n2")

    def test_run_with_peak_drains_after_a_protocol_error(self) -> None:
        # The bad line comes first; the harness then still prints `done` and parks: it must be released.
        code = ("import sys; sys.stdout.write('oops\\n' + '1\\n' * 100000 + 'done\\n'); sys.stdout.flush(); "
                "sys.stdin.readline()")
        result = bd._run_with_peak([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0)
        self.assertRegex(str(result.error), "line 1 is not a sample: 'oops'")
        self.assertTrue(result.trailing.endswith(b"done\n"))

    def test_run_with_peak_no_park(self) -> None:
        result = bd._run_with_peak([sys.executable, "-c", "print('done')"])
        self.assertEqual((result.returncode, result.output, result.trailing), (0, bd.BenchOutput([], None), b""))

    def test_watchdog_kills_a_child_stuck_before_done(self) -> None:
        stuck = {
            "parks without done": "import sys; print(1, flush=True); sys.stdin.readline()",
            "done without newline": "import sys; sys.stdout.write('done'); sys.stdout.flush(); sys.stdin.readline()",
            "parks without flushing": "import sys; sys.stdout.write('done\\n'); sys.stdin.readline()",
            "answer shorter than N": "import sys; print('answer 9\\n3\\ndone', flush=True); sys.stdin.readline()",
        }
        for name, code in stuck.items():
            with self.subTest(name):
                start = time.monotonic()
                result = bd._run_with_peak([sys.executable, "-c", code], timeout=0.5)
                self.assertLess(time.monotonic() - start, 20)
                self.assertTrue(result.timed_out)  # reported before whatever the truncated output says
                self.assertNotEqual(result.returncode, 0)

    def test_watchdog_spares_a_conforming_child(self) -> None:
        result = bd._run_with_peak([sys.executable, "-c", PARKED_64_MIB], timeout=60)
        self.assertEqual((result.timed_out, result.returncode, result.error), (False, 0, None))

    def test_keyboard_interrupt_kills_the_parked_child(self) -> None:
        procs: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def spy(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            proc = real_popen(*args, **kwargs)  # type: ignore[call-overload]
            procs.append(proc)
            return proc

        with mock.patch.object(bd.subprocess, "Popen", side_effect=spy), \
                mock.patch.object(bd, "read_bench_output", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                bd._run_with_peak([sys.executable, "-c", "import sys; sys.stdin.readline()"])
        (proc,) = procs
        self.assertIsNotNone(proc.returncode)
        assert proc.stdin is not None
        self.assertTrue(proc.stdin.closed)
        if os.name != "nt":
            with self.assertRaises(ChildProcessError):  # reaped: no child left behind
                os.waitpid(proc.pid, os.WNOHANG)

    def test_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(bd.BenchError, "cannot run ") as caught:
                bd._run_with_peak([str(Path(tmp) / "no-such-solution")])
        self.assertEqual(caught.exception.code, bd.EXIT_SOLUTION)


class DriverTest(unittest.TestCase):
    """`aoc-toolkit bench` end to end against fake_harness.py."""

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
                     PYTHONIOENCODING="utf-8", PYTHONPATH=SRC)
        clean.update(env)
        cmd = [sys.executable, str(FAKE)] if cmd is None else cmd
        return subprocess.run(
            [*BENCH, *args, "--", *cmd],
            cwd=self.sol, capture_output=True, text=True, encoding="utf-8", env=clean, timeout=120,
        )

    def run_in_process(self, *args: str, **env: str) -> tuple[int, str, str]:
        """bench_day.main in this process (so its helpers can be patched): exit code, stdout, stderr."""
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FAKE_")}
        clean.update(FAKE_COUNTER=str(self.counter), **env)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, clean, clear=True), contextlib.chdir(self.sol), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bd.main([*args, "--", sys.executable, str(FAKE)])
        return code, out.getvalue(), err.getvalue()

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
            self.assertEqual(sorted(p), ["answer", "iterations", "ns", "part", "rss_baseline_bytes",
                                         "rss_delta_bytes", "rss_peak_bytes", "wall", "warmup"])
            # The fake harness runs 20 iterations of 1 µs to fill the 20 µs budget; 2 are warmup.
            self.assertEqual((p["iterations"], p["warmup"]), (18, 2))
            self.assertEqual(p["ns"], {"min": 1000, "median": 1000, "mean": 1000.0, "stddev": 0.0,
                                       "p99": 1000, "max": 1000})
            baseline, peak = p["rss_baseline_bytes"], p["rss_peak_bytes"]
            if peak is None:
                self.assertEqual((baseline, p["rss_delta_bytes"]), (None, None))
            else:
                self.assertLessEqual(abs(peak - baseline), MIB)  # nothing allocated: same as the baseline
                self.assertEqual(p["rss_delta_bytes"], peak - baseline)
            self.assertIsNone(p["wall"])
        inp = str((self.sol / "input.txt").resolve())
        # Per part: run-mode check, zero-iteration baseline (same buffer size), measured run.
        self.assertEqual(self.calls(), [
            ["1"], ["bench", "1", inp, "0", "0", "100000"], ["bench", "1", inp, "20000", "10", "100000"],
            ["2"], ["bench", "2", inp, "0", "0", "100000"], ["bench", "2", inp, "20000", "10", "100000"],
        ])
        self.assertIn("part 1: benchmarking ~0.02 ms (10\u2013100000 iterations)\n", r.stderr)
        self.assertIn("part 1: 20 iterations in 0.02 ms (2 warmup trimmed)\n", r.stderr)
        self.assertTrue(r.stdout.startswith("| Part | Min | Median |"), r.stdout)
        self.assertIn("| 1 | 1.00 µs | 1.00 µs | 1.00 µs ± 0 ns | 1.00 µs | 18 | ", r.stdout)
        self.assertTrue(r.stdout.endswith("wrote bench.json\n"))
        self.assertNotIn("hyperfine not found", r.stderr)  # --no-hyperfine: no note
        # Atomic write leaves no temp file behind.
        self.assertEqual(sorted(p.name for p in self.sol.iterdir()), ["bench.json", "input.txt"])

    def test_warmup_trimmed(self) -> None:
        r = self.run_driver("--iterations", "2", "--warmup", "2", "--no-hyperfine", FAKE_NS="9000,9000,100,300")
        self.assertEqual(r.returncode, 0, r.stderr)
        ns = self.bench_json()["parts"][0]["ns"]
        self.assertEqual((ns["min"], ns["max"], ns["mean"]), (100, 300, 200.0))

    def test_explicit_warmup(self) -> None:
        r = self.run_driver("--time", "0.00002", "--warmup", "5", "--no-hyperfine", FAKE_NS="1000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(p["iterations"], p["warmup"]) for p in self.bench_json()["parts"]], [(15, 5), (15, 5)])
        self.assertIn("part 2: 20 iterations in 0.02 ms (5 warmup trimmed)\n", r.stderr)

    def test_slow_part_runs_min_iterations(self) -> None:
        r = self.run_driver("--no-hyperfine", FAKE_NS="200000000")  # 0.2 s per call: the budget ends after 5
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(p["iterations"], p["warmup"]) for p in self.bench_json()["parts"]], [(9, 1), (9, 1)])
        self.assertIn("part 1: benchmarking ~1.00 s (10\u2013100000 iterations)\n", r.stderr)
        self.assertIn("part 1: 10 iterations in 2.00 s (1 warmup trimmed)\n", r.stderr)

    def test_fixed_iterations(self) -> None:
        r = self.run_driver("--iterations", "7", "--no-hyperfine")
        self.assertEqual(r.returncode, 0, r.stderr)
        inp = str((self.sol / "input.txt").resolve())
        self.assertEqual([c for c in self.calls() if c[:2] == ["bench", "1"]],
                         [["bench", "1", inp, "0", "0", "8"], ["bench", "1", inp, "0", "8", "8"]])  # 7 + warmup 1
        self.assertEqual([(p["iterations"], p["warmup"]) for p in self.bench_json()["parts"]], [(7, 1), (7, 1)])
        self.assertIn("part 1: running 7 (+1 warmup) iterations\n", r.stderr)
        self.assertIn("part 1: 8 iterations in 0.01 ms (1 warmup trimmed)\n", r.stderr)

    def test_single_fixed_iteration_has_no_warmup(self) -> None:
        r = self.run_driver("--iterations", "1", "--no-hyperfine")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c[-3:] for c in self.calls() if c[:2] == ["bench", "1"]], [["0", "0", "1"], ["0", "1", "1"]])
        self.assertEqual([(p["iterations"], p["warmup"]) for p in self.bench_json()["parts"]], [(1, 0), (1, 0)])

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
        self.assertIn("note: --input example1.txt: the result goes to bench.json, but `aoc-toolkit collect` "
                      "will not merge it into benchmarks.json", r.stderr)
        self.assertEqual(self.bench_json()["input"], "example1.txt")

    def test_missing_or_empty_input(self) -> None:
        (self.sol / "input.txt").unlink()
        self.assert_failed(self.run_driver(), 2, "input.txt missing — paste your puzzle input")
        (self.sol / "input.txt").write_text("")
        self.assert_failed(self.run_driver(), 2, "input.txt is empty")
        self.assertEqual(self.calls(), [])

    def test_solution_failures_exit_2(self) -> None:
        r = self.run_driver("--time", "0.00002", "--no-hyperfine", FAKE_MODE="crash")
        self.assert_failed(r, 2, f"bench mode `{shlex.join([sys.executable, str(FAKE)])} bench 1 ")
        self.assertIn("20000 10 100000` failed with exit status 101:\nfake harness crashed", r.stderr)
        self.assertNotIn("old harness", r.stderr)
        r = self.run_driver("--no-hyperfine", FAKE_MODE="run-crash")
        self.assert_failed(r, 2, "run mode `")
        self.assertIn("fake run mode crashed", r.stderr)
        r = self.run_driver("--no-hyperfine", cmd=[str(self.tmp / "no-such-solution")])
        self.assert_failed(r, 2, "cannot run ")

    def test_old_harness_hint(self) -> None:
        self.counter.unlink(missing_ok=True)
        r = self.run_driver("--no-hyperfine", FAKE_MODE="old-harness")
        self.assert_failed(r, 2, "bench mode `")
        self.assertIn(" 0 0 100000` failed with exit status 1:\n"
                      "usage: fake_harness.py bench <1|2> <input-path> <iterations>\n"
                      "(old harness? regenerate the bench file from the template)\n", r.stderr)
        inp = str((self.sol / "input.txt").resolve())
        self.assertEqual(self.calls(), [["1"], ["bench", "1", inp, "0", "0", "100000"]])  # baseline fails fast

    def test_protocol_violations_exit_3(self) -> None:
        cases = {
            "garbage": "part 1: bench mode output line 1 is not a sample: 'fast' (is process() printing something?)",
            "debug-line": "part 1: bench mode output line 1 is not a sample: 'debug print from process()'",
            "no-answer": "part 1: bench mode printed 20 sample(s) but no `answer` block",
            "too-few": "part 1: bench mode printed 9 samples, expected between 10 and 30",
            "too-many": "part 1: bench mode printed 31 samples, expected between 10 and 30",
            "no-stdout": "part 1: bench mode exited without printing `done`",
            "no-done": "part 1: bench mode exited without printing `done`",
            "after-done": "part 1: bench mode printed output after `done`: 'stray line after done'",
            "baseline-noise": "part 1: bench mode printed output with zero iterations: 'debug print from main()'",
        }
        for mode, message in cases.items():
            with self.subTest(mode=mode):
                r = self.run_driver("--time", "0.00002", "--max-iterations", "30", "--no-hyperfine", FAKE_MODE=mode)
                self.assert_failed(r, 3, message)
                self.assertNotIn("old harness", r.stderr)

    def test_crash_wins_over_malformed_output(self) -> None:
        r = self.run_driver("--time", "0.00002", "--no-hyperfine", FAKE_MODE="garbage-crash")
        self.assert_failed(r, 2, "bench mode `")
        self.assertIn("failed with exit status 3", r.stderr)
        self.assertNotIn("not a sample", r.stderr)

    def test_watchdog_timeouts_exit_3(self) -> None:
        for mode in ("park-before-done", "short-answer"):
            with self.subTest(mode=mode):
                start = time.monotonic()
                r = self.run_driver("--time", "0.00002", "--timeout", "0.5", "--no-hyperfine", FAKE_MODE=mode)
                self.assertLess(time.monotonic() - start, 30)
                self.assert_failed(r, 3, "part 1: bench mode timed out after 0.5 s waiting for `done` (see --timeout)")

    def test_baseline_watchdog(self) -> None:
        with mock.patch.object(bd, "BASELINE_TIMEOUT_S", 0.5):
            code, out, err = self.run_in_process("--no-hyperfine", FAKE_MODE="baseline-park")
        self.assertEqual(code, 3, err)
        self.assertIn("error: part 1: bench mode timed out after 0.5 s waiting for `done` "
                      "(the zero-iteration baseline run)", err)
        self.assertEqual(out, "")

    def test_baseline_empty_line(self) -> None:
        harness = ("import sys\n"
                   "if sys.argv[1:2] != ['bench']: print(3)\n"
                   "else: sys.stdout.write('\\ndone\\n'); sys.stdout.flush(); sys.stdin.readline()\n")
        r = self.run_driver("--no-hyperfine", cmd=[sys.executable, "-c", harness])
        self.assert_failed(r, 3, "part 1: bench mode printed output with zero iterations: an empty line (`")

    def test_harness_that_does_not_park(self) -> None:
        r = self.run_driver("--time", "0.00002", "--no-hyperfine", FAKE_MODE="no-park")
        self.assertEqual(r.returncode, 0, r.stderr)
        parts = self.bench_json()["parts"]
        self.assertEqual([p["iterations"] for p in parts], [18, 18])
        for p in parts:  # whether the live read won the race against the exit varies; never a mix
            if p["rss_delta_bytes"] is None:
                self.assertEqual((p["rss_baseline_bytes"], p["rss_peak_bytes"]), (None, None))
                self.assertIn(f"warning: part {p['part']}: harness did not park, Peak RSS Δ will be n/a", r.stderr)
            else:
                self.assertLessEqual(abs(p["rss_delta_bytes"]), MIB)

    @unittest.skipUnless(LINUX and hasattr(os, "waitid"), "Linux live peaks")
    def test_exit_without_parking_is_na(self) -> None:
        real = bd._live_peak

        def after_exit(pid: int) -> int | None:  # lose the race on purpose: read the zombie's status
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
            return real(pid)

        with mock.patch.object(bd, "_live_peak", side_effect=after_exit):
            code, out, err = self.run_in_process("--iterations", "3", "--no-hyperfine", FAKE_MODE="no-park")
        self.assertEqual(code, 0, err)
        for p in self.bench_json()["parts"]:
            self.assertEqual((p["rss_baseline_bytes"], p["rss_peak_bytes"], p["rss_delta_bytes"]), (None, None, None))
        self.assertEqual(err.count("harness did not park, Peak RSS Δ will be n/a"), 2)
        self.assertIn("| 3 | n/a | n/a |", out)

    @unittest.skipUnless(LINUX, "Linux live peaks")
    def test_live_and_reaped_peaks_never_mix(self) -> None:
        real, calls = bd._live_peak, []

        def measured_missing(pid: int) -> int | None:
            calls.append(pid)
            return None if len(calls) % 2 == 0 else real(pid)  # baseline live, measured not

        with mock.patch.object(bd, "_live_peak", side_effect=measured_missing):
            code, out, err = self.run_in_process("--iterations", "3", "--no-hyperfine")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(calls), 4)
        self.assertEqual([p["rss_delta_bytes"] for p in self.bench_json()["parts"]], [None, None])
        self.assertIn("warning: part 1: harness did not park", err)

    def test_answer_ending_in_carriage_return(self) -> None:
        r = self.run_driver("--iterations", "3", "--no-hyperfine", FAKE_ANSWER="abc\r")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["answer"] for p in self.bench_json()["parts"]], ["abc", "abc"])

    def test_answer_mismatch_exit_4(self) -> None:
        r = self.run_driver("--no-hyperfine", "--iterations", "3", FAKE_RUN_ANSWER="4")
        self.assert_failed(r, 4, "answer mismatch: run mode printed '4', bench mode returned '3'")

    def test_multi_line_answer(self) -> None:
        answer = "#..\n.#.\n..#"
        r = self.run_driver("--no-hyperfine", "--iterations", "3", FAKE_ANSWER=answer)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["answer"] for p in self.bench_json()["parts"]], [answer, answer])

    def test_usage_errors_exit_1(self) -> None:
        for args, message in [
            ((), "no command given after `--`"),
            (("--bogus",), "unrecognized arguments: --bogus"),
            (("--iterations", "0"), "argument --iterations: invalid positive integer value: '0'"),
            (("--time", "-1"), "argument --time: invalid positive number value: '-1'"),
            (("--min-iterations", "10", "--max-iterations", "5"), "--min-iterations is larger"),
            (("--warmup", "10", "--min-iterations", "10"), "--warmup must be smaller than --min-iterations"),
            (("--warmup", "10"), "--warmup must be smaller than --min-iterations"),
            (("--time", "1e10"), "--time must be at most 9223372036 s"),
            (("--max-iterations", "2147483648"), "--max-iterations must be at most 2147483647"),
            (("--iterations", "2147483648"), "--iterations must be at most 2147483647"),
            (("--iterations", "2147483647"), "--iterations plus warmup must be at most 2147483647"),
            (("--iterations", "5", "--warmup", "2147483648"), "--warmup must be at most 2147483647"),
            (("--timeout", "0"), "argument --timeout: invalid positive number value: '0'"),
        ]:
            with self.subTest(args=args):
                r = subprocess.run([*BENCH, *args], cwd=self.sol, env={**os.environ, "PYTHONPATH": SRC},
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(f"error: {message}", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        r = self.run_driver(cmd=[])
        self.assert_failed(r, 1, "no command given after `--`")

    def test_rss_null_is_na(self) -> None:
        with mock.patch.object(bd, "_reap", side_effect=lambda proc, handle: (proc.wait(), None)[1]), \
                mock.patch.object(bd, "_live_peak", return_value=None):
            code, out, err = self.run_in_process("--iterations", "3", "--no-hyperfine")
        self.assertEqual(code, 0, err)
        for p in self.bench_json()["parts"]:
            self.assertEqual((p["rss_baseline_bytes"], p["rss_peak_bytes"], p["rss_delta_bytes"]), (None, None, None))
        self.assertIn("| 3 | n/a | n/a |", out)

    def test_rss_delta_measures_allocation(self) -> None:
        alloc = 64 * MIB
        r = self.run_driver("--iterations", "3", "--no-hyperfine", FAKE_ALLOC_MIB="64")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in self.bench_json()["parts"]:
            if p["rss_peak_bytes"] is None:
                self.skipTest("peak RSS unavailable on this platform")
            self.assertLess(p["rss_baseline_bytes"], p["rss_peak_bytes"])
            self.assertGreaterEqual(p["rss_delta_bytes"], 0.9 * alloc)
            self.assertEqual(p["rss_delta_bytes"], p["rss_peak_bytes"] - p["rss_baseline_bytes"])
        self.assertRegex(r.stdout, r"\| 3 \| 6\d\.\d MiB \| n/a \|")

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
