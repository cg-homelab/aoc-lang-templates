from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import collect_benches as cb

SYSTEM = {"cpu": "Test CPU", "os": "TestOS 1 (arm64)"}
FALLBACK_SYSTEM = {"cpu": "Collector CPU", "os": "CollectorOS 2 (x86_64)"}
SETTINGS = {"time": 1.0, "min_iterations": 10, "max_iterations": 100000}
WALL = {"mean_s": 0.000912, "stddev_s": 0.000038, "median_s": 0.0009, "min_s": 0.0008, "max_s": 0.0011, "runs": 300}


def fallback() -> tuple[dict[str, str] | None, str | None]:
    return dict(FALLBACK_SYSTEM), "fff0000 (dirty)"


# --- fixtures: bench.json documents ----------------------------------------


def bench_part(part: int, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "part": part,
        "answer": "SECRET-ANSWER",
        "iterations": 1000,
        "warmup": 100,
        "ns": {"min": 110, "median": 120, "mean": 123.0, "stddev": 5.2, "p99": 150, "max": 400},
        "rss_delta_bytes": 0,
        "wall": dict(WALL),
    }
    data.update(overrides)
    return data


def bench_doc(*parts: dict[str, object], **overrides: object) -> dict[str, object]:
    """A bench.json as bench_day.py writes it (with system and commit, unless removed)."""
    data: dict[str, object] = {
        "schema": 1,
        "name": "x",
        "command": ["./secret-solution"],
        "input": "input.txt",
        "benched_at": "2026-10-29T12:00:00+00:00",
        "system": dict(SYSTEM),
        "commit": "abc1234",
        "settings": dict(SETTINGS),
        "parts": list(parts) or [bench_part(1), bench_part(2)],
    }
    data.update(overrides)
    return data


def v3_doc(**overrides: object) -> dict[str, object]:
    """A lang-templates v3.0.0 bench.json: no system, no commit."""
    data = bench_doc(**overrides)
    del data["system"], data["commit"]
    return data


# --- fixtures: a benchmarks.json document ----------------------------------


def entry_part(n: int, median: int, wall: bool = True, rss: int | None = 0) -> dict[str, Any]:
    return {
        "part": n, "iterations": 1000, "warmup": 100,
        "ns": {"min": max(median - 10, 0), "median": median, "mean": median + 3.0, "stddev": 5.2,
               "p99": median + 30, "max": median + 280},
        "rss_delta_bytes": rss,
        "wall": dict(WALL) if wall else None,
    }


def entry(*parts: dict[str, Any], benched_at: str | None = "2026-10-29T12:00:00+00:00",
          system: dict[str, str] | None = SYSTEM, commit: str | None = "abc1234") -> dict[str, Any]:
    e: dict[str, Any] = {"system": system, "commit": commit, "settings": dict(SETTINGS), "parts": list(parts)}
    if benched_at is not None:
        e["benched_at"] = benched_at
    return e


# Two days, five solutions: a shared rank (equal medians), a missing part (rust-v2 has no
# part 2), wall=None and RSS=None, a fastest median of 0, an unknown system/commit, a
# non-UTC benched_at whose local date differs from the UTC date, and no benched_at.
DOC: dict[str, Any] = {"schema": 1, "solutions": {
    "day-01-zig-v1": entry(entry_part(1, 410), entry_part(2, 12_700, wall=False, rss=None), system=None, commit=None),
    "day-01-rust-v2": entry(entry_part(1, 120, rss=25_690_112), commit="abc1234 (dirty)"),
    "day-01-rust-v1": entry(entry_part(1, 120), entry_part(2, 1000)),
    "day-02-python-v1": entry(entry_part(1, 50), benched_at=None),
    "day-02-go-v1": entry(entry_part(1, 0), entry_part(2, 1_500_000), benched_at="2026-12-02T23:30:00-05:00"),
}}

