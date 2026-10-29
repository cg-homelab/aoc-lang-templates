#!/usr/bin/env python3
"""Benchmark one solution in-process: time only process(input), per part.

Usage: python3 lang-templates/scripts/bench_day.py [--name NAME] [--input input.txt]
           [--time 1.0] [--min-iterations 10] [--max-iterations 100000]
           [--iterations N] [--warmup K] [--no-hyperfine] [--json-out bench.json]
           -- <cmd> [args...]

Run from the solution directory. <cmd> is the solution executable (already
split into words), which must implement the executable contract:

    <cmd> 1|2                                    print the answer for input.txt
    <cmd> bench <part> <input-path> <iterations> print one JSON line (schema 1)

For each part the driver checks run mode (`<cmd> <part>`, only with the default
--input) and calibrates (skipped with --iterations): a 1-iteration bench run
gives t1; unless t1 >= 0.1 s (slow parts are not calibrated further), a second
run of clamp(0.1 s / t1, 5, 1000) iterations gives the per-iteration time as
the median of its last 80% (warm) samples. It then picks N iterations so the
measured run takes about --time seconds (clamped to [--min-iterations,
--max-iterations]) plus K warmup iterations (N//10, 1-100, unless --warmup),
runs `<cmd> bench <part> <input> N+K`, drops the warmup samples and
summarises the rest. With hyperfine on PATH (and the default
--input, without --no-hyperfine) it also records hyperfine's whole-process
wall time of `<cmd> <part>`. The summary, with the machine (`system`: CPU and
OS) and the year repo's `commit` (short hash, `(dirty)` when tracked files are
modified, null outside a git repo), is written atomically to bench.json and
printed as a markdown table. collect_benches.py merges bench.json files into
the year repo's benchmarks.json / benchmarks.md.

Exit status: 0 ok, 1 usage, 2 solution failed, 3 protocol violation,
4 answer mismatch. Errors are `error: ...` lines on stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = 1
DEFAULT_INPUT = "input.txt"
CALIBRATION_SLOW_NS = 100_000_000  # stage 1 at least this slow: no stage 2
CALIBRATION_TARGET_NS = 100_000_000  # stage 2 aims for about this much work
CALIBRATION_MIN_ITERATIONS, CALIBRATION_MAX_ITERATIONS = 5, 1000
HYPERFINE_WARMUP = 3
BENCH_KEYS = frozenset({"schema", "part", "iterations", "ns", "answer", "max_rss_before", "max_rss_after"})

EXIT_OK, EXIT_USAGE, EXIT_SOLUTION, EXIT_PROTOCOL, EXIT_MISMATCH = 0, 1, 2, 3, 4

TABLE_HEADER = "| Part | Min | Median | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |"
TABLE_SEPARATOR = "|---:|---:|---:|---:|---:|---:|---:|---:|"
NOT_AVAILABLE = "n/a"

NS_UNITS = (("ns", 1.0), ("µs", 1e3), ("ms", 1e6), ("s", 1e9))
BYTE_UNITS = (("B", 1), ("KiB", 1024), ("MiB", 1024**2), ("GiB", 1024**3))


class BenchError(Exception):
    """A failure that ends the run with an exit code and an `error: ...` message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProtocolError(Exception):
    """The bench-mode output does not follow the executable contract."""


# --- formatting ------------------------------------------------------------


def _scaled(value: float, limit: float) -> str | None:
    """value with 2/1/0 decimals for [0,10)/[10,100)/[100,limit); None if it rounds to >= limit."""
    for decimals, below in ((2, 10.0), (1, 100.0), (0, limit)):
        text = f"{value:.{decimals}f}"
        if abs(float(text)) < below:
            return text
    return None


def _fmt_units(value: float, units: Sequence[tuple[str, float]], limit: float) -> str:
    if value == 0:
        return f"0 {units[0][0]}"
    for i, (unit, scale) in enumerate(units):
        last = i == len(units) - 1
        text = _scaled(value / scale, math.inf if last else limit)
        if text is not None:
            return f"{text} {unit}"
    raise AssertionError("unreachable: the last unit has no upper limit")


def fmt_ns(ns: float) -> str:
    """Nanoseconds as `123 ns`, `1.23 µs`, `12.3 ms`, `1.50 s` (value in [1, 1000))."""
    return _fmt_units(float(ns), NS_UNITS, 1000.0)


def fmt_bytes(n: float) -> str:
    """Bytes as `512 B`, `1.50 KiB`, `24.5 MiB`, `123 MiB`, `2.00 GiB`."""
    if abs(n) < 1024:
        return f"{round(n)} B"
    return _fmt_units(float(n), BYTE_UNITS, 1024.0)


