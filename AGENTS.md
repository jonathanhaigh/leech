<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Repository Guidelines

## Project Structure & Module Organization

Leech is a Python 3.14 compiler. Compiler code lives in `src/leech/`: parsing uses
`parse.py` and `leech.lark`, semantic and IR work uses `typcheck.py`, `ir_*.py`, and
`comptime.py`, and LLVM output is produced by
`codegen.py`. Standard-library sources are under `src/leech/std/`. Tests mirror language
features in `tests/test_*.py`; shared test helpers live in `tests/util.py`.

## Build, Test, and Development Commands

- `uv sync` installs the locked runtime and development dependencies.
- `uv run pytest` runs the full suite with the console script on `PATH`.
- `uv run pytest tests/test_traits.py::test_name` runs one test.
- `uv run ruff check .` checks lint and import rules; `uv run ruff format .` formats code.
- `uv run basedpyright` performs static type checking.
- `uv run leech input.leech -o output.ll` compiles a source file to LLVM IR.
- `uv run reuse lint` verifies license metadata.

Tests that execute generated programs require `llvm-link` and `lli` on `PATH`.

## Coding Style & Naming Conventions

Use four-space indentation and Ruff's 100-column limit. Follow standard Python naming:
`snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_CASE` for
constants. Prefer `Optional[T]` for nullable annotations. Keep imports module-qualified
(for example, `from leech import typs`, then `typs.Typ`) rather than importing individual
symbols. Use Sphinx-style docstrings, but never Sphinx cross-reference roles (`:class:`,
`:func:`, `:meth:`, `:attr:`, and the like); name the referenced item in plain text
instead. Document an item's purpose, interface, surprising behavior, and non-obvious
contracts; omit details apparent from its name, signature, annotations, or adjacent code.
Do not describe callers, usage sites, history, or neighboring components unless essential
to using the item. Omit a `:param:` or `:return:` field that only restates what's already
clear from the parameter's or return value's name, type, or the rest of the docstring.
Most non-public items need no docstring at all; add one only when its behavior is
genuinely surprising or its contract isn't obvious from the signature. An override of a
base class method usually needs no docstring either, public or not, unless its behavior
diverges from the base method's documented contract. Do not add `:raises:` fields.
Use comments sparingly to explain non-obvious reasons and constraints, not visible code.
Use assertions liberally—prefer helpers from the `asserts` module—and use
`opt_util.py` helpers where applicable. Avoid multi-line conditional
(`if`/`else`) *expressions*; use an `if` statement instead, or a `match` statement when
dispatching on a small closed set of cases.

## Testing Guidelines

Pytest is configured with strict `xfail` handling. Name files `test_<feature>.py` and
tests `test_<behavior>`. Add focused unit tests beside the closest feature coverage and
use `tests/util.py` for compile/run assertions. Run the full suite, lint, and type checks
before submitting. No numeric coverage threshold is configured; new behavior and
regressions should be covered explicitly.

## Review Guidelines

When reviewing changes, inspect every added or changed docstring and comment against the
guidance above. Flag unnecessary non-public or override docstrings, repetition, caller
lists, cross-references, redundant `:param:`/`:return:` fields, narration of visible code,
and details coupled to other items.

## Commit & Pull Request Guidelines

Recent commits use concise, imperative subjects such as `Add std::mem...` and
`Fix pytest suite...`. Keep each commit scoped to one coherent change. Pull requests
should explain behavior and impact, link issues, list validation commands,
and include sample diagnostics or generated IR when compiler output changes. Update SPDX
headers for new files and avoid committing generated `.ll` files unless they are fixtures.

Every commit references the GitHub issue it belongs to, unless the repository owner says
otherwise for that change. Include both its `#` reference and title text, preferably in a
commit-body line formatted `For: #N: Issue title`. File an issue first if none covers the work.

## Issue Tracking

- **Closing is the owner's call.** When work appears to finish an issue, say so and ask
  whether to close it. Never close one unprompted.
- **Keep the dependency graph current.** Creating or closing an issue means updating the
  dependency graph issue (#55) in the same session: add the new node and its `A --> B`
  edges, or delete the closed issue's node line and every line mentioning it. An issue
  with no blockers and nothing blocked by it needs no entry.
