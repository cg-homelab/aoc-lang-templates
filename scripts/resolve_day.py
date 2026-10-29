#!/usr/bin/env python3
"""Resolve a `just run/test/bench` target to one solution directory under days/.

Usage: python3 lang-templates/scripts/resolve_day.py [--root DIR] <target>

<target> is either a day number (`3`) or a solution directory name
(`day-03-rust-v2`, optionally prefixed with `days/`). A day number with exactly
one solution resolves to it; with several, the user picks one from a numbered
list when stdin is a TTY, otherwise the script fails and lists them.

--root is the year repo root containing days/ (default: the current
directory). The chosen path (e.g. `days/day-03-rust-v2`) is printed on stdout,
relative to the root; prompts and errors go to stderr. Exit status 1 on failure.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NoReturn, TextIO

# Solution directory name: day-NN-<lang>-vN (e.g. day-03-rust-v2). The language
# may contain inner '-'/'_' (c-sharp); the trailing -vN keeps the split unambiguous.
NAME_PATTERN = r"day-([0-9]{2})-([a-z0-9]+(?:[-_][a-z0-9]+)*)-v([0-9]+)"
FIRST_DAY, LAST_DAY = 1, 25
DIR_RE = re.compile(rf"^{NAME_PATTERN}$")


class ResolveError(Exception):
    pass


def solution_key(name: str) -> tuple[int, str, int]:
    """Sort key (day, lang, version) for a solution directory name."""
    m = DIR_RE.match(name)
    if m is None:
        raise ValueError(f"not a solution directory name: {name!r}")
    return int(m.group(1)), m.group(2), int(m.group(3))


def solution_dirs(days_dir: Path) -> list[str]:
    """All solution directory names under days_dir, sorted by (day, lang, version)."""
    if not days_dir.is_dir():
        return []
    names = [p.name for p in days_dir.iterdir() if p.is_dir() and DIR_RE.match(p.name)]
    return sorted(names, key=solution_key)


def candidates(days_dir: Path, day: int) -> list[str]:
    """Solution directory names for one day, sorted by (lang, version)."""
    return [n for n in solution_dirs(days_dir) if solution_key(n)[0] == day]


def prompt_choice(names: list[str], stdin: TextIO, stderr: TextIO) -> str:
    """Show a numbered list of names on stderr and read a choice from stdin."""
    for i, name in enumerate(names, 1):
        print(f"  {i}) {name}", file=stderr)
    while True:
        stderr.write(f"solution [1-{len(names)}]: ")
        stderr.flush()
        line = stdin.readline()
        if not line:
            stderr.write("\n")
            raise ResolveError("no solution chosen")
        answer = line.strip()
        if re.fullmatch(r"[0-9]+", answer) and 1 <= int(answer) <= len(names):
            return names[int(answer) - 1]
        print(f"enter a number between 1 and {len(names)}", file=stderr)


def resolve(
    target: str,
    root: Path,
    interactive: bool = False,
    stdin: TextIO | None = None,
    stderr: TextIO | None = None,
) -> str:
    """Return the relative path (days/<name>) of the solution directory for target."""
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    days_dir = root / "days"
    target = target.strip()

    if re.fullmatch(r"[0-9]+", target):
        if not re.fullmatch(r"[0-9]{1,2}", target) or not FIRST_DAY <= int(target) <= LAST_DAY:
            raise ResolveError(f"day must be {FIRST_DAY}-{LAST_DAY}")
        day = int(target)
        names = candidates(days_dir, day)
        if not names:
            raise ResolveError(f"no solutions for day {day}, run `just new-day {day} <lang>`")
        if len(names) == 1:
            return f"days/{names[0]}"
        if not interactive:
            listing = "\n".join(f"  {n}" for n in names)
            raise ResolveError(
                f"day {day} has {len(names)} solutions:\n{listing}\n"
                f"pass the directory name instead of the day number, e.g. {names[-1]}"
            )
        print(f"day {day} has {len(names)} solutions:", file=stderr)
        chosen = prompt_choice(names, stdin, stderr)
        return f"days/{chosen}"

    name = target.rstrip("/")
    if name.startswith("days/"):
        name = name[len("days/"):]
    if DIR_RE.match(name):
        if (days_dir / name).is_dir():
            return f"days/{name}"
        raise ResolveError(f"no such solution directory: days/{name}")
    raise ResolveError(
        f"invalid target {target!r}: expected a day number (3) "
        "or a solution directory name (day-03-rust-v2)"
    )


def is_interactive() -> bool:
    """Prompt only when the user can both see the list (stderr) and answer it (stdin)."""
    try:
        return (
            sys.stdin is not None
            and sys.stdin.isatty()
            and sys.stderr is not None
            and sys.stderr.isatty()
        )
    except (AttributeError, ValueError):
        return False


class _ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1 (not argparse's 2), like every other failure."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    """CLI entry point; an explicit root overrides --root (used by tests)."""
    parser = _ArgumentParser(
        prog="resolve_day.py",
        usage="%(prog)s [--root DIR] <target>",
        description="Resolve a day number or solution directory name to days/<name>.",
    )
    parser.add_argument("target", help="day number (3) or solution directory name (day-03-rust-v2)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="year repo root containing days/ (default: current directory)",
    )
    args = parser.parse_args(argv)
    root = args.root if root is None else root
    try:
        path = resolve(args.target, root, interactive=is_interactive())
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
