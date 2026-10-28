#!/usr/bin/env python3
"""Scaffold a new AoC day from a language template.

Usage: python3 lang-templates/scripts/new_day.py [--root <dir>] <day 1-25> <lang>

Copies the git-tracked files of templates/<lang> to <root>/days/day-NN (root
defaults to the current directory), replaces the __DAY__ token with the
zero-padded day number in file contents and names, and creates an empty
input.txt (gitignored) ready for the puzzle input to be pasted in.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
TOKEN = "__DAY__"
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
FALLBACK_IGNORE = (*ARTIFACT_DIRS, "input.txt", "bench.md", "solution")
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


def fail(message: str) -> None:
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
    return sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir())


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


def replace_token(dest: Path, padded: str) -> None:
    # Depth-first so renames don't invalidate child paths.
    for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file():
            # Bytes in, bytes out: no newline translation (CRLF survives) and no
            # locale encoding. Path.read_text(newline=) needs Python 3.13.
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            if TOKEN in text:
                path.write_bytes(text.replace(TOKEN, padded).encode("utf-8"))
        if TOKEN in path.name:
            path.rename(path.with_name(path.name.replace(TOKEN, padded)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a new AoC day from a language template.")
    parser.add_argument("day", help="day number, 1-25")
    parser.add_argument("lang", help="template name under templates/")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="year repo root; days/ is created here (default: current directory)",
    )
    args = parser.parse_args()
    day_arg, lang, root = args.day, args.lang, args.root

    try:
        day = int(day_arg)
    except ValueError:
        fail(f"day must be a number, got {day_arg!r}")
    if not 1 <= day <= 25:
        fail(f"day must be 1-25, got {day}")
    padded = f"{day:02d}"

    langs = available_langs()
    if not langs:
        fail(
            f"templates not found at {TEMPLATES}. "
            "If this is a git submodule, run: git submodule update --init"
        )

    if lang not in langs:
        fail(f"unknown lang {lang!r} (available: {', '.join(langs)})")
    src = TEMPLATES / lang

    dest = root / "days" / f"day-{padded}"
    if dest.exists():
        fail(f"{display(dest)} already exists")

    # Build in a sibling temp dir and rename at the end, so a failure never
    # leaves a partial day that the next run would refuse to overwrite.
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        copy_template(src, tmp, lang)
        replace_token(tmp, padded)
        (tmp / "input.txt").write_text("")
        tmp.rename(dest)
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        fail(f"scaffolding {display(dest)} from templates/{lang} failed: {exc}")

    print(f"created {display(dest)}/ from templates/{lang}")
    print(f"paste your puzzle input into {display(dest / 'input.txt')}, then:")
    print(f"  just run {day} 1   just test {day}   just bench {day}")


if __name__ == "__main__":
    main()
