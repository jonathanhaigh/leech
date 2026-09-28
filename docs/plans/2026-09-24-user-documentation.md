<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# User Documentation with Tested Leech Examples — Implementation Plan

For: #23: Write user documentation with automatically tested code snippets

Design: [User documentation design](../specs/2026-09-24-user-documentation-design.md).

Only the planning documents are written in this planning session. The tasks below are for
the later implementation, after manual approval and a separate instruction to implement.

## Task 1: Make separately compiled module names available from the CLI

**Files:** `src/leech/driver.py`, `src/leech/parse.py`, `src/leech/leech.lark`,
`tests/test_cli.py`, `tests/test_parsing.py`, `tests/test_packages.py`,
`docs/specs/2026-09-24-user-documentation-design.md`, and this plan.

- Add `--module-name NAME`; validate nonempty Leech identifier segments joined by `::`.
  Reject a reserved final segment because that is the name an import binds; allow reserved
  intermediate filesystem path segments. Malformed values produce argparse usage text on
  stderr and exit code 2. Parse the identifier sequence with the existing Lark grammar and
  use `reserved.is_reserved` rather than duplicating either language rule. Leave the
  implicit filename-stem default unvalidated, preserving existing behavior even for
  unusual stems.
  Preserve `-o` behavior and compiler diagnostics.
  Pass the chosen name through `compile_to_llvm_ir` without changing import resolution
  or the Python API. Set argparse's `prog` to the actual console-script name, `leech`.
- Test a default-named module, an explicitly qualified module, and malformed names.
  Test that `main.leech` (or `--module-name main`) emits the unmangled `main` entry symbol,
  whereas a differently named library module does not. Compile a `std::io` user program
  and its imported source with the CLI and confirm that linking resolves `std::io`
  symbols. The prelude remains separately compiled.