PREAMBLE = (
    "# Benchmarks\n\n"
    "Generated from `benchmarks.json` by `just bench <day>` / `just bench-all` / `just benchmarks [mode]`. "
    "In-process columns time `process()` only; Wall is hyperfine's whole-process time when hyperfine was "
    "installed. Rank and ×fastest compare medians within one day and part. Numbers from different machines "
    "are not comparable; see Runs.\n\n"
)
RUNS = (
    "## Runs\n\n"
    "- `day-01-rust-v1`: benchmarked 2026-10-29 on Test CPU, TestOS 1 (arm64), commit abc1234\n"
    "- `day-01-rust-v2`: benchmarked 2026-10-29 on Test CPU, TestOS 1 (arm64), commit abc1234 (dirty)\n"
    "- `day-01-zig-v1`: benchmarked 2026-10-29 on unknown system, commit unknown\n"
    "- `day-02-go-v1`: benchmarked 2026-12-02 on Test CPU, TestOS 1 (arm64), commit abc1234\n"
    "- `day-02-python-v1`: benchmarked at an unknown date on Test CPU, TestOS 1 (arm64), commit abc1234\n"
)
WALL_CELL = "912 µs ± 38.0 µs"
SINGLE = PREAMBLE + (
    "## Results\n\n"
    "| Day | Part | Rank | Solution | Median | ×fastest | Min | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |\n"
    "|---:|---:|---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    f"| 01 | 1 | 1 | `day-01-rust-v1` | 120 ns | 1.00× | 110 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 01 | 1 | 1 | `day-01-rust-v2` | 120 ns | 1.00× | 110 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 24.5 MiB | {WALL_CELL} |\n"
    f"| 01 | 1 | 3 | `day-01-zig-v1` | 410 ns | 3.42× | 400 ns | 413 ns ± 5.20 ns | 440 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 01 | 2 | 1 | `day-01-rust-v1` | 1.00 µs | 1.00× | 990 ns | 1.00 µs ± 5.20 ns | 1.03 µs | 1000 | 0 B | {WALL_CELL} |\n"
    "| 01 | 2 | 2 | `day-01-zig-v1` | 12.7 µs | 12.7× | 12.7 µs | 12.7 µs ± 5.20 ns | 12.7 µs | 1000 | n/a | n/a |\n"
    f"| 02 | 1 | 1 | `day-02-go-v1` | 0 ns | 1.00× | 0 ns | 3.00 ns ± 5.20 ns | 30.0 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 02 | 1 | 2 | `day-02-python-v1` | 50.0 ns | n/a | 40.0 ns | 53.0 ns ± 5.20 ns | 80.0 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 02 | 2 | 1 | `day-02-go-v1` | 1.50 ms | 1.00× | 1.50 ms | 1.50 ms ± 5.20 ns | 1.50 ms | 1000 | 0 B | {WALL_CELL} |\n"
    "\n"
) + RUNS
RANKED_HEADER = (
    "| Rank | Solution | Median | ×fastest | Min | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |\n"
    "|---:|:---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
)
RANKING = PREAMBLE + (
    "## Day 01, part 1\n\n" + RANKED_HEADER
    + f"| 1 | `day-01-rust-v1` | 120 ns | 1.00× | 110 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 1 | `day-01-rust-v2` | 120 ns | 1.00× | 110 ns | 123 ns ± 5.20 ns | 150 ns | 1000 | 24.5 MiB | {WALL_CELL} |\n"
    f"| 3 | `day-01-zig-v1` | 410 ns | 3.42× | 400 ns | 413 ns ± 5.20 ns | 440 ns | 1000 | 0 B | {WALL_CELL} |\n"
    "\n## Day 01, part 2\n\n" + RANKED_HEADER
    + f"| 1 | `day-01-rust-v1` | 1.00 µs | 1.00× | 990 ns | 1.00 µs ± 5.20 ns | 1.03 µs | 1000 | 0 B | {WALL_CELL} |\n"
    "| 2 | `day-01-zig-v1` | 12.7 µs | 12.7× | 12.7 µs | 12.7 µs ± 5.20 ns | 12.7 µs | 1000 | n/a | n/a |\n"
    "\n## Day 02, part 1\n\n" + RANKED_HEADER
    + f"| 1 | `day-02-go-v1` | 0 ns | 1.00× | 0 ns | 3.00 ns ± 5.20 ns | 30.0 ns | 1000 | 0 B | {WALL_CELL} |\n"
    f"| 2 | `day-02-python-v1` | 50.0 ns | n/a | 40.0 ns | 53.0 ns ± 5.20 ns | 80.0 ns | 1000 | 0 B | {WALL_CELL} |\n"
    "\n## Day 02, part 2\n\n" + RANKED_HEADER
    + f"| 1 | `day-02-go-v1` | 1.50 ms | 1.00× | 1.50 ms | 1.50 ms ± 5.20 ns | 1.50 ms | 1000 | 0 B | {WALL_CELL} |\n"
    "\n"
) + RUNS


