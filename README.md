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
.github/actions/setup-toolchains/      composite action: just, hyperfine and per-language toolchains
```

## Per-day contract

Each template has a `justfile` with the same recipes, run from the day
directory:

| Recipe | Behavior |
|---|---|
| `just run <part>` | Solve part 1 or 2 against `input.txt`, print the answer |
| `just test` | Language-native tests against `example1.txt`/`example2.txt` |
| `just build` | Produce an optimized artifact (no-op where not applicable) |
| `just bench` | Build, then hyperfine each part, exporting `bench.md` |

The solution executable takes the part number as its only argument
(`<cmd> 1` / `<cmd> 2`) and reads `input.txt` from the day directory.
Templates ship a working placeholder (part 1: line count, part 2: total
characters across lines) so everything passes right after scaffolding.

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
| `__NAME__` | the solution directory name, e.g. `day-01-rust-v1` | package/module/project names, binary names and paths, usage strings, justfile header and hyperfine `-n` labels (`'__NAME__ part1'`), so every solution is unique in `bench.md` |
| `__DAY__` | the zero-padded day number, `01` to `25` | identifiers that cannot contain `-`, such as the C#/F# `Day__DAY__` namespaces, modules and project/solution file names |

Only git-tracked template files are copied, so local build artifacts never
leak into a new day. When git is unavailable or the template is not tracked
yet (while adding a language), it warns and copies the whole directory
instead, minus known artifact directories (`target/`, `node_modules/`,
`.venv/`, `bin/`, `obj/`, ...) and `input.txt`, `bench.md` and `solution`.

## Using it in a year repo

```sh
git submodule add https://github.com/cg-homelab/aoc-lang-templates.git lang-templates
git -C lang-templates checkout v2.0.0   # pin a release tag
git add lang-templates

# from the year repo root: creates days/day-03-rust-v1/ from templates/rust
python3 lang-templates/scripts/new_day.py 3 rust
# again: days/day-03-rust-v2/; another language: days/day-03-zig-v1/
python3 lang-templates/scripts/new_day.py 3 rust
python3 lang-templates/scripts/new_day.py 3 zig
# no arguments: prompts for the day and the language
python3 lang-templates/scripts/new_day.py

# pick up template changes later: move to a newer tag...
git -C lang-templates fetch --tags && git -C lang-templates checkout v2.1.0
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

After cloning a year repo, run `git submodule update --init` if
`lang-templates/` is empty.

## CI

`.github/workflows/ci.yml` runs the script's unit tests, then, for every
template, scaffolds day 1 into a scratch directory as `days/day-01-<lang>-v1`
with the `setup-toolchains` action and checks the contract: `just test` passes,
`just run 1`/`just run 2` print `3`/`9` for a three-line sample input, and
`just bench` writes `bench.md`. It also checks that scaffolding left the
checkout clean. It runs on pushes to `main`, pull requests and weekly, to
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
2. Add the language to the matrix in `.github/workflows/ci.yml`.
3. In `.github/actions/setup-toolchains/action.yml`, add a guarded setup
   step, add the name to the `Validate lang input` case list, and update the
   `lang` input description.
4. Add it to the language list at the top of this README.
5. Run the unit tests: `python3 -m unittest discover -s scripts -p 'test_*.py'`.
