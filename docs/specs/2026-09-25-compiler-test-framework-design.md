<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Structured Compiler Test Framework — Design

For: #94: Replace ad hoc compiler test helpers with a structured framework

Implementation plan: [Structured compiler test framework plan](../plans/2026-09-25-compiler-test-framework.md).

## Outcome

Leech's tests should describe the source program under test and the compiler phase being
exercised without also encoding temporary-file layout, LLVM-link commands, standard-library
link lists, or positional conventions for returned artifacts. The framework will replace the
ad hoc functions in `tests/util.py`, and every existing test will migrate to the new API.

The common one-file case remains terse:

```python
def test_answer(compiler):
    compiler.check(src, stdout="", exit_status=42)
```

Tests that need more control construct an explicit program:

```python
program = harness.TestProgram.from_main(
    main_src,
    harness.ModSrc("pkg::a", a_src),
    harness.ModSrc(
        "sub::helper",
        helper_src,
        path="pkg/sub/helper.leech",
    ),
)
compiler.check(program, stdout="", exit_status=11)
```

Here the helper module is physically below `pkg/` but is named `sub::helper` because that is
the relative import written by `pkg/a.leech`. The name, contents, and exceptional placement
are one object rather than a `**modules` entry plus a separate `qualified_names` mapping.

## Current problems

`tests/util.py` mixes source creation, compiler phases, linking, execution, and assertions.
Its most-used interfaces are `check_prog_output` (432 calls), `compile_str` (355), `build_ir_mod` (89),
`find_pos` (63), `parse_mod` (44), and `compile_modules` (41). The problems are structural,
not merely naming:

- `**modules: str` turns framework option names into forbidden module names and makes static
  analysis conservatively bind arbitrary source strings to unrelated parameters.
- Multi-module source paths and qualified names live in separate dictionaries.
- `compile_modules` returns a position-dependent list of paths.
- `check_prog_output` combines program construction, LLVM generation, linking, execution,
  output matching, and exit-status matching.
- stdout and stderr are merged. A negative expected status implicitly changes exact output
  matching into prefix matching.
- source and generated LLVM are printed for every successful operation, producing noisy
  captured output on an unrelated failure.
- external processes have no timeout.
- `compile_str`, `build_ir_mod`, and `parse_mod` name their input representation but not the
  compiler phase or returned representation.
- direct file-writing helpers expose filesystem setup that most tests should not coordinate.

Adding positional-only parameters would remove the immediate keyword collision but preserve
the underlying overload of one call with program data, build policy, and assertions.

## Public test model

The framework lives in `tests/harness.py`. `tests/__init__.py` makes `tests` a package so all
tests use the single canonical `tests.harness` module rather than loading helper state under
both `util` and `tests.util`. Repository-root `conftest.py` supplies the fixture so tests and
the later documentation collector can share it.

### `ModSrc`

`ModSrc` describes one source file. Its fields are `Final`, so basedpyright rejects
reassignment without requiring frozen-dataclass initialization machinery:

```python
class ModSrc:
    name: Final[str]
    src: Final[str]
    path: Final[pathlib.Path]

    def __init__(
        self,
        name: str,
        src: str,
        path: Optional[str] = None,
    ) -> None: ...
```

Callers pass paths as ordinary strings. The constructor converts a path to `pathlib.Path`
internally because the value is ultimately materialized below pytest's concrete `tmp_path`.
`PurePosixPath` is not used: the test target and compiler target are currently Unix-like,
and no cross-platform virtual path operations justify exposing a second path flavour. Python's
documentation recommends `Path` for normal filesystem work and distinguishes it from pure
paths that cannot perform I/O.

When `path` is omitted, it is derived from the qualified name: `a` becomes `a.leech` and
`pkg::a` becomes `pkg/a.leech`. An explicit path is needed only when import resolution makes
physical placement differ from the compiler-qualified name.

Construction validates:

- `name` with Leech's existing qualified-name parser and reserved-final-segment rule;
- a relative `.leech` path with no empty name, absolute anchor, or `..` component; and
- that `prelude` is not supplied by a test program, because `ModLoader` always owns the bundled
  ambient prelude.

`TestProgram` rejects duplicate names and paths. The harness separately verifies during
materialization that every normalized destination remains below its workspace.