class RenderTest(unittest.TestCase):
    def test_single_golden(self) -> None:
        self.assertEqual(cb.render(DOC, "single"), SINGLE)

    def test_ranking_golden(self) -> None:
        self.assertEqual(cb.render(DOC, "ranking"), RANKING)

    def test_default_is_single(self) -> None:
        self.assertEqual(cb.render(DOC), SINGLE)

    def test_same_runs_block_in_both_modes(self) -> None:
        single, ranking = cb.render(DOC, "single"), cb.render(DOC, "ranking")
        self.assertEqual(single[single.index("## Runs"):], ranking[ranking.index("## Runs"):])

    def test_render_is_pure(self) -> None:
        before = copy.deepcopy(DOC)
        cb.render(DOC, "single")
        cb.render(DOC, "ranking")
        self.assertEqual(DOC, before)

    def test_unknown_tablemode(self) -> None:
        with self.assertRaises(ValueError):
            cb.render(DOC, "per-solution")

    def test_competition_ranking(self) -> None:
        doc = {"schema": 1, "solutions": {
            f"day-03-l{i}-v1": entry(entry_part(1, median)) for i, median in enumerate([300, 100, 100, 200, 300, 400])
        }}
        rows = cb.ranked_groups(doc)[(3, 1)]
        self.assertEqual([(rank, name) for rank, name, _ in rows], [
            (1, "day-03-l1-v1"), (1, "day-03-l2-v1"), (3, "day-03-l3-v1"),
            (4, "day-03-l0-v1"), (4, "day-03-l4-v1"), (6, "day-03-l5-v1"),
        ])

    def test_number_check(self) -> None:
        for value in (0, 1, 1.5, 10**300, 1e300):
            self.assertTrue(cb._is_number(value), value)
        for value in (True, None, "1", 10**400, math.inf, math.nan):
            self.assertFalse(cb._is_number(value), value)
        self.assertTrue(cb._is_number(1e299, 1e9))
        self.assertFalse(cb._is_number(1e300, 1e9))

    def test_fmt_ratio(self) -> None:
        cases = [
            ((100, 100), "1.00×"), ((342, 100), "3.42×"), ((1270, 100), "12.7×"), ((12_700, 100), "127×"),
            ((150_000, 100), "1500×"), ((0, 0), "1.00×"), ((5, 0), "n/a"), ((99.96, 10), "10.0×"),
            ((1e308, 1e-10), "n/a"),
        ]
        for (median, fastest), text in cases:
            with self.subTest(median=median, fastest=fastest):
                self.assertEqual(cb.fmt_ratio(median, fastest), text)

    def test_run_line_fallbacks(self) -> None:
        line = cb.run_line("day-05-go-v1", {"benched_at": "garbage", "system": None, "commit": None, "parts": []})
        self.assertEqual(line, "- `day-05-go-v1`: benchmarked at an unknown date on unknown system, commit unknown")

    def test_naive_benched_at_is_utc(self) -> None:
        self.assertEqual(cb.instant("2026-10-29T12:00:00"), cb.instant("2026-10-29T12:00:00+00:00"))
        self.assertEqual(cb.instant("2026-10-29T12:00:00Z"), cb.instant("2026-10-29T14:00:00+02:00"))
        self.assertIsNone(cb.instant("yesterday"))
        self.assertIsNone(cb.instant(None))


class RootTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "days").mkdir()
        self.env_calls = 0

    def environment(self) -> tuple[dict[str, str] | None, str | None]:
        self.env_calls += 1
        return fallback()

    def day(self, name: str, bench: dict[str, object] | str | bytes | None = None) -> Path:
        d = self.root / "days" / name
        d.mkdir(parents=True, exist_ok=True)
        if isinstance(bench, bytes):
            (d / "bench.json").write_bytes(bench)
        elif bench is not None:
            text = bench if isinstance(bench, str) else json.dumps(bench)
            (d / "bench.json").write_text(text, encoding="utf-8")
        return d

    def merge(self, existing: dict[str, Any] | None, mtime: float | None = None) -> tuple[dict[str, Any], str]:
        err = io.StringIO()
        out = cb.merge(existing, self.root, mtime, self.environment, stderr=err)
        return out, err.getvalue()


def existing_doc(**entries: dict[str, Any]) -> dict[str, Any]:
    return {"schema": 1, "solutions": entries}


NOTHING_TO_RENDER = (
    "error: nothing to render: benchmarks.json has no entries and no days/day-NN-<lang>-vN/bench.json "
    "files were found — run `just bench <day>` first\n"
)
HUGE_INT = "1" + "0" * 400        # parses, but overflows a float
TOO_MANY_DIGITS = "1" * 5000      # over the int max_str_digits limit: json.loads raises ValueError


def with_raw_median(doc: dict[str, Any], raw: str) -> str:
    """doc as JSON with the first part's ns.median replaced by raw JSON text."""
    text = json.dumps(doc)
    marker = '"median": 999999'
    if marker not in text:
        marker = '"median": 120'
    assert marker in text
    return text.replace(marker, f'"median": {raw}', 1)


OLD = entry(entry_part(1, 999_999), entry_part(2, 999_999), benched_at="2026-10-29T12:00:00+00:00")


