from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

import collect_benches as cb


def META(d: date) -> str:
    return f"Benchmarked {d.isoformat()} on Test CPU, TestOS 1 (arm64), commit abc1234"


def bench_part(part: int, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "part": part,
        "answer": "SECRET-ANSWER",
        "iterations": 1000,
        "warmup": 100,
        "ns": {"min": 110, "median": 120, "mean": 123.0, "stddev": 5.2, "p99": 150, "max": 400},
        "rss_delta_bytes": 0,
        "wall": {"mean_s": 0.000912, "stddev_s": 0.000038, "median_s": 0.0009, "min_s": 0.0008,
                 "max_s": 0.0011, "runs": 300},
    }
    data.update(overrides)
    return data


def bench_doc(*parts: dict[str, object]) -> dict[str, object]:
    return {
        "schema": 1,
        "name": "x",
        "command": ["./solution"],
        "input": "input.txt",
        "benched_at": "2026-10-29T12:00:00+00:00",
        "settings": {"time": 1.0, "min_iterations": 10, "max_iterations": 100000},
        "parts": list(parts),
    }


# One bench.json with hyperfine wall time and RSS, one without either.
WITH_WALL = bench_doc(bench_part(1), bench_part(2, rss_delta_bytes=25_690_112))
NO_WALL = bench_doc(bench_part(1, wall=None, rss_delta_bytes=None), bench_part(2, wall=None, rss_delta_bytes=None))


