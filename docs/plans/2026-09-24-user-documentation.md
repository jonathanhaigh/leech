<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# User Documentation with Tested Leech Examples — Implementation Plan

For: #23: Write user documentation with automatically tested code snippets

Design: [User documentation design](../specs/2026-09-24-user-documentation-design.md).

Revised on 2026-10-27 for `leech build`/`run`/`check`/`doctor`, location-based modules, the
diagnostic catalogue, and native test execution; see the design's
[Revision](../specs/2026-09-24-user-documentation-design.md#revision) section. Tasks 1 and 2
are complete. The remaining tasks are for later implementation, after manual approval and a
separate instruction to implement.

## Task 1: Make separately compiled module names available from the CLI (done)

Landed in 346eb22. The option was removed with the per-module compiler when `leech build`
began building whole programs (c3a4d8c, 584c894). Nothing remains.

## Task 2: Build and test the Markdown case collector (done)

Landed in aecd743 as `tests/doc.py`, `tests/test_doc.py`, and the root `conftest.py` hook,
with `markdown-it-py` as a direct development dependency. Later commits removed `std=` and
`module=`, switched `error=`/`warning=` to catalogue names, gave each case its own
`diag.Diags`, and ran cases as native executables. Task 2B adapts the remaining rules.

## Task 2B: Adapt the collector and harness to the revised contract

**Files:** `tests/doc.py`, `tests/test_doc.py`, `tests/harness.py`, `tests/test_harness.py`,
and every test calling `CompilerHarness.check_signal` (currently `tests/test_prelude.py` and
others found by `grep -rn check_signal tests`).

- **Root by `mode=`.** Replace the fixed `main.leech` root with the design's rule: the root
  is the one source fence carrying `mode=`; other fences omit it. The root file must be a
  single `NAME.leech` with no directory part, and its module name is `NAME`; build the
  root `harness.ModSrc` with that name. Report collection errors for no `mode=` fence, two
  `mode=` fences, and a root in a subdirectory. Let invalid names (`std`, and reserved words,
  which the design rejects deliberately although the CLI accepts them) fail through the
  existing `ModSrc`/`TestProgram` validation as a collection error at the root fence.
- **Entry points.** Keep `mode=compile` and `mode=error` checking without an entry point
  (`entry=False`) and `mode=run` building with one. Fix any docstring or comment in
  `tests/doc.py` that says otherwise, and add a test that a `mode=compile` case without
  `main` passes while a `mode=run` case without `main` fails. Reword messages that mention `main.leech` (for example "mode=error is limited
  to a single main.leech file" becomes "... to a single file").
- **Exact `SIGABRT` output.** An `exit=SIGABRT` case's `output=` fence matches stderr
  exactly, honoring `newline=no`; an absent fence means empty stderr; stdout must be empty.
  Remove the "nonempty prefix" and "newline=no is not allowed with exit=SIGABRT" rules.
- **Harness.** Rename `CompilerHarness.check_signal`'s `stderr_prefix` parameter to
  `stderr` and compare exactly; fix its docstring, which still describes LLVM's stderr.
  Update every caller, adding the trailing newline the program actually writes where a
  prefix omitted it. Rename `test_check_signal_accepts_stable_stderr_prefix` and test that
  extra stderr now fails.
- **Collector tests.** Update fixtures that use `file=main.leech` as an implicit root where
  the test is about rooting. Add tests for a root with another name (`hello.leech`), a
  nested helper (`text/greeting.leech` imported as `text::greeting`), no root, two roots,
  a root in a subdirectory, a reserved/`std` root name, an exact-match `SIGABRT` case with
  and without `newline=no`, an extra stderr line failing a `SIGABRT` case, and `module=`
  still rejected. Keep the existing coverage of every other rule.

**Done when:** the collector implements the design's contract, `uv run pytest tests/test_doc.py
tests/test_harness.py tests/test_prelude.py` passes, and no test or docstring refers to a
stderr prefix or to `main.leech` as the required root.

## Task 3: Write the quickstart and navigation

**Files:** `README.md`, `docs/guide/index.md`, `docs/guide/getting-started.md`,
`docs/guide/tour/index.md`, `tests/test_doc_build.py`, and `AGENTS.md`.

Start from main; do not rebase `issue-23-quickstart-docs`. Its `docs/guide/index.md`,
`docs/guide/tour/index.md`, README links, and `AGENTS.md` note may be reused as text after
updating them; its `getting-started.md` and `tests/test_doc_build.py` are replaced.

- **README.** Reduce it to a short orientation: what Leech is, its target (x86-64 Linux),
  its pre-1.0 status, and links to getting started (install), the guide index, and the
  tour. Move the Install and "Build and run a program" sections into getting-started,
  keeping all their facts (tool install variants, `update-shell`, `leech doctor`,
  `uv build` and the wheel, the `Private :: Do Not Upload` classifier, `uv run leech` in the
  checkout, `--emit` kinds, `-o`, `-O`, failed builds writing nothing, and the package and
  `std::` import rules).
- **Guide index.** Scope, links, and the contributor note on test annotations inside an
  outer `markdown` fence. The note uses `file=hello.leech`, explains that the root is the
  fence with `mode=`, and gives a real catalogue name for `error=` (for example
  `let-type-mismatch`), not an exception class.
- **Getting started.** Follow the design's eight-part outline. The Hello World source is
  `leech test=quickstart-hello file=hello.leech mode=run` with an `output=quickstart-hello`
  fence, followed by the single tested `bash test=quickstart-build` fence
  (`leech build hello.leech` then `./hello`). Add an untested `leech run hello.leech`
  fence. The several-modules section is a collected `mode=run` case with a sibling helper
  and, if it reads naturally, a nested `text/` helper; its build command is an untested
  fence. The errors section has a `mode=error` case with a one-line excerpt and describes
  the rendered layout in prose, linking #118. The under-the-hood section's commands are
  untested. Link #107, #110, and #118 where the text meets those limits.
- **Tour index.** A placeholder listing the planned chapters, linking back to the guide
  index, until Task 4 fills it.
- **Smoke test.** Implement `tests/test_doc_build.py` as the design's
  [Smoke test](../specs/2026-09-24-user-documentation-design.md#smoke-test) describes: find
  exactly one tested Bash fence, `quickstart-build`; take the `quickstart-hello` case from
  `doc.parse_doc_page`; write its source under its `file=` name in `tmp_path`; run the fence
  there with `bash -eu -o pipefail -c` and `PATH` prefixed with
  `pathlib.Path(sys.executable).parent`, `start_new_session=True`, and a 60-second timeout
  that kills the process group (`os.killpg`) before reporting the fence and its output so
  far; assert success and stdout equal to the case's expected output. Negative tests feed
  the fence finder pages with no tested fence, two, and an unknown ID, and run the fence
  runner on a `sleep` fence with a short timeout to check that it fails, reports, and
  leaves no process behind.
- **AGENTS.md.** Add one sentence: pytest collects and runs Leech fences in `README.md` and
  `docs/guide/**/*.md`, with the contract summarized in `docs/guide/index.md` and
  implemented in `tests/doc.py`. Do not add the obsolete `LEECH_REQUIRE_NATIVE_DOCS`
  variable. Include SPDX headers on new files.
- **Cleanup, with the owner's confirmation.** Once the useful text has been copied, present
  three separate deletion targets and ask about each: the local branch
  `issue-23-quickstart-docs`, the remote branch `origin/issue-23-quickstart-docs`, and the
  stash whose subject is "WIP issue 23 documentation infrastructure task 2" (its collector
  work landed in aecd743). Find the stash by subject at that time, not by ordinal; show its
  current `stash@{N}` ref, object ID, and file list; and drop it by that object's ref only
  after confirmation.

**Done when:** a reader on the supported target can install `leech` and follow the page from
a fresh shell, the quickstart test passes, and README no longer duplicates guide content.

## Task 4: Write the language overview and tour

Every chapter uses current syntax from `src/leech/leech.lark`, standard-library sources,
and the closest feature tests. Each has at least one complete tested program, links back
to the guide index and onward to the next chapter, and links notable gaps to real open
issues. Use `mode=run` as the normal teaching path, `mode=compile` for non-executed
declarations, and `mode=error` with a verified message excerpt for a useful failure.
Name roots after what they do (`counter.leech`), not `main.leech`. Do not put unimplemented
syntax in a `leech` fence. Fill in `docs/guide/tour/index.md` as chapters land.

### Task 4A: Position the language and teach the basics

**Files:** `docs/guide/language-at-a-glance.md`, `docs/guide/tour/01-basics.md`.

- Compare Leech's familiar `fn`/`let`/`trait`/`impl` style with Rust, its compile-time
  facilities with Zig, `extern`/native linking with C and C++, and its tagged unions with
  Swift's payload-bearing enum cases. Do not imply Leech has Rust ownership, broad C++
  interoperability, or a mature standard library. Keep this overview brief.
- Teach `main` (no parameters, returning `i32` as the exit status; link #107 and #110),
  function signatures and return values, integer and boolean types, literals, `let` and
  mutable bindings, expressions, and a representative legal/illegal coercion. Ensure at
  least one runnable example, one using `exit=N`, and one checked diagnostic.

**Checkpoint:** the overview and basics page pass the docs collector and accurately
describe the current type syntax and coercions.

### Task 4B: Teach control flow and sum types

**File:** `docs/guide/tour/02-control-flow.md`.

- Cover blocks, `if`, `while`, labeled `break`/`continue`, `match` patterns, `never`,
  C-like enums, and tagged unions. Show the enum/union distinction in executable code.
  Document exhaustiveness and reachability behavior at a level useful for a tour; an
  unreachable arm may illustrate a `warning=unreachable-match-arm` case.
- Show a prelude `assert`/`panic` example with `exit=SIGABRT` and its exact stderr if
  runtime failure makes the feature clearer.

**Checkpoint:** every named construct has a passing checked example or is part of a
larger passing example, and the page does not imply future pattern features exist.

### Task 4C: Teach data, pointers, and methods

**File:** `docs/guide/tour/03-data-and-pointers.md`.

- Cover structs, arrays and current `array[T, N]` / `.[index]` spellings, field access,
  address and explicit dereference, mutable pointers, and inherent methods. Show a
  compile-fail example for an illegal mutation or pointer use.
- State the current ownership limitation with a link to #34. Treat `array[T, N]` and
  `.[index]` as implemented; #9 remains open despite this syntax having landed. Link
  pointer-member sugar (#12) and slices (#26) at the specific affected examples.

**Checkpoint:** readers can distinguish mutable bindings, mutable fields, and mutable
pointers, and every displayed Leech program is checked.

### Task 4D: Teach the program's package, imports, and the small standard library

**File:** `docs/guide/tour/04-modules-and-stdlib.md`.

- Teach the design's module rules: the root's directory is the package; imports are
  absolute from it, from every file, including files in subdirectories; `import x::a;`
  binds `a`; `import std::...` names the bundled library; a module path whose first segment
  is `std` is reserved. Cover visibility (`pub`), `extern`, the implicit prelude (`std::prelude`),
  `std::io` `print`/`println`, and `std::mem` allocation/deallocation.
- Use a case with a nested module that imports a sibling by its absolute path
  (`text/greeting.leech` importing `text::style`), so the example shows that imports are not
  relative to the importing file. Include a `mode=error` case for using a private item of
  another module. Do not describe separate compilation or linking: `leech build` builds the
  whole program.
- Check rule details against `src/leech/ir_loader.py` and `docs/specs/2026-10-02-build-tooling-design.md`
  before finalizing, and confirm which import errors are worth a checked example.

**Checkpoint:** the nested example builds and runs, `std::io::print` exercises output
without a newline, and the prelude is described as an implicit `std` module.

### Task 4E: Teach generics, traits, and compile-time work

**File:** `docs/guide/tour/05-generics-traits-comptime.md`.

- Cover generic functions and structs, type and value parameters, trait declarations,
  bounds, inherent and trait impls, method calls, top-level compile-time evaluation,
  and the four available compiler builtins (`__size_of`, `__ptr_cast_mut`, `__is_null`,
  `__enum_to_int`). Check builtin signatures against `src/leech/ir_builtins.py` before
  finalizing examples.
- Link trait method disambiguation (#11), `Option`/`Result` in the prelude (#88), and
  other gaps only where the text actually runs into them. Avoid presenting planned
  capabilities as current ones.

**Checkpoint:** at least one checked example combines generics with a trait or impl,
another demonstrates compile-time evaluation, and every named builtin is introduced
accurately.

**Task 4 complete when:** the explicit #23 inventory is checked off across 4A–4E:
generics; traits and impls; enums; compile-time evaluation; modules/imports and
visibility; `never`; coercions; arrays; structs; pointer mutability; and compiler
builtins. Current tagged unions, `match`, methods, prelude, `std::io`, and `std::mem`
are covered too. The whole guide collects and passes under `uv run pytest`.

## Task 5: Final verification and review

- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run basedpyright`, and `uv run reuse lint`.
- Verify Markdown links, issue numbers, commands, and the first info-string word of every
  public Leech fence. Confirm collection includes README and all guide pages and excludes
  historical plans/specs. Review every new comment/docstring against `AGENTS.md`.
- Manually install with `uv tool install` into temporary `UV_TOOL_DIR` and
  `UV_TOOL_BIN_DIR` directories, then follow getting-started from a fresh shell outside
  the checkout, including the untested `leech run`, `leech check`, and `--emit` fences.
- Inspect the working-tree diff for generated files or unrelated changes. In the eventual
  implementation summary, list validation results. The owner explicitly narrowed #23's
  original `docs/**/*.md` acceptance to the README and public guide; note that exception in
  the issue review. Treat #23 as complete only after the user documentation lands; ask the
  owner before closing it. If #23 is closed, update issue #55 in the same session according
  to the repository's dependency-graph rule.

**Done when:** the full suite and repository quality gates pass, the install and quickstart
path has been followed manually on x86-64 Linux, and every public Leech fence is accounted
for.

## Sequence and risks

Task 2B settles the fence contract before any guide page relies on it. Task 3 proves the
reader's first path, then Task 4 broadens coverage; Task 5 is the final gate. Chapters 4A–4E
can be written in parallel once Task 2B lands, provided writers use the same fence contract
and issue-link convention.

The largest practical risks are drift in untested command fences, silent collector
omissions, and teaching module rules that have recently changed. The executed
`quickstart-build` fence, the manual install walk-through, collector tests that inject
malformed fences, and checking module rules against the loader address these. No migration
of historical plans/specs is included: their existing Leech fences remain outside the
public-guide test boundary approved for this work.
