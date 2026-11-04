# aoc-toolkit

Language templates for [Advent of Code](https://adventofcode.com/) days, plus
the `aoc-toolkit` CLI that scaffolds a day from them, resolves day numbers to
solution directories, benchmarks a solution and aggregates the results. It is
consumed as a git submodule at `toolkit/` by the year template
[advent-of-code-template](https://github.com/cg-homelab/advent-of-code-template),
so every year repo gets template and tooling fixes by bumping the submodule.

Languages: `rust`, `go`, `python`, `ts`, `php`, `csharp`, `fsharp`, `zig`, `c`,
`c3` (see `templates/`).

## Layout

```
templates/<lang>/                      one directory per language template
src/aoc_toolkit/cli.py                 the `aoc-toolkit` entry point: dispatches `new`, `resolve`, `bench`, `collect`
src/aoc_toolkit/new_day.py             `aoc-toolkit new`: scaffolds days/day-NN-<lang>-vN from a template
src/aoc_toolkit/resolve_day.py         `aoc-toolkit resolve`: resolves a day number or solution name to its directories
src/aoc_toolkit/collect_benches.py     `aoc-toolkit collect`: merges every day's bench.json into benchmarks.json and renders benchmarks.md
src/aoc_toolkit/bench_day.py           `aoc-toolkit bench`: in-process benchmark driver behind `just bench`
tests/                                 pytest suite (unittest-style) for the CLI modules
pyproject.toml, uv.lock, .python-version   the uv project (runtime is stdlib-only; Python 3.13)
.github/actions/setup-toolchains/      composite action: just, uv, hyperfine and per-language toolchains
```

The year repo's root justfile calls the CLI through the `AOC_TOOLKIT`
environment variable (see [Justfile variables](#justfile-variables)).

## Per-day contract

Each template has a `justfile` with the same recipes, run from the day
directory:

| Recipe | Behavior |
|---|---|
| `just run <part>` | Solve part 1 or 2 against `input.txt`, print the answer |
| `just test` | Language-native tests against `example1.txt`/`example2.txt` |
| `just build` | Produce an optimized artifact (no-op where not applicable) |
| `just bench` | Build, then run `aoc-toolkit bench` for both parts: in-process timing (+ hyperfine whole-process wall time when installed), written to `bench.json` |

The solution executable takes the part number as its only argument
(`<cmd> 1` / `<cmd> 2`) and reads `input.txt` from the day directory.
Templates ship a working placeholder (part 1: line count, part 2: total
characters across lines) so everything passes right after scaffolding.

### Executable contract

The solution executable has two invocations, both run from the day directory:

```
<cmd> 1|2                                                           print the answer for input.txt
<cmd> bench <part> <input-path> <budget-ns> <min-iter> <max-iter>   print samples and answer
```

Bench mode follows these rules (each template has a separate harness file,
for example `bench.py` or `src/bench.rs`, that users never edit):

1. Validate before printing anything: part is 1 or 2, and `budget-ns`,
   `min-iter` and `max-iter` are non-negative integers with
   `max-iter >= min-iter`. A usage error or an unreadable input exits 1 with a
   message on stderr and nothing on stdout.
2. Read the whole file at `<input-path>` into memory. Allocate and touch the
   sample buffer, sized `max-iter`, before the loop. It is identical in the
   zero-iteration baseline run and the measured run, so it cancels out of the
   memory delta.
3. Loop `i = 0; while i < min_iter or (i < max_iter and now() - t_loop < budget_ns)`:
   time one `process()` call with the monotonic nanosecond clock, compare its
   answer with the first one (outside the timed window), then `i += 1`. With
   `min-iter` 0 and `budget-ns` 0 the loop runs zero times. An answer mismatch
   exits 1 with a message on stderr.
4. Write through buffered stdout one line per sample (nanoseconds as a decimal
   integer). Then, only if `i > 0`, a line `answer <N>`, where `N` is the
   UTF-8 byte length of the answer without its trailing newline, followed by
   exactly those `N` bytes and a newline. Then always a line `done`. Flush.
5. Block until one line arrives on stdin (a newline or EOF both release it,
   the content is ignored), then exit 0. Usage and input errors and answer
   mismatches exit 1 before printing anything and do not park.

Example output for three iterations and the answer `3`:

```
1532
1498
1501
answer 1
3
done
```

Zero iterations print exactly `done`. When you run bench mode by hand you must
supply stdin, for example `echo | <cmd> bench 1 input.txt 0 0 5`.

There is no JSON and no memory measurement in the harness. Diagnostics inside
`process()` must go to stderr: anything extra on stdout breaks the output and
the driver rejects it. The harness does no warmup; `aoc-toolkit bench` discards the
warmup samples and measures peak memory from the outside.

### Justfile variables

Every template justfile declares two variables:

```just
solution := "./target/release/__NAME__"
toolkit := env("AOC_TOOLKIT", "../../toolkit")

# In-process timing of both parts (+ hyperfine wall time if installed) -> bench.json
bench *args: build
    uv run --quiet --frozen --no-dev --project "{{toolkit}}" aoc-toolkit bench --name __NAME__ {{args}} -- {{solution}}
```

- `solution` is the executable command, which may be several words
  (`.venv/bin/python main.py`, `php main.php`). It is deliberately not quoted
  in the recipe so it splits into arguments.
- `toolkit` is the toolkit submodule root, the directory holding
  `pyproject.toml`. The year repo's root justfile exports `AOC_TOOLKIT` (its
  `toolkit/` directory); CI here sets it to `$GITHUB_WORKSPACE`. The default
  only works for a day at `days/<name>` next to `toolkit/`.

The uv flags: `--project` makes uv use the toolkit's project and environment
while the day directory stays the working directory, which the driver needs.
The first run creates `toolkit/.venv` (gitignored in the toolkit) and may
download Python 3.13. `--frozen` never rewrites `uv.lock`, so the submodule
stays clean, and `--no-dev` skips pytest and ruff. `--quiet` hides uv's own
progress output.

Extra arguments pass straight through to the driver, for example
`just bench --time 3 --no-hyperfine`. `{{args}}` is re-split by the shell, so a
flag value containing a space (for example `--json-out "x y.json"`) is not
supported from `just bench`; run `aoc-toolkit bench` directly for that. Its main
flags:

| Flag | Meaning |
|---|---|
| `--time S` | Time budget the harness fills per part (default 1 s, at most about 9.2e9 s) |
| `--timeout S` | Kill the measured bench run if it has not printed `done` after S seconds (exit 3); off by default so slow solutions are never cut short |
| `--min-iterations N`, `--max-iterations N` | Bounds for the iteration count the harness picks (defaults 10 and 100000, at most 2^31-1) |
| `--iterations N` | N measured iterations (the driver asks the harness for N+K with budget 0) |
| `--warmup K` | Fixed number of discarded warmup samples; must be smaller than `--min-iterations` |
| `--input FILE` | Time against another input (skips the run-mode check and hyperfine). The result is written to `bench.json`, but `aoc-toolkit collect` does not merge runs made with another input into `benchmarks.json` (the existing entry in `benchmarks.json` is kept); re-run `just bench` without `--input` to refresh |
| `--no-hyperfine` | Skip the whole-process wall time |
| `--json-out FILE` | Where to write the summary (default `bench.json`) |

`aoc-toolkit bench` exits 0 on success, 1 on usage errors, 2 when the solution
fails, 3 on a protocol violation and 4 on an answer mismatch.

Per part the driver runs the run-mode check (default input only), then a
zero-iteration baseline run `bench <part> <input> 0 0 <max-iterations>`. Its
peak RSS is the idle baseline. The driver cannot read it after the child
exits, because on Linux a child's peak RSS reported after exit is floored at
the parent's own RSS. Instead it reads the harness's own peak while the
process is parked on stdin (`VmHWM` in `/proc/<pid>/status` on Linux,
`PeakWorkingSetSize` on Windows, `ru_maxrss` from `wait4` on macOS), then
releases it by writing a newline to its stdin. The zero-iteration baseline run always has a 60 s
watchdog. A harness that exits without parking makes the driver report the
memory columns as n/a with a warning, because on Linux it cannot mix the live
peak with the after-exit counter.
Then it runs the measured `bench <part> <input> <time in ns> <min-iterations>
<max-iterations>`, and the harness decides how many iterations fit the budget.
The driver discards the first K samples as warmup (K is N/10 clamped to
[1, 100], and 0 when there is only one sample) and reports min, median, mean,
stddev, p99 and max over the rest, the peak RSS delta (peak of the measured run
minus peak of the baseline run) and, when hyperfine is installed, the
whole-process wall time from `hyperfine -N --warmup 3`. Numbers under about
1 µs mostly measure clock overhead. The RSS delta compares two different
processes, so expect small noise, and it is only indicative for
garbage-collected runtimes. In `bench.json` (schema 1) each part carries
`rss_baseline_bytes`, `rss_peak_bytes` and `rss_delta_bytes`, each an int or
null.

## Solution directories

Every scaffolded solution lives in `days/day-NN-<lang>-vN`, so a day can be
solved several times, in the same language or in others:

```
days/day-01-rust-v1      first attempt at day 1 in rust
days/day-01-rust-v2      second attempt in rust
days/day-01-zig-v1       first attempt in zig
```

`NN` is the zero-padded day, `<lang>` the template name and `vN` one past the
highest existing version for that day and language, so a deleted `v2` next to
`v1` and `v3` is not reused (the next one is `v4`). Each language starts at
`v1`. Plain `days/day-NN` directories from v1.x are not recognised.

All solutions of a day share the puzzle input: when another
`days/day-NN-*-v*` directory of the same day has a non-empty `input.txt`, the
new solution gets a copy of it (from the most recently modified one) and the
scaffolder says where it came from. Otherwise `input.txt` starts empty.

## Tokens

`aoc-toolkit new` replaces two literal tokens, in file contents and in file
or directory names:

| Token | Replaced with | Use it for |
|---|---|---|
| `__NAME__` | the solution directory name, e.g. `day-01-rust-v1` | package/module/project names, binary names and paths, usage strings, justfile header and the bench driver's `--name`, so every solution is unique in `benchmarks.json` / `benchmarks.md` |
| `__DAY__` | the zero-padded day number, `01` to `25` | identifiers that cannot contain `-`, such as the C#/F# `Day__DAY__` namespaces, modules and project/solution file names |

Only git-tracked template files are copied, so local build artifacts never
leak into a new day. When git is unavailable or the template is not tracked
yet (while adding a language), it warns and copies the whole directory
instead, minus known artifact directories (`target/`, `node_modules/`,
`.venv/`, `bin/`, `obj/`, ...) and `input.txt`, `bench.md`, `bench.json` and `solution`.

## Using it in a year repo

```sh
git submodule add https://github.com/cg-homelab/aoc-toolkit.git toolkit
git -C toolkit checkout v5.0.0   # pin a release tag
git add toolkit

# from the year repo root: creates days/day-03-rust-v1/ from templates/rust
uv run --frozen --no-dev --project toolkit aoc-toolkit new 3 rust
# again: days/day-03-rust-v2/; another language: days/day-03-zig-v1/
uv run --frozen --no-dev --project toolkit aoc-toolkit new 3 rust
uv run --frozen --no-dev --project toolkit aoc-toolkit new 3 zig
# no arguments: prompts for the day and the language
uv run --frozen --no-dev --project toolkit aoc-toolkit new

# pick up toolkit changes later: move to a newer tag...
git -C toolkit fetch --tags && git -C toolkit checkout v5.1.0
# ...or follow main
git submodule update --remote toolkit
git add toolkit
```

The first `uv run` creates `toolkit/.venv` and may download Python 3.13 (see
[Justfile variables](#justfile-variables) for the flags). Without uv, run the
CLI on Python 3.13 or newer straight from the source tree:

```sh
PYTHONPATH=toolkit/src python3 -m aoc_toolkit new 3 rust
```

`aoc-toolkit new` creates `days/` in the current directory (override with
`--root <dir>`), never touches an existing solution directory, and creates
`input.txt` (copied from a sibling solution of the same day, else empty). It
prints the created directory and a hint that names it, for example
`just run day-03-rust-v2 1   just test day-03-rust-v2   just bench day-03-rust-v2`
(the year repo's justfile takes a day number or an exact solution directory
name).

`day` and `lang` are optional. When one is missing and stdin is a terminal,
`aoc-toolkit new` prompts for it: the day as a number from 1 to 25, then the
language, either by name or by its number in the printed list, which also
shows the solutions that already exist for that day. Prompts go to stderr, so
stdout only carries the result. A single argument that is a language name
(`aoc-toolkit new rust`) is taken as the language, so only the day is prompted
for. When stdin is not a terminal (CI, pipes), a missing argument is an error
that prints the usage line and exits 1.

`aoc-toolkit resolve` and `aoc-toolkit collect` are run from the year repo
root and use the current directory as the root (`--root <dir>` to override).

### benchmarks.md

`aoc-toolkit collect [--root DIR] [--tablemode {single,ranking}]` merges every
`days/day-NN-<lang>-vN/bench.json` into `benchmarks.json` at the year repo
root and renders `benchmarks.md` from it. `just bench` and `just bench-all`
run it after benchmarking; `just benchmarks` runs it without benchmarking.

`benchmarks.json` is the committed source of truth. It holds one entry per
solution with the timings of both parts, the date it was benched, and the CPU,
OS and commit it was benched on (`aoc-toolkit bench` records `system` and `commit`
in `bench.json`; `(dirty)` after the commit means tracked files other than
`benchmarks.json` / `benchmarks.md` had uncommitted changes). Puzzle answers
are stripped. `benchmarks.md` is generated from it, so it can be rebuilt at
any time, even on a fresh clone without any `bench.json`.

The table layout is the tablemode, `single` by default. `single` renders one
table under `## Results`, ordered by day, part and in-process median:

```
| Day | Part | Rank | Solution | Median | ×fastest | Min | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |
```

`ranking` renders one table per day and part under `## Day NN, part P`:

```
| Rank | Solution | Median | ×fastest | Min | Mean ± σ | p99 | Iterations | Peak RSS Δ | Wall (hyperfine) |
```

Rank and `×fastest` compare medians within one day and part, and equal
medians share a rank. Both modes end with a `## Runs` section with one bullet
per solution:

```
- `day-01-rust-v1`: benchmarked 2026-10-29 on AMD Ryzen 9 3900X 12-Core Processor, Linux 7.3.0-4-cachyos (x86_64), commit 41e9bc8 (dirty)
```

The year repo's justfile has a `tablemode := "single"` variable that `bench`
and `bench-all` pass as `--tablemode`. Override it per run with
`just tablemode=ranking bench 3`. `just benchmarks [single|ranking]` runs
the same merge as `bench` (it picks up any newer local `bench.json`, drops
entries whose directory is gone and fills `system`/`commit` for v3
`bench.json` files) and then renders `benchmarks.md`; the only difference is
that it does not benchmark anything.

Merge rules:

- An entry is replaced only when the `bench.json`'s `benched_at` is later than
  the entry's.
- An entry whose `days/<name>` directory is gone is dropped.
- A missing `bench.json` (it is gitignored, so a fresh clone has none) keeps
  the entry.
- An invalid `bench.json` (unreadable, not JSON, wrong schema, malformed
  numbers) prints a warning and keeps the entry.
- Runs made with a non-default `--input` are not merged, so the existing entry
  is kept.
- A `bench.json` from toolkit v3 (then still called lang-templates) has no `system`/`commit`; they are
  filled from the machine and commit running the merge.
- `just bench-all` rebuilds every entry whose bench succeeded.
- A corrupt `benchmarks.json` makes `aoc-toolkit collect` exit 1 without writing
  anything.

After cloning a year repo, run `git submodule update --init` if
`toolkit/` is empty.

## CI

`.github/workflows/ci.yml` has a `toolkit` job that runs `ruff check` and the
pytest suite through uv (`uv run --locked ruff check`, `uv run --locked
pytest -q`). A `validate` matrix job then, for every
template, scaffolds day 1 into a scratch directory as `days/day-01-<lang>-v1`
with the `setup-toolchains` action and checks the contract: `just test` passes,
`just run 1`/`just run 2` print `3`/`9` for a three-line sample input, and
`just bench` writes a valid `bench.json`. A `Harness protocol` step first runs
`<solution> bench 1 input.txt 200000000 5 5` and `bench 2 input.txt 200000000 3 3`
directly and checks that stdout is exactly that many sample lines, then
`answer 1`, the answer line and `done`, that zero iterations print exactly
`done` (with stdin at EOF), that the harness parks until stdin is released,
and that `min-iter > max-iter` fails with empty stdout. The `Benchmark` step
then checks `bench.json` (schema 1, parts 1 and 2, answers `3`/`9`, a
hyperfine wall time, integer RSS baseline, peak and delta, all `ns`
statistics) and that no `bench.md` appears. It also checks that scaffolding
left the checkout clean. It runs on pushes to `main`, pull requests and weekly, to
catch toolchain churn.

Year repos can reuse the toolchain setup. The action is Linux-only (it
installs the .NET Native AOT prerequisites with `apt-get`), so use it on
`ubuntu-*` runners:

```yaml
- uses: actions/checkout@v7
  with:
    submodules: true
- uses: ./toolkit/.github/actions/setup-toolchains
  with:
    lang: all   # or a single language
```

## Releases

Releases are semver tags (`v1.0.0`), created automatically by the Release
workflow when a PR merges to main. The bump comes from the PR label
`release/major`, `release/minor` or `release/patch`, and is a patch when the PR
is unlabeled. `release/skip` merges without creating a tag. A breaking change
to the per-day contract or to the `aoc-toolkit` CLI's interface must carry
`release/major`. A major bump also bumps `version` in `pyproject.toml` and
then runs `uv lock` so `uv.lock` matches (CI runs with `--locked` and fails
otherwise); minor and patch tags do not touch `pyproject.toml`, so `version`
only tracks the major line. Year repos pin a tag in their submodule.
Dependabot's `gitsubmodule` ecosystem can open bump PRs, but it moves the
submodule to the head of its tracked branch rather than to the newest tag, so
review those against the release notes.

**v5.0.0 is breaking for consumers.** The repo is renamed from
`aoc-lang-templates` to `aoc-toolkit` and is mounted at `toolkit/`. The
scripts are replaced by one CLI, `aoc-toolkit new|resolve|bench|collect`, with
the same flags and exit codes. Template justfiles read `AOC_TOOLKIT` instead
of `AOC_SCRIPTS` and run the CLI through uv, so uv is required for
`just bench` in every language. Year repos must `git mv lang-templates
toolkit`, update the `.gitmodules` URL and run `git submodule sync`, replace
their root justfile and CI with the year template's v5 versions, and update
line 4 and the bench recipe of every committed `days/*/justfile`.

**v4.0.0 is breaking for consumers.** The collector keeps its results in
a committed `benchmarks.json` and renders `benchmarks.md` from it, in either
the `single` or the `ranking` table layout (see [benchmarks.md](#benchmarksmd)),
so the per-solution `## day-NN-...` sections are gone. Sections written by v3
hold only formatted numbers and cannot be imported. Year repos must add the
`tablemode := "single"` variable and the `benchmarks` recipe to their root
justfile, pass `--tablemode` from `bench` and `bench-all`, run
`just bench-all` once, and commit `benchmarks.json`.

**v3.0.0 is breaking for consumers.** `just bench` writes `bench.json` through
the bench driver instead of hyperfine's `bench.md`, so every solution
executable must implement a `bench` mode (see
[Executable contract](#executable-contract)). The resolve and collect scripts
moved here from the year repo. Year repos must replace their root justfile and
delete their own `scripts/`; see the year template's
README for the migration.

**v2.0.0 is breaking for consumers.** Solutions are scaffolded into
`days/day-NN-<lang>-vN` instead of `days/day-NN`, the templates use the new
`__NAME__` token for package, binary and bench names, and the scaffolder no
longer recognises plain `days/day-NN` directories. Year repos on the old
layout either stay on a v1.x tag or rename each `days/day-NN` once (for
example to `days/day-NN-<lang>-v1`) and update anything that refers to the
old paths, such as the year repo's justfile.

## Adding a language

1. Add a directory under `templates/` containing a `justfile` with the four
   recipes above, `example1.txt`/`example2.txt`, and a placeholder solution
   that follows the executable contract. Use `__NAME__` for names, binary
   paths, usage strings and bench labels, and `__DAY__` only where a hyphen
   is not allowed (see [Tokens](#tokens)). The directory name is the
   language name and must be lowercase letters and digits with optional
   single `-` or `_` separators (`^[a-z0-9]+(?:[-_][a-z0-9]+)*$`), the pattern
   the year repo uses to resolve `day-NN-<lang>-vN`; `aoc-toolkit new` ignores
   template directories with any other name and a unit test enforces it.
   Declare `solution` and `toolkit`, add the `bench *args: build` recipe (see
   [Justfile variables](#justfile-variables)), and implement bench mode per the
   [Executable contract](#executable-contract) in a separate harness file
   users never edit.
2. Add the language to the matrix in `.github/workflows/ci.yml`.
3. In `.github/actions/setup-toolchains/action.yml`, add a guarded setup
   step, add the name to the `Validate lang input` case list, and update the
   `lang` input description.
4. Add it to the language list at the top of this README.
5. Run the unit tests: `uv run pytest`.
6. Scaffold the language into a scratch year with `AOC_TOOLKIT=<repo>`
   (`uv run --project . aoc-toolkit new --root <scratch> 1 <lang>`), write a sample
   `input.txt`, and check that `just bench` in the new day directory writes
   `bench.json`.

## Requirements

- [just](https://github.com/casey/just) 1.17 or newer
- [uv](https://docs.astral.sh/uv/), which provides Python 3.13 for the toolkit on first use
- The toolchain of each language you use
  - C needs a C17 compiler available as `cc` (override with the `CC`
    environment variable); `just build` runs it directly, there is no Makefile
  - C3 needs [`c3c`](https://github.com/c3lang/c3c) 0.8.5 or newer
- [hyperfine](https://github.com/sharkdp/hyperfine) is optional: when present
  it adds the whole-process Wall column
