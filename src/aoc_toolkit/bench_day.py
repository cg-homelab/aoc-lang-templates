#!/usr/bin/env python3
"""Benchmark one solution in-process: time only process(input), per part.

Usage: aoc-toolkit bench [--name NAME] [--input input.txt]
           [--time 1.0] [--min-iterations 10] [--max-iterations 100000]
           [--iterations N] [--warmup K] [--timeout SECONDS] [--no-hyperfine]
           [--json-out bench.json] -- <cmd> [args...]

Run from the solution directory. <cmd> is the solution executable (already
split into words), which must implement the executable contract (harness
protocol 2):

    <cmd> 1|2                                                          print the answer for input.txt
    <cmd> bench <part> <input-path> <budget-ns> <min-iter> <max-iter>  print samples and the answer

Bench mode reads the input, allocates the sample buffer for max-iter samples
and calls process() while `i < min-iter or (i < max-iter and elapsed <
budget-ns)`. It prints one sample (nanoseconds per call, a decimal integer)
per line, then, if it ran at least once, a line `answer <N>` followed by the
N-byte UTF-8 answer (as run mode prints it, without the trailing newline) and
a newline, then always a line `done`. It flushes stdout and parks until a
line (or EOF) arrives on stdin, then exits 0. Usage errors, unreadable input
and answers that differ between iterations exit 1 with nothing on stdout.

For each part the driver checks run mode (`<cmd> <part>`, only with the
default --input), then runs bench mode twice: a zero-iteration baseline
(`... 0 0 <max-iter>`: same startup, input read and buffer, no process()
calls; prints only `done`) and the measured run (`... <--time in ns>
<--min-iterations> <--max-iterations>`; with --iterations N, `... 0 N+K
N+K`). On Linux the driver reads each harness's own peak RSS (VmHWM in
/proc/<pid>/status) while it is parked; a harness that exits without parking
gets n/a (the kernel's after-exit ru_maxrss there includes the driver's own
RSS). Elsewhere it takes the peak from the kernel after exit (ru_maxrss on
macOS, PeakWorkingSetSize on Windows). Peak RSS Δ is measured minus
baseline. The driver drops the first K samples as warmup (n//10, 1-100,
unless --warmup) and summarises the rest. With hyperfine on PATH (and the
default --input, without --no-hyperfine) it also records hyperfine's
whole-process wall time of `<cmd> <part>`. The summary, with the machine
(`system`: CPU and OS) and the year repo's `commit` (short hash, `(dirty)`
when tracked files are modified, null outside a git repo), is written
atomically to bench.json and printed as a markdown table. `aoc-toolkit
collect` merges bench.json files into the year repo's benchmarks.json / benchmarks.md.

A harness that never prints `done` (or a complete answer) would block the
driver forever, so a watchdog kills it: after 60 s for the zero-iteration
baseline run (startup, input and buffer only), and after --timeout seconds
for the measured run (no limit by default, so a slow harness is never
killed). Bounds: --time at most 9223372036 s (budget-ns fits in 63 bits);
--min-iterations, --max-iterations, --iterations (plus warmup) and --warmup at
most 2147483647.

Exit status: 0 ok, 1 usage, 2 solution failed, 3 protocol violation,
4 answer mismatch. Errors are `error: ...` lines on stderr.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import platform
import shlex
import shutil
import statistics
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple, NoReturn

SCHEMA = 1
DEFAULT_INPUT = "input.txt"
HYPERFINE_WARMUP = 3
OLD_HARNESS_HINT = "(old harness? regenerate the bench file from the template)"
READ_CHUNK = 1 << 16
MAX_DIGITS = 20  # samples and answer lengths are at most 64-bit numbers
BASELINE_TIMEOUT_S = 60.0
MAX_BUDGET_NS = 2**63 - 1
MAX_ITERATIONS = 2**31 - 1
PROCESS_VM_READ = 0x0010

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


def warmup_for(iterations: int) -> int:
    """Default warmup: a tenth of the iterations, between 1 and 100 (none for a single iteration)."""
    return 0 if iterations < 2 else max(1, min(100, iterations // 10))


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


def _is_sample(text: str) -> bool:
    """A non-negative decimal integer of at most MAX_DIGITS ASCII digits (int() would also take `+1`,
    ` 1`, `1_0`, and raises on more than 4300 digits)."""
    return 0 < len(text) <= MAX_DIGITS and text.isascii() and text.isdigit()


def _strip_answer(text: str) -> str:
    """The answer as printed: one trailing newline (`\n`, then `\r`) removed."""
    return text.removesuffix("\n").removesuffix("\r")


def _decode(raw: bytes) -> str:
    """Decode like subprocess text mode (UTF-8, errors replaced, universal newlines), as _run does."""
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _line_excerpt(raw: bytes) -> str:
    return _excerpt(raw.decode("utf-8", errors="replace"))


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    """Up to n bytes from stream, fewer only at EOF (in chunks: n comes from the harness)."""
    chunks: list[bytes] = []
    while n > 0:
        chunk = stream.read(min(n, READ_CHUNK))
        if not chunk:
            break
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


class BenchOutput(NamedTuple):
    """What bench mode printed up to `done`: the samples and the answer (None without an answer block)."""

    ns: list[int]
    answer: str | None


def read_bench_output(stream: BinaryIO, consumed: bytearray | None = None) -> BenchOutput:
    """Read one bench-mode stdout (protocol 2) up to and including its `done` line; raise ProtocolError.

    Lines are samples until an `answer <N>` header, which is followed by exactly N bytes (newlines
    inside them are part of the answer) and a newline; then the only valid line is `done`. A
    trailing "\r" on a line is tolerated. Nothing after `done` is read, so a parked harness is
    still alive when this returns. Every byte read is appended to consumed.
    """
    sink = bytearray() if consumed is None else consumed
    ns: list[int] = []
    answer: str | None = None
    lineno = 0
    while True:
        raw = stream.readline()
        sink += raw
        if not raw:
            raise ProtocolError("bench mode exited without printing `done`")
        lineno += 1
        line = raw.removesuffix(b"\n").removesuffix(b"\r")
        if line == b"done":
            return BenchOutput(ns, answer)
        if answer is not None:
            raise ProtocolError(f"bench mode output line {lineno} after the answer is not `done`: "
                                f"{_line_excerpt(line)}")
        text = line.decode("utf-8", errors="replace")
        if _is_sample(text):
            ns.append(int(text))
            continue
        if text == "answer" or text.startswith("answer "):
            size = text.removeprefix("answer").removeprefix(" ")
            if not _is_sample(size):
                raise ProtocolError(f"bench mode output line {lineno} is a malformed `answer` header: "
                                    f"{_excerpt(text)} (expected `answer <byte length>`)")
            n = int(size)
            data = _read_exact(stream, n)
            sink += data
            if len(data) < n:
                raise ProtocolError(f"bench mode answer header says {n} bytes, but stdout ended after {len(data)}")
            newline = stream.read(1)
            if newline == b"\r":
                newline += stream.read(1)
            sink += newline
            if newline not in (b"\n", b"\r\n"):
                rest = newline + stream.readline()
                sink += rest[len(newline):]
                raise ProtocolError(f"bench mode answer ({n} bytes) is not followed by a newline: "
                                    f"got {_line_excerpt(rest) if rest else 'end of output'}")
            lineno += data.count(b"\n") + 1
            # Run mode's text-mode decode turns a final "\r\n" into "\n" before stripping it: match that.
            answer = _decode(data.removesuffix(b"\r"))
            continue
        old_harness = lineno == 1 and text.lstrip().startswith("{")  # protocol 1 printed one JSON object
        hint = OLD_HARNESS_HINT if old_harness else "(is process() printing something?)"
        raise ProtocolError(f"bench mode output line {lineno} is not a sample: {_excerpt(text)} {hint}")


def check_bench_output(output: BenchOutput, trailing: bytes, lo: int, hi: int) -> tuple[list[int], str]:
    """Samples and answer of a measured run that printed `done`; raise ProtocolError."""
    if trailing:
        first = trailing.split(b"\n", 1)[0]
        raise ProtocolError(f"bench mode printed output after `done`: {_line_excerpt(first)}")
    ns = output.ns
    if not lo <= len(ns) <= hi:
        expected = str(lo) if lo == hi else f"between {lo} and {hi}"
        raise ProtocolError(f"bench mode printed {len(ns)} samples, expected {expected}")
    if output.answer is None:
        raise ProtocolError(f"bench mode printed {len(ns)} sample(s) but no `answer` block")
    return ns, output.answer


def parse_bench_output(stdout: bytes, lo: int, hi: int) -> tuple[list[int], str]:
    """Samples and answer from a complete measured-run stdout (read_bench_output + check_bench_output)."""
    stream = io.BytesIO(stdout)
    output = read_bench_output(stream)
    return check_bench_output(output, stream.read(), lo, hi)


# --- running the solution --------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
    except OSError as e:
        raise BenchError(EXIT_SOLUTION, f"cannot run {shlex.join(cmd)}: {e.strerror or e}") from None


class ChildResult(NamedTuple):
    """A finished bench-mode process.

    output is what it printed up to `done` (None if reading failed: see error), consumed the
    bytes read for that, trailing anything after `done`. live is its own peak RSS read while it
    was parked (Linux), reaped the peak the kernel reported after exit; either may be None.
    timed_out: the watchdog killed it.
    """

    returncode: int
    stderr: str
    live: int | None
    reaped: int | None
    output: BenchOutput | None
    error: ProtocolError | None
    consumed: bytes
    trailing: bytes
    timed_out: bool


def _platform_peak(result: ChildResult) -> int | None:
    """The peak RSS to report: live on Linux (the after-exit value has the driver's RSS as a floor)."""
    return result.live if sys.platform.startswith("linux") else result.reaped


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _open_process_handle(pid: int) -> int | None:
    """Windows: our own handle to the child (for its memory counters after exit); None elsewhere."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        # GetProcessMemoryInfo documents QUERY_(LIMITED_)INFORMATION | VM_READ; try that first.
        for access in (PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, PROCESS_QUERY_LIMITED_INFORMATION):
            handle = kernel32.OpenProcess(access, 0, pid)
            if handle:
                return handle
        return None
    except Exception:
        return None


def _close_process_handle(handle: int | None) -> None:
    if handle is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle(handle)
    except Exception:
        pass


def _windows_peak(handle: int | None) -> int | None:
    """PeakWorkingSetSize of the (exited) process behind handle, or None."""
    if handle is None:
        return None
    try:
        import ctypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                )
            ]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters),
                                               ctypes.c_ulong)
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize)
    except Exception:
        return None


def _live_peak(pid: int) -> int | None:
    """Linux: VmHWM of a live (parked) process in bytes, its own peak; None elsewhere or if unreadable.

    On Linux the ru_maxrss reported after exit is at least the spawning process's own RSS
    (recorded at exec), which would hide small allocations; VmHWM is per address space.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("VmHWM:"):
            fields = line.split()
            if len(fields) == 3 and fields[1].isdigit() and fields[2] == "kB":
                return int(fields[1]) * 1024
    return None


def _release(proc: subprocess.Popen[bytes]) -> None:
    """Send the parked harness its line on stdin and close it (it may already be gone)."""
    assert proc.stdin is not None
    try:
        proc.stdin.write(b"\n")
        proc.stdin.flush()
    except OSError:
        pass
    try:
        proc.stdin.close()
    except OSError:
        pass


def _reap(proc: subprocess.Popen[bytes], handle: int | None) -> int | None:
    """Wait for proc (setting proc.returncode) and return its peak RSS in bytes, or None.

    POSIX: os.wait4 reaps the child and returns its own rusage (RUSAGE_CHILDREN would be a
    max over all children so far). ru_maxrss is in KiB on Linux, bytes on macOS. If the child
    was already reaped elsewhere its exit status is lost: BenchError.
    """
    if os.name == "nt":
        proc.wait()
        return _windows_peak(handle)
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    except ChildProcessError:
        # Popen.wait() would now invent exit status 0, turning a crash into a success.
        raise BenchError(EXIT_SOLUTION, f"{shlex.join(map(str, proc.args))}: exit status unavailable "
                                        "(child reaped elsewhere)") from None
    proc.returncode = os.waitstatus_to_exitcode(status)
    return usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024


class _Watchdog:
    """Kill a child after `seconds` (None: never). stop() guarantees no kill afterwards, so the
    pid cannot be reused by the time it is killed (POSIX kills by pid: Popen.kill() would poll and
    could reap the child, losing its rusage)."""

    def __init__(self, proc: subprocess.Popen[bytes], seconds: float | None) -> None:
        self.fired = False
        self._proc = proc
        self._lock = threading.Lock()
        self._stopped = False
        self._timer = None if seconds is None else threading.Timer(seconds, self._fire)
        if self._timer is not None:
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self.fired = True
            try:
                if os.name == "nt":
                    self._proc.kill()
                else:
                    os.kill(self._proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        if self._timer is not None:
            self._timer.cancel()


def _wait_exited(proc: subprocess.Popen[bytes]) -> None:
    """Block until the child exits without reaping it (its pid stays ours), where supported."""
    if os.name == "nt" or not hasattr(os, "waitid"):
        return
    try:
        os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
    except ChildProcessError:
        pass


def _close_stdin(proc: subprocess.Popen[bytes]) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass


def _run_with_peak(argv: list[str], timeout: float | None = None) -> ChildResult:
    """One parked bench-mode session: read stdout up to `done`, take the live peak RSS while the
    harness waits on stdin, release it, read the rest of stdout and reap it.

    stderr goes to a temporary file so stdout is the only pipe read. Not communicate(): it reaps
    with waitpid and loses the rusage. If reading fails the harness is still released, drained
    and reaped, so the caller can report a non-zero exit before the protocol error. A watchdog
    kills the child after timeout seconds (the kill ends stdout, so every read returns).
    """
    with tempfile.TemporaryFile() as errfile:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errfile)
        except OSError as e:
            raise BenchError(EXIT_SOLUTION, f"cannot run {shlex.join(argv)}: {e.strerror or e}") from None
        watchdog = _Watchdog(proc, timeout)
        handle = _open_process_handle(proc.pid)
        consumed = bytearray()
        output: BenchOutput | None = None
        error: ProtocolError | None = None
        live: int | None = None
        try:
            assert proc.stdout is not None
            with proc.stdout:
                try:
                    output = read_bench_output(proc.stdout, consumed)
                except ProtocolError as e:
                    error = e
                else:
                    live = _live_peak(proc.pid)
                _release(proc)
                trailing = proc.stdout.read()
            _wait_exited(proc)  # still under the watchdog: a child may hang after closing stdout
            watchdog.stop()
            reaped = _reap(proc, handle)
        finally:
            watchdog.stop()
            _close_stdin(proc)
            if proc.returncode is None:  # interrupted (Ctrl-C) while the child runs or is parked
                proc.kill()
                proc.wait()
            _close_process_handle(handle)
        errfile.seek(0)
        stderr = _decode(errfile.read())
    return ChildResult(proc.returncode, stderr, live, reaped, output, error, bytes(consumed), trailing,
                       watchdog.fired)


def _failure(what: str, result: subprocess.CompletedProcess[str] | ChildResult) -> BenchError:
    detail = result.stderr.strip()
    message = f"{what} failed with exit status {result.returncode}"
    return BenchError(EXIT_SOLUTION, f"{message}:\n{detail}" if detail else message)


def run_mode_answer(cmd: list[str], part: int) -> str:
    result = _run([*cmd, str(part)])
    if result.returncode != 0:
        raise _failure(f"run mode `{shlex.join([*cmd, str(part)])}`", result)
    return _strip_answer(result.stdout)


class BenchRun(NamedTuple):
    """The measured run's samples and answer, and the peak RSS of the baseline and measured runs."""

    ns: list[int]
    answer: str
    rss_baseline: int | None
    rss_peak: int | None


def _bench_child(part: int, argv: list[str], timeout: float | None, timeout_help: str) -> ChildResult:
    """Run one bench-mode invocation: a watchdog kill is a protocol error, then a non-zero exit is a
    solution failure (both before anything the output says)."""
    result = _run_with_peak(argv, timeout)
    if result.timed_out:
        raise BenchError(EXIT_PROTOCOL, f"part {part}: bench mode timed out after {timeout:g} s waiting for `done` "
                                        f"({timeout_help}): `{shlex.join(argv)}`")
    if result.returncode != 0:
        error = _failure(f"bench mode `{shlex.join(argv)}`", result)
        if "usage:" in result.stderr:
            error = BenchError(error.code, f"{error}\n{OLD_HARNESS_HINT}")
        raise error
    return result


def _first_output_line(raw: bytes) -> bytes:
    """The first non-empty line other than `done` (what a zero-iteration run should not have printed);
    b"" if there is none (it printed only empty lines)."""
    for line in raw.split(b"\n"):
        line = line.removesuffix(b"\r")
        if line not in (b"done", b""):
            return line
    return b""


def bench_mode(cmd: list[str], part: int, input_path: Path, budget_ns: int, lo: int, hi: int,
               timeout: float | None = None) -> BenchRun:
    """The zero-iteration baseline run (first: cheap, and fails fast on an old harness), then the measured run."""
    prefix = [*cmd, "bench", str(part), str(input_path)]
    baseline_argv = [*prefix, "0", "0", str(hi)]
    baseline = _bench_child(part, baseline_argv, BASELINE_TIMEOUT_S, "the zero-iteration baseline run")
    if baseline.error is not None and not baseline.consumed:  # silent exit 0: no `done`
        raise BenchError(EXIT_PROTOCOL, f"part {part}: {baseline.error} (`{shlex.join(baseline_argv)}`)")
    if baseline.error is not None or baseline.output != BenchOutput([], None) or baseline.trailing:
        line = _first_output_line(baseline.consumed + baseline.trailing)
        what = _line_excerpt(line) if line else "an empty line"
        hint = f" {OLD_HARNESS_HINT}" if line.lstrip().startswith(b"{") else ""
        raise BenchError(EXIT_PROTOCOL, f"part {part}: bench mode printed output with zero iterations: "
                                        f"{what} (`{shlex.join(baseline_argv)}`){hint}")
    measured = _bench_child(part, [*prefix, str(budget_ns), str(lo), str(hi)], timeout, "see --timeout")
    try:
        if measured.error is not None:
            raise measured.error
        assert measured.output is not None
        ns, answer = check_bench_output(measured.output, measured.trailing, lo, hi)
    except ProtocolError as e:
        raise BenchError(EXIT_PROTOCOL, f"part {part}: {e}") from None
    rss_baseline, rss_peak = _platform_peak(baseline), _platform_peak(measured)
    if sys.platform.startswith("linux") and (rss_baseline is None or rss_peak is None):
        # Never mix a live peak with an after-exit one: the latter includes the driver's own RSS.
        print(f"warning: part {part}: harness did not park, Peak RSS Δ will be n/a", file=sys.stderr)
        rss_baseline = rss_peak = None
    return BenchRun(ns, answer, rss_baseline, rss_peak)


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


# Files `aoc-toolkit collect` regenerates at the repo root: modifying them is not "dirty".
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


def bench_part(args: argparse.Namespace, cmd: list[str], part: int, input_path: Path,
               check_run: bool, use_hyperfine: bool) -> dict[str, Any]:
    run_answer = run_mode_answer(cmd, part) if check_run else None

    fixed_warmup: int | None = None
    if args.iterations is not None:
        fixed_warmup = warmup_for(args.iterations) if args.warmup is None else args.warmup
        budget_ns, lo = 0, args.iterations + fixed_warmup
        hi = lo
        print(f"part {part}: running {args.iterations} (+{fixed_warmup} warmup) iterations", file=sys.stderr)
    else:
        budget_ns, lo, hi = round(args.time * 1e9), args.min_iterations, args.max_iterations
        print(f"part {part}: benchmarking ~{_fmt_seconds(args.time)} ({lo}–{hi} iterations)", file=sys.stderr)

    measured = bench_mode(cmd, part, input_path, budget_ns, lo, hi, args.timeout)
    ns, answer = measured.ns, measured.answer
    if fixed_warmup is not None:
        k = fixed_warmup
    else:
        k = warmup_for(len(ns)) if args.warmup is None else args.warmup
    samples = trim_warmup(ns, k)
    print(f"part {part}: {len(ns)} iterations in {_fmt_seconds(sum(ns) / 1e9)} ({k} warmup trimmed)",
          file=sys.stderr)
    if run_answer is not None and run_answer != answer:
        raise BenchError(EXIT_MISMATCH, f"answer mismatch: run mode printed {run_answer!r}, "
                                        f"bench mode returned {answer!r}")

    baseline, peak = measured.rss_baseline, measured.rss_peak
    wall = None
    if use_hyperfine:
        print(f"part {part}: hyperfine (whole process)", file=sys.stderr)
        wall = hyperfine_wall(cmd, part)
    return {
        "part": part,
        "answer": answer,
        "iterations": len(samples),
        "warmup": k,
        "ns": summarize(samples),
        "rss_baseline_bytes": baseline,
        "rss_peak_bytes": peak,
        "rss_delta_bytes": None if baseline is None or peak is None else peak - baseline,
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
        prog="aoc-toolkit bench",
        usage="%(prog)s [options] -- <cmd> [args...]",
        description="Time a solution's process() in-process, per part, and write bench.json.",
    )
    parser.add_argument("--name", help="solution name (default: current directory name)")
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help="puzzle input; a non-default input skips the run-mode check and hyperfine")
    parser.add_argument("--time", type=_positive_float, default=1.0, help="target seconds per part (default 1.0)")
    parser.add_argument("--min-iterations", type=_positive_int, default=10)
    parser.add_argument("--max-iterations", type=_positive_int, default=100_000)
    parser.add_argument("--iterations", type=_positive_int,
                        help="fixed number of measured iterations (ignores --time and the iteration bounds)")
    parser.add_argument("--warmup", type=_non_negative_int,
                        help="warmup iterations to drop (default n//10, 1-100)")
    parser.add_argument("--timeout", type=_positive_float,
                        help="kill the measured bench run after this many seconds without `done` (default: no "
                             f"limit; the zero-iteration baseline run always gets {BASELINE_TIMEOUT_S:g} s)")
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
    if args.iterations is None and args.warmup is not None and args.warmup >= args.min_iterations:
        parser.error("--warmup must be smaller than --min-iterations (or use --iterations)")
    if round(args.time * 1e9) > MAX_BUDGET_NS:
        parser.error(f"--time must be at most {MAX_BUDGET_NS // 10**9} s")
    for option in ("min_iterations", "max_iterations", "iterations", "warmup"):
        value = getattr(args, option)
        if value is not None and value > MAX_ITERATIONS:
            parser.error(f"--{option.replace('_', '-')} must be at most {MAX_ITERATIONS}")
    if args.iterations is not None:
        warmup = warmup_for(args.iterations) if args.warmup is None else args.warmup
        if args.iterations + warmup > MAX_ITERATIONS:
            parser.error(f"--iterations plus warmup must be at most {MAX_ITERATIONS}")
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
        print(f"note: --input {args.input}: the result goes to {args.json_out}, but `aoc-toolkit collect` "
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
