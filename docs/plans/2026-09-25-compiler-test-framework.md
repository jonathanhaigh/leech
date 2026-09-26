<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Structured Compiler Test Framework — Implementation Plan

For: #94: Replace ad hoc compiler test helpers with a structured framework

Design: [Structured compiler test framework design](../specs/2026-09-25-compiler-test-framework-design.md).

Only the planning documents are written in this planning session. Implementation begins only
after manual approval and a separate instruction. The issue #23 documentation implementation
remains stashed while every task in this plan is completed and committed.

## Global constraints

- Preserve compiler behavior, emitted LLVM, diagnostic classes, diagnostic locations, and
  test coverage. This issue changes test infrastructure, not Leech semantics.
- Keep the ordinary single-source calls concise: `compiler.parse(src)`,
  `compiler.build(src)`, `compiler.compile(src)`, and
  `compiler.check(src, stdout=..., exit_status=...)`.
- Accept plain string paths at the public module boundary and convert them internally to
  concrete `pathlib.Path` values. Do not require tests to construct `PurePath` objects.
- Keep imports module-qualified. Add `tests/__init__.py` so the new harness has one canonical
  import name.
- Use explicit `TestProgram`/`ModSrc` objects for multi-file cases; do not add mappings
  parallel to those objects or accept source modules through `**kwargs`.
- Compatibility wrappers may keep intermediate commits green, but the final change removes
  `tests/util.py` and every old helper use.
- Keep `pytest.raises` and specialized assertions in test bodies when they communicate intent
  better than a harness abstraction.
- Do not pop or drop `stash@{0}` by ordinal during implementation. Locate it by its message,
  `WIP issue 23 documentation infrastructure task 2`, because newer stashes may change the
  ordinal. Retain it until #23 explicitly restores and verifies every intended file.
- Run focused tests after each migration batch. Before completion run the full pytest suite,
  Ruff check and format check, basedpyright, REUSE, and
  `git diff --check`.
- Every implementation commit references `#94: Replace ad hoc compiler test helpers with a
  structured framework` in its body.

## Task 1: Add the program model and pytest harness fixture

Implementation baseline: `pytest --collect-only -q` collected 1,593 tests before Task 1.

**Files:**

- Create `tests/__init__.py`.
- Create `tests/harness.py`.
- Create or extend root `conftest.py`.
- Create `tests/test_harness.py`.
- Modify existing test imports from top-level `util` to canonical `tests.util`.

### Work

1. Record the current collected-test count with `pytest --collect-only -q` and save it in the
   implementation notes. This guards broad migration from silently losing test modules.
2. Add `tests/__init__.py` and mechanically change existing `import util` statements to
   `from tests import util`. Adding the package changes pytest's import context, so this must
   land atomically; do not leave the legacy helper loaded under two module names.
3. Give every new file its SPDX copyright and license header before running REUSE.
4. Add the final-field `ModSrc` and immutable `TestProgram` types as specified by the design.
5. Make `ModSrc(name, src, path=None)` accept an ordinary optional string path, derive
   the common path from the qualified name, and store a normalized `pathlib.Path`.
6. Reuse Leech's qualified-name parser and reserved-name rules. Validate `.leech` suffixes,
   relative paths, traversal, and empty names in `ModSrc`; validate duplicate names and
   paths in `TestProgram`; and reject a test-supplied `prelude` module.
7. Add the workspace-bound `CompilerHarness` shell and a function-scoped `compiler` fixture
   backed by pytest's `tmp_path`.
8. Add the one coercion boundary that converts a bare source string into
   `TestProgram.from_main(src)`. Keep all lower layers typed in terms of `TestProgram`.
9. Add unit tests showing two fixture instances have independent paths and that public string
   paths become concrete paths without requiring callers to import `pathlib`.

### Validation

Run `tests/test_harness.py`, Ruff on the new files, and basedpyright. The existing suite must
still collect and pass through the unchanged legacy helpers.

### Commit boundary

Commit the model, validation, fixture, focused tests, package marker, and repository-wide
canonical import rewrite together. This is a broad but mechanical import-context transition;
the collected-count and full-suite checks are required before committing it.

## Task 2: Add materialization, parsing, and semantic-IR operations

**Files:**

- Modify `tests/harness.py` and `tests/test_harness.py`.
- Modify every test that uses `parse_mod`, `build_ir_mod`, or `write_whole_file` solely to
  prepare parser or semantic-IR input.

### Work

1. Implement private whole-program materialization. Write every source before invoking a
   compiler phase, enforce containment below the fixture workspace, use UTF-8, and do not
   print contents.
2. Implement `CompilerHarness.parse(src_or_mod)` by materializing one source file and
   calling `parse.parse_mod_ast`. A bare string becomes `main.leech`; `ModSrc` lets a test
   choose the name and path recorded in spans. Keep raw-Lark tests on `parse.build_parser`.