def _fmt_spread(mean: float, stddev: float) -> str:
    return f"{fmt_ns(mean)} ± {fmt_ns(stddev)}"


def render_table(data: dict[str, Any]) -> str:
    """Markdown table of a bench.json document (answers are never shown)."""
    rows = [TABLE_HEADER, TABLE_SEPARATOR]
    for part in data["parts"]:
        ns = part["ns"]
        rss = part.get("rss_delta_bytes")
        wall = part.get("wall")
        cells = [
            str(part["part"]),
            fmt_ns(ns["min"]),
            fmt_ns(ns["median"]),
            _fmt_spread(ns["mean"], ns["stddev"]),
            fmt_ns(ns["p99"]),
            str(part["iterations"]),
            NOT_AVAILABLE if rss is None else fmt_bytes(rss),
            NOT_AVAILABLE if wall is None else _fmt_spread(wall["mean_s"] * 1e9, wall["stddev_s"] * 1e9),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


# --- pure helpers ----------------------------------------------------------


def compute_iterations(time_s: float, t_cal_ns: float, min_iterations: int, max_iterations: int) -> int:
    """Iterations that fill about time_s seconds at t_cal_ns per iteration, clamped."""
    n = round(time_s * 1e9 / max(t_cal_ns, 1))
    return max(min_iterations, min(max_iterations, n))


def calibration_iterations(t1_ns: float) -> int:
    """Stage-2 calibration size: about CALIBRATION_TARGET_NS of work, clamped to [5, 1000]."""
    n = round(CALIBRATION_TARGET_NS / max(t1_ns, 1))
    return max(CALIBRATION_MIN_ITERATIONS, min(CALIBRATION_MAX_ITERATIONS, n))


def warm_median(ns: Sequence[int]) -> float:
    """Median of the samples after the first 20% (the calibration run's own warmup)."""
    return statistics.median(ns[len(ns) // 5:])


def warmup_for(iterations: int) -> int:
    """Default warmup: a tenth of the measured iterations, between 1 and 100."""
    return max(1, min(100, iterations // 10))


def trim_warmup(ns: Sequence[int], warmup: int) -> list[int]:
    """The measured samples: everything after the first `warmup` iterations."""
    return list(ns[warmup:])


def summarize(samples: Sequence[int]) -> dict[str, float | int]:
    """min, median, mean, sample stddev (0.0 for one sample), p99 (nearest rank), max."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)
    n = len(ordered)
    rank = -(-99 * n // 100)  # ceil(0.99 * n) in integer math
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "stddev": statistics.stdev(ordered) if n > 1 else 0.0,
        "p99": ordered[rank - 1],
        "max": ordered[-1],
    }


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _excerpt(text: str, limit: int = 80) -> str:
    text = text.strip()
    return repr(text if len(text) <= limit else text[:limit] + "...")


def parse_bench_output(stdout: str, part: int, iterations: int) -> dict[str, Any]:
    """Validate one bench-mode stdout against the schema-1 contract; raise ProtocolError."""
    # Only "\n" separates lines: splitlines() would also split on U+0085/U+2028/U+2029,
    # which a harness may legitimately pass through raw inside the answer string.
    lines = [line for line in stdout.split("\n") if line.strip()]
    if not lines:
        raise ProtocolError("bench mode printed nothing on stdout (expected one JSON line)")
    if len(lines) > 1:
        raise ProtocolError(
            f"bench mode printed {len(lines)} lines on stdout, expected exactly one JSON line "
            f"(is process() printing something?); first line: {_excerpt(lines[0])}"
        )
    try:
        doc = json.loads(lines[0])
    except ValueError:
        raise ProtocolError(f"bench mode output is not JSON: {_excerpt(lines[0])}") from None
    if not isinstance(doc, dict):
        raise ProtocolError(f"bench mode output is not a JSON object: {_excerpt(lines[0])}")
    unknown = sorted(set(doc) - BENCH_KEYS)
    if unknown:
        raise ProtocolError(f"bench mode output has unknown key(s): {', '.join(unknown)}")
    missing = sorted(BENCH_KEYS - set(doc))
    if missing:
        raise ProtocolError(f"bench mode output is missing key(s): {', '.join(missing)}")
    if not _is_int(doc["schema"]) or doc["schema"] != SCHEMA:
        raise ProtocolError(f"unsupported schema {doc['schema']!r}, expected {SCHEMA}")
    if not _is_int(doc["part"]) or doc["part"] != part:
        raise ProtocolError(f"bench mode reported part {doc['part']!r}, expected {part}")
    if not _is_int(doc["iterations"]) or doc["iterations"] != iterations:
        raise ProtocolError(f"bench mode reported iterations {doc['iterations']!r}, expected {iterations}")
    ns = doc["ns"]
    if not isinstance(ns, list):
        raise ProtocolError("ns is not a list")
    if len(ns) != iterations:
        raise ProtocolError(f"ns has {len(ns)} samples, expected {iterations}")
    if not all(_is_int(v) and v >= 0 for v in ns):
        raise ProtocolError("ns must contain only non-negative integers")
    if not isinstance(doc["answer"], str):
        raise ProtocolError(f"answer must be a string, got {doc['answer']!r}")
    before, after = doc["max_rss_before"], doc["max_rss_after"]
    for key, value in (("max_rss_before", before), ("max_rss_after", after)):
        if value is not None and not (_is_int(value) and value >= 0):
            raise ProtocolError(f"{key} must be a non-negative integer or null, got {value!r}")
    if (before is None) != (after is None):
        raise ProtocolError("max_rss_before and max_rss_after must both be integers or both null")
    if before is not None and after < before:
        raise ProtocolError(f"max_rss_after ({after}) is smaller than max_rss_before ({before})")
    return doc


# --- running the solution --------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError as e:
        raise BenchError(EXIT_SOLUTION, f"cannot run {shlex.join(cmd)}: {e.strerror or e}") from None


def _failure(what: str, result: subprocess.CompletedProcess[str]) -> BenchError:
    detail = result.stderr.strip()
    message = f"{what} failed with exit status {result.returncode}"
    return BenchError(EXIT_SOLUTION, f"{message}:\n{detail}" if detail else message)


def run_mode_answer(cmd: list[str], part: int) -> str:
    result = _run([*cmd, str(part)])
    if result.returncode != 0:
        raise _failure(f"run mode `{shlex.join([*cmd, str(part)])}`", result)
    return result.stdout.removesuffix("\n").removesuffix("\r")


def bench_mode(cmd: list[str], part: int, input_path: Path, iterations: int) -> dict[str, Any]:
    argv = [*cmd, "bench", str(part), str(input_path), str(iterations)]
    result = _run(argv)
    if result.returncode != 0:
        raise _failure(f"bench mode `{shlex.join(argv)}`", result)
    try:
        return parse_bench_output(result.stdout, part, iterations)
    except ProtocolError as e:
        raise BenchError(EXIT_PROTOCOL, f"part {part}: {e}") from None


def parse_hyperfine_json(doc: Any) -> dict[str, Any]:
    """The `wall` entry from a hyperfine --export-json document; raise ValueError if unusable.

    hyperfine 1.x puts mean/stddev/median/min/max (seconds) and `times` on results[0];
    2.x (schema_version 2) puts them, with `count`, under results[0].summary.time_wall_clock.
    """
    try:
        result = doc["results"][0]
        if "summary" in result:
            stats = result["summary"]["time_wall_clock"]
            if stats.get("unit", "second") != "second":
                raise ValueError(f"unexpected --export-json format (time unit {stats['unit']!r})")
            runs = stats["count"]
        else:
            stats = result
            runs = len(stats["times"])
        stddev = stats.get("stddev")
        wall = {
            "mean_s": float(stats["mean"]),
            "stddev_s": 0.0 if stddev is None else float(stddev),
            "median_s": float(stats["median"]),
            "min_s": float(stats["min"]),
            "max_s": float(stats["max"]),
            "runs": int(runs),
        }
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise ValueError(f"unexpected --export-json format ({type(e).__name__}: {e})") from None
    if not all(math.isfinite(v) for k, v in wall.items() if k != "runs"):
        raise ValueError("unexpected --export-json format (non-finite time)")
    return wall


def hyperfine_wall(cmd: list[str], part: int) -> dict[str, Any] | None:
    """hyperfine's whole-process timing of `<cmd> <part>`, or None (with a warning) on failure."""
    fd, tmp = tempfile.mkstemp(prefix="bench-hyperfine-", suffix=".json")
    os.close(fd)
    try:
        argv = [
            "hyperfine", "-N", "--warmup", str(HYPERFINE_WARMUP), "--style", "none",
            "--export-json", tmp, "--", shlex.join([*cmd, str(part)]),
        ]
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
        except OSError as e:
            raise ValueError(f"cannot run hyperfine: {e.strerror or e}") from None
        if result.returncode != 0:
            lines = result.stderr.strip().splitlines()
            raise ValueError(f"exit status {result.returncode}" + (f": {lines[-1]}" if lines else ""))
        with open(tmp, encoding="utf-8") as f:
            return parse_hyperfine_json(json.load(f))
    except (OSError, ValueError) as e:
        print(f"warning: hyperfine failed for part {part} ({e}); Wall column will be n/a", file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# --- run environment -------------------------------------------------------


def _capture(cmd: list[str], cwd: Path | None = None) -> str:
    """stdout of cmd, stripped; raise OSError / CalledProcessError on failure."""
    return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def cpu_name() -> str:
    """Best-effort CPU model name."""
    try:
        if platform.system() == "Darwin":
            name = _capture(["sysctl", "-n", "machdep.cpu.brand_string"])
            if name:
                return name
        elif platform.system() == "Linux":
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine() or "unknown CPU"


def os_name() -> str:
    """OS name, version and architecture, e.g. `Linux 6.8.0 (x86_64)`, `macOS 14.5 (arm64)`."""
    system = platform.system() or "unknown OS"
    if system == "Darwin":
        system, version = "macOS", platform.mac_ver()[0]
    else:
        version = platform.release()
    parts = f"{system} {version}".strip()
    return f"{parts} ({platform.machine() or 'unknown'})"


# Files collect_benches.py regenerates at the repo root: modifying them is not "dirty".
GENERATED_PATHSPECS = (":(top,exclude)benchmarks.json", ":(top,exclude)benchmarks.md")


def git_commit(directory: Path | None = None) -> str | None:
    """Short HEAD hash of the repo containing directory, `<hash> (dirty)` when tracked files
    are modified, None when git fails or there is no repo/commit.

    Untracked and ignored files never count, nor do the generated benchmarks.json /
    benchmarks.md at the repo root (otherwise every bench after the first would be dirty).
    Exclude-only pathspecs match everything else, including a submodule pointer.
    """
    try:
        commit = _capture(["git", "rev-parse", "--short", "HEAD"], directory)
    except (OSError, subprocess.SubprocessError):
        return None
    if not commit:
        return None
    try:
        dirty = bool(_capture(["git", "status", "--porcelain", "--untracked-files=no", "--",
                               *GENERATED_PATHSPECS], directory))
    except (OSError, subprocess.SubprocessError):
        dirty = False
    return f"{commit} (dirty)" if dirty else commit


# --- output ----------------------------------------------------------------


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def write_text_atomic(path: Path, text: str) -> None:
    """Write text as UTF-8 with `\n` line endings (on every OS), atomically, with umask permissions."""
    path = path.absolute()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, 0o666 & ~_umask())  # mkstemp creates 0600; give it normal permissions
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: dict[str, Any], sort_keys: bool = False) -> None:
    """Write data as indented UTF-8 JSON plus a trailing newline, atomically (see write_text_atomic)."""
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=sort_keys) + "\n")


def _fmt_seconds(s: float) -> str:
    return f"{s:.2f} s" if s >= 0.01 else f"{s * 1000:.2f} ms"


def calibrate(cmd: list[str], part: int, input_path: Path) -> tuple[float, list[str]]:
    """Per-iteration time (ns) from warm samples, and the answers each calibration run returned."""
    stage1 = bench_mode(cmd, part, input_path, 1)
    t1 = stage1["ns"][0]
    if t1 >= CALIBRATION_SLOW_NS:
        return float(t1), [stage1["answer"]]
    stage2 = bench_mode(cmd, part, input_path, calibration_iterations(t1))
    return float(warm_median(stage2["ns"])), [stage1["answer"], stage2["answer"]]


def bench_part(args: argparse.Namespace, cmd: list[str], part: int, input_path: Path,
               check_run: bool, use_hyperfine: bool) -> dict[str, Any]:
    run_answer = run_mode_answer(cmd, part) if check_run else None

    calibration_answers: list[str] = []
    if args.iterations is not None:
        n = args.iterations
        t_cal = None
    else:
        t_cal, calibration_answers = calibrate(cmd, part, input_path)
        n = compute_iterations(args.time, t_cal, args.min_iterations, args.max_iterations)
    k = warmup_for(n) if args.warmup is None else args.warmup

    if t_cal is None:
        print(f"part {part}: running {n} (+{k} warmup) iterations", file=sys.stderr)
    else:
        estimate = _fmt_seconds((n + k) * t_cal / 1e9)
        print(f"part {part}: ~{fmt_ns(t_cal)}/iter, running {n} (+{k} warmup) iterations (~{estimate})",
              file=sys.stderr)

    measured = bench_mode(cmd, part, input_path, n + k)
    answer = measured["answer"]
    for calibration_answer in calibration_answers:
        if calibration_answer != answer:
            raise BenchError(EXIT_MISMATCH, f"answer mismatch: calibration returned {calibration_answer!r}, "
                                            f"measured run returned {answer!r}")
    if run_answer is not None and run_answer != answer:
        raise BenchError(EXIT_MISMATCH, f"answer mismatch: run mode printed {run_answer!r}, "
                                        f"bench mode returned {answer!r}")

    before, after = measured["max_rss_before"], measured["max_rss_after"]
    wall = None
    if use_hyperfine:
        print(f"part {part}: hyperfine (whole process)", file=sys.stderr)
        wall = hyperfine_wall(cmd, part)
    return {
        "part": part,
        "answer": answer,
        "iterations": n,
        "warmup": k,
        "ns": summarize(trim_warmup(measured["ns"], k)),
        "rss_delta_bytes": None if before is None else after - before,
        "wall": wall,
    }


# --- CLI -------------------------------------------------------------------


class UsageArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1 (argparse would exit 2, which means "solution failed" here)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise ValueError(text)
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise ValueError(text)
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if not (value > 0 and math.isfinite(value)):
        raise ValueError(text)
    return value


_positive_int.__name__ = "positive integer"
_non_negative_int.__name__ = "non-negative integer"
_positive_float.__name__ = "positive number"


def build_parser() -> UsageArgumentParser:
    parser = UsageArgumentParser(
        prog="bench_day.py",
        usage="%(prog)s [options] -- <cmd> [args...]",
        description="Time a solution's process() in-process, per part, and write bench.json.",
    )
    parser.add_argument("--name", help="solution name (default: current directory name)")
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help="puzzle input; a non-default input skips the run-mode check and hyperfine")
    parser.add_argument("--time", type=_positive_float, default=1.0, help="target seconds per part (default 1.0)")
    parser.add_argument("--min-iterations", type=_positive_int, default=10)
    parser.add_argument("--max-iterations", type=_positive_int, default=100_000)
    parser.add_argument("--iterations", type=_positive_int, help="fixed iteration count (skips calibration)")
    parser.add_argument("--warmup", type=_non_negative_int, help="warmup iterations (default N//10, 1-100)")
    parser.add_argument("--no-hyperfine", action="store_true", help="skip hyperfine's wall-time column")
    parser.add_argument("--json-out", default="bench.json", help="output file (default bench.json)")
    return parser


def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Options before the first `--`, command after it."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def run(argv: list[str]) -> int:
    parser = build_parser()
    options, cmd = split_argv(argv)
    args = parser.parse_args(options)
    if args.min_iterations > args.max_iterations:
        parser.error("--min-iterations is larger than --max-iterations")
    if not cmd or not cmd[0]:
        parser.error("no command given after `--`")

    name = args.name or Path.cwd().name
    input_path = Path(args.input)
    if not input_path.is_file():
        raise BenchError(EXIT_SOLUTION, f"{args.input} missing — paste your puzzle input")
    if input_path.stat().st_size == 0:
        raise BenchError(EXIT_SOLUTION, f"{args.input} is empty — paste your puzzle input")
    abs_input = input_path.resolve()

    default_input = args.input == DEFAULT_INPUT
    if not default_input:
        print(f"note: --input {args.input}: the result goes to {args.json_out}, but collect_benches.py "
              "will not merge it into benchmarks.json", file=sys.stderr)
    use_hyperfine = False
    if default_input and not args.no_hyperfine:
        if shutil.which("hyperfine"):
            use_hyperfine = True
        else:
            print("note: hyperfine not found; Wall (hyperfine) column will be n/a", file=sys.stderr)

    parts = [bench_part(args, cmd, part, abs_input, default_input, use_hyperfine) for part in (1, 2)]
    data = {
        "schema": SCHEMA,
        "name": name,
        "command": cmd,
        "input": args.input,
        "benched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "system": {"cpu": cpu_name(), "os": os_name()},
        "commit": git_commit(Path.cwd()),
        "settings": {
            "time": args.time,
            "min_iterations": args.min_iterations,
            "max_iterations": args.max_iterations,
        },
        "parts": parts,
    }
    out = Path(args.json_out)
    try:
        write_json_atomic(out, data)
    except OSError as e:
        raise BenchError(EXIT_USAGE, f"cannot write {args.json_out}: {e.strerror or e}") from None
    print(render_table(data))
    print(f"\nwrote {args.json_out}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # µ/±/Δ on a legacy Windows console
        except (AttributeError, ValueError):
            pass
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except BenchError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
