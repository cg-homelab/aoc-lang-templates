from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import resolve_day as rd

SCRIPTS = Path(__file__).resolve().parent


class ResolveTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "days").mkdir()

    def mk(self, *names: str) -> None:
        for name in names:
            (self.root / "days" / name).mkdir()

    def resolve(self, target: str, choice: str | None = None) -> str:
        stderr = io.StringIO()
        if choice is None:
            # Empty stdin: a regression into prompting fails instead of blocking.
            return rd.resolve(target, self.root, stdin=io.StringIO(""), stderr=stderr)
        return rd.resolve(target, self.root, interactive=True, stdin=io.StringIO(choice), stderr=stderr)

    def test_unique_day(self) -> None:
        self.mk("day-03-rust-v1", "day-04-rust-v1")
        self.assertEqual(self.resolve("3"), "days/day-03-rust-v1")
        self.assertEqual(self.resolve("03"), "days/day-03-rust-v1")

    def test_day_range(self) -> None:
        self.mk("day-25-rust-v1", "day-01-rust-v1")
        self.assertEqual(self.resolve("25"), "days/day-25-rust-v1")
        self.assertEqual(self.resolve("1"), "days/day-01-rust-v1")
        for target in ["0", "00", "26", "99", "001", "5" * 5000]:
            with self.subTest(target=target[:10]), self.assertRaisesRegex(rd.ResolveError, "^day must be 1-25$"):
                self.resolve(target)

    def test_no_solutions(self) -> None:
        self.mk("day-04-rust-v1")
        with self.assertRaisesRegex(rd.ResolveError, r"no solutions for day 3, run `just new-day 3 <lang>`"):
            self.resolve("3")

    def test_no_days_dir(self) -> None:
        (self.root / "days").rmdir()
        with self.assertRaisesRegex(rd.ResolveError, "no solutions for day 1"):
            self.resolve("1")

    def test_several_non_interactive_lists_candidates(self) -> None:
        self.mk("day-01-rust-v1", "day-01-zig-v1", "day-01-rust-v2")
        with self.assertRaises(rd.ResolveError) as cm:
            self.resolve("1")
        msg = str(cm.exception)
        self.assertIn("day 1 has 3 solutions", msg)
        self.assertLess(msg.index("day-01-rust-v1"), msg.index("day-01-rust-v2"))
        self.assertLess(msg.index("day-01-rust-v2"), msg.index("day-01-zig-v1"))
        self.assertIn("pass the directory name", msg)

    def test_several_interactive_choice(self) -> None:
        self.mk("day-01-rust-v1", "day-01-rust-v2", "day-01-zig-v1")
        self.assertEqual(self.resolve("1", "2\n"), "days/day-01-rust-v2")
        self.assertEqual(self.resolve("1", "3\n"), "days/day-01-zig-v1")

    def test_exact_name(self) -> None:
        self.mk("day-01-rust-v1", "day-01-rust-v2")
        self.assertEqual(self.resolve("day-01-rust-v2"), "days/day-01-rust-v2")
        self.assertEqual(self.resolve("days/day-01-rust-v2"), "days/day-01-rust-v2")
        self.assertEqual(self.resolve("days/day-01-rust-v2/"), "days/day-01-rust-v2")

    def test_exact_name_missing(self) -> None:
        with self.assertRaisesRegex(rd.ResolveError, "no such solution directory: days/day-01-rust-v9"):
            self.resolve("day-01-rust-v9")

    def test_bad_targets(self) -> None:
        self.mk("day-01")  # old naming scheme is not supported
        for target in ["", "day-01", "days/day-01", "..", "days/..", "x", "-1", "day-1-rust-v1", "day-01-Rust-v1", "\u00b2"]:
            with self.subTest(target=target), self.assertRaisesRegex(rd.ResolveError, "invalid target"):
                self.resolve(target)

    def test_sort_order(self) -> None:
        self.mk(
            "day-10-go-v1", "day-02-zig-v1", "day-02-rust-v10", "day-02-rust-v2",
            "day-02-go-v1", "day-01-zig-v1", "not-a-day", "day-03",
        )
        (self.root / "days" / "day-05-rust-v1").write_text("a file, not a directory")
        self.assertEqual(
            rd.solution_dirs(self.root / "days"),
            ["day-01-zig-v1", "day-02-go-v1", "day-02-rust-v2", "day-02-rust-v10", "day-02-zig-v1", "day-10-go-v1"],
        )
        self.assertEqual(
            rd.candidates(self.root / "days", 2),
            ["day-02-go-v1", "day-02-rust-v2", "day-02-rust-v10", "day-02-zig-v1"],
        )

    def test_hyphenated_language(self) -> None:
        self.mk("day-04-c-sharp-v1", "day-04-c-v1", "day-04-c_sharp-v2", "day-04-csharp-v1")
        self.assertEqual(
            rd.candidates(self.root / "days", 4),
            ["day-04-c-v1", "day-04-c-sharp-v1", "day-04-c_sharp-v2", "day-04-csharp-v1"],
        )
        self.assertEqual(self.resolve("day-04-c-sharp-v1"), "days/day-04-c-sharp-v1")
        for bad in ["day-04-c--sharp-v1", "day-04--c-v1", "day-04-c--v1", "day-04-c-sharp-"]:
            self.assertIsNone(rd.DIR_RE.match(bad), bad)

    def test_solution_key(self) -> None:
        self.assertEqual(rd.solution_key("day-03-rust-v12"), (3, "rust", 12))
        self.assertEqual(rd.solution_key("day-04-c-sharp-v1"), (4, "c-sharp", 1))
        self.assertEqual(rd.solution_key("day-04-c-sharp-v2-v3"), (4, "c-sharp-v2", 3))
        with self.assertRaises(ValueError):
            rd.solution_key("day-03")


