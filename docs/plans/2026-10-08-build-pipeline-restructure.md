<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Build Pipeline and CLI Restructure — Implementation Plan

For: #124, #125, #126 (see [Issue tracking](#issue-tracking)).

Design: [Build pipeline and CLI restructure](../specs/2026-10-08-build-pipeline-restructure-design.md).
Design rationale lives there and is not repeated here.

Parent plan: [Diagnostics](2026-10-05-diagnostics.md). This sub-plan runs after its Task 3
(#113, done) and before its Task 4 (#115).

## Conventions

- One issue per task, landed in order: Task 1 (A, #124), Task 2 (C, #125), Task 3 (B, #126). The spec's
  [Sequencing](../specs/2026-10-08-build-pipeline-restructure-design.md#sequencing) explains
  why C comes before B.
- Each commit message carries a `For: #N: Title` line (see `AGENTS.md`). Filing the issues
  updates the #55 dependency graph in the same session.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run basedpyright`, `uv run reuse lint` and `git diff --check` before each commit.
- Follow `AGENTS.md` style: module-qualified imports, `Optional[T]`, `asserts` helpers,
  docstrings that don't point at plans or issues, and no docstrings that only restate a
  name.
- New files carry SPDX headers.

## Task 1 (#124): Restructure the pipeline and command line

No behaviour change: the same commands, options, outputs, diagnostics and exit statuses.
`leechc` and per-module compilation stay for now.

**Files:** new `src/leech/session.py`, `src/leech/program.py`, `src/leech/toolchain.py`,
`src/leech/cli/__init__.py`, `src/leech/cli/common.py`, `src/leech/cli/leech.py`,
`src/leech/cli/leechc.py`, `src/leech/cli/doctor.py`; removed `src/leech/driver.py`,
`src/leech/build.py`, `src/leech/cli.py`, `src/leech/doctor.py`; `compilation.py`,
`pyproject.toml`; `tests/harness.py`,
`tests/doc.py` and every test importing `driver`, `build` or `cli` (`test_build.py`,
`test_check.py`, `test_cli.py`, `test_diag.py`, `test_packages.py`, `test_recovery.py`,
`test_run.py`, `test_doctor.py`, `test_packaging.py`).

1. **`Session`.** Add `session.Session(diags, opt_level)` as specified. Change
   `compilation.Ctx(diags)` to `Ctx(session: Optional[Session] = None)`, keeping `ctx.diags`
   as a property reading `session.diags`, so the many `Ctx()` and `ctx.diags` uses don't
   change.
2. **`program.py`, with today's semantics.** `Program(root, *, qualified_name=None, entry)`
   (`entry` keyword-only with no default, as specified) and
   `Program.check(session) -> CheckedProgram`.
   - **One module's compilation** is a private function in `program.py`: today's
     `driver.compile_to_ir` (load, check, discover, raise the first error) and
     `lower_to_llvm_ir`, moved. The transitional "raise the first error" rule lives only
     here.
   - **`Program.check`** does what `build._compile_program` does today: compile the root,
     then every module the root's compilation loaded, each separately, stopping at the first
     module with an error. `CheckedProgram` holds each module's checked compilation and
     offers `module_llvm_irs() -> Mapping[str, str]`, generating each module's IR.
     `driver.compile_module`'s `generate_ir` flag is not carried over: `leech check` calls
     only `check`.
   - **Diagnostics, transitionally.** Each module's compilation gets a **child session**,
     `dataclasses.replace(session, diags=diag.Diags())`, which shares the invocation's
     options and has its own sink, so "did this module fail?" is that sink's `has_errors`,
     as today. Each child sink is merged into the invocation session's sink with
     `Diags.merge` when that module's compilation ends, even if it crashes, as
     `build._compile_module` does. This keeps today's file ranks, proofs, deduplication and
     first-error ordering. Task 3 removes child sessions and `Diags.merge`.
3. **`toolchain.py`.** Move `build.resolve_cc` and `build._link` into `Linker.from_env()` and
   `Linker.link(obj, exe)`; move `_prepare_out_dir`, `_emit_object` and `OUT_DIR_NAME` into
   an `OutputDir` class (removed again in Task 2). Errors are unchanged
   (`CcInvalidError`, `CcNotFoundError`, `LinkFailedError`, `BuildOutputError`).
   `OutputDir`'s object emission keeps converting IR that fails to link or verify into
   `diag.InternalError` until Task 2 moves that to `CheckedProgram.llvm_module()`.
4. **`cli` package.** `cli/common.py`: the `Command` and `OptionGroup` bases,
   `reporting_crashes(session)` (today's `driver.reporting_crashes`), `version_text` and the
   shared `-O` group. The `Command` base implements the spec's
   [error boundary](../specs/2026-10-08-build-pipeline-restructure-design.md#errors-and-the-command-boundary):
   it runs `run` inside `reporting_crashes`, catches `errors.UserError` (reporting it to
   `session.diags`) and `diag.ReportedError`, returns status 1 for them, then renders the
   diagnostics and exits. `cli/leech.py`: `CheckCommand`, `BuildCommand` and `RunCommand`,
   with `RootArgument` and today's argument splitting at `--`, validation and exit statuses.
   `cli/doctor.py`: `DoctorCommand`, with today's `doctor.diagnose` checks as its methods,
   building through `program` and `toolchain`.
5. **`leechc`, unchanged.** `cli/leechc.py` is `leechc` as one command with its options. It
   must keep compiling **only its file's module**, not every module that file imports, so it
   calls `program.py`'s private single-module compilation directly rather than
   `Program.check` (it is deleted in Task 2). Its severity-based exit status is kept by
   overriding the base's status computation.
6. **Entry points.** `pyproject.toml`: `leech = "leech.cli.leech:main"`,
   `leechc = "leech.cli.leechc:main"`; update `tests/test_packaging.py`.
7. **Harness and tests.** `tests/harness.py` builds and compiles through `Program` (keeping
   its methods and per-module `CompiledProgram`), and `emit_error_while_checking` patches
   the same discovery call. Tests importing `driver`, `build` or `cli` move to the new
   modules; assertions don't change. The `Compilation` dataclass's uses in tests become
   `Program`/`CheckedProgram` calls.
8. **Docs.** `AGENTS.md`'s file map (`driver.py`, `cli.py`, `build.py`) and the diagnostics
   spec's references to `driver.compile_to_ir`, `driver.reporting_crashes` and
   `build._compile_module` are updated.

**Tests:** the whole suite passes with assertions unchanged except for moved imports. New
tests:

- `Session` defaults; `Ctx(session).diags is session.diags`; an `OptionGroup` configuring a
  session from parsed arguments; the command list builds every subcommand's parser.
- The error boundary, through `leech build`: a type error, `CC` naming a missing command, a
  failing link (`CC` set to `false`), and an unwritable output each print a normal
  diagnostic, no internal-compiler-error banner, and exit 1.
- `leechc` compiles only its file: with a root importing a module whose public function
  has unreachable code, `leechc` on the root reports no warning (exit 0) while
  `leech build` on the same root reports the imported module's `UnreachableCodeWarning`.
- A multi-module program's diagnostics keep today's order and deduplication through child
  sessions.

**Acceptance:** `driver.py`, `build.py`, `cli.py` and `doctor.py` are gone; no
`generate_ir` parameter exists; `leech` and `leechc` behave exactly as before (the existing
CLI tests pass unchanged).

## Task 2 (#125): Remove `leechc` and add `leech build --emit`

**Files:** `src/leech/cli/leech.py`, `src/leech/cli/common.py`, `src/leech/toolchain.py`,
`src/leech/program.py`; removed `src/leech/cli/leechc.py`; `pyproject.toml`, `README.md`,
`AGENTS.md`, `src/leech/__init__.py` (its docstring names `leechc`), the build-tooling spec
(superseded note), the diagnostics spec and plan; `tests/test_cli.py`,
`tests/test_build.py`, `tests/test_run.py`, `tests/test_check.py`,
`tests/test_packaging.py`, `tests/test_doctor.py`.

1. **Remove `leechc`.** Delete `cli/leechc.py` and its script entry. Move `test_cli.py`'s
   behaviour tests that still apply (version text, `-O` validation, emit kinds, crash
   rendering) to `leech` equivalents; delete the rest (`--module-name`, `--entry`,
   severity-based exit status). Remove `Command.failure_status`, which exists only for
   `leechc`'s severity-based status: `Command.execute` returns 1 when `run` raises a user
   error.
2. **`OutputOptions`.** `--emit KIND[,KIND...]` with `exe` (default), `obj`, `asm`,
   `llvm-ir`, `llvm-bc`; `-o PATH` only with exactly one kind, otherwise a usage error
   (exit 2); an unknown kind is a usage error. Only `BuildCommand` uses the group.
3. **Whole-program output.** Every kind is produced from one LLVM module: in this task,
   `CheckedProgram.llvm_module()` links the per-module IR in-process (`ll_emit.link`) and
   optimizes it at the session's `-O`, exactly what `leech build` already does before
   emitting its object, and converts IR that fails to parse, link or verify into
   `diag.InternalError` (taken over from `OutputDir`). `llvm-ir` and `llvm-bc` print that
   module.
4. **Paths, staging and commit.** Default paths are `./<stem>`, `./<stem>.o`, `.s`, `.ll`,
   `.bc`. Remove `OutputDir` and `leech-out/`. Implement the spec's
   [stage-then-commit protocol](../specs/2026-10-08-build-pipeline-restructure-design.md#outputs-d12)
   in `toolchain.py`: stage every requested kind in a `tempfile.TemporaryDirectory` in the
   fixed order, then copy each to a `tempfile.mkstemp` file in its destination's directory
   and `os.replace` it, removing any leftover temporary on failure. The executable keeps its
   mode bits. Replace `build._link`'s fixed `.{name}.leech-tmp` name with these unique
   names. `Linker.from_env()` is called only when `exe` is requested.
5. **`leech run`'s executable (D14).** Compute `<cache>` (absolute `XDG_CACHE_HOME`, else
   `~/.cache`), create `<cache>/leech/run/<sha256 of the root's absolute path>/` with mode
   `0o700`, commit the executable there through the same protocol, then `execv` it as
   today.
6. **`leech doctor`** builds with `-o` inside its own temporary directory.
7. **Docs and issues.** `README.md` and `AGENTS.md` describe `leech build`, `--emit` and the
   current-directory outputs instead of `leechc` and `leech-out/`; `src/leech/__init__.py`'s
   docstring names the `leech` command. Add a note at the top of
   `docs/specs/2026-10-02-build-tooling-design.md` that `leechc`, `leech-out/` and
   per-module compilation are superseded by this design. Update the diagnostics spec and plan
   so their options target `leech check`/`build`/`run` only. Edit #108, #117, #119, #120 and
   #121 to drop `leechc` (`leechc --explain` becomes `leech explain` only), and #106 to note
   that outputs go to the current directory.

**Tests:**

- `leech build` writes `./<stem>` and nothing else in the current directory or beside the
  root; no `leech-out/` is created.
- Each kind alone, with and without `-o`; `--emit llvm-ir,exe` writes both, and the IR is
  the program's (contains the root's and `std`'s symbols); `-o` with two kinds and an
  unknown kind are usage errors (exit 2); a repeated kind is accepted.
- `--emit obj,exe` links the requested object; `--emit exe` leaves no object anywhere
  outside the temporary directory, which is removed afterwards (assert via a patched
  `tempfile.TemporaryDirectory` or by listing the temp root before and after).
- A failed build (type error) writes nothing; `-o` naming a directory reports
  `BuildOutputError` with no internal-error banner.
- With existing `./<stem>.ll`, `./<stem>.o` and `./<stem>` files, `--emit llvm-ir,obj,exe`
  with a failing link (`CC=false`) leaves all three byte-for-byte unchanged and no temporary
  file beside them.
- With `CC` naming a missing command, `--emit llvm-ir`, `llvm-bc`, `asm` and `obj` each
  succeed; `--emit exe` reports `CcNotFoundError` as a normal diagnostic (exit 1).
- `leech run` still passes the program's arguments, exit status and signal through, and no
  executable appears in the current directory. A second run of the same root replaces the
  cached executable; a relative `XDG_CACHE_HOME` is ignored in favour of `~/.cache`; two
  concurrent builds committing the same cache path (two threads or processes) both succeed
  and leave a complete executable and no temporary files.
- `leech check` and `leech doctor` write nothing in the current directory.

**Acceptance:** `leechc` is not installed; `leech build --emit` covers every output
`leechc --emit` produced, for the whole program; no command writes a file the user didn't
ask for outside the temporary directory and `leech run`'s cache.

## Task 3 (#126): Check and generate a whole program in one compilation

**Files:** `src/leech/program.py`, `mono.py`, `codegen.py`, `ir_module.py`, `diag.py`,
`toolchain.py` (if it still links several modules); `tests/harness.py`, `tests/doc.py`, and
the tests that read per-module IR or depend on per-module compilation (about 30:
`test_builtins.py`, `test_entry.py`, `test_generic_structs.py`, `test_generic_fns.py`,
`test_vars.py`, `test_call.py`, `test_import.py`, `test_check.py`, `test_recovery.py`,
`test_build.py`, `test_diag.py`, `test_unions.py`, `test_packages.py`).

1. **One compilation.** `Program.check` makes one `Ctx(session)`, loads the root, runs
   `ModLoader.check_declarations`, designates the entry point (with `entry`), and discovers
   instances once for the program, then raises if there is an error. Remove the per-module
   compilations, their child sessions, and `Program`'s `qualified_name` parameter (the
   root's name is its stem; naming a root otherwise goes with per-module compilation).
2. **Discovery for the program.** `mono.discover(program)` starts from every loaded module's
   public non-generic functions and module variables, and the entry point, and defines every
   concrete instance with a body. Remove `_is_imported_fn_instance`,
   `MonoResult.imported_fn_instances`, and `Mod.discover_instances`, `Mod.instances` and
   `Mod._instances`; `CheckedProgram.instances` holds the result.
3. **One LLVM module.** `codegen.Compiler(checked_program)` declares and defines every item
   of every module once (replacing `_program_items`' local-plus-public-imported walk), every
   discovered instance, and the entry point. Names, linkage and `source_filename` (the
   root's file) are as the spec describes. `CheckedProgram.llvm_ir()` returns its text;
   `llvm_module()` parses and optimizes it, replacing Task 2's in-process link.
4. **Extern declarations (closes #28).** While checking, group every loaded module's
   `extern fn` declarations by symbol name. Declarations with the same signature share one
   LLVM declaration in codegen; different signatures report a new
   `ConflictingExternDeclError` at the later declaration, with a note at the earliest one
   with a different signature, where "later" is the spec's order (file load order, prelude
   first, then position in the file). Add it to `errors.py` with its message test in
   `tests/test_errors.py`.
5. **Remove cross-compilation merging.** Delete `Diags.merge` and its tests; keep structural
   deduplication and `note_file`.
6. **Harness.** `CompilerHarness.compile` returns one program IR (`CompiledProgram.llvm_ir`,
   replacing `mods[name].llvm_ir`); `run` emits and links that one module, and
   `_bundled_mod_llvm_ir` is removed. Update tests reading `mods["main"].llvm_ir`, checking
   assertions on absence against `std`'s symbols.
7. **Docs.** The diagnostics spec's discovery section (discovery per program, results on
   `CheckedProgram`), `docs/specs/2026-08-07-mono-module-design.md`'s description of roots,
   and #49's note about `Compiler.compile`.

**Tests:**

- A three-module program is loaded, checked and discovered once: `ModLoader.load` parses
  each user module once and `check_declarations` checks each body once (count with a
  patched `typcheck.TypCheck.check_fn` or an analysis-unit counter).
- A diagnostic in a module imported by two others is reported once without merging.
- A private function reached only through another module's public function is generated.
- A generic instance used by two modules is defined once.
- #28's reproducer (redeclaring the prelude's `write`) compiles and runs; two unrelated
  modules declaring `malloc` identically compile; differing signatures report
  `ConflictingExternDeclError` at the declaration in the later-loaded module, with the note
  at the earlier one, including when the earlier declaration is the prelude's.
- `leech build` of a multi-module program produces the same program behaviour (the
  existing run tests); the emitted IR contains every module's symbols once.
- Discovery still recovers per request (the existing `test_check.py` discovery tests, moved
  to the program).
- Measure the suite's run time before and after, and record it in the commit message.

**Acceptance:** `leech build` and `leech check` make one `compilation.Ctx`; no module is
parsed or checked twice; `Mod` has no discovery state; `Diags.merge` is gone; IR for a
program is one LLVM module.

## Issue tracking

Done on 2026-10-08, when the plan was approved:

| Task | Issue | Title | Blocked by |
| --- | --- | --- | --- |
| 1 (A) | #124 | Restructure the build pipeline and command line into session, program, toolchain and cli modules | none |
| 2 (C) | #125 | Replace `leechc` with `leech build --emit`, writing outputs to the current directory | #124 |
| 3 (B) | #126 | Check and generate a whole program in one compilation | #124 |

#126 closes #28, whose fix it needs.

- #55: added `124 --> 125`, `124 --> 126` and `126 --> 105`. #125 before #126 is a sequencing
  preference, recorded as a soft note.
- #105: removed "Let one `ModLoader` codegen every module".
- #28: linked to #126, which closes it.
- #108, #117, #119, #120, #121: commented that `leechc` is being removed (#125), so their
  options go on `leech` only; their bodies are edited when #125 lands.
- #106: commented that `leech build` will write to the current directory, and that a
  manifest may introduce a project output directory.
- #49: commented that, after #126, validation-only requests are still drained into the
  program's instances, pointing at the spec's
  [Future work](../specs/2026-10-08-build-pipeline-restructure-design.md#future-work).

The parent plan's "Task 3b" entry points here; Task 2 changes its shared-conventions bullet
("Both `leechc` and `leech check`/`build`/`run` accept every diagnostics option") to name
`leech check`/`build`/`run` only.
