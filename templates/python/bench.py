"""In-process timing harness.

Protocol: `python main.py bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>`

Runs process(input) at least min-iter times, then keeps going while fewer than
max-iter runs are done and the loop has used less than budget-ns. stdout gets one
decimal nanosecond sample per line, then (only if at least one iteration ran)
`answer <N>\n`, exactly N bytes of the UTF-8 answer and `\n`, then always a line
`done`. After flushing, the harness blocks on one line of stdin (newline or EOF)
so the runner can read its peak RSS while it is alive, then exits 0. Errors go to
stderr with exit code 1, nothing on stdout and no waiting.
"""

import sys
import time
from array import array
from pathlib import Path

from part1 import process as part1
from part2 import process as part2

USAGE = "usage: python main.py bench <1|2> <input-path> <budget-ns> <min-iter> <max-iter>"

CHUNK = 1000


def is_uint(s: str) -> bool:
    return s.isascii() and s.isdigit()


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[0] not in ("1", "2") or not all(is_uint(a) for a in argv[2:]):
        print(USAGE, file=sys.stderr)
        return 1
    part, path = argv[0], argv[1]
    budget_ns, min_iter, max_iter = int(argv[2]), int(argv[3]), int(argv[4])
    if max_iter < min_iter:
        print(USAGE, file=sys.stderr)
        return 1

    try:
        text = Path(path).read_text()
    except (OSError, UnicodeDecodeError):
        print(f"bench: cannot read {path}", file=sys.stderr)
        return 1

    process = part1 if part == "1" else part2
    # Allocated and touched up front, identically for any iteration count.
    ns = array("q", bytes(8 * max_iter))

    first = ""
    i = 0
    clock = time.perf_counter_ns
    t_loop = clock()
    while i < min_iter or (i < max_iter and clock() - t_loop < budget_ns):
        t0 = clock()
        ans = process(text)
        t1 = clock()
        ns[i] = t1 - t0
        ans = ans if isinstance(ans, str) else str(ans)

        if i == 0:
            first = ans
        elif ans != first:
            print(
                f'bench: answer mismatch at iteration {i}: got "{ans}", want "{first}"',
                file=sys.stderr,
            )
            return 1
        i += 1

    out = sys.stdout.buffer
    for start in range(0, i, CHUNK):
        out.write("".join(f"{v}\n" for v in ns[start : min(start + CHUNK, i)]).encode("ascii"))
    if i > 0:
        ans_bytes = first.encode("utf-8")
        out.write(b"answer %d\n" % len(ans_bytes))
        out.write(ans_bytes)
        out.write(b"\n")
    out.write(b"done\n")
    out.flush()
    if sys.stdin is not None:
        sys.stdin.readline()
    return 0