class MergeTest(RootTestCase):
    def test_new_entry(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        out, err = self.merge(None)
        self.assertEqual(err, "")
        self.assertEqual(out, {"schema": 1, "solutions": {"day-01-rust-v1": {
            "benched_at": "2026-10-29T12:00:00+00:00",
            "system": SYSTEM,
            "commit": "abc1234",
            "settings": SETTINGS,
            "parts": [{k: v for k, v in bench_part(n).items() if k != "answer"} for n in (1, 2)],
        }}})
        self.assertEqual(self.env_calls, 0)  # bench.json has system and commit: no fallback needed

    def test_keeps_entries_without_bench_json(self) -> None:
        self.day("day-01-rust-v1")
        self.day("day-02-rust-v1", bench_doc())
        out, _ = self.merge(existing_doc(**{"day-01-rust-v1": OLD}))
        self.assertEqual(list(out["solutions"]), ["day-01-rust-v1", "day-02-rust-v1"])
        self.assertIs(out["solutions"]["day-01-rust-v1"], OLD)

    def test_newer_replaces_equal_or_older_keeps(self) -> None:
        cases = [
            ("2026-10-29T12:00:01+00:00", True),
            ("2026-10-29T12:00:00+00:00", False),
            ("2026-10-29T11:59:59+00:00", False),
            ("2026-10-29T14:00:00+02:00", False),  # same instant; string compare would say newer
            ("2026-10-29T13:00:00+02:00", False),  # older (11:00Z); string compare would say newer
            ("2026-10-29T08:00:00-05:00", True),   # newer (13:00Z); string compare would say older
            ("2026-10-29T12:00:01Z", True),
            ("2026-10-29T12:00:01", True),         # naive: UTC
        ]
        for benched_at, replaced in cases:
            with self.subTest(benched_at=benched_at):
                self.day("day-01-rust-v1", bench_doc(benched_at=benched_at))
                out, err = self.merge(existing_doc(**{"day-01-rust-v1": OLD}))
                self.assertEqual(err, "")
                got = out["solutions"]["day-01-rust-v1"]
                if replaced:
                    self.assertEqual(got["benched_at"], benched_at)
                    self.assertEqual(got["parts"][0]["ns"]["median"], 120)
                else:
                    self.assertIs(got, OLD)

    def test_entry_without_usable_benched_at_is_replaced(self) -> None:
        self.day("day-01-rust-v1", bench_doc(benched_at="2000-01-01T00:00:00+00:00"))
        for broken in (entry(entry_part(1, 5), benched_at=None), entry(entry_part(1, 5), benched_at="soon")):
            with self.subTest(entry=broken.get("benched_at")):
                out, _ = self.merge(existing_doc(**{"day-01-rust-v1": broken}))
                self.assertEqual(out["solutions"]["day-01-rust-v1"]["benched_at"], "2000-01-01T00:00:00+00:00")

    def test_bench_json_without_benched_at_uses_mtime(self) -> None:
        data = bench_doc()
        del data["benched_at"]
        bench = self.day("day-01-rust-v1", data) / "bench.json"
        os.utime(bench, (1_000_000.75, 1_000_000.75))
        out, _ = self.merge(None)
        self.assertEqual(out["solutions"]["day-01-rust-v1"]["benched_at"], "1970-01-12T13:46:40+00:00")
        # The stored value is the file's instant, so a re-run keeps the entry ...
        again, _ = self.merge(out)
        self.assertIs(again["solutions"]["day-01-rust-v1"], out["solutions"]["day-01-rust-v1"])
        # ... and an entry benched later is not replaced by the older file.
        out, _ = self.merge(existing_doc(**{"day-01-rust-v1": OLD}))
        self.assertIs(out["solutions"]["day-01-rust-v1"], OLD)
        # Unparsable benched_at: same rule.
        bench.write_text(json.dumps(bench_doc(benched_at="last tuesday")), encoding="utf-8")
        os.utime(bench, (1_000_000, 1_000_000))
        out, _ = self.merge(None)
        self.assertEqual(out["solutions"]["day-01-rust-v1"]["benched_at"], "1970-01-12T13:46:40+00:00")

    def test_dropped_dir_noted(self) -> None:
        self.day("day-01-rust-v1")
        out, err = self.merge(existing_doc(**{"day-01-rust-v1": OLD, "day-02-go-v1": OLD, "day-02-zig-v1": OLD}))
        self.assertEqual(list(out["solutions"]), ["day-01-rust-v1"])
        self.assertEqual(
            err,
            "note: dropping day-02-go-v1 from benchmarks.json (days/day-02-go-v1 is gone)\n"
            "note: dropping day-02-zig-v1 from benchmarks.json (days/day-02-zig-v1 is gone)\n",
        )

    def test_invalid_bench_json_keeps_entry_and_warns(self) -> None:
        bad = {
            "day-02-rust-v1": "{not json",
            "day-03-rust-v1": json.dumps({"schema": 2, "parts": []}),
            "day-04-rust-v1": json.dumps(bench_doc({"part": 1})),
            "day-05-rust-v1": json.dumps([1, 2]),
            "day-06-rust-v1": json.dumps(bench_doc(bench_part(1, ns={"min": 1, "median": "fast"}))),
            "day-07-rust-v1": json.dumps(bench_doc(bench_part(1), bench_part(1))),
            "day-08-rust-v1": json.dumps(bench_doc(system="my laptop")),
        }
        for name, text in bad.items():
            self.day(name, text)
        self.day("day-09-rust-v1", b"\xff\xfe{}")  # not UTF-8
        out, err = self.merge(existing_doc(**{"day-03-rust-v1": OLD}))
        self.assertEqual(list(out["solutions"]), ["day-03-rust-v1"])
        self.assertIs(out["solutions"]["day-03-rust-v1"], OLD)
        for name in [*bad, "day-09-rust-v1"]:
            self.assertIn(f"warning: skipping days/{name}/bench.json: ", err)
        self.assertNotIn("Traceback", err)

    def test_bench_json_with_bad_numbers_keeps_entry_and_warns(self) -> None:
        bad = {
            "day-01-rust-v1": with_raw_median(bench_doc(), HUGE_INT),
            "day-02-rust-v1": with_raw_median(bench_doc(), TOO_MANY_DIGITS),
            "day-03-rust-v1": with_raw_median(bench_doc(), "Infinity"),
            "day-04-rust-v1": json.dumps(bench_doc(bench_part(1, wall={**WALL, "mean_s": 1e300}))),
            "day-05-rust-v1": json.dumps(bench_doc(bench_part(1, rss_delta_bytes=10**400))),
            "day-06-rust-v1": "[" * 100_000 + "]" * 100_000,
        }
        for name, text in bad.items():
            self.day(name, text)
        existing = existing_doc(**{name: OLD for name in bad})
        out, err = self.merge(existing)
        self.assertEqual(list(out["solutions"]), list(bad))
        for name in bad:
            self.assertIs(out["solutions"][name], OLD)
            self.assertIn(f"warning: skipping days/{name}/bench.json: ", err)
        self.assertNotIn("Traceback", err)
        cb.render(out, "single")  # and what is kept still renders

    def test_merge_strips_unknown_keys(self) -> None:
        self.day("day-01-rust-v1", bench_doc(bench_part(1, extra="SECRET"), notes="SECRET"))
        out, _ = self.merge(None)
        e = out["solutions"]["day-01-rust-v1"]
        self.assertEqual(sorted(e), ["benched_at", "commit", "parts", "settings", "system"])
        self.assertEqual(sorted(e["parts"][0]), ["iterations", "ns", "part", "rss_delta_bytes", "wall", "warmup"])

    def test_other_input_keeps_entry_and_notes(self) -> None:
        self.day("day-01-rust-v1", bench_doc(input="example1.txt", benched_at="2027-01-01T00:00:00+00:00"))
        self.day("day-02-rust-v1", bench_doc(input="example1.txt"))
        out, err = self.merge(existing_doc(**{"day-01-rust-v1": OLD}))
        self.assertEqual(list(out["solutions"]), ["day-01-rust-v1"])
        self.assertIs(out["solutions"]["day-01-rust-v1"], OLD)
        self.assertEqual(
            err,
            "note: days/day-01-rust-v1/bench.json was produced with --input example1.txt; "
            "not merged into benchmarks.json\n"
            "note: days/day-02-rust-v1/bench.json was produced with --input example1.txt; "
            "not merged into benchmarks.json\n",
        )

    def test_old_bench_md_warns(self) -> None:
        old = self.day("day-01-rust-v1")
        (old / "bench.md").write_text("| Command | Mean [ms] |\n|:---|---:|\n")
        both = self.day("day-02-rust-v1", bench_doc())
        (both / "bench.md").write_text("stale")
        stale = self.day("day-03-rust-v1")
        (stale / "bench.md").write_text("stale")
        os.utime(old / "bench.md", (3_000_000, 3_000_000))  # newer than benchmarks.json
        os.utime(stale / "bench.md", (1_000_000, 1_000_000))  # older: already reported before
        out, err = self.merge(existing_doc(**{"day-01-rust-v1": OLD, "day-03-rust-v1": OLD}), 2_000_000)
        self.assertIs(out["solutions"]["day-01-rust-v1"], OLD)
        self.assertEqual(
            err,
            "warning: days/day-01-rust-v1/bench.md is from the pre-v3 hyperfine-only contract; "
            "port the solution (see README → Migrating)\n",
        )
        _, err = self.merge(None, None)  # no benchmarks.json: every bench.md counts as new
        self.assertIn("days/day-01-rust-v1/bench.md", err)
        self.assertIn("days/day-03-rust-v1/bench.md", err)
        self.assertNotIn("day-02", err)

    def test_collect_uses_benchmarks_json_mtime_for_bench_md(self) -> None:
        old = self.day("day-01-rust-v1")
        (old / "bench.md").write_text("old")
        os.utime(old / "bench.md", (1_000_000, 1_000_000))
        self.day("day-02-rust-v1", bench_doc())
        path = self.root / "benchmarks.json"
        path.write_text(json.dumps(existing_doc(**{"day-01-rust-v1": OLD})), encoding="utf-8")
        os.utime(path, (2_000_000, 2_000_000))
        err = io.StringIO()
        cb.collect(self.root, self.environment, err)
        self.assertEqual(err.getvalue(), "")
        os.utime(path, (500_000, 500_000))
        cb.collect(self.root, self.environment, err)
        self.assertIn("warning: days/day-01-rust-v1/bench.md is from the pre-v3", err.getvalue())

    def test_v3_bench_json_gets_environment_fallback(self) -> None:
        self.day("day-01-rust-v1", v3_doc())
        self.day("day-02-rust-v1", v3_doc())
        self.day("day-03-rust-v1", bench_doc(system=None, commit=None))  # bench_day knew nothing: kept null
        self.day("day-04-rust-v1", {k: v for k, v in bench_doc().items() if k != "commit"})
        out, _ = self.merge(None)
        sols = out["solutions"]
        for name in ("day-01-rust-v1", "day-02-rust-v1"):
            self.assertEqual((sols[name]["system"], sols[name]["commit"]), (FALLBACK_SYSTEM, "fff0000 (dirty)"))
        self.assertEqual((sols["day-03-rust-v1"]["system"], sols["day-03-rust-v1"]["commit"]), (None, None))
        self.assertEqual((sols["day-04-rust-v1"]["system"], sols["day-04-rust-v1"]["commit"]),
                         (SYSTEM, "fff0000 (dirty)"))
        self.assertEqual(self.env_calls, 1)  # computed once per run, only when needed

    def test_sorted_by_solution_key(self) -> None:
        for name in ("day-10-rust-v1", "day-01-zig-v1", "day-01-rust-v10", "day-01-rust-v2", "day-02-go-v1"):
            self.day(name, bench_doc())
        out, _ = self.merge(None)
        self.assertEqual(list(out["solutions"]), [
            "day-01-rust-v2", "day-01-rust-v10", "day-01-zig-v1", "day-02-go-v1", "day-10-rust-v1",
        ])

    def test_ignores_other_directory_names(self) -> None:
        for name in ["day-01", "day-1-rust-v1", "day-01-rust"]:
            self.day(name, bench_doc())
        out, err = self.merge(None)
        self.assertEqual((out["solutions"], err), ({}, ""))


class UpgradeWarningTest(RootTestCase):
    V3_MD = (
        "# Benchmarks\n\nintro\n\n"
        "## day-01-rust-v1\n\nBenchmarked 2026-10-29 on X\n\n| Part |\n\n"
        "## day-02\n\nstale\n\n"
        "## day-03-go-v1\n\nBenchmarked 2026-10-29 on X\n"
    )

    def collect(self) -> str:
        err = io.StringIO()
        cb.collect(self.root, self.environment, err)
        return err.getvalue()

    def test_v3_sections_without_benchmarks_json_warn(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        (self.root / "benchmarks.md").write_text(self.V3_MD, encoding="utf-8")
        self.assertEqual(
            self.collect(),
            "warning: benchmarks.md has 2 sections from lang-templates v3 that cannot be imported; "
            "run `just bench-all` to rebuild them\n",
        )

    def test_no_warning_with_benchmarks_json_or_new_layout(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        (self.root / "benchmarks.md").write_text(SINGLE, encoding="utf-8")
        self.assertEqual(self.collect(), "")
        (self.root / "benchmarks.md").write_text(self.V3_MD, encoding="utf-8")
        (self.root / "benchmarks.json").write_text(json.dumps(existing_doc()), encoding="utf-8")
        self.assertEqual(self.collect(), "")


class CliTestCase(RootTestCase):
    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cb.main(["--root", str(self.root), *argv], environment=self.environment)
        return code, out.getvalue(), err.getvalue()

    def files(self) -> tuple[bytes | None, bytes | None]:
        return tuple(  # type: ignore[return-value]
            (self.root / n).read_bytes() if (self.root / n).exists() else None
            for n in ("benchmarks.json", "benchmarks.md")
        )


class MainTest(CliTestCase):
    def test_writes_both_files(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        self.day("day-01-go-v1", v3_doc(benched_at="2026-10-30T09:00:00+00:00"))
        code, out, err = self.main()
        self.assertEqual((code, out, err), (0, "wrote benchmarks.json and benchmarks.md (2 solutions, single)\n", ""))
        raw_json, raw_md = self.files()
        assert raw_json is not None and raw_md is not None
        doc = json.loads(raw_json)
        self.assertEqual(raw_json.decode(), json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        self.assertNotIn(b"\r\n", raw_json + raw_md)
        self.assertEqual(raw_md.decode(), cb.render(doc, "single"))
        self.assertIn("- `day-01-go-v1`: benchmarked 2026-10-30 on Collector CPU, CollectorOS 2 (x86_64), "
                      "commit fff0000 (dirty)\n", raw_md.decode())
        self.assertIn("| 01 | 1 | 1 | `day-01-go-v1` | 120 ns | 1.00× |", raw_md.decode())

    def test_answers_command_input_never_published(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        self.day("day-02-rust-v1", v3_doc())
        self.assertEqual(self.main()[0], 0)
        raw_json, raw_md = self.files()
        assert raw_json is not None and raw_md is not None
        for raw in (raw_json, raw_md):
            for secret in (b"SECRET-ANSWER", b"answer", b"secret-solution", b"command", b"input.txt", b'"name"'):
                self.assertNotIn(secret, raw)
        for e in json.loads(raw_json)["solutions"].values():
            self.assertEqual(sorted(e), ["benched_at", "commit", "parts", "settings", "system"])
            for p in e["parts"]:
                self.assertEqual(sorted(p), ["iterations", "ns", "part", "rss_delta_bytes", "wall", "warmup"])

    def test_idempotent(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        self.day("day-01-zig-v1", v3_doc())
        data = bench_doc()
        del data["benched_at"]
        self.day("day-02-go-v1", data)
        for mode in ("single", "ranking"):
            with self.subTest(mode=mode):
                self.assertEqual(self.main("--tablemode", mode)[0], 0)
                first = self.files()
                code, out, err = self.main("--tablemode", mode)
                self.assertEqual((code, out, err), (0, f"wrote benchmarks.json and benchmarks.md (3 solutions, {mode})\n", ""))
                self.assertEqual(self.files(), first)

    def test_rerender_without_bench_json(self) -> None:
        """Fresh clone: bench.json files are gitignored, benchmarks.json alone re-renders."""
        for name in ("day-01-rust-v1", "day-02-rust-v1"):
            self.day(name, bench_doc())
        self.assertEqual(self.main()[0], 0)
        first = self.files()
        for bench in (self.root / "days").glob("*/bench.json"):
            bench.unlink()
        self.assertEqual(self.main()[:2], (0, "wrote benchmarks.json and benchmarks.md (2 solutions, single)\n"))
        self.assertEqual(self.files(), first)
        code, _, _ = self.main("--tablemode", "ranking")
        self.assertEqual(code, 0)
        self.assertEqual(self.files()[1], cb.render(json.loads(first[0] or b""), "ranking").encode())

    def test_ranking_mode(self) -> None:
        (self.root / "benchmarks.json").write_text(json.dumps(DOC), encoding="utf-8")
        for name in DOC["solutions"]:
            self.day(name)
        code, out, err = self.main("--tablemode", "ranking")
        self.assertEqual((code, out, err), (0, "wrote benchmarks.json and benchmarks.md (5 solutions, ranking)\n", ""))
        self.assertEqual(self.files()[1], RANKING.encode())

    def test_nothing_found(self) -> None:
        self.day("day-01-rust-v1")
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, NOTHING_TO_RENDER)
        self.assertEqual(self.files(), (None, None))

    def test_empty_benchmarks_json_is_nothing_to_render(self) -> None:
        (self.root / "benchmarks.json").write_text('{"schema":1,"solutions":{}}', encoding="utf-8")
        before = self.files()
        code, out, err = self.main()
        self.assertEqual((code, out, err), (1, "", NOTHING_TO_RENDER))
        self.assertEqual(self.files(), before)

    def test_all_entries_dropped_is_nothing_to_render(self) -> None:
        (self.root / "benchmarks.json").write_text(json.dumps(existing_doc(**{"day-01-rust-v1": OLD})),
                                                   encoding="utf-8")
        before = self.files()
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "note: dropping day-01-rust-v1 from benchmarks.json (days/day-01-rust-v1 is gone)\n"
                         + NOTHING_TO_RENDER)
        self.assertEqual(self.files(), before)

    def test_benchmarks_md_written_atomically(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        with mock.patch.object(cb, "write_text_atomic", wraps=cb.write_text_atomic) as spy:
            self.assertEqual(self.main()[0], 0)
        spy.assert_called_once_with(self.root / "benchmarks.md", mock.ANY)
        _, raw_md = self.files()
        assert raw_md is not None
        doc = json.loads((self.root / "benchmarks.json").read_text(encoding="utf-8"))
        self.assertEqual(raw_md, cb.render(doc).encode())
        self.assertNotIn(b"\r", raw_md)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["benchmarks.json", "benchmarks.md", "days"])

    def test_benchmarks_md_write_failure(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        (self.root / "benchmarks.md").mkdir()  # os.replace onto a directory fails on every OS
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: cannot write benchmarks.md: "), err)
        self.assertTrue((self.root / "benchmarks.md").is_dir())
        self.assertFalse([p for p in self.root.iterdir() if p.name.endswith(".tmp")])

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "POSIX permissions; root ignores them")
    def test_unwritable_root(self) -> None:
        self.day("day-01-rust-v1", bench_doc())
        self.root.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(self.root.chmod, stat.S_IRWXU)
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: cannot write benchmarks.json: "), err)

    def test_hand_added_keys_never_published(self) -> None:
        tampered = entry(entry_part(1, 120), entry_part(2, 1000))
        tampered["parts"][0]["answer"] = "SECRET-ANSWER"
        tampered["command"] = ["./secret-solution"]
        tampered["notes"] = "SECRET-NOTE"
        (self.root / "benchmarks.json").write_text(json.dumps(existing_doc(**{"day-01-rust-v1": tampered})),
                                                   encoding="utf-8")
        self.day("day-01-rust-v1")
        self.assertEqual(self.main()[0], 0)
        raw_json, raw_md = self.files()
        assert raw_json is not None and raw_md is not None
        for raw in (raw_json, raw_md):
            for secret in (b"SECRET-ANSWER", b"answer", b"secret-solution", b"SECRET-NOTE"):
                self.assertNotIn(secret, raw)
        e = json.loads(raw_json)["solutions"]["day-01-rust-v1"]
        self.assertEqual(sorted(e), ["benched_at", "commit", "parts", "settings", "system"])

    def test_only_invalid_bench_json_is_nothing_found(self) -> None:
        self.day("day-01-rust-v1", "{not json")
        code, _, err = self.main()
        self.assertEqual(code, 1)
        self.assertIn("warning: skipping", err)
        self.assertTrue(err.endswith(NOTHING_TO_RENDER), err)

    def test_bad_args_exit_1(self) -> None:
        for argv, message in ((["--bogus"], "error: unrecognized arguments: --bogus"),
                              (["--tablemode", "per-solution"], "error: argument --tablemode: invalid choice"),
                              (["--tablemode", ""], "error: argument --tablemode: invalid choice")):
            with self.subTest(argv=argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                    cb.main(argv, root=self.root)
                self.assertEqual(cm.exception.code, 1)
                self.assertIn("usage: collect_benches.py", err.getvalue())
                self.assertIn(message, err.getvalue())
        self.assertEqual(self.files(), (None, None))


class LoadTest(CliTestCase):
    """An unusable benchmarks.json is an error; nothing is written."""

    def assert_rejected(self, text: str, why: str) -> None:
        self.day("day-01-rust-v1", bench_doc(benched_at="2030-01-01T00:00:00+00:00"))
        (self.root / "benchmarks.json").write_text(text, encoding="utf-8")
        (self.root / "benchmarks.md").write_text("old render\n", encoding="utf-8")
        before = self.files()
        code, out, err = self.main()
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("error: benchmarks.json: "), err)
        self.assertIn(why, err)
        self.assertTrue(err.endswith(" — fix or delete it\n"), err)
        self.assertEqual(self.files(), before)

    def test_corrupt(self) -> None:
        self.assert_rejected("{not json", "not valid JSON")

    def test_not_an_object(self) -> None:
        self.assert_rejected("[]", "not a JSON object")

    def test_wrong_schema(self) -> None:
        self.assert_rejected(json.dumps({"schema": 2, "solutions": {}}), "unsupported schema 2, expected 1")
        self.assert_rejected(json.dumps({"solutions": {}}), "unsupported schema None, expected 1")

    def test_solutions_not_object(self) -> None:
        self.assert_rejected(json.dumps({"schema": 1, "solutions": []}), "solutions is not an object")

    def test_bad_key(self) -> None:
        self.assert_rejected(json.dumps(existing_doc(**{"day-1-rust": OLD})),
                             "'day-1-rust' is not a day-NN-<lang>-vN solution name")

    def test_malformed_parts(self) -> None:
        bad_parts = [
            {**OLD, "parts": "none"},
            {**OLD, "parts": [{"part": 1}]},
            entry({**entry_part(1, 5), "ns": {"median": 5}}),
            entry({**entry_part(1, 5), "wall": {"mean_s": "slow"}}),
        ]
        for bad in bad_parts:
            with self.subTest(parts=bad["parts"]):
                self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": bad})), "day-01-rust-v1: ")

    def test_hand_edited_numbers(self) -> None:
        doc = existing_doc(**{"day-01-rust-v1": OLD})
        for raw in (HUGE_INT, TOO_MANY_DIGITS, "Infinity", "NaN", "-1"):
            with self.subTest(median=raw[:12]):
                self.assert_rejected(with_raw_median(doc, raw), "benchmarks.json: ")

    def test_wall_must_stay_finite_in_ns(self) -> None:
        bad = entry({**entry_part(1, 5), "wall": {**WALL, "mean_s": 1e300}})
        self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": bad})),
                             "day-01-rust-v1: part 1: wall is not null or an object with finite mean_s")
        bad = entry({**entry_part(1, 5), "wall": {**WALL, "stddev_s": 1e300}})
        self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": bad})), "finite mean_s and stddev_s")

    def test_deeply_nested(self) -> None:
        self.assert_rejected("[" * 100_000 + "]" * 100_000, "benchmarks.json: ")

    def test_malformed_entry(self) -> None:
        self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": []})), "day-01-rust-v1: entry is not an object")
        self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": {**OLD, "system": "laptop"}})),
                             "day-01-rust-v1: system is not null")
        self.assert_rejected(json.dumps(existing_doc(**{"day-01-rust-v1": {**OLD, "commit": 41}})),
                             "day-01-rust-v1: commit is not a string")


if __name__ == "__main__":
    unittest.main()
