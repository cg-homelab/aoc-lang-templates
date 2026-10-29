#!/usr/bin/env python3
"""Merge every days/day-NN-<lang>-vN/bench.json (written by bench_day.py) into
benchmarks.json and render benchmarks.md from it.

Usage: python3 lang-templates/scripts/collect_benches.py [--root DIR]
           [--tablemode {single,ranking}]

benchmarks.json at <root> (root defaults to the current directory) is the
committed source of truth: one entry per solution, keyed by directory name,
without answers, command or input. An entry is replaced when its bench.json
has a later `benched_at` instant, and dropped when its directory is gone. A
missing bench.json keeps the entry (bench.json is not committed); an unreadable
one is skipped with a warning, one benchmarked with a non-default --input is
skipped with a note, and a pre-v3 bench.md without a bench.json only warns.
A benchmarks.json that cannot be loaded is an error: nothing is written.

benchmarks.md is a pure render of benchmarks.json. --tablemode single (the
default) puts every row in one table ordered by day, part and median;
--tablemode ranking writes one table per day and part. Both rank solutions by
in-process median within a day and part and end with a `## Runs` list saying
when, where and at which commit each solution was benchmarked.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

# Import the sibling modules even under `python -P` / PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_day import (  # noqa: E402
    DEFAULT_INPUT,
    NOT_AVAILABLE,
    SCHEMA,
    UsageArgumentParser,
    _fmt_spread,
    _is_int,
    _scaled,
    cpu_name,
    fmt_bytes,
    fmt_ns,
    git_commit,
    os_name,
    write_json_atomic,
    write_text_atomic,
)
from resolve_day import DIR_RE, solution_key  # noqa: E402

JSON_NAME = "benchmarks.json"
MD_NAME = "benchmarks.md"
TABLEMODES = ("single", "ranking")
DEFAULT_TABLEMODE = "single"

# Keys kept in benchmarks.json, per entry and per part; everything else (the answer!) is
# dropped, both when merging a bench.json and when loading a hand-edited benchmarks.json.
ENTRY_KEYS = ("benched_at", "system", "commit", "settings", "parts")
PART_KEYS = ("part", "iterations", "warmup", "ns", "rss_delta_bytes", "wall")
NS_KEYS = ("min", "median", "mean", "stddev", "p99")

HEADING = "# Benchmarks"
INTRO = (
    "Generated from `benchmarks.json` by `just bench <day>` / `just bench-all` / "
    "`just benchmarks [mode]`. In-process columns time `process()` only; Wall is "
    "hyperfine's whole-process time when hyperfine was installed. Rank and ×fastest "
    "compare medians within one day and part. Numbers from different machines are "
    "not comparable; see Runs."
)
RANKED_COLUMNS = ("Rank", "Solution", "Median", "×fastest", "Min", "Mean ± σ", "p99", "Iterations",
                  "Peak RSS Δ", "Wall (hyperfine)")
SINGLE_COLUMNS = ("Day", "Part", *RANKED_COLUMNS)
# A v3 benchmarks.md had one `## day-NN-<lang>-vN` section per solution.
V3_SECTION_RE = re.compile(r"^## (\S+)[ \t]*$", re.MULTILINE)

Environment = Callable[[], tuple[dict[str, str] | None, str | None]]


class BenchmarksError(Exception):
    """benchmarks.json exists but cannot be used."""


# --- validation ------------------------------------------------------------


def _is_number(value: object, scale: float = 1.0) -> bool:
    """A finite int/float that stays finite as a float after multiplying by scale (the
    renderer turns wall seconds into ns); False for bools and huge ints."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value) * scale)
    except OverflowError:  # an int too large for a float
        return False


