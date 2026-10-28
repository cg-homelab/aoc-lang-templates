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
scripts/new_day.py                     scaffolds days/day-NN from a template
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

## The `__DAY__` token

Use the literal token `__DAY__` anywhere the zero-padded day number (`01` to
`25`) should appear: in file contents (package names, binary names, bench
labels) and in file or directory names (for example `Day__DAY__.slnx`).
`scripts/new_day.py` replaces it in both. Only git-tracked template files are
copied, so local build artifacts never leak into a new day. When git is
unavailable or the template is not tracked yet (while adding a language), it
warns and copies the whole directory instead, minus known artifact
directories (`target/`, `node_modules/`, `.venv/`, `bin/`, `obj/`, ...) and
`input.txt`, `bench.md` and `solution`.

## Using it in a year repo

```sh
git submodule add https://github.com/cg-homelab/aoc-lang-templates.git lang-templates
git -C lang-templates checkout v1.0.0   # pin a release tag
git add lang-templates

# from the year repo root: creates days/day-03/ from templates/rust
python3 lang-templates/scripts/new_day.py 3 rust

# pick up template changes later: move to a newer tag...
git -C lang-templates fetch --tags && git -C lang-templates checkout v1.1.0
# ...or follow main
git submodule update --remote lang-templates
git add lang-templates
```

`new_day.py` creates `days/` in the current directory (override with
`--root <dir>`), refuses to overwrite an existing day, and creates an empty
`input.txt` for the puzzle input. After cloning a year repo, run
`git submodule update --init` if `lang-templates/` is empty.

## CI

`.github/workflows/ci.yml` runs the script's unit tests, then, for every
template, scaffolds day 1 into a scratch directory with the
`setup-toolchains` action and checks the contract: `just test` passes,
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

Releases are semver tags (`v1.0.0`). A breaking change to the per-day
contract or to `new_day.py`'s interface bumps the major version. Year repos
pin a tag in their submodule. Dependabot's `gitsubmodule` ecosystem can open
bump PRs, but it moves the submodule to the head of its tracked branch rather
than to the newest tag, so review those against the release notes.

## Adding a language

1. Add a directory under `templates/` containing a `justfile` with the four
   recipes above, `example1.txt`/`example2.txt`, and a placeholder solution
   that follows the executable contract. Use `__DAY__` for the day number.
2. Add the language to the matrix in `.github/workflows/ci.yml`.
3. In `.github/actions/setup-toolchains/action.yml`, add a guarded setup
   step, add the name to the `Validate lang input` case list, and update the
   `lang` input description.
4. Add it to the language list at the top of this README.
5. Run the unit tests: `python3 -m unittest discover -s scripts -p 'test_*.py'`.