3. Implement `CompilerHarness.build(program)` by materializing a program and invoking
   `driver.compile_to_ir` on its root using the root's qualified name.
4. Implement `write_mod(ModSrc) -> pathlib.Path` only for loader-level tests that
   intentionally bypass the driver.
5. Add focused tests for imports seeing all materialized sources, exceptional path/name pairs,
   containment, clean success output, and propagation of production `UserError` subclasses.
6. Migrate all parser-AST tests from `parse_mod` to `compiler.parse`.
7. Migrate semantic-IR tests from `build_ir_mod` to `compiler.build`, using `TestProgram` where
   they currently write helper files manually.
8. Migrate direct writes in loader tests to `write_mod`; defer only writes that belong to a
   later LLVM/run migration.

### Validation

Run `tests/test_harness.py`, `tests/test_ast.py`, `tests/test_typs.py`, `tests/test_loader.py`,
and every file changed in the semantic-IR migration. Run the full suite before committing.

### Commit boundary

Commit materialization plus the parse/build migration. Legacy LLVM and execution helpers remain
temporarily available for unmigrated tests.

## Task 3: Add structured LLVM compilation and migrate compile-only tests

**Files:**

- Modify `tests/harness.py` and `tests/test_harness.py`.
- Modify all tests using `compile_str`, `compile_file`, or `compile_modules` without subsequent
  program execution.

### Work

1. Add `CompiledMod` and `CompiledProgram`. Preserve module declaration order and provide a
   qualified-name mapping with source path, LLVM path, and LLVM text.
2. Implement `CompilerHarness.compile(program)`. Materialize once, compile every supplied
   module independently under its declared qualified name, and write each LLVM artifact without
   printing it.
3. Add focused tests for a one-file program, multiple modules, a transitive relative import,
   same-stem modules in different directories, duplicate rejection, compile errors, and stable
   artifact lookup by qualified name.
4. Migrate compile-success and compile-error tests to `compiler.compile(src_or_program)`.
5. Replace tuple unpacking and `.read_text()` with structured access such as
   `compiled.mods["main"].llvm_ir`.
6. Preserve tests that deliberately compile an imported module separately. Express each file
   as `ModSrc` rather than reconstructing a qualified-name mapping.
7. Compare representative emitted LLVM before and after migration to ensure the harness has not
   changed module names, linkage, or entry-point selection.

### Validation

Run `tests/test_harness.py`, package/import tests, every compile-only file changed in the batch,
then the full suite, Ruff, and basedpyright.

### Commit boundary

Commit the structured compilation layer and complete compile-only migration together. The old
execution helpers may still delegate to their legacy compilation path until Task 5.

## Task 4: Add automatic runtime linking and explicit process results

**Files:**

- Modify `tests/harness.py` and `tests/test_harness.py`.
- Modify `tests/test_std_io.py`, `tests/test_std_mem.py`, `tests/test_prelude.py`,
  `tests/test_packages.py`, and `tests/test_peer_typs.py` as focused adopters.

### Work

1. Return `subprocess.CompletedProcess[str]` from runnable programs, with text streams captured
   separately.
2. Discover bundled `.leech` files from the compiler package. Compile their LLVM strings under
   `prelude` or `std::<stem>` and cache only immutable strings across tests.
3. Always link the bundled prelude. Link every other bundled implementation automatically for a
   runnable program except a `std::` qualified name explicitly supplied by the test program.
   Add focused tests for `std::io`, `std::mem`, prelude panic, new `std::` module shadowing, and
   duplicate-symbol avoidance.
4. Implement LLVM artifact materialization and `llvm-link` invocation with separate output
   capture and a 30-second timeout. A link failure or timeout reports the command, streams, and
   retained workspace.
5. Implement `run` with `lli --disable-symbolication`, separate streams, the same timeout, and
   no assertion on the program's status.
6. Implement `check` for exact stdout, exact stderr, and numeric status. Implement
   `check_signal` for exact stdout, a stable stderr prefix, and an explicit signal.
7. Migrate the focused standard-library, panic, package, and direct-run tests. Remove all
   caller-supplied standard-library lists in those files.
8. Confirm that panic text is on stderr and ordinary `std::io` text is on stdout. Update tests
   that previously relied on merged streams without changing the Leech program behavior.

### Validation

Run the focused files above plus `tests/test_harness.py`, including all signal tests. Run the
full suite to expose accidental symbol collisions from automatic library linking.

### Commit boundary

Commit runtime linking, structured execution, and representative migrations. The new execution
API is stable before the mechanical full-suite batch.

## Task 5: Migrate every runnable-program test

**Files:**

- Modify all remaining test files using `check_prog_output` or `link_and_run`.

### Work

1. Replace ordinary calls with named expectations:

   ```python
   compiler.check(src, stdout="", exit_status=42)
   ```

