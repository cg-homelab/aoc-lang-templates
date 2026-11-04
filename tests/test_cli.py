"""Tests for the `aoc-toolkit` dispatcher (cli.py)."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aoc_toolkit import cli, resolve_day


def run(*argv: str) -> tuple[int | str | None, str, str]:
    """cli.main in this process: exit code, stdout, stderr.

    argparse usage errors in a command raise SystemExit, as they do when the
    console script runs; its code counts as the exit code here.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_no_args_prints_usage_on_stderr(self) -> None:
        code, out, err = run()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("usage: aoc-toolkit <command>", err)
        for name in cli.COMMANDS:
            self.assertIn(f"  {name} ", err)

    def test_help_prints_usage_on_stdout(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                code, out, err = run(flag)
                self.assertEqual((code, err), (0, ""))
                self.assertIn("usage: aoc-toolkit <command>", out)

    def test_unknown_command(self) -> None:
        code, out, err = run("nope", "1")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("usage: aoc-toolkit <command>", err)
        self.assertIn("error: unknown command 'nope'", err)

    def test_resolve_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "days" / "day-02-rust-v1").mkdir(parents=True)
            with mock.patch.object(resolve_day, "is_interactive", return_value=False):
                code, out, err = run("resolve", "--root", tmp, "2")
        self.assertEqual((code, out, err), (0, "days/day-02-rust-v1\n", ""))

    def test_bench_without_command(self) -> None:
        # Arguments reach bench_day untouched: no `--` means no command.
        code, out, err = run("bench")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("usage: aoc-toolkit bench", err)
        self.assertIn("error: no command given after `--`", err)

    def test_none_return_is_success(self) -> None:
        with mock.patch.dict(cli.COMMANDS, {"new": (lambda argv: None, "stub")}):
            self.assertEqual(run("new", "1", "rust")[0], 0)


if __name__ == "__main__":
    unittest.main()
