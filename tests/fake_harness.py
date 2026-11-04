#!/usr/bin/env python3
"""A fake solution executable implementing the executable contract, for tests.

Usage: python3 fake_harness.py 1|2
       python3 fake_harness.py bench <part> <input-path> <budget-ns> <min-iter> <max-iter>

Run mode prints the answer. Bench mode "runs" the loop of harness protocol 2
without timing anything: iteration i takes FAKE_NS[i] nanoseconds, and it keeps
going while i < min-iter or (i < max-iter and the summed nanoseconds are below
budget-ns), so the iteration count is deterministic. It then prints one sample
per line, `answer <N>` and the N-byte answer (only after at least one
iteration), and `done`, flushes, and parks until a line or EOF arrives on
stdin. Its behaviour is steered by environment variables:

  FAKE_NS          nanoseconds per iteration: an int, or a comma list that is
                   cycled (default 1000)
  FAKE_ANSWER      answer (default "3" for part 1, "9" for part 2)
  FAKE_RUN_ANSWER  answer printed in run mode (default FAKE_ANSWER)
  FAKE_ALLOC_MIB   MiB to allocate and touch (before `done`) when at least one
                   iteration runs (default 0), so the measured run peaks
                   above the baseline
  FAKE_MODE        bench-mode behaviour (with at least one iteration unless
                   noted): ok (default); crash (exit 101 before any output);
                   no-stdout (exit 0, print nothing); garbage (a non-integer
                   sample line); debug-line (a stray line before the
                   samples); no-answer (samples and `done`, no answer
                   block); too-few / too-many (one sample fewer than
                   min-iter / more than max-iter); no-done (exit 0 after the
                   answer block); short-answer (an `answer <N>` header with
                   N 5 bytes too large, then `done`, and park: a reader
                   waits for bytes that never come); park-before-done (park
                   after the answer block without printing `done`);
                   garbage-crash (a non-integer sample line, then exit 3);
                   baseline-park (park without `done` in a zero-iteration
                   run);
                   after-done (a line after
                   `done` in the measured run); no-park (exit right after
                   `done`, also with zero iterations); baseline-noise (a line before `done` in a
                   zero-iteration run); old-harness (exit 1 with the
                   protocol-1 usage line, whatever the arguments); run-crash
                   makes run mode fail instead
  FAKE_COUNTER     file to append one line per invocation (the JSON argv)

Not named test_*.py, so unittest discovery does not import it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import BinaryIO

DEFAULT_ANSWERS = {1: "3", 2: "9"}
USAGE = "usage: fake_harness.py bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>"
OLD_USAGE = "usage: fake_harness.py bench <1|2> <input-path> <iterations>"
PAGE = 4096


def answer_for(part: int) -> str:
    return os.environ.get("FAKE_ANSWER", DEFAULT_ANSWERS[part])


def fake_samples(budget_ns: int, lo: int, hi: int) -> list[int]:
    """The samples the protocol-2 loop would record if each call took the next FAKE_NS value."""
    cycle = [int(v) for v in os.environ.get("FAKE_NS", "1000").split(",")]
    ns: list[int] = []
    total = 0
    while len(ns) < lo or (len(ns) < hi and total < budget_ns):
        ns.append(cycle[len(ns) % len(cycle)])
        total += ns[-1]
    return ns


def allocate(mib: int) -> bytearray:
    """mib MiB, every page written so it counts towards the peak RSS."""
    buf = bytearray(mib * 1024 * 1024)
    buf[::PAGE] = b"\x01" * len(range(0, len(buf), PAGE))
    return buf


def bench(args: list[str]) -> int:
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "old-harness":
        print(OLD_USAGE, file=sys.stderr)
        return 1
    if len(args) != 5 or args[0] not in ("1", "2") or not all(a.isascii() and a.isdigit() for a in args[2:]):
        print(USAGE, file=sys.stderr)
        return 1
    part, path = int(args[0]), args[1]
    budget_ns, lo, hi = (int(a) for a in args[2:])
    if hi < lo:
        print(USAGE, file=sys.stderr)
        return 1
    try:
        Path(path).read_bytes()
    except OSError:
        print(f"bench: cannot read {path}", file=sys.stderr)
        return 1

    ns = fake_samples(budget_ns, lo, hi)
    out = sys.stdout.buffer
    if not ns:
        if mode == "baseline-noise":
            out.write(b"debug print from main()\n")
        if mode == "baseline-park":
            park()
            return 0
        return finish(out, "ok" if mode == "after-done" else mode)
    if mode == "crash":
        print("fake harness crashed", file=sys.stderr)
        return 101
    keep = allocate(int(os.environ.get("FAKE_ALLOC_MIB", "0")))  # noqa: F841 (alive until exit)
    if mode == "no-stdout":
        return 0
    if mode == "too-few":
        ns = ns[:max(lo - 1, 0)]
    elif mode == "too-many":
        ns = fake_samples(0, hi + 1, hi + 1)

    lines = [str(v) for v in ns]
    if mode in ("garbage", "garbage-crash"):
        lines[0] = "fast"
    if mode == "debug-line":
        lines.insert(0, "debug print from process()")
    out.write("".join(f"{line}\n" for line in lines).encode())
    if mode == "garbage-crash":
        out.flush()
        return 3
    answer = answer_for(part).encode()
    if mode != "no-answer":
        size = len(answer) + 5 if mode == "short-answer" else len(answer)
        out.write(f"answer {size}\n".encode() + answer + b"\n")
    if mode == "no-done":
        return 0
    if mode == "park-before-done":
        out.flush()
        park()
        return 0
    return finish(out, mode)


def finish(out: BinaryIO, mode: str) -> int:
    """Print `done`, flush and park until a line (or EOF) arrives on stdin."""
    out.write(b"done\n")
    if mode == "after-done":
        out.write(b"stray line after done\n")
    out.flush()
    if mode != "no-park":
        park()
    return 0


def park() -> None:
    """Block until a line (or EOF) arrives on stdin."""
    if sys.stdin is not None:
        sys.stdin.readline()


def main(argv: list[str]) -> int:
    counter = os.environ.get("FAKE_COUNTER")
    if counter:
        with open(counter, "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\n")
    if argv[:1] == ["bench"]:
        return bench(argv[1:])
    if len(argv) != 1 or argv[0] not in ("1", "2"):
        print("usage: fake_harness.py 1|2", file=sys.stderr)
        return 1
    if os.environ.get("FAKE_MODE") == "run-crash":
        print("fake run mode crashed", file=sys.stderr)
        return 1
    part = int(argv[0])
    print(os.environ.get("FAKE_RUN_ANSWER", answer_for(part)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
