# aoc-lang-templates

Language templates for [Advent of Code](https://adventofcode.com/) days, plus
the script that scaffolds a day from them. It is consumed as a git submodule
at `lang-templates/` by the year template
[advent-of-code-template](https://github.com/cg-homelab/advent-of-code-template),
so every year repo gets template fixes by bumping the submodule.

Languages: `rust`, `go`, `python`, `ts`, `php`, `csharp`, `fsharp`, `zig`
(see `templates/`).

## Layout

```
templates/<lang>/                      one directory per language template
scripts/new_day.py                     scaffolds days/day-NN-<lang>-vN from a template
scripts/resolve_day.py                 resolves a day number or solution name to its directories
scripts/collect_benches.py             merges every day's bench.json into benchmarks.md
scripts/bench_day.py                   in-process benchmark driver behind `just bench`
.github/actions/setup-toolchains/      composite action: just, hyperfine and per-language toolchains
```

The year repo's root justfile calls the scripts through the `AOC_SCRIPTS`
environment variable (see [Justfile variables](#justfile-variables)).

## Per-day contract

Each template has a `justfile` with the same recipes, run from the day
directory:

| Recipe | Behavior |
|---|---|
| `just run <part>` | Solve part 1 or 2 against `input.txt`, print the answer |
| `just test` | Language-native tests against `example1.txt`/`example2.txt` |
| `just build` | Produce an optimized artifact (no-op where not applicable) |
| `just bench` | Build, then run `bench_day.py` for both parts: in-process timing (+ hyperfine whole-process wall time when installed), written to `bench.json` |

The solution executable takes the part number as its only argument
(`<cmd> 1` / `<cmd> 2`) and reads `input.txt` from the day directory.
Templates ship a working placeholder (part 1: line count, part 2: total
characters across lines) so everything passes right after scaffolding.

### Executable contract

The solution executable has two invocations, both run from the day directory:

```
<cmd> 1|2                                      print the answer for input.txt
<cmd> bench <part> <input-path> <iterations>   print one JSON line (schema 1)
```

Bench mode follows these rules (each template has a separate harness file,
for example `bench.py` or `src/bench.rs`, that users never edit):

- Read the whole file at `<input-path>` into memory before timing.
- Time each `process()` call with the monotonic nanosecond clock, and compare
  every answer with the first one (outside the timed window).
- Record the max RSS before and after the loop, in bytes, or `null` where the
  platform has no support.
- Print exactly one JSON line and nothing else on stdout, then exit 0.
- Exit 1 with a message on stderr for usage errors (part not 1 or 2,
  iterations below 1), an unreadable input, or an answer mismatch.

| Field | Type | Rule |
|---|---|---|
| `schema` | int | Always `1` |
| `part` | int | `1` or `2`, as requested |
| `iterations` | int | As requested; equals `len(ns)` |
| `ns` | int[] | Nanoseconds per `process()` call, in order, each `>= 0` |
| `answer` | string | Exactly what `<cmd> <part>` prints, without the newline |
| `max_rss_before` | int or null | Peak RSS in bytes before the loop |
| `max_rss_after` | int or null | Peak RSS in bytes after the loop |

No other keys are allowed. Diagnostics inside `process()` must go to stderr:
anything extra on stdout breaks the JSON line and the driver rejects it.
The harness does no warmup or iteration planning; `bench_day.py` does.

### Justfile variables

Every template justfile declares two variables:

```just
solution := "./target/release/__NAME__"
scripts := env("AOC_SCRIPTS", "../../lang-templates/scripts")

# In-process timing of both parts (+ hyperfine wall time if installed) -> bench.json
bench *args: build
    python3 "{{scripts}}/bench_day.py" --name __NAME__ {{args}} -- {{solution}}
```

- `solution` is the executable command, which may be several words
  (`.venv/bin/python main.py`, `php main.php`). It is deliberately not quoted
  in the recipe so it splits into arguments.
- `scripts` is the directory holding `bench_day.py`. The year repo's root
  justfile exports `AOC_SCRIPTS` (its `lang-templates/scripts` directory); CI
  here sets it to `$GITHUB_WORKSPACE/scripts`. The default only works for a
  day at `days/<name>` next to `lang-templates/`.

Extra arguments pass straight through to the driver, for example
`just bench --time 3 --no-hyperfine`. `{{args}}` is re-split by the shell, so a
flag value containing a space (for example `--json-out "x y.json"`) is not
supported from `just bench`; run `bench_day.py` directly for that. Its main
flags:

| Flag | Meaning |
|---|---|
| `--time S` | Target measured time per part (default 1 s) |
| `--min-iterations N`, `--max-iterations N` | Clamp for the iteration count (defaults 10 and 100000) |
| `--iterations N` | Fixed iteration count, skips calibration |
| `--warmup K` | Fixed number of discarded warmup iterations |
| `--input FILE` | Time against another input (skips the run-mode check and hyperfine). The result is written to `bench.json`, but `collect_benches.py` does not merge runs made with another input into `benchmarks.md` (the previous section is kept); re-run `just bench` without `--input` to refresh |
| `--no-hyperfine` | Skip the whole-process wall time |
| `--json-out FILE` | Where to write the summary (default `bench.json`) |

`bench_day.py` exits 0 on success, 1 on usage errors, 2 when the solution
fails, 3 on a protocol violation and 4 on an answer mismatch.

The driver calibrates in two stages: one iteration, and if that takes under
0.1 s, a second calibration run of enough iterations to fill about 0.1 s,
taking the median of the warm samples. It then picks N iterations to fill
`--time`, clamped to [10, 100000], and a warmup K of N/10 clamped to [1, 100]
whose samples are discarded. Per part it reports min, median, mean, stddev,
p99 and max, the peak RSS delta, and, when hyperfine is installed, the
whole-process wall time from `hyperfine -N --warmup 3`. Numbers under about
1 µs mostly measure clock overhead, and the RSS delta is only indicative for
garbage-collected runtimes.

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
script says where it came from. Otherwise `input.txt` starts empty.

## Tokens

`scripts/new_day.py` replaces two literal tokens, in file contents and in file
or directory names:

| Token | Replaced with | Use it for |
|---|---|---|
| `__NAME__` | the solution directory name, e.g. `day-01-rust-v1` | package/module/project names, binary names and paths, usage strings, justfile header and the bench driver's `--name`, so every solution is unique in `benchmarks.md` |
| `__DAY__` | the zero-padded day number, `01` to `25` | identifiers that cannot contain `-`, such as the C#/F# `Day__DAY__` namespaces, modules and project/solution file names |

Only git-tracked template files are copied, so local build artifacts never
leak into a new day. When git is unavailable or the template is not tracked
yet (while adding a language), it warns and copies the whole directory
instead, minus known artifact directories (`target/`, `node_modules/`,
`.venv/`, `bin/`, `obj/`, ...) and `input.txt`, `bench.md`, `bench.json` and `solution`.

## Using it in a year repo

```sh
git submodule add https://github.com/cg-homelab/aoc-lang-templates.git lang-templates
git -C lang-templates checkout v3.0.0   # pin a release tag
git add lang-templates

# from the year repo root: creates days/day-03-rust-v1/ from templates/rust
python3 lang-templates/scripts/new_day.py 3 rust
# again: days/day-03-rust-v2/; another language: days/day-03-zig-v1/
python3 lang-templates/scripts/new_day.py 3 rust
python3 lang-templates/scripts/new_day.py 3 zig
# no arguments: prompts for the day and the language
python3 lang-templates/scripts/new_day.py

# pick up template changes later: move to a newer tag...
git -C lang-templates fetch --tags && git -C lang-templates checkout v3.1.0
# ...or follow main
git submodule update --remote lang-templates
git add lang-templates
```

`new_day.py` creates `days/` in the current directory (override with
`--root <dir>`), never touches an existing solution directory, and creates
`input.txt` (copied from a sibling solution of the same day, else empty). It
prints the created directory and a hint that names it, for example
`just run day-03-rust-v2 1   just test day-03-rust-v2   just bench day-03-rust-v2`
(the year repo's justfile takes a day number or an exact solution directory
name).

`day` and `lang` are optional. When one is missing and stdin is a terminal,
the script prompts for it: the day as a number from 1 to 25, then the
language, either by name or by its number in the printed list, which also
shows the solutions that already exist for that day. Prompts go to stderr, so
stdout only carries the result. A single argument that is a language name
(`new_day.py rust`) is taken as the language, so only the day is prompted
for. When stdin is not a terminal (CI, pipes), a missing argument is an error
that prints the usage line and exits 1.

`resolve_day.py` and `collect_benches.py` are called from the year repo root
and use the current directory as the root (`--root <dir>` to override).

After cloning a year repo, run `git submodule update --init` if
`lang-templates/` is empty.

## CI

`.github/workflows/ci.yml` runs the script's unit tests, then, for every
template, scaffolds day 1 into a scratch directory as `days/day-01-<lang>-v1`
with the `setup-toolchains` action and checks the contract: `just test` passes,
`just run 1`/`just run 2` print `3`/`9` for a three-line sample input, and
`just bench` writes a valid `bench.json`. A `Harness protocol` step first runs
`<solution> bench 1 input.txt 5` and `bench 2 input.txt 3` directly and checks
that stdout is a single JSON line with exactly the contract's keys, the right
part, iteration count and answer, and that zero iterations fails with empty
stdout. The `Benchmark` step then checks `bench.json` (schema 1, parts 1 and 2,
answers `3`/`9`, a hyperfine wall time, an integer RSS delta, all `ns`
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
- uses: ./lang-templates/.github/actions/setup-toolchains
  with:
    lang: all   # or a single language
```

## Releases

Releases are semver tags (`v1.0.0`), created automatically by the Release
workflow when a PR merges to main. The bump comes from the PR label
`release/major`, `release/minor` or `release/patch`, and is a patch when the PR
is unlabeled. `release/skip` merges without creating a tag. A breaking change
to the per-day contract or to `new_day.py`'s interface must carry
`release/major`. Year repos pin a tag in their submodule. Dependabot's
`gitsubmodule` ecosystem can open bump PRs, but it moves the submodule to the
head of its tracked branch rather than to the newest tag, so review those
against the release notes.

**v2.0.0 is breaking for consumers.** Solutions are scaffolded into
`days/day-NN-<lang>-vN` instead of `days/day-NN`, the templates use the new
`__NAME__` token for package, binary and bench names, and `new_day.py` no
longer recognises plain `days/day-NN` directories. Year repos on the old
layout either stay on a v1.x tag or rename each `days/day-NN` once (for
example to `days/day-NN-<lang>-v1`) and update anything that refers to the
old paths, such as the year repo's justfile.

**v3.0.0 is breaking for consumers.** `just bench` writes `bench.json` through
`bench_day.py` instead of hyperfine's `bench.md`, so every solution executable
must implement `bench <part> <input> <iterations>` (see
[Executable contract](#executable-contract)). `resolve_day.py` and
`collect_benches.py` moved here from the year repo. Year repos must replace
their root justfile and delete their own `scripts/`; see the year template's
README for the migration.

## Adding a language

1. Add a directory under `templates/` containing a `justfile` with the four
   recipes above, `example1.txt`/`example2.txt`, and a placeholder solution
   that follows the executable contract. Use `__NAME__` for names, binary
   paths, usage strings and bench labels, and `__DAY__` only where a hyphen
   is not allowed (see [Tokens](#tokens)). The directory name is the
   language name and must be lowercase letters and digits with optional
   single `-` or `_` separators (`^[a-z0-9]+(?:[-_][a-z0-9]+)*$`), the pattern
   the year repo uses to resolve `day-NN-<lang>-vN`; `new_day.py` ignores
   template directories with any other name and a unit test enforces it.
   Declare `solution` and `scripts`, add the `bench *args: build` recipe (see
   [Justfile variables](#justfile-variables)), and implement bench mode per the
   [Executable contract](#executable-contract) in a separate harness file
   users never edit.
2. Add the language to the matrix in `.github/workflows/ci.yml`.
3. In `.github/actions/setup-toolchains/action.yml`, add a guarded setup
   step, add the name to the `Validate lang input` case list, and update the
   `lang` input description.
4. Add it to the language list at the top of this README.
5. Run the unit tests: `python3 -m unittest discover -s scripts -p 'test_*.py'`.
6. Scaffold the language into a scratch year with `AOC_SCRIPTS=<repo>/scripts`
   (`python3 scripts/new_day.py --root <scratch> 1 <lang>`), write a sample
   `input.txt`, and check that `just bench` in the new day directory writes
   `bench.json`.

## Requirements

- [just](https://github.com/casey/just) 1.17 or newer
- Python 3.11+ for the scripts and `just bench`
- The toolchain of each language you use
- [hyperfine](https://github.com/sharkdp/hyperfine) is optional: when present
  it adds the whole-process Wall column
