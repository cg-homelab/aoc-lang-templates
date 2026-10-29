#!/usr/bin/env python3
"""A fake solution executable implementing the executable contract, for tests.

Usage: python3 fake_harness.py 1|2
       python3 fake_harness.py bench <part> <input-path> <iterations>

Run mode prints the answer; bench mode prints one schema-1 JSON line. Its
behaviour is steered by environment variables:

  FAKE_NS          nanoseconds per iteration: an int, or a comma list that is
                   cycled (default 1000)
  FAKE_ANSWER      answer (default "3" for part 1, "9" for part 2)
  FAKE_RUN_ANSWER  answer printed in run mode (default FAKE_ANSWER)
  FAKE_RSS         "before,after" in bytes, or "null" (default 1048576,1052672)
  FAKE_MODE        bench-mode behaviour: ok (default), garbage, two-lines,
                   short-ns, wrong-part, extra-key, crash, no-stdout; run-crash
                   makes run mode fail instead
  FAKE_COUNTER     file to append one line per invocation (the JSON argv)
  FAKE_ANSWER_SEQ  bench-mode answers by bench invocation number (a comma list,
                   counted from the FAKE_COUNTER file); absent or exhausted:
                   FAKE_ANSWER

Not named test_*.py, so unittest discovery does not import it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_ANSWERS = {1: "3", 2: "9"}
USAGE = "usage: fake_harness.py bench <1|2> <input-path> <iterations>"


def answer_for(part: int) -> str:
    return os.environ.get("FAKE_ANSWER", DEFAULT_ANSWERS[part])


def bench_answer_for(part: int) -> str:
    """FAKE_ANSWER_SEQ[i] for the i-th bench-mode invocation (this one included in the count)."""
    seq, counter = os.environ.get("FAKE_ANSWER_SEQ"), os.environ.get("FAKE_COUNTER")
    if seq and counter:
        with open(counter, encoding="utf-8") as f:
            index = sum(1 for line in f if json.loads(line)[:1] == ["bench"]) - 1
        answers = seq.split(",")
        if 0 <= index < len(answers):
            return answers[index]
    return answer_for(part)


def bench(args: list[str]) -> int:
    mode = os.environ.get("FAKE_MODE", "ok")
    if len(args) != 3 or args[0] not in ("1", "2") or not args[2].isdigit() or int(args[2]) < 1:
        print(USAGE, file=sys.stderr)
        return 1
    part, path, iterations = int(args[0]), args[1], int(args[2])
    try:
        Path(path).read_bytes()
    except OSError:
        print(f"bench: cannot read {path}", file=sys.stderr)
        return 1
    if mode == "crash":
        print("fake harness crashed", file=sys.stderr)
        return 101
    if mode == "no-stdout":
        return 0
    if mode == "garbage":
        print("this is not json")
        return 0

    cycle = [int(v) for v in os.environ.get("FAKE_NS", "1000").split(",")]
    ns = [cycle[i % len(cycle)] for i in range(iterations)]
    rss = os.environ.get("FAKE_RSS", "1048576,1052672")
    before, after = (None, None) if rss == "null" else (int(v) for v in rss.split(","))
    doc: dict[str, object] = {
        "schema": 1,
        "part": 3 - part if mode == "wrong-part" else part,
        "iterations": iterations,
        "ns": ns[:-1] if mode == "short-ns" else ns,
        "answer": bench_answer_for(part),
        "max_rss_before": before,
        "max_rss_after": after,
    }
    if mode == "extra-key":
        doc["extra"] = 1
    if mode == "two-lines":
        print("debug print from process()")
    print(json.dumps(doc, separators=(",", ":")))
    return 0


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
