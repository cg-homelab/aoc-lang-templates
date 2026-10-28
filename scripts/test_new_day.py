"""Tests for new_day.py.

Each test builds a throwaway year repo in a temp dir with a copy of this repo
nested at lang-templates/ (its own git repo, like a submodule checkout), so
the tests exercise the git-tracked copy path without depending on what is
committed or built in the working tree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from new_day import FALLBACK_IGNORE, git_env  # noqa: E402


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        env=git_env(),
        check=True,
        capture_output=True,
    )


def copy_repo(dest: Path) -> None:
    """Copy templates/ and scripts/ without build artifacts or local inputs."""
    ignore = shutil.ignore_patterns(*FALLBACK_IGNORE)
    shutil.copytree(REPO / "templates", dest / "templates", ignore=ignore)
    shutil.copytree(REPO / "scripts", dest / "scripts", ignore=ignore)


class NewDayTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.year = self.tmp / "year"
        self.sub = self.year / "lang-templates"
        copy_repo(self.sub)

        # Synthetic template covering nested renames, binary and CRLF files.
        x = self.sub / "templates" / "x"
        nested = x / "a__DAY__" / "b__DAY__"
        nested.mkdir(parents=True)
        (nested / "f__DAY__.txt").write_bytes(b"__DAY__")
        (x / "blob.dat").write_bytes(b"\xff__DAY__")
        (x / "crlf.txt").write_bytes(b"a\r\n__DAY__\r\n")

        git(self.year, "init", "-q")
        git(self.sub, "init", "-q")
        git(self.sub, "add", "-A")

    def scaffold(
        self,
        *args: str,
        cwd: Path | None = None,
        script: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script or self.sub / "scripts" / "new_day.py"), *args],
            cwd=cwd or self.year,
            env=env or git_env(),
            capture_output=True,
            text=True,
        )

    def test_replaces_token_in_contents_and_names(self) -> None:
        result = self.scaffold("3", "csharp")
        self.assertEqual(result.returncode, 0, result.stderr)
        day = self.year / "days" / "day-03"
        self.assertTrue((day / "Day03.slnx").is_file())
        self.assertTrue((day / "src" / "Day03.csproj").is_file())
        self.assertTrue((day / "tests" / "Day03.Tests.csproj").is_file())
        for path in day.rglob("*"):
            self.assertNotIn("__DAY__", path.name, path)
            if path.is_file():
                self.assertNotIn("__DAY__", path.read_text(), path)
        self.assertIn("day-03", (day / "justfile").read_text())
        self.assertIn(f"created {os.path.join('days', 'day-03')}/ from templates/csharp", result.stdout)

    def test_nested_binary_and_crlf(self) -> None:
        result = self.scaffold("5", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        day = self.year / "days" / "day-05"
        self.assertEqual((day / "a05" / "b05" / "f05.txt").read_bytes(), b"05")
        self.assertFalse((day / "a__DAY__").exists())
        self.assertEqual((day / "blob.dat").read_bytes(), b"\xff__DAY__")
        self.assertEqual((day / "crlf.txt").read_bytes(), b"a\r\n05\r\n")

    def test_creates_empty_input(self) -> None:
        result = self.scaffold("1", "python")
        self.assertEqual(result.returncode, 0, result.stderr)
        input_txt = self.year / "days" / "day-01" / "input.txt"
        self.assertTrue(input_txt.is_file())
        self.assertEqual(input_txt.read_text(), "")

    def test_root_flag(self) -> None:
        other = self.tmp / "elsewhere"
        result = self.scaffold("--root", str(other), "12", "rust", cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((other / "days" / "day-12" / "justfile").is_file())
        self.assertFalse((self.year / "days").exists())
        self.assertIn(os.path.join("elsewhere", "days", "day-12"), result.stdout)

    def test_copies_only_tracked_files(self) -> None:
        # Not an ignored name, so only the git path (not the fallback) skips it.
        (self.sub / "templates" / "rust" / "notes.txt").write_text("untracked")
        result = self.scaffold("1", "rust")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stderr)
        day = self.year / "days" / "day-01"
        self.assertTrue((day / "Cargo.toml").is_file())
        self.assertFalse((day / "notes.txt").exists())

    def test_fallback_without_git(self) -> None:
        plain = self.tmp / "plain"
        copy_repo(plain)
        rust = plain / "templates" / "rust"
        (rust / "target").mkdir()
        (rust / "target" / "junk").write_text("build artifact")
        (rust / "input.txt").write_text("someone's puzzle input")
        (rust / "bench.md").write_text("stale bench")
        env = {**git_env(), "GIT_CEILING_DIRECTORIES": str(self.tmp)}
        root = self.tmp / "fallback-year"
        result = self.scaffold(
            "--root", str(root), "1", "rust", cwd=self.tmp, script=plain / "scripts" / "new_day.py", env=env
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: git unavailable", result.stderr)
        day = root / "days" / "day-01"
        self.assertTrue((day / "Cargo.toml").is_file())
        self.assertFalse((day / "target").exists())
        self.assertFalse((day / "bench.md").exists())
        self.assertEqual((day / "input.txt").read_text(), "")

    def test_fallback_for_untracked_template(self) -> None:
        new = self.sub / "templates" / "new"
        new.mkdir()
        (new / "justfile").write_text("# day-__DAY__\n")
        result = self.scaffold("2", "new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no git-tracked files", result.stderr)
        self.assertEqual((self.year / "days" / "day-02" / "justfile").read_text(), "# day-02\n")

    def test_no_partial_day_on_failure(self) -> None:
        part2 = self.sub / "templates" / "rust" / "src" / "part2.rs"
        saved = part2.read_bytes()
        part2.unlink()  # still tracked, so the copy fails midway
        result = self.scaffold("4", "rust")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list((self.year / "days").iterdir()), [])

        part2.write_bytes(saved)
        result = self.scaffold("4", "rust")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.year / "days" / "day-04" / "src" / "part2.rs").is_file())
        self.assertEqual([p.name for p in (self.year / "days").iterdir()], ["day-04"])

    def test_day_out_of_range(self) -> None:
        for day in ("0", "26", "x"):
            with self.subTest(day=day):
                result = self.scaffold(day, "rust")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)
        self.assertFalse((self.year / "days").exists())

    def test_unknown_lang_lists_available(self) -> None:
        result = self.scaffold("1", "cobol")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown lang 'cobol'", result.stderr)
        langs = sorted(p.name for p in (self.sub / "templates").iterdir() if p.is_dir())
        self.assertIn("rust", langs)
        self.assertIn(f"available: {', '.join(langs)})", result.stderr)
        self.assertFalse((self.year / "days").exists())

    def test_refuses_existing_day(self) -> None:
        self.assertEqual(self.scaffold("5", "go").returncode, 0)
        marker = self.year / "days" / "day-05" / "main.go"
        marker.write_text("// my solution\n")
        result = self.scaffold("5", "rust")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("day-05 already exists", result.stderr)
        self.assertEqual(marker.read_text(), "// my solution\n")

    def test_missing_templates(self) -> None:
        shutil.rmtree(self.sub / "templates")
        result = self.scaffold("1", "rust")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("templates not found", result.stderr)
        self.assertIn("git submodule update --init", result.stderr)


if __name__ == "__main__":
    unittest.main()