def validate_parts(parts: object) -> None:
    """Raise ValueError unless parts is a renderable list of per-part summaries."""
    if not isinstance(parts, list):
        raise ValueError("parts is not a list")
    seen: set[int] = set()
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("a part is not an object")
        n = part.get("part")
        if not _is_int(n) or n < 1:
            raise ValueError(f"bad part number {n!r}")
        if n in seen:
            raise ValueError(f"part {n} appears twice")
        seen.add(n)
        if not _is_int(part.get("iterations")):
            raise ValueError(f"part {n}: iterations is not an integer")
        ns = part.get("ns")
        if not isinstance(ns, dict):
            raise ValueError(f"part {n}: ns is not an object")
        for key in NS_KEYS:
            if not _is_number(ns.get(key)) or ns[key] < 0:
                raise ValueError(f"part {n}: ns.{key} is not a non-negative number")
        rss = part.get("rss_delta_bytes")
        if rss is not None and not _is_number(rss):
            raise ValueError(f"part {n}: rss_delta_bytes is not a number or null")
        wall = part.get("wall")
        if wall is not None:
            if not isinstance(wall, dict) or not all(_is_number(wall.get(k), 1e9) for k in ("mean_s", "stddev_s")):
                raise ValueError(f"part {n}: wall is not null or an object with finite mean_s and stddev_s")


def validate_environment(data: dict[str, Any]) -> None:
    """Raise ValueError unless the optional system/commit keys are null or well-formed."""
    system = data.get("system")
    if system is not None and not (
        isinstance(system, dict) and isinstance(system.get("cpu"), str) and isinstance(system.get("os"), str)
    ):
        raise ValueError("system is not null or an object with cpu and os strings")
    commit = data.get("commit")
    if commit is not None and not isinstance(commit, str):
        raise ValueError("commit is not a string or null")


def load_bench_json(path: Path) -> dict[str, Any]:
    """Parse a bench.json; raise ValueError if it cannot be merged."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read: {e}") from None
    except (ValueError, RecursionError) as e:  # JSONDecodeError, int digit limit, deep nesting
        raise ValueError(f"not valid JSON: {e}") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("not a schema-1 bench.json")
    try:
        validate_parts(data.get("parts"))
        validate_environment(data)
    except ValueError as e:
        raise ValueError(f"malformed ({e})") from None
    return data


def load_benchmarks(path: Path) -> dict[str, Any] | None:
    """Parse benchmarks.json; None when absent, BenchmarksError when unusable."""
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise BenchmarksError(f"cannot read: {e}") from None
    except (ValueError, RecursionError) as e:  # JSONDecodeError, int digit limit, deep nesting
        raise BenchmarksError(f"not valid JSON: {e}") from None
    if not isinstance(doc, dict):
        raise BenchmarksError("not a JSON object")
    if not _is_int(doc.get("schema")) or doc["schema"] != SCHEMA:
        raise BenchmarksError(f"unsupported schema {doc.get('schema')!r}, expected {SCHEMA}")
    solutions = doc.get("solutions")
    if not isinstance(solutions, dict):
        raise BenchmarksError("solutions is not an object")
    for name, entry in solutions.items():
        if not DIR_RE.match(name):
            raise BenchmarksError(f"{name!r} is not a day-NN-<lang>-vN solution name")
        if not isinstance(entry, dict):
            raise BenchmarksError(f"{name}: entry is not an object")
        try:
            validate_parts(entry.get("parts"))
            validate_environment(entry)
        except ValueError as e:
            raise BenchmarksError(f"{name}: {e}") from None
    return {"schema": SCHEMA, "solutions": {name: normalize_entry(entry) for name, entry in solutions.items()}}


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Only the ENTRY_KEYS of an entry and the PART_KEYS of its parts (no answers, ever)."""
    kept = {k: entry[k] for k in ENTRY_KEYS if k in entry}
    kept["parts"] = [{k: part[k] for k in PART_KEYS if k in part} for part in entry["parts"]]
    return kept


# --- merging ---------------------------------------------------------------