class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def day(self, n: int, bench: dict[str, object] | None = None, lang: str = "rust", version: int = 1) -> Path:
        d = self.root / "days" / f"day-{n:02d}-{lang}-v{version}"
        d.mkdir(parents=True)
        if bench is not None:
            with open(d / "bench.json", "w", encoding="utf-8") as f:
                json.dump(bench, f)
        return d

    def merge(self, existing: str, mtime: float | None = None) -> tuple[dict[str, str], str]:
        err = io.StringIO()
        out = cb.merge_sections(existing, self.root, META, mtime, stderr=err)
        return out, err.getvalue()

    def existing(self, *days: int) -> str:
        return cb.render({f"day-{n:02d}-rust-v1": f"OLD {n}" for n in days})

    def test_new_day_keeps_others(self) -> None:
        self.day(1)
        self.day(2, WITH_WALL)
        out = cb.merge_sections(self.existing(1), self.root, META)
        self.assertEqual(out["day-01-rust-v1"], "OLD 1")
        self.assertTrue(out["day-02-rust-v1"].startswith("Benchmarked "))

    def test_replace_existing(self) -> None:
        self.day(1, NO_WALL)
        out = cb.merge_sections(self.existing(1), self.root, META)
        self.assertNotIn("OLD", out["day-01-rust-v1"])
        self.assertIn("| Part |", out["day-01-rust-v1"])
        self.assertIn("n/a", out["day-01-rust-v1"])

    def test_drop_missing_dir(self) -> None:
        self.day(1)
        out = cb.merge_sections(self.existing(1, 2), self.root, META)
        self.assertEqual(list(out), ["day-01-rust-v1"])

    def test_sorted(self) -> None:
        self.day(10, WITH_WALL)
        self.day(2, WITH_WALL)
        self.day(1, WITH_WALL)
        out = cb.collect(self.root, META)
        assert out is not None
        self.assertLess(out.index("## day-01-rust-v1"), out.index("## day-02-rust-v1"))
        self.assertLess(out.index("## day-02-rust-v1"), out.index("## day-10-rust-v1"))

    def test_several_solutions_per_day(self) -> None:
        self.day(1, NO_WALL, "zig", 1)
        self.day(1, WITH_WALL, "rust", 10)
        self.day(1, NO_WALL, "rust", 2)
        self.day(1, WITH_WALL, "rust", 1)
        self.day(2, NO_WALL, "go", 1)
        out = cb.collect(self.root, META)
        assert out is not None
        names = ["day-01-rust-v1", "day-01-rust-v2", "day-01-rust-v10", "day-01-zig-v1", "day-02-go-v1"]
        self.assertEqual(list(cb.parse_sections(out)), names)
        positions = [out.index(f"## {n}\n") for n in names]
        self.assertEqual(positions, sorted(positions))
        sections = cb.parse_sections(out)
        self.assertIn("| Part |", sections["day-01-rust-v1"])
        self.assertNotIn("n/a", sections["day-01-rust-v1"])
        self.assertIn("n/a", sections["day-01-rust-v2"])
        self.assertIn("n/a", sections["day-01-zig-v1"])
        self.assertNotIn("SECRET-ANSWER", out)

    def test_ignores_other_directory_names(self) -> None:
        for name in ["day-01", "day-1-rust-v1", "day-01-rust"]:
            d = self.root / "days" / name
            d.mkdir(parents=True)
            (d / "bench.json").write_text(json.dumps(NO_WALL))
        self.assertIsNone(cb.collect(self.root, META))

    def test_empty_returns_none(self) -> None:
        self.day(1)
        self.assertIsNone(cb.collect(self.root, META))

    def test_render_roundtrip(self) -> None:
        out = cb.render({"day-02-go-v1": "y", "day-01-rust-v1": "x"})
        self.assertTrue(out.startswith("# Benchmarks\n\n"))
        self.assertEqual(cb.parse_sections(out), {"day-01-rust-v1": "x", "day-02-go-v1": "y"})
        self.assertEqual(list(cb.parse_sections(out)), ["day-01-rust-v1", "day-02-go-v1"])

    def test_unrecognised_heading_dropped(self) -> None:
        text = (
            "# Benchmarks\n\nintro\n\n"
            "## day-01-rust-v1\n\nNEW 1\n\n"
            "## day-02\n\nSTALE 2\n\n"
            "## day-03-go-v1\n\nNEW 3\n"
        )
        self.assertEqual(cb.parse_sections(text), {"day-01-rust-v1": "NEW 1", "day-03-go-v1": "NEW 3"})
        self.day(1)
        self.day(3, lang="go")
        (self.root / "benchmarks.md").write_text(text)
        out = cb.collect(self.root, META)
        assert out is not None
        self.assertNotIn("STALE", out)
        self.assertNotIn("## day-02", out)
        self.assertIn("## day-01-rust-v1\n\nNEW 1\n\n## day-03-go-v1\n\nNEW 3\n", out)

    def test_mtime_rules(self) -> None:
        self.day(1, NO_WALL)
        self.day(2, NO_WALL)
        self.day(3, NO_WALL)
        bench = lambda n: self.root / "days" / f"day-{n:02d}-rust-v1" / "bench.json"  # noqa: E731
        os.utime(bench(1), (1_000_000, 1_000_000))  # older than benchmarks.md
        os.utime(bench(2), (3_000_000, 3_000_000))  # newer
        os.utime(bench(3), (1_000_000, 1_000_000))  # older, but no section
        existing = self.existing(1, 2)
        out = cb.merge_sections(existing, self.root, META, 2_000_000)
        self.assertEqual(out["day-01-rust-v1"], "OLD 1")
        self.assertNotIn("OLD", out["day-02-rust-v1"])
        self.assertIn("Benchmarked 1970-01-12", out["day-03-rust-v1"])
        out = cb.merge_sections(existing, self.root, META, None)
        self.assertTrue(all("OLD" not in v for v in out.values()))

    def test_collect_uses_benchmarks_md_mtime(self) -> None:
        self.day(1, NO_WALL)
        bench = self.root / "days" / "day-01-rust-v1" / "bench.json"
        os.utime(bench, (1_000_000, 1_000_000))
        (self.root / "benchmarks.md").write_text(self.existing(1))
        os.utime(self.root / "benchmarks.md", (2_000_000, 2_000_000))
        out = cb.collect(self.root, META)
        assert out is not None
        self.assertIn("OLD 1", out)

    def test_section_content(self) -> None:
        bench = self.day(1, WITH_WALL) / "bench.json"
        os.utime(bench, (1_000_000, 1_000_000))
        out = cb.merge_sections("", self.root, META)
        self.assertEqual(
            out["day-01-rust-v1"],
            "Benchmarked " + date.fromtimestamp(1_000_000).isoformat() + " on Test CPU, TestOS 1 (arm64), commit abc1234\n\n"
            "| Part | Min | Median | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
            "| 1 | 110 ns | 120 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 0 B | 912 µs ± 38.0 µs |\n"
            "| 2 | 110 ns | 120 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 24.5 MiB | 912 µs ± 38.0 µs |",
        )

    def test_invalid_bench_json_skipped_with_warning(self) -> None:
        self.day(1, WITH_WALL)
        bad = {
            2: "{not json",
            3: json.dumps({"schema": 2, "parts": []}),
            4: json.dumps(bench_doc({"part": 1})),
            5: json.dumps([1, 2]),
        }
        for n, text in bad.items():
            (self.day(n) / "bench.json").write_text(text)
        out, err = self.merge(self.existing(1, 3))
        self.assertEqual(list(out), ["day-01-rust-v1", "day-03-rust-v1"])
        self.assertEqual(out["day-03-rust-v1"], "OLD 3")  # kept: the broken file does not replace it
        for n in bad:
            self.assertIn(f"warning: skipping days/day-0{n}-rust-v1/bench.json: ", err)
        self.assertNotIn("Traceback", err)

    def test_only_invalid_bench_json_is_nothing_found(self) -> None:
        (self.day(1) / "bench.json").write_text("{not json")
        err = io.StringIO()
        self.assertIsNone(cb.collect(self.root, META, stderr=err))
        self.assertIn("warning: skipping", err.getvalue())

    def test_other_input_not_merged(self) -> None:
        self.day(1, {**WITH_WALL, "input": "example1.txt"})
        self.day(2, {**WITH_WALL, "input": "example1.txt"})
        self.day(3, WITH_WALL)
        out, err = self.merge(self.existing(1))
        self.assertEqual(list(out), ["day-01-rust-v1", "day-03-rust-v1"])
        self.assertEqual(out["day-01-rust-v1"], "OLD 1")  # existing section kept
        self.assertEqual(
            err,
            "note: days/day-01-rust-v1/bench.json was produced with --input example1.txt; "
            "not merged into benchmarks.md\n"
            "note: days/day-02-rust-v1/bench.json was produced with --input example1.txt; "
            "not merged into benchmarks.md\n",
        )

    def test_old_bench_md_warns(self) -> None:
        old = self.day(1)
        (old / "bench.md").write_text("| Command | Mean [ms] |\n|:---|---:|\n")
        both = self.day(2, WITH_WALL)
        (both / "bench.md").write_text("stale")
        stale = self.day(3)
        (stale / "bench.md").write_text("stale")
        os.utime(old / "bench.md", (3_000_000, 3_000_000))  # newer than benchmarks.md
        os.utime(stale / "bench.md", (1_000_000, 1_000_000))  # older: already reported before
        out, err = self.merge(self.existing(1, 3), 2_000_000)
        self.assertEqual(out["day-01-rust-v1"], "OLD 1")
        self.assertEqual(out["day-03-rust-v1"], "OLD 3")
        self.assertEqual(
            err,
            "warning: days/day-01-rust-v1/bench.md is from the pre-v3 hyperfine-only contract; "
            "port the solution (see README → Migrating)\n",
        )
        _, err = self.merge("", None)  # no benchmarks.md: every bench.md counts as new
        self.assertIn("days/day-01-rust-v1/bench.md", err)
        self.assertIn("days/day-03-rust-v1/bench.md", err)
        self.assertNotIn("day-02", err)


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "days").mkdir()

    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cb.main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_nothing_found(self) -> None:
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("error: no days/day-NN-<lang>-vN/bench.json files found", err)

    def test_bad_args_exit_1(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cb.main(["--bogus"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("usage: collect_benches.py", err.getvalue())
        self.assertIn("error: unrecognized arguments: --bogus", err.getvalue())

    def test_writes_benchmarks_md(self) -> None:
        d = self.root / "days" / "day-01-rust-v1"
        d.mkdir()
        (d / "bench.json").write_text(json.dumps(WITH_WALL), encoding="utf-8")
        code, out, err = self.main()
        self.assertEqual((code, out, err), (0, "wrote benchmarks.md (1 solutions)\n", ""))
        text = (self.root / "benchmarks.md").read_text(encoding="utf-8")
        self.assertIn("## day-01-rust-v1\n\nBenchmarked ", text)
        self.assertIn("In-process columns time `process()` only", text)
        self.assertIn("| 2 | 110 ns | 120 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 24.5 MiB |", text)


if __name__ == "__main__":
    unittest.main()
