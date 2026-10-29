"""In-process timing harness: `python main.py bench <1|2> <input-path> <iterations>`."""

import sys
import time
from array import array
from pathlib import Path

from part1 import process as part1
from part2 import process as part2

try:
    import resource
except ImportError:  # Windows
    resource = None

USAGE = "usage: python main.py bench <1|2> <input-path> <iterations>"


def max_rss_bytes() -> int | None:
    if resource is None:
        return None
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024


def json_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def json_int_or_null(v: int | None) -> str:
    return "null" if v is None else str(v)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("1", "2") or not (argv[2].isascii() and argv[2].isdigit()) or int(argv[2]) < 1:
        print(USAGE, file=sys.stderr)
        return 1
    part, path = argv[0], argv[1]
    iterations = int(argv[2])

    try:
        text = Path(path).read_text()
    except OSError:
        print(f"bench: cannot read {path}", file=sys.stderr)
        return 1

    process = part1 if part == "1" else part2
    # Allocated and touched up front so the buffer is not counted in the RSS delta.
    ns = array("q", bytes(8 * iterations))
    rss_before = max_rss_bytes()

    first = ""
    checksum = 0
    for i in range(iterations):
        t0 = time.perf_counter_ns()
        ans = process(text)
        t1 = time.perf_counter_ns()
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
        checksum += len(ans)

    rss_after = max_rss_bytes()

    line = (
        f'{{"schema":1,"part":{part},"iterations":{iterations},'
        f'"ns":[{",".join(map(str, ns))}],"answer":{json_string(first)},'
        f'"max_rss_before":{json_int_or_null(rss_before)},'
        f'"max_rss_after":{json_int_or_null(rss_after)}}}'
    )
    sys.stdout.write(line + "\n")
    return 0 if checksum >= 0 else 1