Intermediate directory segments may use reserved Leech words, matching module resolution and
the `--module-name` contract. Diagnostics render paths using the host convention.

### `TestProgram`

`TestProgram` contains a root and zero or more supporting modules:

```python
@dataclasses.dataclass(frozen=True)
class TestProgram:
    root: ModSrc
    mods: tuple[ModSrc, ...] = ()

    @classmethod
    def from_main(cls, src: str, *mods: ModSrc) -> TestProgram: ...
```

`from_main` creates `ModSrc("main", src)`. Harness methods also accept a bare source
string and coerce it through `from_main`, preserving the concise single-file case.

A program rejects duplicate paths and duplicate qualified names. A runnable program requires
the root name `main`, because that is the symbol contract for Leech's entry point. Compile and
semantic-IR operations may use another explicit root name when a test needs it.

The model is deliberately in-memory. The harness, not each test, owns materialization into the
unique per-test directory supplied by pytest's `tmp_path` fixture.

## Harness and phase boundaries

`CompilerHarness` is constructed with one concrete workspace path and exposed as the
function-scoped `compiler` fixture. It offers phase-specific operations rather than one
variable-meaning function:

```python
class CompilerHarness:
    def parse(self, src: str | ModSrc) -> ast.Mod: ...
    def build(self, program: str | TestProgram) -> ir_module.Mod: ...
    def compile(self, program: str | TestProgram) -> CompiledProgram: ...
    def run(self, program: str | TestProgram) -> subprocess.CompletedProcess[str]: ...
    def check(
        self,
        program: str | TestProgram,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_status: int = 0,
    ) -> None: ...
    def check_signal(
        self,
        program: str | TestProgram,
        *,
        expected_signal: signal.Signals,
        stdout: str = "",
        stderr_prefix: str,
    ) -> None: ...
```

`parse` turns a bare string into `ModSrc("main", src)`, materializes that file, calls
`parse.parse_mod_ast`, and returns an AST. A caller may pass `ModSrc` to control the name
and path recorded in source spans. This deliberately adopts production `UserError` translation
for malformed modules; tests of raw Lark failures and non-module start rules continue to call
`parse.build_parser` directly. `build` materializes all sources and calls the production
semantic-IR entry point for the root. `compile` compiles each supplied module under its declared
qualified name, preserving current separate-compilation coverage. All sources are materialized
before any compilation so imports see the complete fixture.

`run` calls `compile`, links the program, and returns captured results without asserting them.
`check` is the high-frequency convenience layer for normal termination. `check_signal` makes
the four abort tests state both the signal and the stderr-prefix rule rather than encoding
both through a negative integer. Mutually exclusive exit and signal behavior therefore lives
in separate methods.

Tests use ordinary pytest assertions when they need anything outside those common contracts.
Expected compiler exceptions remain explicit `pytest.raises` blocks around the appropriate
phase method.

### Direct materialization

A narrow `write_mod(mod: ModSrc) -> pathlib.Path` operation remains available for
tests of `ModLoader` itself. Those tests intentionally invoke the loader below the normal
driver boundary and therefore need a real path. There is no general `write_whole_file` helper,
and materialization never prints file contents as a side effect.

## Structured results

Compilation returns identities and contents rather than an anonymous list:

```python
@dataclasses.dataclass(frozen=True)
class CompiledMod:
    mod: ModSrc
    src_path: pathlib.Path
    llvm_path: pathlib.Path
    llvm_ir: str


class CompiledProgram:
    mods: Final[Mapping[str, CompiledMod]]

    def __init__(self, mods: Mapping[str, CompiledMod]) -> None: ...
```

The read-only mapping is keyed by qualified module name and retains source order; the
implementation wraps its private dictionary rather than exposing mutable result state.
Construction rejects duplicates before compilation. Tests inspect
`compiled.mods["main"].llvm_ir` rather than unpacking a path tuple and rereading it.

Execution returns the standard subprocess result directly:

```python
subprocess.CompletedProcess[str]
```

The harness invokes `lli` with text output captured separately, so `stdout` and `stderr` are
strings. Keeping them separate lets ordinary output, panic text, linker diagnostics, and LLVM's
crash handler be described accurately. `lli --disable-symbolication` remains necessary for
abort tests. `check_signal` checks exact stdout, the expected negative signal status, and only
the stable prefix of stderr because `lli` appends an address-dependent crash report.

