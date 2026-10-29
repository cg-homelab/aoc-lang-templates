#!/usr/bin/env python3
"""Scaffold a new AoC day from a language template.

Usage: python3 lang-templates/scripts/new_day.py [--root <dir>] [<day 1-25>] [<lang>]

Copies the git-tracked files of templates/<lang> to
<root>/days/day-NN-<lang>-vN (root defaults to the current directory), where
vN is one past the highest existing version of that day in that language.
Replaces the __DAY__ token with the zero-padded day number and the __NAME__
token with the directory name (day-NN-<lang>-vN) in file contents and names,
and creates input.txt (gitignored): a copy of the newest non-empty input.txt
of another solution of the same day, else empty, ready for the puzzle input to
be pasted in. A missing day or lang is prompted for when stdin is a terminal;
a lone language argument (new_day.py rust) is taken as the lang.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, NoReturn, TextIO, TypeVar

T = TypeVar("T")

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
DAY_TOKEN = "__DAY__"  # zero-padded day number: 03
NAME_TOKEN = "__NAME__"  # solution directory name: day-03-rust-v1
# Template (language) directory names. The year repo's justfile resolves
# day-NN-<lang>-vN names with the same pattern, so other names are ignored.
LANG_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
NUMBER = re.compile(r"[0-9]+")
ARTIFACT_DIRS = (
    "bin",
    "obj",
    "out",
    "target",
    "node_modules",
    ".venv",
    "__pycache__",
    ".zig-cache",
    "zig-out",
    ".pytest_cache",
    "TestResults",
)
# Skipped by the fallback copy: build output plus per-day files that must
# never be carried over from a template that was run in place.
FALLBACK_IGNORE = (*ARTIFACT_DIRS, "input.txt", "bench.md", "bench.json", "solution")
# Inherited from git hooks or `git submodule foreach`; they would point
# `git ls-files` at another repository.
GIT_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")


def git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in GIT_ENV_VARS}


def tracked_files(src: Path) -> list[Path] | None:
    """Template files tracked by git, relative to src; None if git is unusable.

    Runs inside src without a pathspec so it also works when this repo is a
    submodule (git ls-files from the superproject does not list its files).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=src,
            env=git_env(),
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [Path(n) for n in result.stdout.decode().split("\0") if n]


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def display(path: Path) -> str:
    """path relative to the cwd when possible (days/day-03), else as given."""
    try:
        return os.path.relpath(path)
    except ValueError:  # different drive on Windows
        return str(path)


def available_langs() -> list[str]:
    if not TEMPLATES.is_dir():
        return []
    return sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir() and LANG_NAME.fullmatch(p.name))


def parse_day(value: str) -> int:
    # ASCII digits only: int() also takes "1_0" and non-ASCII digits.
    if not NUMBER.fullmatch(value.strip()):
        raise ValueError(f"day must be a number, got {value!r}")
    day = int(value)
    if not 1 <= day <= 25:
        raise ValueError(f"day must be 1-25, got {day}")
    return day


def parse_lang(value: str, langs: list[str]) -> str:
    """A template name, or its 1-based number in langs."""
    if NUMBER.fullmatch(value) and 1 <= int(value) <= len(langs):
        return langs[int(value) - 1]
    if value in langs:
        return value
    raise ValueError(f"unknown lang {value!r} (available: {', '.join(langs)})")


def solution_name(padded: str, lang: str, version: int) -> str:
    return f"day-{padded}-{lang}-v{version}"


class Solution(NamedTuple):
    lang: str
    version: int
    path: Path


def solution_entries(days: Path, padded: str) -> list[Solution]:
    """Every entry under days/ named day-NN-<lang>-vN for this day, files included."""
    pattern = re.compile(rf"day-{padded}-(.+)-v([0-9]+)")
    found = []
    if days.is_dir():
        for path in days.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                found.append(Solution(match.group(1), int(match.group(2)), path))
    return sorted(found)


def existing_solutions(days: Path, padded: str) -> list[Solution]:
    """This day's solution directories, sorted by lang and version."""
    return [s for s in solution_entries(days, padded) if s.path.is_dir()]


def next_version(days: Path, padded: str, lang: str) -> int:
    """One past the highest version in use (by a directory or anything else
    with a matching name), so gaps are never refilled and a stray file cannot
    block scaffolding."""
    return 1 + max((s.version for s in solution_entries(days, padded) if s.lang == lang), default=0)


def shared_input(days: Path, padded: str) -> Path | None:
    """The most recently modified non-empty input.txt among this day's solutions."""
    inputs = []
    for solution in existing_solutions(days, padded):
        path = solution.path / "input.txt"
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file() and stat.st_size > 0:
            inputs.append((stat.st_mtime, path))
    return max(inputs)[1] if inputs else None


def stdin_is_tty() -> bool:
    # sys.stdin is None when fd 0 is closed (`new_day.py 1 rust <&-`).
    return sys.stdin is not None and sys.stdin.isatty()


