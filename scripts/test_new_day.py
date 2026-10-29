"""Tests for new_day.py.

Each test builds a throwaway year repo in a temp dir with a copy of this repo
nested at lang-templates/ (its own git repo, like a submodule checkout), so
the tests exercise the git-tracked copy path without depending on what is
committed or built in the working tree.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import new_day  # noqa: E402
from new_day import FALLBACK_IGNORE, git_env  # noqa: E402

LANGS = sorted(p.name for p in (REPO / "templates").iterdir() if p.is_dir())


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
        (nested / "__NAME__.txt").write_bytes(b"__NAME__ day __DAY__")
        (x / "blob.dat").write_bytes(b"\xff__DAY__ __NAME__")
        (x / "crlf.txt").write_bytes(b"a\r\n__DAY__\r\n__NAME__\r\n")

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
        # stdin is never a terminal here, even when the suite runs in one.
        return subprocess.run(
            [sys.executable, str(script or self.sub / "scripts" / "new_day.py"), *args],
            cwd=cwd or self.year,
            env=env or git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def days(self) -> list[str]:
        return sorted(p.name for p in (self.year / "days").iterdir())

    def test_replaces_token_in_contents_and_names(self) -> None:
        result = self.scaffold("3", "csharp")
        self.assertEqual(result.returncode, 0, result.stderr)
        day = self.year / "days" / "day-03-csharp-v1"
        self.assertTrue((day / "Day03.slnx").is_file())
        self.assertTrue((day / "src" / "Day03.csproj").is_file())
        self.assertTrue((day / "tests" / "Day03.Tests.csproj").is_file())
        for path in day.rglob("*"):
            self.assertNotIn("__DAY__", path.name, path)
            if path.is_file():
                self.assertNotIn("__DAY__", path.read_text(), path)
        self.assertIn("<AssemblyName>day-03-csharp-v1</AssemblyName>", (day / "src" / "Day03.csproj").read_text())
        self.assertIn("<RootNamespace>Day03</RootNamespace>", (day / "src" / "Day03.csproj").read_text())
        self.assertIn(f"created {os.path.join('days', 'day-03-csharp-v1')}/ from templates/csharp", result.stdout)
        self.assertIn(
            "just run day-03-csharp-v1 1   just test day-03-csharp-v1   just bench day-03-csharp-v1", result.stdout
        )

    def test_nested_binary_and_crlf(self) -> None:
        result = self.scaffold("5", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        day = self.year / "days" / "day-05-x-v1"
        self.assertEqual((day / "a05" / "b05" / "f05.txt").read_bytes(), b"05")
        self.assertEqual((day / "a05" / "b05" / "day-05-x-v1.txt").read_bytes(), b"day-05-x-v1 day 05")
        self.assertFalse((day / "a__DAY__").exists())
        self.assertEqual((day / "blob.dat").read_bytes(), b"\xff__DAY__ __NAME__")
        self.assertEqual((day / "crlf.txt").read_bytes(), b"a\r\n05\r\nday-05-x-v1\r\n")

    def test_creates_empty_input(self) -> None:
        result = self.scaffold("1", "python")
        self.assertEqual(result.returncode, 0, result.stderr)
        input_txt = self.year / "days" / "day-01-python-v1" / "input.txt"
        self.assertTrue(input_txt.is_file())
        self.assertEqual(input_txt.read_text(), "")

    def test_root_flag(self) -> None:
        other = self.tmp / "elsewhere"
        result = self.scaffold("--root", str(other), "12", "rust", cwd=self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((other / "days" / "day-12-rust-v1" / "justfile").is_file())
        self.assertFalse((self.year / "days").exists())
        self.assertIn(os.path.join("elsewhere", "days", "day-12-rust-v1"), result.stdout)

    def test_copies_only_tracked_files(self) -> None:
        # Not an ignored name, so only the git path (not the fallback) skips it.
        (self.sub / "templates" / "rust" / "notes.txt").write_text("untracked")
        result = self.scaffold("1", "rust")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stderr)
        day = self.year / "days" / "day-01-rust-v1"
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
        (rust / "bench.json").write_text('{"schema": 1}')
        env = {**git_env(), "GIT_CEILING_DIRECTORIES": str(self.tmp)}
        root = self.tmp / "fallback-year"
        result = self.scaffold(
            "--root", str(root), "1", "rust", cwd=self.tmp, script=plain / "scripts" / "new_day.py", env=env
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: git unavailable", result.stderr)
        day = root / "days" / "day-01-rust-v1"
        self.assertTrue((day / "Cargo.toml").is_file())
        self.assertFalse((day / "target").exists())
        self.assertFalse((day / "bench.md").exists())
        self.assertFalse((day / "bench.json").exists())
        self.assertEqual((day / "input.txt").read_text(), "")

    def test_fallback_for_untracked_template(self) -> None:
        new = self.sub / "templates" / "new"
        new.mkdir()
        (new / "justfile").write_text("# __NAME__ (day __DAY__)\n")
        result = self.scaffold("2", "new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no git-tracked files", result.stderr)
        self.assertEqual(
            (self.year / "days" / "day-02-new-v1" / "justfile").read_text(), "# day-02-new-v1 (day 02)\n"
        )

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
        self.assertTrue((self.year / "days" / "day-04-rust-v1" / "src" / "part2.rs").is_file())
        self.assertEqual(self.days(), ["day-04-rust-v1"])

    def test_day_out_of_range(self) -> None:
        for day in ("0", "26", "x", "1_0", "\u0665", " 5x"):
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

    def test_missing_templates(self) -> None:
        shutil.rmtree(self.sub / "templates")
        result = self.scaffold("1", "rust")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("templates not found", result.stderr)
        self.assertIn("git submodule update --init", result.stderr)

    def test_versions_increment(self) -> None:
        first = self.scaffold("1", "rust")
        self.assertEqual(first.returncode, 0, first.stderr)
        marker = self.year / "days" / "day-01-rust-v1" / "src" / "main.rs"
        marker.write_text("// my solution\n")
        second = self.scaffold("1", "rust")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(f"created {os.path.join('days', 'day-01-rust-v2')}/", second.stdout)
        self.assertEqual(self.days(), ["day-01-rust-v1", "day-01-rust-v2"])
        self.assertEqual(marker.read_text(), "// my solution\n")
        cargo = (self.year / "days" / "day-01-rust-v2" / "Cargo.toml").read_text()
        self.assertIn('name = "day-01-rust-v2"', cargo)

    def test_versions_do_not_refill_gaps(self) -> None:
        days = self.year / "days"
        for name in ("day-01-rust-v1", "day-01-rust-v3", "day-02-rust-v7", "day-01-zig-v5", "day-01"):
            (days / name).mkdir(parents=True)
        result = self.scaffold("1", "rust")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((days / "day-01-rust-v4" / "Cargo.toml").is_file())

    def test_file_with_a_solution_name_is_skipped(self) -> None:
        days = self.year / "days"
        (days / "day-07-php-v1").mkdir(parents=True)
        (days / "day-07-php-v2").write_text("a stray file, not a solution")
        for expected in ("day-07-php-v3", "day-07-php-v4"):
            result = self.scaffold("7", "php")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((days / expected).is_dir(), expected)
        self.assertEqual((days / "day-07-php-v2").read_text(), "a stray file, not a solution")
        self.assertNotIn("day-07-php-v2", [s.path.name for s in new_day.existing_solutions(days, "07")])

    def test_zero_padded_version_sibling(self) -> None:
        days = self.year / "days"
        sibling = days / "day-08-php-v01"
        sibling.mkdir(parents=True)
        (sibling / "input.txt").write_text("puzzle\n")
        _, err = self.run_main("8", stdin="php\n")
        self.assertIn("existing solutions for day 8: day-08-php-v01", err)
        self.assertEqual(self.days(), ["day-08-php-v01", "day-08-php-v2"])
        self.assertEqual((days / "day-08-php-v2" / "input.txt").read_text(), "puzzle\n")

    def test_languages_counted_separately(self) -> None:
        self.assertEqual(self.scaffold("1", "rust").returncode, 0)
        result = self.scaffold("1", "zig")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.days(), ["day-01-rust-v1", "day-01-zig-v1"])

    def test_no_tokens_left_in_any_template(self) -> None:
        for lang in LANGS:
            with self.subTest(lang=lang):
                result = self.scaffold("3", lang)
                self.assertEqual(result.returncode, 0, result.stderr)
                day = self.year / "days" / f"day-03-{lang}-v1"
                for path in day.rglob("*"):
                    for token in ("__DAY__", "__NAME__"):
                        self.assertNotIn(token, path.name, path)
                        if path.is_file():
                            self.assertNotIn(token, path.read_text(), path)
                justfile = (day / "justfile").read_text()
                self.assertIn(f"--name day-03-{lang}-v1", justfile)
                self.assertIn('scripts := env("AOC_SCRIPTS"', justfile)
                self.assertRegex(justfile, re.compile(r'^solution := "', re.M))
        cargo = (self.year / "days" / "day-03-rust-v1" / "Cargo.toml").read_text()
        self.assertIn('name = "day-03-rust-v1"', cargo)

    def test_missing_args_without_tty(self) -> None:
        for args in ((), ("4",)):
            with self.subTest(args=args):
                result = self.scaffold(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("usage:", result.stderr)
                self.assertIn("required when stdin is not a terminal", result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertFalse((self.year / "days").exists())

    def test_lang_only_argument(self) -> None:
        result = self.scaffold("rust")
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stderr)
        self.assertNotIn("day must be a number", result.stderr)
        self.assertFalse((self.year / "days").exists())

        _, err = self.run_main("rust", stdin="6\n")
        self.assertIn("day (1-25): ", err)
        self.assertNotIn("language: ", err)
        self.assertEqual(self.days(), ["day-06-rust-v1"])

    def test_unknown_word_is_not_taken_as_lang(self) -> None:
        result = self.scaffold("cobol")
        self.assertEqual(result.returncode, 1)
        self.assertIn("usage:", result.stderr)  # lang missing, no tty
        self.run_main("4", stdin="rust\n")  # day only still prompts for lang
        self.assertEqual(self.days(), ["day-04-rust-v1"])

    def test_copies_input_from_newest_sibling(self) -> None:
        days = self.year / "days"
        for args in (("2", "rust"), ("2", "python"), ("2", "go")):
            self.assertEqual(self.scaffold(*args).returncode, 0)
        (days / "day-02-rust-v1" / "input.txt").write_text("old\n")
        (days / "day-02-python-v1" / "input.txt").write_text("new\n")
        os.utime(days / "day-02-rust-v1" / "input.txt", (1_000_000, 1_000_000))
        # day-02-go-v1 keeps its empty input; another day's input is ignored.
        self.assertEqual(self.scaffold("3", "rust").returncode, 0)
        (days / "day-03-rust-v1" / "input.txt").write_text("day 3\n")

        result = self.scaffold("2", "zig")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((days / "day-02-zig-v1" / "input.txt").read_text(), "new\n")
        expected = os.path.join("days", "day-02-python-v1", "input.txt")
        self.assertIn(f"copied the puzzle input from {expected}", result.stdout)
        self.assertNotIn("paste your puzzle input", result.stdout)

    def test_empty_input_without_filled_sibling(self) -> None:
        days = self.year / "days"
        result = self.scaffold("8", "rust")
        self.assertIn("paste your puzzle input", result.stdout)
        result = self.scaffold("8", "rust")  # sibling exists, but its input is empty
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((days / "day-08-rust-v2" / "input.txt").read_text(), "")
        self.assertIn("paste your puzzle input", result.stdout)
        self.assertNotIn("copied", result.stdout)

    def run_main(self, *args: str, stdin: str) -> tuple[str, str]:
        """Run main() in-process as if stdin were a terminal fed with stdin."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(new_day, "TEMPLATES", self.sub / "templates"))
            stack.enter_context(mock.patch.object(new_day, "stdin_is_tty", return_value=True))
            stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(stdin)))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            new_day.main(["--root", str(self.year), *args])
        return out.getvalue(), err.getvalue()

    def test_prompts_for_day_and_lang(self) -> None:
        self.assertEqual(self.scaffold("3", "rust").returncode, 0)
        rust = LANGS.index("rust") + 1
        out, err = self.run_main(stdin=f"x\n26\n3\nnope\n{rust}\n")
        self.assertEqual(self.days(), ["day-03-rust-v1", "day-03-rust-v2"])
        self.assertIn("day must be a number, got 'x'", err)
        self.assertIn("day must be 1-25, got 26", err)
        self.assertIn("unknown lang 'nope'", err)
        self.assertIn(f"{rust:>4}) rust", err)
        self.assertIn("existing solutions for day 3: day-03-rust-v1", err)
        # Prompts and lists stay on stderr; stdout is only the result.
        self.assertNotIn("day (1-25)", out)
        self.assertTrue(out.startswith(f"created {new_day.display(self.year / 'days' / 'day-03-rust-v2')}/"), out)

    def test_prompts_only_for_missing_lang(self) -> None:
        self.run_main("7", stdin="zig\n")
        self.assertEqual(self.days(), ["day-07-zig-v1"])

    def test_prompt_eof_fails(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_main(stdin="")
        self.assertEqual(cm.exception.code, 1)
        self.assertFalse((self.year / "days").exists())

    def test_ctrl_c_exits_130_without_partial_day(self) -> None:
        with mock.patch.object(new_day, "copy_template", side_effect=KeyboardInterrupt):
            with self.assertRaises(SystemExit) as cm:
                self.run_main("1", "rust", stdin="")
        self.assertEqual(cm.exception.code, 130)
        self.assertEqual(self.days(), [])

    def test_ctrl_c_at_prompt_exits_130(self) -> None:
        with mock.patch.object(new_day, "prompt_day", side_effect=KeyboardInterrupt):
            with self.assertRaises(SystemExit) as cm:
                self.run_main(stdin="")
        self.assertEqual(cm.exception.code, 130)
        self.assertFalse((self.year / "days").exists())

    def test_closed_stdin(self) -> None:
        # fd 0 closed: sys.stdin is None in the child. Full arguments still work.
        script = self.sub / "scripts" / "new_day.py"
        code = "import os, subprocess, sys; os.close(0); sys.exit(subprocess.call(sys.argv[1:]))"
        for args, status in ((("1", "php"), 0), (("php",), 1)):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, "-c", code, sys.executable, str(script), *args],
                    cwd=self.year,
                    env=git_env(),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.days(), ["day-01-php-v1"])

    def test_template_names_match_lang_pattern(self) -> None:
        # The year repo's justfile resolves day-NN-<lang>-vN with this pattern.
        for path in (REPO / "templates").iterdir():
            if path.is_dir():
                self.assertRegex(path.name, r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

    def test_ignores_template_dirs_with_bad_names(self) -> None:
        for bad in ("Rust", "my lang", "a--b", "-x"):
            (self.sub / "templates" / bad).mkdir()
            (self.sub / "templates" / bad / "justfile").write_text("# __NAME__\n")
        with mock.patch.object(new_day, "TEMPLATES", self.sub / "templates"):
            langs = new_day.available_langs()
        self.assertEqual(langs, sorted(LANGS + ["x"]))
        result = self.scaffold("1", "Rust")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown lang 'Rust'", result.stderr)

    @unittest.skipUnless(hasattr(os, "openpty"), "needs a pseudo-terminal")
    def test_prompts_on_real_tty(self) -> None:
        primary, secondary = os.openpty()
        self.addCleanup(os.close, primary)
        try:
            # Canonical-mode tty: the lines are buffered until the script reads them.
            os.write(primary, b"9\npython\n")
            result = subprocess.run(
                [sys.executable, str(self.sub / "scripts" / "new_day.py")],
                cwd=self.year,
                env=git_env(),
                stdin=secondary,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            os.close(secondary)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("language: ", result.stderr)
        self.assertEqual(self.days(), ["day-09-python-v1"])


class PromptHelpersTest(unittest.TestCase):
    def test_parse_day_ascii_digits_only(self) -> None:
        self.assertEqual(new_day.parse_day(" 07 "), 7)
        for value in ("1_0", "\u0665", "+5", "-1", "5.0", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    new_day.parse_day(value)

    def test_prompt_day_retries_until_valid(self) -> None:
        out = io.StringIO()
        self.assertEqual(new_day.prompt_day(io.StringIO("\n0\n abc\n 12 \n"), out), 12)
        self.assertEqual(out.getvalue().count("day (1-25): "), 4)

    def test_prompt_lang_by_number_or_name(self) -> None:
        langs = ["go", "rust", "zig"]
        self.assertEqual(new_day.prompt_lang(1, langs, [], io.StringIO("2\n"), io.StringIO()), "rust")
        out = io.StringIO()
        self.assertEqual(new_day.prompt_lang(1, langs, ["day-01-go-v1"], io.StringIO("4\ncobol\nzig\n"), out), "zig")
        text = out.getvalue()
        self.assertIn("   3) zig", text)
        self.assertIn("existing solutions for day 1: day-01-go-v1", text)
        self.assertIn("unknown lang '4'", text)
        self.assertIn("unknown lang 'cobol'", text)
        out = io.StringIO()
        new_day.prompt_lang(5, langs, [], io.StringIO("go\n"), out)
        self.assertIn("existing solutions for day 5: none", out.getvalue())

    def test_existing_solutions_and_next_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            days = Path(tmp)
            self.assertEqual(new_day.next_version(days / "missing", "01", "rust"), 1)
            for name in ("day-01-rust-v2", "day-01-rust-v10", "day-01-ts-v1", "day-11-rust-v4", "day-01-rust"):
                (days / name).mkdir()
            found = [(s.lang, s.version, s.path.name) for s in new_day.existing_solutions(days, "01")]
            self.assertEqual(
                found, [("rust", 2, "day-01-rust-v2"), ("rust", 10, "day-01-rust-v10"), ("ts", 1, "day-01-ts-v1")]
            )
            self.assertEqual(new_day.next_version(days, "01", "rust"), 11)
            self.assertEqual(new_day.next_version(days, "01", "go"), 1)


if __name__ == "__main__":
    unittest.main()