- Keep this option limited to qualification; module search paths belong to
  [#41](https://github.com/jonathanhaigh/leech/issues/41).

**Done when:** the CLI can emit an object-compatible `std::io` module matching the names
expected by `import std::io`, while existing CLI tests remain green.

## Task 2: Build and test the Markdown case collector

**Files:** `pyproject.toml`, `uv.lock`, root `conftest.py` (or an equivalent pytest plugin
loaded from the repository root), `tests/doc.py`, and focused collector tests in
`tests/test_doc.py`.

- Declare `markdown-it-py` as a direct development dependency (it is currently transitive
  through `rich`). Parse `README.md` and `docs/guide/**/*.md` at collection time; do not
  collect `docs/specs/` or `docs/plans/`.
  Create one pytest item per page-local `test` ID, with a name containing the root fence
  line; pytest supplies the Markdown path through the item's parent node. Ensure ordinary
  `uv run pytest` discovers these items.
- Implement the precise fence contract from the design: metadata keys, one root file,
  group files, optional module-name overrides, modes, expected output/diagnostic blocks,
  bounded relative `.leech` paths, and collection failures for invalid or orphan metadata.
  Parse Markdown structurally so fences inside other fences and non-Leech code are not
  misclassified. Validate `error=` / `warning=` against concrete `errors.UserError`
  subclasses at collection time. Reject known keys on the wrong mode or fence, including
  `module=` on the root, `warning=` with `mode=error`, `std=` everywhere, `exit=` outside
  `mode=run`, and `newline=no` on a diagnostic fence.
- Convert every validated source fence directly to `harness.ModSrc`, preserving its source,
  qualified module name, and relative path in one object. Build a `harness.TestProgram` for
  execution. Check a nested transitive example using `module=sub::helper` on its file fence
  so the emitted name matches the name assigned during relative import resolution.
- For `run`, use `CompilerHarness.check` or `check_signal`; bundled standard-library modules
  and the prelude are linked automatically. For `compile`, use `CompilerHarness.compile`.
  For `error`, which is single-file, compile the same `TestProgram` and assert the exact
  raised `errors.UserError` subclass, its `ERROR` severity, a nonempty excerpt in its primary
  message, and no unrelated registered diagnostics.
- Add a narrowly local context manager in `tests/doc.py` to reset and restore
  `errors._errors` and `_error_level` around each case, even on failure. Issue #93 will
  replace this temporary process-global isolation with per-compilation state. Successful
  `run`/`compile` cases must leave no registered diagnostics unless they declare
  `warning=...`; that mode is single-file and requires exactly one matching warning and its
  diagnostic excerpt.
  Normalize diagnostic fences by removing one structural final newline only. Normalize
  `output=` using the stated `newline=no` rule, preserving every other byte.
- Wrap execution errors with the page, test ID, and original fence line; report the
  relative temporary module name as well when relevant. Do not turn unexpected Python
  exceptions into a successful expected-error case. Task 3 documents the annotation
  syntax for contributors in `docs/guide/index.md`; put illustrative Leech fences inside
  an outer `markdown` fence, as in the design, so they are not accidentally live snippets.
- Test successful single- and multi-module cases, std linking, empty/nonempty output,
  output without a final newline, nonzero and signal exit, compile-only declarations,
  warnings, expected errors, and nested transitive imports. Negative collector tests
  cover unknown/duplicate keys, duplicate files/IDs, unsafe paths, missing root or
  diagnostic, orphan or misordered result fences, nonconcrete/unknown diagnostic classes,
  obsolete `std=` metadata, invalid key/mode combinations, root `module=`, broken
  Leech syntax, unexpected warnings, a wrong error class/message, and leaked diagnostic
  state.

**Done when:** a deliberately broken fixture page fails collection or execution with its
source location, and every valid public fence belongs to a collected case.

## Task 3: Write the quickstart and navigation

**Files:** `README.md`, `docs/guide/index.md`, `docs/guide/getting-started.md`,
`tests/test_doc_build.py`, and `AGENTS.md`.

- Give the README a concise project description, current status, first-run requirements,
  and links to the quickstart and tour. Keep detailed instructions in the guide. Add the
  contributor note for test annotations to the index, using an outer `markdown` fence for
  illustrative Leech examples.
- Write a source-checkout installation path for Python 3.14 and uv (`uv sync`) and list
  `llvm-link`, `lli`, `llc`, and a C linker. State the tested x86-64 Linux target and how
  to check tool availability. Do not imply cross-platform code generation.
- Start with a complete `std::io` Hello World saved as `build/main.leech`; the root must be
  named `main` to emit the linker entry point. First show a setup command block creating
  `build/`, then tell the reader exactly where to save the displayed Leech program.
  Show compiling its root, the bundled `std::io` source with `--module-name std::io`, and
  the prelude with its correct module name; link with `llvm-link`, run with `lli`, then
  produce an object with `llc` and link a native executable. Explain the role of each
  file and why imported bodies need linking.
- Keep shell build artifacts under `build/`, which `.gitignore` already ignores. Use a
  `LEECH_BUILD_DIR` variable defaulting to `build` so a smoke test can redirect outputs to
  `tmp_path`. Use exactly three ordered, unique Bash fences on the quickstart page:
  `test=quickstart-setup`, `test=quickstart-ir`, and `test=quickstart-native`. Each defines
  the build-directory default independently, so a reader can copy it into a fresh shell.
  The smoke test parses and executes those literal blocks in guide order from the checkout
  root; it must not maintain a parallel transcription. Missing, duplicate, reordered, or
  unknown `bash test=...` phases fail the test. No other Bash fence is executed.
- Give the Hello World `leech` fence the fixed ID `test=quickstart-hello`. The smoke test
  selects that exact fence, checks `file=main.leech`, and writes its content after the
  setup phase to `LEECH_BUILD_DIR/main.leech`. It then runs the IR and native phases.
  Run each Bash fence in a fresh shell with the same temporary `LEECH_BUILD_DIR` environment
  value, so the test detects missing per-fence initialization.
- Include a second local multi-module program and a short troubleshooting section for
  missing LLVM tools, unresolved symbols, mismatched module names, compiler errors, and
  native PIE relocation errors. Explain that `llc` currently emits a static-relocation
  object and `cc -no-pie` avoids a PIE-default linker rejection. Mention
  `lli --disable-symbolication` for aborting examples, matching the test helper.
  Make the command snippets and file contents copyable from a fresh checkout.
- The smoke test checks output from `lli` and the native executable. Keep the `lli` phase
  within the existing required `llvm-link`/`lli` test environment. Skip the native phase
  with a clear reason when `llc` or `cc` is unavailable in an ordinary test run. Add
  `LEECH_REQUIRE_NATIVE_DOCS=1` to turn that skip into a failure for the required
  x86-64 Linux validation run; record this in `AGENTS.md`. Include SPDX headers on new
  repository files.

**Done when:** a reader on the supported target can copy the quickstart commands into a
fresh checkout and build both forms of the Hello World program.

## Task 4: Write the language overview and tour

Every chapter uses current syntax from `src/leech/leech.lark`, standard-library sources,
and the closest feature tests. Each has at least one complete tested program, links back
to the guide index and onward to the next chapter, and links notable gaps to real open
issues. Use `mode=run` as the normal teaching path, `mode=compile` for non-executed
declarations, and `mode=error` with a verified message excerpt for a useful failure.
Do not put unimplemented syntax in a `leech` fence.

### Task 4A: Position the language and teach the basics

**Files:** `docs/guide/language-at-a-glance.md`, `docs/guide/tour/01-basics.md`.

- Compare Leech's familiar `fn`/`let`/`trait`/`impl` style with Rust, its compile-time
  facilities with Zig, `extern`/native linking with C and C++, and its tagged unions with
  Swift's payload-bearing enum cases. Do not imply Leech has Rust ownership, broad C++
  interoperability, or a mature standard library. Keep this overview brief.
- Teach function signatures and return values, integer and boolean types, literals,
  `let` and mutable bindings, expressions, and a representative legal/illegal coercion.
  Ensure at least one runnable example and one checked diagnostic.

**Checkpoint:** the overview and basics page pass the docs collector and accurately
describe the current type syntax and coercions.

### Task 4B: Teach control flow and sum types

**File:** `docs/guide/tour/02-control-flow.md`.

- Cover blocks, `if`, `while`, labeled `break`/`continue`, `match` patterns, `never`,
  C-like enums, and tagged unions. Show the enum/union distinction in executable code.
  Document exhaustiveness and reachability behavior at a level useful for a tour.
- Show a prelude `assert`/`panic` example with `exit=SIGABRT` if runtime failure makes the
  feature clearer; explain that the displayed program output is a prefix of `lli` output.

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

### Task 4D: Teach imports, visibility, and the small standard library

**File:** `docs/guide/tour/04-modules-and-stdlib.md`.

- Cover local and nested imports, module visibility, `extern`, prelude names, `std::io`
  `print`/`println`, and `std::mem` allocation/deallocation. Show that
  `import pkg::a;` binds `a`, and imports resolve relative to the importing file.
- Use a multi-module case with `module=` on a transitive nested helper to exercise the
  actual import-qualified name. Explain that ordinary imported function definitions need
  separate linking while reachable generic instances are emitted at their use sites.

**Checkpoint:** the nested example links and runs, `std::io::print` exercises output
without a newline, and the page never suggests the prelude is a `std::` module.

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
- Inspect the working-tree diff for generated LLVM files or unrelated changes. In the
  eventual implementation PR, explain the CLI behavior, show the `std::io` build commands,
  and list validation results. The owner explicitly narrowed #23's original
  `docs/**/*.md` acceptance to the README and public guide; note that exception in the
  PR/issue review. Treat #23 as complete only after the user documentation and collector
  both land; ask the owner before closing it. If #23 is closed, update issue #55 in the
  same session according to the repository's dependency-graph rule.

**Done when:** the full suite and repository quality gates pass, the documentation build
path has been exercised on x86-64 Linux, and every public Leech fence is accounted for.

## Sequence and risks

Task 1 enables the guide's CLI path. Task 2 establishes the test contract before writing
many examples. Task 3 proves the end-to-end build, then Task 4 broadens coverage; Task 5
is the final gate. Tasks 3 and 4 can be written in parallel once Tasks 1 and 2 are stable,
provided both writers use the same fence contract and issue-link convention.

The largest practical risks are the hardcoded target, the need to compile imported bodies
separately, and silent collector omissions. The quickstart labels its supported platform,
the CLI/link smoke test covers the actual symbols, and collector tests deliberately inject
broken/malformed fences. No migration of historical plans/specs is included: their existing
Leech fences remain outside the public-guide test boundary approved for this work.