## Standard-library linking

Runnable tests link implementations for every currently bundled standard-library source
automatically. The harness enumerates `src/leech/std/*.leech`, compiles immutable LLVM text
once per process, and materializes it into each run workspace. `prelude.leech` uses the
qualified name `prelude`; every other file uses `std::<stem>`. The bundled prelude is always
linked and cannot be replaced: `ModLoader` constructs it before loading the program. A
program-supplied `std::` module with the same qualified name suppresses that bundled standard
module's definition. This is a new explicitly tested harness capability matching the loader's
importing-directory-first resolution.

This removes both `std_modules` from Python tests and `std=` from issue #23's documentation
fence contract. A compiler-accepted reference still requires the appropriate source import;
linking a definition does not add a name to Leech scope.

Linkable runtime cost is accepted deliberately: a peer-review measurement of a small program
found `lli` taking about 37 ms with only the prelude and 45 ms with all three current library
modules, approximately 8 ms or 22% extra per run and roughly 3–4 seconds across today's runnable
tests. The exact timing is environment-dependent, but it establishes the present scale.

Linking the whole current library is an intentionally bounded interim policy. It is reasonable
while the library contains only `prelude`, `io`, and `mem`, all supported by the one current
target. It must be revisited when the library gains platform-specific modules, optional native
dependencies, material startup cost, or conflicting runtime variants. The production build
driver should ultimately expose and link the resolved module dependency closure; this test
cleanup does not invent a second dependency resolver.

## External tools and failure reporting

`llvm-link` and `lli` run with separate stdout and stderr capture and a named timeout constant,
initially 30 seconds per command. A timeout or unsuccessful link raises an assertion containing
the command, return status when present, stdout, stderr, and workspace path. The run operation
returns any program status without treating it as a harness failure.

Successful compilation does not print source or LLVM. Pytest retains recent `tmp_path`
directories, so failure messages identify the workspace and artifacts remain inspectable.
This follows the useful behavior of LLVM's `lit`: commands and full output are failure detail,
not unconditional noise.

## Parsing, semantic, LLVM, and span helpers

The migration removes ambiguous free functions:

| Existing helper | Replacement |
| --- | --- |
| `parse_mod(tmp_path, src)` | `compiler.parse(src)` |
| `build_ir_mod(tmp_path, src)` | `compiler.build(src)` |
| `compile_str(tmp_path, src)` | `compiler.compile(src)` |
| `compile_file(...)` | internal harness method or `compiler.compile(TestProgram(...))` |
| `compile_modules(...)` | `compiler.compile(TestProgram(...))` |
| `link_and_run(...)` | `compiler.run(program)` |
| `check_prog_output(...)` | `compiler.check(...)` or `compiler.check_signal(...)` |
| `write_whole_file(...)` | `TestProgram`/`ModSrc`; `compiler.write_mod(...)` only when directly testing the loader |

`src_position(src, substring)` is the clearer public successor to `find_pos` and retains
today's first-occurrence behavior. Ordered diagnostic-note tests use it to build expected
position sequences. `assert_span_at(span, src, substring)` replaces the repeated
`span is not None` and single line/column tuple comparison and reports expected and actual
locations explicitly. Tests that need a different occurrence or more detailed span boundaries
keep direct assertions.

The end-to-end CLI test that invokes `leech` on the real bundled library intentionally locates
those production sources directly; it is testing the CLI build instructions, not using the
harness as a program builder.

No helper hides `pytest.raises`, exact diagnostic classes, or diagnostic-note inspection.

## Diagnostics ownership boundary

Issue #93 will move registered diagnostics into per-compilation state. This framework neither
depends on nor duplicates that refactor. Structured compile results leave room to expose a
future compilation result or diagnostic collection. Until #93 lands, only the narrowly needed
temporary isolation may exist in issue #23's documentation runner; it is not generalized into
the new harness.

## Migration and compatibility

All tests migrate in #94. Compatibility wrappers for the old API may exist only inside an
intermediate implementation commit and must be deleted before the issue is complete. Keeping
both APIs would preserve exactly the ambiguity this work is intended to remove.