2. Use `TestProgram.from_main` plus `ModSrc` for every multi-file case. Keep explicit paths
   only where physical layout differs from the qualified name.
3. Replace negative signal statuses with `check_signal` and state the stable stderr prefix.
4. For tests that inspect a completed process beyond common expectations, call `run` and keep
   the assertions in the test.
5. Audit every previously nonempty expected output. Classify it as stdout or stderr rather than
   mechanically assigning merged output to one stream.
6. Remove every `std_modules` argument.
7. Run migration searches after the edits and fail the task if any use of `check_prog_output`,
   `link_and_run`, `std_modules`, or `**modules` remains in behavioral tests.

### Validation

Run changed files in manageable groups, then the full suite. Compare the collected-test count
with Task 1 and account for any intentional test additions; no existing test may disappear.

### Commit boundary

Commit the full runtime-test migration as one mechanical API transition after the new behavior
has already landed and been reviewed in Task 4.

## Task 6: Replace source-position helpers and finish low-level migration

**Files:**

- Modify `tests/harness.py` and `tests/test_harness.py`.
- Modify all tests using `find_pos` or remaining direct source-file helpers.

### Work

1. Replace `find_pos` with public `src_position(src, substring)`, retaining its
   first-occurrence semantics so ordered lists of diagnostic-note positions remain readable.
2. Add `assert_span_at(span, src, substring)` with explicit expected/actual location detail.
   Keep `src_position` or direct assertions for tests needing ordered collections, another
   occurrence, end positions, file identity, or note-specific structure.
3. Migrate repeated `span is not None` plus `(start_line, start_col)` comparisons where the new
   helper improves readability.
4. Convert remaining sibling-module setup to `TestProgram`/`ModSrc`; use `write_mod`
   only where the loader itself is under test.
5. Keep direct construction of `src.SrcFile`, parsers for non-module grammar rules, and compiler
   internals where those objects are themselves the unit under test.
6. Ensure framework assertion failures name the relevant module/path and source substring.

### Validation

Run every diagnostic-focused file changed in the migration, especially `tests/test_errors.py`,
then the full suite and basedpyright.

### Commit boundary

Commit the focused diagnostic/readability cleanup independently from legacy API deletion.

## Task 7: Remove the legacy utility module and validate the final framework

**Files:**

- Delete `tests/util.py`.
- Modify any remaining imports or call sites.
- Modify `tests/harness.py`, `tests/test_harness.py`, and root `conftest.py` as final review finds.

### Work

1. Search the repository for every old helper name, `import util`, `from tests import util`, and
   direct test dependency on the standard-library filesystem. Resolve each intentional result.
2. Delete compatibility wrappers and `tests/util.py`.
3. Verify all test modules import helpers canonically as `from tests import harness` where a
   direct type or constructor is needed. Fixture-only tests need no harness import.
4. Add a focused test with two harness workspaces showing that they share only cached immutable
   standard-library LLVM text and keep every materialized artifact workspace-local. Do not add
   an xdist dependency solely for this check.
5. Review every new docstring and comment against `AGENTS.md`; remove narration and migration
   history that is not part of the API contract.
6. Re-run collection and compare counts with the baseline plus intentional additions.
7. Run final gates:

   ```bash
   uv run pytest -q
   uv run ruff check .
   uv run ruff format --check .
   uv run basedpyright
   uv run reuse lint
   git diff --check
   ```

### Acceptance

- No old utility interface remains.
- The entire existing suite uses the framework.
- Simple tests are concise, complex program layout is explicit, and artifact/process results
  are structured.
- Automatic standard-library linking, stream separation, signal checks, timeouts, shadowing,
  and failure reporting have focused coverage.
- No compiler or language behavior changed.

### Commit boundary

Commit legacy removal and final consistency fixes. Report that #94 appears complete, but do not
close it without the owner's instruction.

## Post-#94 handoff to issue #23

This is not implementation work for #94 and must occur only after #94 is approved, committed,
and the owner resumes #23.

1. Find the stash by its message rather than assuming `stash@{0}`.
2. Inspect its tracked, untracked, and base-parent contents before applying anything.
3. Restore the Markdown collector, collector tests, dependency changes, and other still-relevant
   #23 work selectively. Do not restore its obsolete `tests/util.py` implementation or broad
   import migration over the completed framework.
4. Adapt documentation `SrcFence` data to construct `harness.ModSrc` and
   `harness.TestProgram`, or remove the duplicate type if the Markdown-specific line metadata can
   remain alongside the shared module object cleanly.
5. Remove `std=` from the documentation fence schema and update the #23 spec/plan before
   continuing implementation, because the harness now links the bundled library automatically.
6. Replace temporary global-diagnostic isolation when #93 lands; until then keep it local to the
   documentation runner.
7. Run the #23 review workflow again for the restored and adapted implementation.