def ask(prompt: str, parse: Callable[[str], T], stdin: TextIO, out: TextIO) -> T:
    """Prompt on out until parse accepts a line from stdin."""
    while True:
        print(prompt, end="", file=out, flush=True)
        line = stdin.readline()
        if not line:  # EOF (Ctrl-D)
            print(file=out)
            fail("no input, aborting")
        try:
            return parse(line.strip())
        except ValueError as exc:
            print(f"  {exc}", file=out)


def prompt_day(stdin: TextIO, out: TextIO) -> int:
    return ask("day (1-25): ", parse_day, stdin, out)


def prompt_lang(day: int, langs: list[str], existing: list[str], stdin: TextIO, out: TextIO) -> str:
    print("languages:", file=out)
    for i, name in enumerate(langs, 1):
        print(f"  {i:>2}) {name}", file=out)
    print(f"existing solutions for day {day}: {', '.join(existing) if existing else 'none'}", file=out)
    return ask("language: ", lambda value: parse_lang(value, langs), stdin, out)


def copy_template(src: Path, dest: Path, lang: str) -> None:
    files = tracked_files(src)
    if not files:
        reason = "git unavailable" if files is None else f"templates/{lang} has no git-tracked files"
        print(
            f"warning: {reason}, copying whole template dir (skipping known artifact dirs)",
            file=sys.stderr,
        )
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*FALLBACK_IGNORE))
        return
    dest.mkdir()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)


def replace_tokens(dest: Path, tokens: dict[str, str]) -> None:
    def replace(text: str) -> str:
        for token, value in tokens.items():
            text = text.replace(token, value)
        return text

    # Depth-first so renames don't invalidate child paths.
    for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file():
            # Bytes in, bytes out: no newline translation (CRLF survives) and no
            # locale encoding. Path.read_text(newline=) needs Python 3.13.
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = replace(text)
            if new != text:
                path.write_bytes(new.encode("utf-8"))
        name = replace(path.name)
        if name != path.name:
            path.rename(path.with_name(name))


def main(argv: list[str] | None = None) -> None:
    try:
        scaffold(argv)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        sys.exit(130)


def scaffold(argv: list[str] | None) -> None:
    parser = argparse.ArgumentParser(description="Scaffold a new AoC day from a language template.")
    parser.add_argument("day", nargs="?", help="day number, 1-25 (prompted for if omitted)")
    parser.add_argument("lang", nargs="?", help="template name under templates/ (prompted for if omitted)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="year repo root; days/ is created here (default: current directory)",
    )
    args = parser.parse_args(argv)
    day_arg, lang, root = args.day, args.lang, args.root

    langs = available_langs()
    if not langs:
        fail(
            f"templates not found at {TEMPLATES}. "
            "If this is a git submodule, run: git submodule update --init"
        )

    # `new_day.py rust` (e.g. from a justfile that drops empty arguments):
    # a lone language name is the lang, not the day; a number stays the day.
    if lang is None and day_arg in langs and not NUMBER.fullmatch(day_arg):
        day_arg, lang = None, day_arg

    # Only consult stdin when something is missing: it may be closed.
    if (day_arg is None or lang is None) and not stdin_is_tty():
        print(parser.format_usage(), end="", file=sys.stderr)
        fail("day and lang are required when stdin is not a terminal")

    if day_arg is None:
        day = prompt_day(sys.stdin, sys.stderr)
    else:
        try:
            day = parse_day(day_arg)
        except ValueError as exc:
            fail(str(exc))
    padded = f"{day:02d}"
    days = root / "days"

    if lang is None:
        existing = [solution.path.name for solution in existing_solutions(days, padded)]
        lang = prompt_lang(day, langs, existing, sys.stdin, sys.stderr)
    elif lang not in langs:
        fail(f"unknown lang {lang!r} (available: {', '.join(langs)})")
    src = TEMPLATES / lang

    name = solution_name(padded, lang, next_version(days, padded, lang))
    dest = days / name
    if dest.exists():  # only if something else created it meanwhile
        fail(f"{display(dest)} already exists")

    # Every solution of a day solves the same puzzle input.
    source_input = shared_input(days, padded)

    # Build in a sibling temp dir and rename at the end, so a failure never
    # leaves a partial day that the next run would refuse to overwrite.
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        copy_template(src, tmp, lang)
        replace_tokens(tmp, {DAY_TOKEN: padded, NAME_TOKEN: name})
        if source_input is None:
            (tmp / "input.txt").write_text("")
        else:
            shutil.copyfile(source_input, tmp / "input.txt")
        tmp.rename(dest)
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        fail(f"scaffolding {display(dest)} from templates/{lang} failed: {exc}")
    except BaseException:  # Ctrl-C: still no partial day left behind
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    print(f"created {display(dest)}/ from templates/{lang}")
    if source_input is None:
        print(f"paste your puzzle input into {display(dest / 'input.txt')}, then:")
    else:
        print(f"copied the puzzle input from {display(source_input)}, then:")
    print(f"  just run {name} 1   just test {name}   just bench {name}")


if __name__ == "__main__":
    main()
