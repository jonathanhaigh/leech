<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Build Tooling: `leechc`, `leech`, and Local Installation — Implementation Plan

For: #95–#104 and #41: Wire up a module search path.

Design: [Build tooling design](../specs/2026-10-02-build-tooling-design.md). Design
rationale lives there and is not repeated here.

Only the planning documents are written in this planning session. The tasks below are for
the later implementation, after manual approval and a separate instruction to implement.

## Global constraints

- Each task is one issue and one or more commits. Each commit references its issue with a
  `For: #N: Title` body line (see `AGENTS.md`). Creating the B-issues updates the #55
  dependency graph in the same session.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run basedpyright`, and `uv run reuse lint` before each commit.
- Follow `AGENTS.md` style: module-qualified imports, `Optional[T]`, no multi-line
  conditional expressions, and docstrings that do not point at plans or specs.
- New user errors are `errors.UserError` subclasses with spans where a source location
  exists. Driver-level failures without a source location (missing `cc`, link failure,
  invalid `ROOT`) are reported through one small driver error type that is rendered as
  `ERROR: …`. They are never Python tracebacks.
- Tests that run `cc` fail with a clear message when it is missing, matching how the
  harness treats `lli` today. They are not silently skipped.

## Suggested order and parallelism

```text
#96 ┄┄► #97 ──┐
  ├┄┄► #98 ───┼──► #100 ──► #101
  └┄┄► #41    │      ├──► #102