The current issue #23 implementation is stored in Git stash
`WIP issue 23 documentation infrastructure task 2`. Do not pop it wholesale after #94: it
contains broad imports and `tests/util.py` edits based on the old interface. After #94 is
committed, restore the documentation collector and dependency changes selectively, then adapt
them to `tests.harness` under #23. Retain the stash until every intended file has been recovered.

## Precedents

Rust's compiletest distinguishes check, build, run, failure, and crash modes, and stores run
stdout and stderr separately. That supports explicit phase methods and separate streams, but
Leech keeps expectations in Python rather than adding source directives to ordinary tests:
[Rust compiletest](https://rustc-dev-guide.rust-lang.org/tests/compiletest.html) and
[UI test modes](https://rustc-dev-guide.rust-lang.org/tests/ui.html).

LLVM's `lit` treats execution policy as suite infrastructure, supports per-test timeouts, and
shows commands and full output when failures need diagnosis. Swift uses lit-based compiler,
runtime, and standard-library suites while keeping unit tests separate. These support a
harness boundary and failure-focused output, not replacing pytest:
[LLVM lit](https://llvm.org/docs/CommandGuide/lit.html),
[LLVM testing guide](https://llvm.org/docs/TestingGuide.html), and
[Swift testing](https://github.com/swiftlang/swift/blob/main/docs/Testing.md).

Zig's compiler-testing direction uses directories of real source files for compiler cases,
while `zig test` integrates building and running with a standard runner. Leech similarly
materializes explicit source descriptions into a private workspace, but retains pytest and
Python assertions:
[Zig test](https://ziglang.org/documentation/master/#Zig-Test) and
[Zig compiler test-suite proposal](https://github.com/ziglang/zig/issues/11288).

Pytest's `tmp_path` already provides a unique concrete `pathlib.Path` per invocation and retains
recent directories. Python distinguishes pure computational paths from concrete `Path` objects;
this framework eventually performs I/O and therefore normalizes caller strings to `Path`:
[pytest temporary paths](https://docs.pytest.org/en/stable/how-to/tmp_path.html) and
[Python pathlib](https://docs.python.org/3/library/pathlib.html).

## Alternatives rejected

### Make configuration parameters positional-only

This fixes keyword collisions but leaves separate module-name maps, anonymous path lists,
merged streams, implicit signal semantics, and caller-managed standard-library linkage.

### Require `PurePosixPath` or `PurePath` from callers

The values are materialized immediately on the host filesystem. Requiring path objects adds
noise to the tests, and choosing POSIX semantics publicly claims portability the compiler does
not yet provide. Plain relative strings with internal `Path` conversion are simpler.

### Link only caller-listed standard modules

This leaks build bookkeeping into every run example and caused the `std_modules` collision.
The current library is small enough for an automatic complete runtime link.

### Parse imports in the harness

A second import parser or dependency resolver would drift from `ModLoader`. Link the bounded
current library now and defer dependency-closure linking to the production build driver.

### Adopt lit, compiletest, or a directive-file suite

Leech already has more than 1,500 pytest tests that mix parser objects, semantic IR, errors,
LLVM text, and execution. A new runner would not simplify those Python-level assertions and
would fragment test selection. Their phase and failure-reporting ideas are useful without
their runner formats.

## Acceptance and risks

- The complete suite uses the new API; old helper names and `tests/util.py` are gone.
- Simple tests are at least as concise after removing the explicit `tmp_path` argument and
  using named expectations.
- Multi-module tests express every file's name, source, and exceptional path together.
- Compiler phases and result types are unambiguous.
- Standard-library callers list no link modules.
- Aborting programs assert an explicit signal and stable stderr prefix.
- Successful tests produce no unconditional artifact dump.
- Tool hangs are bounded and failures identify retained artifacts.
- Two harnesses keep all materialized artifacts workspace-local; immutable standard-library
  LLVM strings are the only shared cache, preserving pytest's process-level parallelism model.

The largest risk is migration breadth: more than 900 helper calls will change. The plan uses
mechanical category-by-category migrations, keeps behavior-characterization tests around the
framework, and runs focused files after each category plus the full suite at task boundaries.
Automatic whole-standard-library linkage is the other deliberate risk; focused shadowing and
duplicate-symbol tests guard today's policy, and the design states when it must be replaced.