def instant(value: object) -> datetime | None:
    """An ISO 8601 benched_at as an aware datetime (naive means UTC); None if unusable."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def default_environment(root: Path) -> Environment:
    """The collecting machine's system and the year repo's commit (best effort)."""
    def environment() -> tuple[dict[str, str] | None, str | None]:
        try:
            system: dict[str, str] | None = {"cpu": cpu_name(), "os": os_name()}
        except Exception:
            system = None
        return system, git_commit(root)
    return environment


def entry_from_bench(data: dict[str, Any], benched_at: str, environment: Environment) -> dict[str, Any]:
    """The benchmarks.json entry for a bench.json document (no answer, command, input or name)."""
    if "system" in data and "commit" in data:
        system, commit = data["system"], data["commit"]
    else:  # lang-templates v3.0.0 bench.json: fill from the collecting machine
        fallback_system, fallback_commit = environment()
        system = data["system"] if "system" in data else fallback_system
        commit = data["commit"] if "commit" in data else fallback_commit
    return normalize_entry({
        "benched_at": benched_at,
        "system": system,
        "commit": commit,
        "settings": data.get("settings"),
        "parts": data["parts"],
    })


def merge(
    existing: dict[str, Any] | None,
    root: Path,
    existing_mtime: float | None = None,
    environment: Environment | None = None,
    stderr: TextIO | None = None,
) -> dict[str, Any]:
    """Merge a loaded benchmarks.json (None if absent) with the bench.json files under root/days.

    An entry is replaced when there is none or its bench.json has a later benched_at
    instant. existing_mtime (benchmarks.json's mtime; None means "always") only decides
    whether a pre-v3 bench.md is reported. Returns the new benchmarks.json document.
    """
    stderr = sys.stderr if stderr is None else stderr
    if environment is None:
        environment = default_environment(root)
    cached: list[tuple[dict[str, str] | None, str | None]] = []

    def env_once() -> tuple[dict[str, str] | None, str | None]:
        if not cached:
            cached.append(environment())
        return cached[0]

    solutions: dict[str, Any] = dict(existing["solutions"]) if existing else {}
    days_dir = root / "days"
    present = {p.name for p in days_dir.glob("day-*") if p.is_dir() and DIR_RE.match(p.name)}
    for name in sorted(set(solutions) - present, key=solution_key):
        print(f"note: dropping {name} from {JSON_NAME} (days/{name} is gone)", file=stderr)
        del solutions[name]

    for name in sorted(present, key=solution_key):
        bench = days_dir / name / "bench.json"
        if not bench.is_file():
            old = days_dir / name / "bench.md"
            if old.is_file() and (existing_mtime is None or old.stat().st_mtime > existing_mtime):
                print(
                    f"warning: days/{name}/bench.md is from the pre-v3 hyperfine-only contract; "
                    "port the solution (see README → Migrating)",
                    file=stderr,
                )
            continue
        try:
            data = load_bench_json(bench)
            mtime = bench.stat().st_mtime
        except (ValueError, OSError, OverflowError, RecursionError) as e:
            print(f"warning: skipping days/{name}/bench.json: {e}", file=stderr)
            continue
        if data.get("input") != DEFAULT_INPUT:
            print(
                f"note: days/{name}/bench.json was produced with --input {data.get('input')}; "
                f"not merged into {JSON_NAME}",
                file=stderr,
            )
            continue
        benched_at = data.get("benched_at")
        when = instant(benched_at)
        if when is None:  # no usable benched_at: the file's mtime stands in, and is stored
            when = datetime.fromtimestamp(mtime, timezone.utc).replace(microsecond=0)
            benched_at = when.isoformat()
        current = solutions.get(name)
        current_when = instant(current.get("benched_at")) if current is not None else None
        if current is not None and current_when is not None and when <= current_when:
            continue
        solutions[name] = entry_from_bench(data, benched_at, env_once)

    return {"schema": SCHEMA, "solutions": {name: solutions[name] for name in sorted(solutions, key=solution_key)}}


def v3_section_count(text: str) -> int:
    """Number of `## day-NN-<lang>-vN` sections (the lang-templates v3 layout) in a benchmarks.md."""
    return sum(1 for m in V3_SECTION_RE.finditer(text) if DIR_RE.match(m.group(1)))


def collect(root: Path, environment: Environment | None = None, stderr: TextIO | None = None) -> dict[str, Any]:
    """Load root/benchmarks.json (BenchmarksError if unusable) and merge root/days into it."""
    stderr = sys.stderr if stderr is None else stderr
    json_path = root / JSON_NAME
    existing = load_benchmarks(json_path)
    mtime = json_path.stat().st_mtime if existing is not None else None
    if existing is None:
        md = root / MD_NAME
        try:
            sections = v3_section_count(md.read_text(encoding="utf-8")) if md.is_file() else 0
        except (OSError, UnicodeDecodeError):
            sections = 0
        if sections:
            print(
                f"warning: {MD_NAME} has {sections} sections from lang-templates v3 that cannot be "
                "imported; run `just bench-all` to rebuild them",
                file=stderr,
            )
    return merge(existing, root, mtime, environment, stderr)


# --- rendering -------------------------------------------------------------


def fmt_ratio(median: float, fastest: float) -> str:
    """median / fastest as `1.00×`, `3.42×`, `12.7×`, `127×`; n/a when fastest is 0 and median is not."""
    if fastest == 0:
        return "1.00×" if median == 0 else NOT_AVAILABLE
    ratio = median / fastest
    return f"{_scaled(ratio, math.inf)}×" if math.isfinite(ratio) else NOT_AVAILABLE


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _header(columns: tuple[str, ...]) -> list[str]:
    separator = "|" + "|".join(":---" if c == "Solution" else "---:" for c in columns) + "|"
    return [_row(list(columns)), separator]


def ranked_groups(doc: dict[str, Any]) -> dict[tuple[int, int], list[tuple[int, str, dict[str, Any]]]]:
    """{(day, part): [(rank, solution, part data), ...]} ordered by day, part, then median.

    Rows of one group are sorted by median, ties by solution_key; equal medians share a
    rank (standard competition ranking: 1, 1, 3).
    """
    groups: dict[tuple[int, int], list[tuple[str, dict[str, Any]]]] = {}
    for name, entry in doc["solutions"].items():
        day = solution_key(name)[0]
        for part in entry["parts"]:
            groups.setdefault((day, part["part"]), []).append((name, part))
    ranked: dict[tuple[int, int], list[tuple[int, str, dict[str, Any]]]] = {}
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda r: (r[1]["ns"]["median"], solution_key(r[0])))
        out: list[tuple[int, str, dict[str, Any]]] = []
        for i, (name, part) in enumerate(rows):
            tied = out and out[-1][2]["ns"]["median"] == part["ns"]["median"]
            out.append((out[-1][0] if tied else i + 1, name, part))
        ranked[key] = out
    return ranked


def ranked_cells(rank: int, name: str, part: dict[str, Any], fastest: float) -> list[str]:
    ns = part["ns"]
    rss = part.get("rss_delta_bytes")
    wall = part.get("wall")
    return [
        str(rank),
        f"`{name}`",
        fmt_ns(ns["median"]),
        fmt_ratio(ns["median"], fastest),
        fmt_ns(ns["min"]),
        _fmt_spread(ns["mean"], ns["stddev"]),
        fmt_ns(ns["p99"]),
        str(part["iterations"]),
        NOT_AVAILABLE if rss is None else fmt_bytes(rss),
        NOT_AVAILABLE if wall is None else _fmt_spread(wall["mean_s"] * 1e9, wall["stddev_s"] * 1e9),
    ]


def render_single(doc: dict[str, Any]) -> list[str]:
    """One `## Results` table, rows ordered by day, part, median."""
    lines = _header(SINGLE_COLUMNS)
    for (day, part_no), rows in ranked_groups(doc).items():
        fastest = rows[0][2]["ns"]["median"]
        for rank, name, part in rows:
            lines.append(_row([f"{day:02d}", str(part_no), *ranked_cells(rank, name, part, fastest)]))
    return ["## Results", "\n".join(lines)]


def render_ranking(doc: dict[str, Any]) -> list[str]:
    """One `## Day NN, part P` table per day and part."""
    blocks: list[str] = []
    for (day, part_no), rows in ranked_groups(doc).items():
        fastest = rows[0][2]["ns"]["median"]
        lines = _header(RANKED_COLUMNS)
        lines += [_row(ranked_cells(rank, name, part, fastest)) for rank, name, part in rows]
        blocks += [f"## Day {day:02d}, part {part_no}", "\n".join(lines)]
    return blocks


def run_line(name: str, entry: dict[str, Any]) -> str:
    """`- `name`: benchmarked <date> on <cpu>, <os>, commit <sha>` for the Runs list."""
    when = instant(entry.get("benched_at"))
    date = when.date().isoformat() if when is not None else "at an unknown date"
    system = entry.get("system")
    where = f"{system['cpu']}, {system['os']}" if system else "unknown system"
    commit = entry.get("commit")
    return f"- `{name}`: benchmarked {date} on {where}, " + (f"commit {commit}" if commit else "commit unknown")


def render_runs(doc: dict[str, Any]) -> list[str]:
    names = sorted(doc["solutions"], key=solution_key)
    return ["## Runs", "\n".join(run_line(name, doc["solutions"][name]) for name in names)]


def render(doc: dict[str, Any], tablemode: str = DEFAULT_TABLEMODE) -> str:
    """benchmarks.md for a benchmarks.json document (answers are never stored, so never shown)."""
    if tablemode == "single":
        tables = render_single(doc)
    elif tablemode == "ranking":
        tables = render_ranking(doc)
    else:
        raise ValueError(f"unknown tablemode {tablemode!r}")
    return "\n\n".join([HEADING, INTRO, *tables, *render_runs(doc)]) + "\n"


# --- CLI -------------------------------------------------------------------


def build_parser() -> UsageArgumentParser:
    parser = UsageArgumentParser(
        prog="collect_benches.py",
        description="Merge days/*/bench.json into benchmarks.json and render benchmarks.md.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="year repo root containing days/ (default: current directory)",
    )
    parser.add_argument(
        "--tablemode",
        choices=TABLEMODES,
        default=DEFAULT_TABLEMODE,
        help="single: one ranked table (default); ranking: one table per day and part",
    )
    return parser


def main(argv: list[str] | None = None, root: Path | None = None, environment: Environment | None = None) -> int:
    """CLI entry point; an explicit root overrides --root and environment the system/commit
    fallback for v3.0.0 bench.json files (both used by tests)."""
    args: argparse.Namespace = build_parser().parse_args(argv)
    root = args.root if root is None else root
    try:
        doc = collect(root, environment)
    except BenchmarksError as e:
        print(f"error: {JSON_NAME}: {e} — fix or delete it", file=sys.stderr)
        return 1
    if not doc["solutions"]:
        print(
            f"error: nothing to render: {JSON_NAME} has no entries and no "
            "days/day-NN-<lang>-vN/bench.json files were found — run `just bench <day>` first",
            file=sys.stderr,
        )
        return 1
    content = render(doc, args.tablemode)
    for name, write in ((JSON_NAME, lambda p: write_json_atomic(p, doc, sort_keys=True)),
                        (MD_NAME, lambda p: write_text_atomic(p, content))):
        try:
            write(root / name)
        except OSError as e:
            print(f"error: cannot write {name}: {e.strerror or e}", file=sys.stderr)
            return 1
    print(f"wrote {JSON_NAME} and {MD_NAME} ({len(doc['solutions'])} solutions, {args.tablemode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