class PromptTest(unittest.TestCase):
    NAMES = ["day-01-rust-v1", "day-01-rust-v2", "day-01-zig-v1"]

    def test_lists_and_picks(self) -> None:
        err = io.StringIO()
        self.assertEqual(rd.prompt_choice(self.NAMES, io.StringIO("1\n"), err), "day-01-rust-v1")
        out = err.getvalue()
        self.assertIn("  1) day-01-rust-v1\n  2) day-01-rust-v2\n  3) day-01-zig-v1\n", out)
        self.assertIn("solution [1-3]: ", out)

    def test_reprompts_until_valid(self) -> None:
        err = io.StringIO()
        choice = rd.prompt_choice(self.NAMES, io.StringIO("\nfoo\n0\n4\n 3 \n"), err)
        self.assertEqual(choice, "day-01-zig-v1")
        self.assertEqual(err.getvalue().count("solution [1-3]: "), 5)
        self.assertEqual(err.getvalue().count("enter a number between 1 and 3"), 4)

    def test_eof_aborts(self) -> None:
        with self.assertRaisesRegex(rd.ResolveError, "no solution chosen"):
            rd.prompt_choice(self.NAMES, io.StringIO("9\n"), io.StringIO())


class InteractiveTest(unittest.TestCase):
    class Stream(io.StringIO):
        def __init__(self, tty: bool) -> None:
            super().__init__()
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    def check(self, stdin: object, stderr: object) -> bool:
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stderr", stderr):
            return rd.is_interactive()

    def test_needs_both_ttys(self) -> None:
        tty, pipe = self.Stream(True), self.Stream(False)
        self.assertTrue(self.check(tty, tty))
        self.assertFalse(self.check(tty, pipe))  # p=$(just run 1 1 2>/dev/null)
        self.assertFalse(self.check(pipe, tty))
        self.assertFalse(self.check(None, tty))  # stdin closed


class MainProcessTest(unittest.TestCase):
    """Run main() in a real subprocess with non-TTY stdin: the CI no-hang guarantee."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ["day-01-rust-v1", "day-01-zig-v1", "day-02-python-v1"]:
            (self.root / "days" / name).mkdir(parents=True)

    def run_main(
        self, *args: str, close_stdin: bool = False, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the script itself; --root is passed unless a cwd is given."""
        cmd = [sys.executable, str(SCRIPTS / "resolve_day.py")]
        cmd += list(args) if cwd is not None else ["--root", str(self.root), *args]
        if close_stdin:
            cmd = ["sh", "-c", '"$@" <&-', "sh", *cmd]
        return subprocess.run(
            cmd, stdin=None if close_stdin else subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, cwd=cwd,
        )

    def test_several_fails_without_prompt(self) -> None:
        for close_stdin in (False, True):
            with self.subTest(close_stdin=close_stdin):
                r = self.run_main("1", close_stdin=close_stdin)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout, "")
                self.assertIn("  day-01-rust-v1\n  day-01-zig-v1\n", r.stderr)
                self.assertIn("pass the directory name", r.stderr)
                self.assertNotIn("solution [1-", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_unique_prints_path_only(self) -> None:
        r = self.run_main("2")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "days/day-02-python-v1\n", ""))
        r = self.run_main("day-01-zig-v1")
        self.assertEqual((r.returncode, r.stdout), (0, "days/day-01-zig-v1\n"))

    def test_root_defaults_to_cwd(self) -> None:
        r = self.run_main("2", cwd=self.root)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "days/day-02-python-v1\n", ""))

    def test_usage_errors_exit_1(self) -> None:
        for args in ((), ("1", "2")):
            with self.subTest(args=args):
                r = self.run_main(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("usage: resolve_day.py [--root DIR] <target>", r.stderr)
                self.assertIn("error: ", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_main_explicit_root(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(rd, "is_interactive", return_value=False):
            self.assertEqual(rd.main(["2"], self.root), 0)
        self.assertEqual(stdout.getvalue(), "days/day-02-python-v1\n")


if __name__ == "__main__":
    unittest.main()