#99 ──────────┘      ├──► #103
#95 (any time)       └──► #104 (also needs #97, #98)
```

Solid arrows are hard dependencies. Dotted arrows are sequencing preferences: #97, #98 and
#41 all edit the `driver.py` argument parser, so landing the rename first avoids churn.
#95 and #99 are independent. #41 and #100 compose, but neither blocks the other. Whichever
lands second wires `--search-path` into the other, and adds its build-level tests.

## Task 1 (#96): Rename the compiler command to `leechc`

**Files:** `pyproject.toml`, `src/leech/__init__.py`, `src/leech/driver.py`,
`tests/test_cli.py`, `AGENTS.md`.

- Replace `[project.scripts] leech = "leech:main"` with `leechc = "leech.driver:main"`.
  Rename `driver.run` to `driver.main`, and delete `leech.main` and its import from
  `__init__.py`. Set argparse `prog="leechc"`, with the description
  "Leech compiler: compile one module".
- Add `--version`. Format it in a helper shared by both commands (in `driver.py` for now),
  which reads `importlib.metadata.version("leech")`, `llvmlite.__version__`,
  `llvmlite.binding.llvm_version_info` and `target.TRIPLE`.
- Update `tests/test_cli.py`'s `run_cli` and its usage-text assertions
  (`usage: leech ` becomes `usage: leechc `). Run `uv sync` so the new script exists in
  `.venv/bin`.
- Update `AGENTS.md` ("`uv run leechc input.leech -o output.ll` …").
- Do **not** add a `leech` script yet. The name stays free until #100.

**Validation:** CLI tests pass. `uv run leechc --version` prints the expected line, which a
new test asserts against `importlib.metadata`.

## Task 2 (#95): Package the compiler for local installation with uv

**Files:** `pyproject.toml`, `uv.lock` (if the build bound changes), `README.md`,
`tests/test_packaging.py` (new).

- Bound the dependency `llvmlite>=0.47.0,<0.48` (the design's "llvmlite compatibility"
  section), then refresh `uv.lock`.
- Add `license = "MPL-2.0"` and `license-files = ["LICENSE", "LICENSES/*.txt"]`. Add
  classifiers: `Private :: Do Not Upload`, `Programming Language :: Python :: 3.14`,
  `Operating System :: POSIX :: Linux`. Give `description` a sentence.
- Run `uv build` into a scratch directory. If uv warns about the `uv_build` bound, raise it
  to the installed minor version.
- New test: run `uv build --wheel --out-dir tmp_path` (through `subprocess`, using `uv` from
  `PATH`), open the wheel, and assert the following:
  - it contains `leech/leech.lark`, every `leech/std/*.leech`, and both license files
    under `.dist-info`;
  - `entry_points.txt` lists `leechc`, plus `leech` once #100 lands (extend the test then);
  - `METADATA` has `Classifier: Private :: Do Not Upload`, `License-Expression: MPL-2.0`,
    and a `Requires-Dist` for llvmlite with the `<0.48` bound.

  Also build the sdist and assert that it contains `LICENSE`.
- `README.md`: an "Install" section with the uv commands from the design (install,
  editable, reinstall, wheel, uninstall, `update-shell`) and a note that publishing is
  deliberately blocked.
- Manual checks, recorded in the PR or commit description:
  - Editable: `uv tool install --editable .`, then `leechc --version` from outside the
    checkout, then `uv tool uninstall leech`.
  - Non-editable wheel, isolated:
    `UV_TOOL_DIR=$(mktemp -d) UV_TOOL_BIN_DIR=$(mktemp -d) uv tool install dist/leech-*.whl`.
    Then run `$UV_TOOL_BIN_DIR/leechc --version` from outside the checkout. Repeat this check
    in #101 with `leech run hello.leech`, because this path resolves dependencies from wheel
    metadata, not from `uv.lock`.

**Validation:** The packaging test passes. `uv run reuse lint` passes.

## Task 3 (#97): Replace the `main`-module entry rule with `leechc --entry`

**Files:** `src/leech/ir_module.py`, `src/leech/ir_loader.py`, `src/leech/mono.py`,
`src/leech/codegen.py`, `src/leech/driver.py`, `src/leech/errors.py`, `tests/harness.py`,
`tests/test_cli.py`, `tests/test_loader.py` (line 85 asserts `not fn.is_main`),
`tests/test_prelude.py` (line 110 selects `main` with `fn.is_main`), the tests asserting
`define i32 @"main"` (`test_generic_fns.py` and others found by grep), and
`tests/test_entry.py` (new).

- Add `entry: bool = False` to `driver.compile_to_ir` and `compile_to_llvm_ir`. Thread it
  to the root `Mod` (for example `ModLoader.load(..., entry=True)` for the root only).
  The loaded module must expose its entry function, or diagnose its absence, after
  `check_declarations`.
- Entry validation: `main` must be a `SrcFnSymbol` with a body, non-generic, with signature
  `() i32`, and either access. Add errors for:
  - a missing `main` (spanless, naming the module file);
  - a `main` that is not a function (span at the item);
  - an `extern fn main` or a generic `main` (span at the declaration);
  - a wrong signature (span at `main`'s signature).
- Remove `SrcFnSymbol.is_main`, the unqualified branch in `FnInstance.qualified_name`, and
  `qualify_name = self.name != "main" or …` in `Mod`. `mono._discover_fn_instances` seeds
  the entry function explicitly instead of `fn.is_main`.
- In codegen, when the module has an entry function, emit
  `define i32 @main() { %r = call i32 @"<qualified main>"() ret i32 %r }` with external
  linkage. A private `main` keeps `private` linkage and is still callable from the wrapper.
- Add `--entry` to `leechc`.
- Harness: `CompilerHarness.compile` passes `entry=True` for `program.root` when the program
  is going to be run. Simplest: always pass it for the root, and drop the requirement that
  a run root is named `main` from `CompilerHarness.run`. Decide whether `TestProgram`'s
  root keeps the name `main` by default. Keeping it is fine, because the name no longer
  matters.
- Update the IR assertions: `define i32 @"main"` checks become checks for the wrapper and
  for `@"main::main"`. In `test_loader.py`, drop the `is_main` assertion. In
  `test_prelude.py`, select `main` by name. In `test_cli.py`, replace
  `test_cli_module_name_controls_entry_symbol` with `--entry` tests. Rewrite
  `test_cli_compiles_linkable_std_io_program` to pass `--entry`.
- New tests (`tests/test_entry.py`): a private `main` runs. A root not named `main` runs.
  A module named `main` without `--entry` emits no `@main`. Missing `main`,
  `main(x: i32) i32`, `main() void`, a generic `main`, `extern fn main() i32;`, and a
  module variable named `main` each produce their diagnostic.
  An entry module can still be imported by name from another module compiled separately.

**Validation:** full suite. Grep confirms that no `is_main` or `"main"` name special case
remains in `src/leech` or `tests/`.

## Task 4 (#98): Emit bitcode, assembly, and objects from `leechc`, with optimization levels

**Files:** `src/leech/ll_emit.py` (new), `src/leech/driver.py`, `tests/test_cli.py`,
`tests/test_ll_emit.py` (new).

- `ll_emit` provides small, typed functions:
  - parse textual IR into `llvmlite.binding.ModuleRef`;
  - link a sequence of parsed modules into the first one and verify it;
  - optimize at a level from 0 to 3 with `create_pass_builder` and
    `PipelineTuningOptions(speed_level=n)` (a no-op at 0);
  - a cached `TargetMachine` for `target.TRIPLE` with `reloc="pic"` and
    `codemodel="default"`;
  - emit the formats `llvm-ir`, `llvm-bc`, `asm`, and `obj` as `bytes` or `str`.

  Initialize the native target and asm printer once, lazily.
- Model the emit kinds as an `enum.Enum` with each kind's file suffix. `leechc` adds
  `--emit` (default `llvm-ir`) and `-O {0,1,2,3}` (default 0). It derives the default output
  suffix from the emit kind, and requires `-o` when the derived path equals the input.
  Write binary formats in binary mode. `--emit=llvm-ir` at `-O0` writes the codegen string
  directly, without an llvmlite round trip, so existing output is byte-identical (the
  existing CLI tests pin this).
- Tests:
  - each format has the expected magic bytes or prefix: `BC\xc0\xde`, `\x7fELF`, and text
    with `.text`;
  - an `--emit=obj` program, plus `--emit=obj` std modules, links with `cc` into an
    executable that runs correctly, without `-no-pie`;
  - `-O2` IR differs from `-O0` IR for a function with an obvious optimization (for
    example, a constant-folded expression);
  - the `-o` derivation rule holds for every kind.

**Validation:** full suite. Manually check that `leechc x.leech --emit=asm` produces readable
assembly.

## Task 5 (#41): Wire up a module search path

**Files:** `src/leech/ir_loader.py`, `src/leech/driver.py`, `tests/test_cli.py`,
`tests/test_packages.py`.

- Update #41's body with the decided rules before starting (see the design's
  "Module search path" section).
- Add a `driver.search_roots(cli_paths)` helper: CLI paths, then `LEECH_PATH` entries split
  on `os.pathsep`, with empty entries ignored. Each entry is resolved against the current
  working directory. A missing directory produces a warning, once, and is dropped. The
  helper returns the roots and the warnings, and does not register them. `leechc`
  registers the warnings, so its severity-based exit status becomes 1. `leech` prints them
  once, before compiling any module (the design's pipeline step 1). Pass the result as `ModLoader(extra_search_roots=…)`, which already searches
  after the importer's directory and the bundled root. Remove the `TODO` in
  `compile_to_ir`, and accept `search_roots` there.
- Add `--search-path DIR` (repeatable, `action="append"`) to `leechc`.
- Tests:
  - a module outside the importer's directory becomes importable through the CLI and
    through `LEECH_PATH`;
  - CLI paths take precedence over `LEECH_PATH`, and the first matching root wins;
  - a `std/io.leech` in a search root does **not** shadow the bundled `std::io`;
  - a missing directory produces a warning;
  - a nested `pkg::mod` resolves inside a search root.

**Validation:** full suite. Update the #55 soft-relationship note for #41.

## Task 6 (#99): Detect modules reached under conflicting qualified names

**Files:** `src/leech/ir_loader.py`, `src/leech/errors.py`, `tests/test_loader.py`,
`tests/test_packages.py`.

- Add a frozen `LoadRequest` dataclass to `ir_loader`. It holds the resolved path, the
  qualified name, and an origin: `ROOT`, `PRELUDE`, or `IMPORT` with the importing
  `ast.Path` span. `resolve_import` keeps the span available to its caller, so the import
  origin can be recorded. `ModLoader.load` takes the origin, and appends a request on
  **every** call, including path-deduplicated hits, before the cache check.
- Expose the requests as a read-only, ordered sequence. Add `ModLoader.validate_names()`,
  which raises one of two new `UserError`s for the design's conflicts:
  - one file, two names: the primary span is the later import, with a note at the first
    request;
  - one name, two files: the primary span is the later request, naming both files, with a
    note at the other request.

  A request without a span (root or prelude) gets a spanless message or note that names
  its origin. This is a loader-level check, so `leechc` can also call it after loading. If
  it does, `leechc` reports these conflicts too, which is harmless and catches the same
  link failures earlier.
- Tests:
  - one file imported as `c` from `a/b.leech` and as `a::c` from the root;
  - a root named `prelude.leech` (spanless root against the bundled prelude);
  - two different files that both claim `x`, if constructible through search paths once
    #41 lands;
  - a path-deduplicated re-import under the *same* name is not an error;
  - each case asserts the primary span and the note spans with the harness span helpers.

**Validation:** full suite.

## Task 7 (#100): Add `leech build` to build a program and its imports into an executable

**Files:** `src/leech/build.py` (new), `src/leech/cli.py` (new), `src/leech/errors.py`,
`pyproject.toml`, `tests/test_build.py` (new), `tests/test_packaging.py`, `AGENTS.md`.

- `pyproject.toml`: add `leech = "leech.cli:main"`, and extend the packaging test.
- `errors`: add a small public API that takes and clears the registered diagnostics and
  their level, for the driver's per-compilation collection. Its docstring states that it
  exists because diagnostics are process-global, without referring to #93.
- `build.py`:
  - validate `ROOT`: it exists, has a `.leech` suffix, and its stem is a single
    identifier (reserved words are allowed). Each failure is a usage error with exit 2;
  - compute the search roots once and print their warnings;
  - discover the graph, call `loader.validate_names()` **before** `check_declarations`,
    then check declarations;
  - compile each module with `driver.compile_to_llvm_ir(..., entry=is_root, search_roots=…)`,
    reusing the discovery loader's result for the root where practical;
  - collect diagnostics, and deduplicate them by a structural key. The key is the class,
    plus each message's (level, text, resolved path, start and end line and column, or
    `None`) for the primary message and every extra message, in order. Do not use span
    objects in the key: `SrcSpan` and `SrcFile` compare by identity, and every compilation
    creates fresh ones;
  - write IR under `leech-out/<stem>.obj/` (`::` becomes a path separator), creating
    regular files `leech-out/.gitignore` (`*\n`) and `leech-out/CACHEDIR.TAG` if they
    are absent. `CACHEDIR.TAG` starts with exactly
    `Signature: 8a477f597d28d172789f06886806bc55\n`, followed by
    `# This file is a cache directory tag created by leech.\n` and
    `# For information about cache directory tags see https://bford.info/cachedir/\n`;
  - link, optimize, and emit through `ll_emit`;
  - resolve `$CC` with one function, `build.resolve_cc()`, which #103 later shares with
    `doctor`. An unset, empty or whitespace-only `CC` means `cc`. A `ValueError` from
    `shlex.split` is a driver error naming `CC` and quoting its value. So is an executable
    that `shutil.which` cannot find, and it is detected before any compilation;
  - link the executable through `subprocess.run`, capturing stderr. A non-zero exit
    produces a driver error with the command and stderr.

  Return a small result value (absolute executable path, diagnostics, success) so `run`
  and `check` reuse it. Keep printing in `cli.py`.
- `cli.py`: argparse with subparsers (`build` for now) plus `--version`. Exit 0 on success
  including warnings, 1 on failure, and 2 on usage errors. Diagnostics use
  `errors.TextErrorRenderer`.
- Tests (`tests/test_build.py`, invoking the installed `leech` script with `subprocess`):
  - single-file hello world;
  - a multi-module program with a nested package and `std::io` plus `std::mem`;
  - a root not named `main`, and a root with a private `main`;
  - a root given as a path outside the current directory (`leech build sub/app.leech`
    writes `sub/leech-out/app`);
  - `-o`;
  - the `leech-out` layout, including `.gitignore` and `CACHEDIR.TAG`, whose first 43
    bytes are asserted to be the signature;
  - a rebuild overwrites cleanly;
  - two roots in the same directory do not clobber each other;
  - a compile error produces exit 1 and no executable;
  - a stem containing `::`, a nonexistent root, and a non-`.leech` root each exit 2. A
    reserved stem such as `array.leech` builds;
  - a graph conflict (from #99) exits 1, before any type error elsewhere in the program is
    reported;
  - a warning in a *user* module imported by two other user modules is printed once, and
    two distinct warnings at different positions are both printed;
  - `-O2` produces a working executable;
  - `--search-path` and `LEECH_PATH`, including a missing directory warned about once, if
    #41 has landed (otherwise #41 adds these);
  - `CC=/nonexistent`, `CC=""`, `CC="   "` (both mean `cc`, so the build succeeds) and
    `CC='cc "'` each produce the expected result, with no traceback;
  - `CC="cc -Wl,--no-such-flag"` produces a link-failure report.
- `AGENTS.md`: add `uv run leech build input.leech`.

**Validation:** full suite. Manually rebuild the quickstart's hello world and the
multi-module example from the #23 branch with `leech build`.

## Task 8 (#101): Add `leech run` to build and execute a program

**Files:** `src/leech/cli.py`, `tests/test_build.py` (or `tests/test_run.py`), `AGENTS.md`.

- Add a `run` subcommand with the same options as `build`, except `-o`. Before argparse
  sees the arguments, `cli.main` splits `sys.argv[1:]` at the first literal `--` when the
  subcommand is `run`. The left side is parsed normally, so options may appear before or
  after `ROOT`. The right side becomes the program arguments verbatim. Do not use
  `argparse.REMAINDER`, which swallows options that follow `ROOT`.
- After a successful build, flush stdout and stderr, then call
  `os.execv(exe, [str(exe), *args])` with the build result's absolute path. Catch
  `OSError` and report it as a driver error with exit 1. On a build failure, exit 1
  without executing anything.
- Tests:
  - stdout and stderr come from the program;
  - the exit status is forwarded (for example 3);
  - `panic` produces SIGABRT, so the subprocess return code is `-6`;
  - argument parsing, asserted in-process by monkeypatching `os.execv` (Leech cannot read
    argv yet):
    - `run app.leech -O 2 -- a b` builds at `-O2` and passes `["a", "b"]`;
    - `run -O 2 app.leech` also builds at `-O2`;
    - `run app.leech -- -O 3 --search-path x` passes all four tokens to the program and
      builds at `-O0`;
  - `leech run sub/app.leech` from another directory executes the absolute
    `sub/leech-out/app`;
  - an `execv` failure (for example, a monkeypatched `OSError`) exits 1 with a message and
    no traceback;
  - a build failure does not run anything.

**Validation:** full suite.

## Task 9 (#102): Add `leech check` for diagnostics without output

**Files:** `src/leech/build.py`, `src/leech/cli.py`, tests.

- Add a `check` subcommand. It shares discovery, validation and per-module compilation with
  `build`, but writes no IR, does not link, and never creates `leech-out/`.
- Tests:
  - a clean program exits 0, and the root directory is unchanged afterwards (compare its
    listing);
  - a type error exits 1 with the diagnostic;
  - a warning exits 0 and prints the warning;
  - an error found only during lowering or monomorphization is reported. Choose a program
    that `CompilerHarness.build` (semantic IR) accepts but `CompilerHarness.compile`
    (LLVM IR) rejects. Find one among the existing generic-instantiation error tests, and
    name it in the test's docstring. This proves `check` runs codegen.

**Validation:** full suite.

## Task 10 (#103): Report toolchain problems clearly and add `leech doctor`

**Files:** `src/leech/build.py`, `src/leech/cli.py`, `tests/test_build.py`, `README.md`.

- `doctor` reuses `build.resolve_cc()` from #100 unchanged. #100 already makes a missing,
  empty, whitespace-only or malformed `CC` fail fast and without a traceback.
- Add a `doctor` subcommand. It prints the versions from the design. It then runs
  `<cc> --version`, builds and runs a one-line program in a `tempfile.TemporaryDirectory`,
  and prints `ok` or the failing step with a fix. Exit 0 when every check passes and 1
  otherwise.
- `README.md`: mention `leech doctor` in the Install section.
- Tests:
  - `leech doctor` exits 0 in the dev environment;
  - with `CC=/nonexistent`, and with `CC='cc "'`, it exits 1 and names `CC`;
  - `leech build` with a missing `cc` fails before writing `leech-out/<stem>.obj`.

**Validation:** full suite.

## Task 11 (#104): Run compiler tests through the native build pipeline

**Files:** `tests/harness.py`, `tests/test_harness.py`, `AGENTS.md`.

- First record the baseline time of `uv run pytest`.
- Replace `CompilerHarness._link` (`llvm-link`) and `run` (`lli`) with `ll_emit` linking and
  object emission, plus the shared `cc` link function from `build.py`, then run the
  executable. Keep linking bundled modules automatically, as today.
- Keep `_TOOL_TIMEOUT_SECONDS` and the failure-report shape. Signal results (`check_signal`)
  must keep the same meaning.
- Re-time the suite. If it regresses by more than about 25%, stop and report it before
  committing. The issue can stay open or be closed as won't-do without affecting the
  user-facing tools.
- `AGENTS.md`: the requirement changes from "`llvm-link` and `lli` on `PATH`" to "`cc` on
  `PATH`".

**Validation:** full suite with `llvm-link` and `lli` absent from `PATH` (for example with
`PATH` filtered for the run).

## Follow-on: #23 quickstart

This is not part of this plan's tasks. Once #100 and #101 land, #23's quickstart resumes. It
documents `uv tool install`, `leech run`, and `leech build`, with `leechc --emit=obj` plus
`cc` as the "under the hood" section. #23 updates its own design and plan, which still
describe the manual `llvm-link`/`lli` pipeline.

## Issue creation checklist (after approval)

1. Create the issues (done: #95–#104) with the titles from the design's issue breakdown. Each body gives its
   motivation, links to the design and plan sections, and lists its acceptance criteria
   from the task above.
2. Edit #41's body with the decided rules.
3. Update #55: add the edges `#97 --> #100`, `#98 --> #100`, `#99 --> #100`, `#100 --> #101`,
   `#100 --> #102`, `#100 --> #103`, `#97 --> #104`, `#98 --> #104`, `#100 --> #104`, `#100 --> 23` and
   `#101 --> 23`. Replace the #41
   "premature" soft note with the #96 sequencing preference and the #41/#100 composition note.
4. Optionally file the design's future issues.
