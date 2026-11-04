"""aoc-toolkit: one command line for the year repo's day tooling.

Usage: aoc-toolkit <command> [args...]

Dispatches by hand rather than with argparse subparsers: argparse.REMAINDER
keeps a literal `--`, and `aoc-toolkit bench [options] -- <cmd>` relies on
bench_day.split_argv seeing it. Every argument after the command name goes to
that command unchanged, so flags and exit codes are the command's own.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from aoc_toolkit import bench_day, collect_benches, new_day, resolve_day

PROG = "aoc-toolkit"

COMMANDS: dict[str, tuple[Callable[[list[str]], int | None], str]] = {
    "new": (new_day.main, "scaffold a new day from a language template"),
    "resolve": (resolve_day.main, "resolve a day number or solution name to days/<name>"),
    "bench": (bench_day.main, "benchmark one solution in-process and write bench.json"),
    "collect": (collect_benches.main, "merge days/*/bench.json into benchmarks.json and benchmarks.md"),
}


def print_usage(stream: TextIO) -> None:
    width = max(map(len, COMMANDS))
    print(f"usage: {PROG} <command> [args...]", file=stream)
    print(file=stream)
    print("commands:", file=stream)
    for name, (_, description) in COMMANDS.items():
        print(f"  {name:<{width}}  {description}", file=stream)
    print(file=stream)
    print(f"Run `{PROG} <command> --help` for a command's options.", file=stream)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        print_usage(sys.stderr)
        return 1
    name, rest = args[0], args[1:]
    if name in ("-h", "--help"):
        print_usage(sys.stdout)
        return 0
    if name not in COMMANDS:
        print_usage(sys.stderr)
        print(f"error: unknown command {name!r}", file=sys.stderr)
        return 1
    command, _ = COMMANDS[name]
    code = command(rest)
    return 0 if code is None else code
