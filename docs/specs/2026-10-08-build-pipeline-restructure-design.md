<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Build Pipeline and CLI Restructure — Design

For: [#124](https://github.com/jonathanhaigh/leech/issues/124): Restructure the build pipeline
and command line, [#125](https://github.com/jonathanhaigh/leech/issues/125): Replace `leechc`
with `leech build --emit`, and [#126](https://github.com/jonathanhaigh/leech/issues/126): Check
and generate a whole program in one compilation.

Plan: [`docs/plans/2026-10-08-build-pipeline-restructure.md`](../plans/2026-10-08-build-pipeline-restructure.md).
Parent plan: [`docs/plans/2026-10-05-diagnostics.md`](../plans/2026-10-05-diagnostics.md),
where this work runs before Task 4 (#115).

## Outcome

The compiler's pipeline is a small set of stage objects that the command line, the test
harness and `leech doctor` compose:

```python
session = Session()
checked = Program(root, entry=True).check(session)  # load, check, discover once
module = checked.llvm_module()            # one LLVM module for the whole program
```

- A **program** (a root module and everything it imports) is loaded, checked and
  discovered **once**, in one compilation, and generated as **one LLVM module**.
- **`leech` is the only command.** `leechc` is removed. `leech build --emit KIND[,KIND...]`
  produces an executable, an object, assembly, LLVM IR or bitcode for the whole program.
- Outputs go to the **current directory**. Anything not asked for, such as the object the
  system linker reads, is written to a temporary directory.
- The code is split by concern: a session, the program pipeline, the toolchain, and a
  `cli` package of command classes sharing option groups.

## Current state

The pipeline and the command line are spread across three modules whose boundaries follow
history rather than concerns:

- **`driver.py`** holds the single-module pipeline as four overlapping entry points
  (`compile_to_ir`, `compile_module`, `compile_to_llvm_ir`, `lower_to_llvm_ir`), which differ
  in how far they go and whether they raise. It also holds `leechc`'s argument parsing and
  `main`, and helpers both tools share (`version_text`, `reporting_crashes`).
- **`build.py`** mixes program orchestration (`_compile_program` compiles every module) with
  the toolchain (`$CC` resolution, `leech-out/`, object emission and linking), and holds
  `check`. `build` names both the module and its main function.
- **`cli.py`** holds `leech`'s argument parsing and dispatch as one `main` function with
  `if` branches per command.
- Every pipeline function takes `diags: Optional[diag.Diags]` and creates one when it is
  missing, and `compile_module` threads a `generate_ir` flag down so `leech check` can stop
  before generating IR.

`leech build` and `leech check` compile **each module separately**, each in a new
`compilation.Ctx` that reloads and re-checks the whole program:

- every user module is re-parsed per compilation (only the bundled `std` ASTs are cached,
  per process, by `ir_loader._parse_bundled_mod_ast`);
- `ModLoader.check_declarations` checks every loaded module, so in an N-module program every
  body is type-checked N times and, since #113, every module variable initializer is
  evaluated N times;
- only discovery and code generation are per module, and each module's discovery result is
  kept on the module (`Mod.instances`), although it describes the compilation, not the
  module;
- the same diagnostic is found by several compilations, so `build.py` merges per-module
  sinks and `Diags` deduplicates them structurally.

This was a deliberate simplification of the build-tooling design
([Pipeline](2026-10-02-build-tooling-design.md#pipeline), risk "Quadratic loading"), which
expected "one loader codegenning every module" to come later with the build cache (#105).

`leechc` compiles one module at a time, like `cc -c`. Nothing in the repository drives it
except its own tests: the test harness, `leech build` and `leech doctor` call the library.
Every upcoming diagnostics feature has to add its options to both tools (#108, #117, #119,
#120, #121).

## Goals and non-goals

Goals:

- One concept per module: session, program pipeline, toolchain, command line.
- Stages whose results can only exist when the previous stage succeeded, so callers decide
  how far to go by which methods they call, with no flags threaded through.
- Check a program once, and generate its code once.
- One command-line tool, with shared option groups that later diagnostics options (#117,
  #120, #121) and `-g` (#108) extend in one place.
- No output files the user didn't ask for.

Non-goals:

- **Incremental or cached builds** (#105). This design keeps them possible and records how
  they would fit (see [Future work](#future-work)); it implements neither.
- **Per-module code generation.** One LLVM module per program for now; partitioning into
  units is deferred to when a cache needs it.
- **Parallel code generation.**
- **A project manifest or project output directory** (#106).
- **Changing what is a user error, or the diagnostics model.** The transitional "raise the
  first error" rule stays until #115.
- **Driving the compiler one module at a time from external build systems.** Removed with
  `leechc`; see [Removing `leechc`](#removing-leechc).

## Decisions at a glance

| # | Decision | Choice |
| --- | --- | --- |
| D1 | Work split | Three issues: A (#124, restructure), C (#125, remove `leechc`, add `--emit`), B (#126, one compilation per program), in the order A, C, B. The owner chose A, B, C before D2 was settled; see [Sequencing](#sequencing). |
| D2 | Codegen units | One LLVM module per program. |
| D3–D5 | Instance placement, per-unit reachability, per-unit types | Not needed with one LLVM module. |
| D6 | `leechc` | Removed. |
| D7 | Failure signalling | Stages raise; the transitional "raise the first error in source order" rule lives in one place, which #115 changes. |
| D8 | Diagnostics and options | A `Session` per invocation holds the diagnostics sink and compilation options; `compilation.Ctx` refers to it. |
| D9 | Crash rendering | A command-line helper, not the library `Session`. |
| D10 | Layout | `session.py`, `program.py`, `toolchain.py`, and a `cli/` package. |
| D11 | Command line | `Command` classes and shared option groups. |
| D12 | Outputs | `leech build --emit KIND[,KIND...]` with `exe`, `obj`, `asm`, `llvm-ir`, `llvm-bc`; `-o` only with one kind; outputs in the current directory; unrequested intermediates in a temporary directory. |
| D13 | Exit status | `leech`'s current policy: 0 on success (warnings included), 1 on failure, 2 on usage errors. |
| D14 | `leech run`'s executable | A per-user cache file, then `execv`, keeping the program's exact exit status and signal. |

D12's `-o` rule and D13 were taken as recommended; the owner confirmed them while asking
for D12's output location to be the current directory rather than `leech-out/`.

## Precedents

These summaries come from the compilers' documentation and source. Links are to the most
useful primary sources.

### Pipeline objects and sessions

| Compiler | Invocation state | Compilation state | How stages report failure |
| --- | --- | --- | --- |
| rustc | `rustc_session::Session`: parsed options, the diagnostic context, target information; one per invocation | `TyCtxt`, which refers to the session | Queries return `Result<_, ErrorGuaranteed>`; the driver stops with `abort_if_errors` |
| Clang | `CompilerInvocation` holds options; `CompilerInstance` owns the `DiagnosticsEngine` | `ASTContext`, owned by the instance | Callers test `hasErrorOccurred()` between phases |
| Swift | `CompilerInvocation` and `CompilerInstance`, as Clang | `ASTContext` | The diagnostic engine's `hadAnyError()` |
| Roslyn | `CompilationOptions`, including warning options | An immutable `Compilation` | `GetDiagnostics()`, and `Emit()` returning an `EmitResult` with `Success` and `Diagnostics` |

Leech's `Session` follows rustc's: per-invocation options and the diagnostics sink, with
the compilation (`Ctx`, like `TyCtxt`) referring to it. Clang and Swift split options from
the instance; Leech has too few options to need two objects. Every compiler above lets the
caller ask "did it fail?" of the diagnostics; Leech raises instead, for now, because the
test suite is written around `pytest.raises`, and #115 revisits it.

Internal-error handling is part of rustc's driver (`rustc_driver`'s panic hook and
`catch_with_exit_code`), not its session, which is why Leech keeps crash rendering in the
command line (D9).

- [rustc driver and interface](https://rustc-dev-guide.rust-lang.org/rustc-driver/intro.html)
- [Clang driver internals](https://clang.llvm.org/docs/DriverInternals.html) and
  [Clang internals: the frontend](https://clang.llvm.org/docs/InternalsManual.html#the-frontend-library)
- [Roslyn overview](https://github.com/dotnet/roslyn/blob/main/docs/wiki/Roslyn-Overview.md)

### Units of compilation and code generation

| Compiler | Unit checked together | LLVM modules generated |
| --- | --- | --- |
| Zig | The whole program from its root file | One per compilation (LLVM backend) |
| rustc | A crate | Several codegen units per crate: about one per source module in incremental builds, 16 by default otherwise |
| Swift | A module | One per primary file without whole-module optimization; one (or one per thread) with it |
| Clang | A translation unit | One per translation unit |
| Go | A package | One object per package |

Leech modules may import each other cyclically, so a Leech module is not a unit that can be
compiled before its dependents, unlike a Go package or a Rust crate. It behaves like a file
in a Swift module or a module in a Rust crate, which those compilers check together with
their siblings. Leech checks the whole program together and, like Zig, generates one LLVM
module. rustc's codegen units show how per-module units could return when a cache needs
them: partitioning is a separate step after discovery, and its cost is a placement rule for
generic instances.

- [rustc monomorphization and partitioning](https://rustc-dev-guide.rust-lang.org/backend/monomorph.html)
- [Zig build system](https://ziglang.org/learn/build-system/)

### Command-line tools and outputs

| Toolchain | Tools | Output kinds | Default output location |
| --- | --- | --- | --- |
| Go | One `go` command; the compiler (`go tool compile`) is internal | `go build` builds packages; `-o` names the output | `go build` writes a main package's executable to the current directory; `go run` builds in a temporary directory and deletes it |
| Zig | One `zig` command with `build-exe`, `build-obj`, `run` | `-femit-bin`, `-femit-asm`, `-femit-llvm-ir`, `-femit-llvm-bc` | The current directory; `zig run` uses the cache |
| rustc | `rustc` (and `cargo` on top) | `--emit llvm-ir,llvm-bc,asm,obj,link`, each optionally `KIND=PATH`; `-o` only with one kind | The current directory |
| GCC/Clang | One driver | `-S`, `-c`, `-emit-llvm` | The current directory |

Leech follows rustc's `--emit` spelling, which `leechc` already used, and the
current-directory default of `rustc`, `go build` and `zig build-exe`. Cargo and `zig build`
use project output directories (`target/`, `zig-out/`), which come with their manifests;
Leech has no manifest yet (#106).

- [rustc command-line arguments: `--emit`](https://doc.rust-lang.org/rustc/command-line-arguments.html)
- [`go` command](https://pkg.go.dev/cmd/go)
- [Cargo subcommand structure](https://github.com/rust-lang/cargo/tree/master/src/bin/cargo/commands):
  each subcommand is a module with `cli()` and `exec()`, and shared argument helpers live in
  `cargo::util::command_prelude`.

## Design

### Layering

```text
cli/            command line: parsing, Session construction, rendering, exit status
  leech.py        Command classes: check, build, run, doctor
  doctor.py       the doctor command's checks
  common.py       Command base, option groups, version text, crash reporting
toolchain.py    the system linker ($CC), temporary build directories, output writing
program.py      Program -> CheckedProgram -> LLVM module          (library API)
session.py      Session: diagnostics sink and compilation options  (library API)
ll_emit.py      llvmlite wrapper: parse, link, optimize, emit       (unchanged)
compilation.py  Ctx: one compilation's state, referring to its Session
```

Each layer uses only those below it. `driver.py`, `build.py` and `cli.py` are removed.

### `Session`

```python
@dataclasses.dataclass
class Session:
    """One invocation of the compiler: where diagnostics go, and options that affect output."""

    diags: diag.Diags = dataclasses.field(default_factory=diag.Diags)
    opt_level: int = 0
```

- A session is per invocation; a `compilation.Ctx` is per compilation and refers to its
  session (`Ctx(session)` replaces `Ctx(diags)`; `ctx.diags` reads `session.diags`). Today
  one invocation makes one compilation, but nothing requires it: the test harness or a
  future multi-root build can make several compilations in one session.
- Options that change what is compiled or emitted belong on the session: the optimization
  level now; the warning policy (#120), debug information (#108) and the target later.
  Options that only change presentation (`-fmax-errors`, `-fdiagnostics-format`, colour)
  stay in the command line, which renders the session's diagnostics.
- `Session` knows nothing about tools, standard streams or rendering.

### `Program` and `CheckedProgram`

```python
class Program:
    """A root module and every module it imports, not yet checked."""

    def __init__(self, root: pathlib.Path, *, entry: bool) -> None: ...

    def check(self, session: Session) -> CheckedProgram:
        """Load, check and discover the whole program, raising if it has an error."""


class CheckedProgram:
    """A program with no errors, so generating its code finds none."""

    ctx: compilation.Ctx
    root: ir_module.Mod
    instances: mono.MonoResult

    def llvm_ir(self) -> str:
        """Generate the program as one module of textual LLVM IR."""

    def llvm_module(self) -> llb.ModuleRef:
        """Generate the program, parsed and optimized at the session's level."""
```

- The root's qualified name is its file stem, as `leech` already requires of `ROOT`. Naming a
  root by any other qualified name, which `leechc --module-name` and the library's
  `qualified_name` parameter allowed so a non-root module could be compiled on its own, is
  removed with `leechc` and per-module compilation.
- `entry` makes the root's `main` the program entry point. It has no default: `leech` always
  passes `True`, and the test harness passes `False` to compile modules that have no `main`.
  Today's library default (`compile_to_ir(entry=False)`) and the command line's behaviour
  differ, so neither is a safe default for every caller.
- **Failure (D7).** `check` raises when the program has an error. Until #115, it raises the
  first error in source order, which is what `driver.compile_to_ir` does today and what the
  test suite's `pytest.raises(errors.X)` expects; #115 changes this one place to raise
  `CompilationFailed`. A `CheckedProgram` therefore only exists for a program without
  errors, so `llvm_ir` needs no "no errors on entry" flag, and `leech check` is just
  `Program(root, entry=True).check(session)` with no `generate_ir` parameter anywhere.
- **Generating code** finds no user errors (#113). A user error raised or reported while
  generating is a compiler bug, raised as `diag.InternalError`, as `lower_to_llvm_ir` does
  today. `llvm_module()` also owns the other internal-error boundary: LLVM IR that fails to
  parse or verify (llvmlite's `RuntimeError`) is raised as `diag.InternalError`, as
  `build._emit_object` does today.
- **Instances** discovered while checking belong to the `CheckedProgram`, not to a module:
  `Mod.discover_instances`, `Mod.instances` and `Mod._instances` are removed.

### Errors and the command boundary

Three kinds of exception cross from the library into the command line, and each has one
owner:

| Raised by | Exception | Already in `session.diags`? | Handled by |
| --- | --- | --- | --- |
| `Program.check` | The first error in source order (until #115), or `diag.ReportedError` | Yes | `Command` base: status 1 |
| `toolchain` (`Linker`, output writing) and `leech run`'s `execv` | `CcInvalidError`, `CcNotFoundError`, `LinkFailedError`, `BuildOutputError`, `RunFailedError` | No | `Command` base: reported to `session.diags`, status 1 |
| Anything else | Any other exception, including `diag.InternalError` | — | `reporting_crashes`: rendered as an internal compiler error, re-raised |

The `Command` base runs a command's `run` inside `reporting_crashes`, and inside that, in a
block that catches `errors.UserError` and `diag.ReportedError`, reports a `UserError` to
`session.diags` (an error already reported is deduplicated, so reporting the first error a
second time adds nothing) and returns status 1. Expected failures therefore never reach
crash rendering. After the command returns, the base renders `session.diags` and exits with
the command's status. This is the boundary `driver.compile_module` and `build.build` provide
today by catching these exceptions themselves.

When #115 replaces the transitional rule, `Program.check` raises
`diag.CompilationFailed`, whose diagnostics are all in `session.diags` already; the
`Command` base catches it in place of the `UserError` it catches today, and nothing else in
the command line changes.

### One compilation per program

`Program.check` makes one `Ctx`, loads the root (which loads every module it reaches,
including the prelude and `std`), runs `ModLoader.check_declarations`, designates the entry
point, and runs discovery once for the whole program. Then it raises if there is an error.

**Discovery** (`mono.discover`) takes the program instead of a module. Its roots are the
union of what each per-module discovery uses today: every module's public non-generic
functions, every module variable, and the entry point. Every concrete instance with a body
is defined; the "imported instance, declared but not defined" case
(`mono._is_imported_fn_instance`, `MonoResult.imported_fn_instances`) disappears, because
nothing is imported from another unit. Struct and union instances are drained and checked as
today. Per-request recovery is unchanged.

**Code generation** (`codegen.Compiler`) takes the `CheckedProgram` and generates every
module's items into one LLVM module:

- `_program_items`'s "local items, plus public imported items for declaration" becomes
  every item of every module, each declared and defined once.
- Symbol names stay module-qualified, so items from different modules cannot clash, and
  generated IR keeps today's names.
- Private items keep `private` linkage, which is correct within one LLVM module. Generic
  instances may keep `linkonce_odr`, which is harmless with one definition; dropping it is
  optional tidying.
- `source_filename` names the root's file.
- **Extern declarations share one symbol.** An `extern fn` keeps its bare name, so two
  modules declaring the same extern now meet in one LLVM module. Today that already crashes
  when a module redeclares a prelude extern (#28: llvmlite's `DuplicatedNameError`), and
  works for unrelated modules only because they are compiled separately. One compilation
  per program therefore fixes #28 as #28 asks: declarations of one extern symbol with the
  same signature share a single LLVM declaration. Declarations with different signatures
  are a user error found while checking (a new `ConflictingExternDeclError`), since they
  cannot both describe the same C function. "Earlier" and "later" use the order diagnostics
  are sorted in: the declaring files' load order (`Diags.note_file`'s rank, which is the
  order `ModLoader` loads modules, prelude first), then position within the file. The error
  is reported at the later declaration, with a note at the earliest one with a different
  signature.

**Diagnostics.** One compilation reports each problem once, so `build.py`'s per-module sinks
and `Diags.merge` are removed. `Diags` keeps structural deduplication, which still guards
against genuine repeats within a compilation, and `note_file`, which orders files for
sorting.

**What changes for users.** Nothing visible except speed and output layout: the same
diagnostics, in the same order, and the same executable behaviour. Code that only another
module's compilation used to reach is now generated once, in the program module.

### Toolchain

`toolchain.py` holds what turns an LLVM module into files, and nothing that knows about
Leech source:

- `Linker.from_env()` resolves `$CC` as `build.resolve_cc` does today, raising
  `CcInvalidError` or `CcNotFoundError`; `Linker.link(obj, exe)` runs it into a given path,
  raising `LinkFailedError`, as `build._link` does. The linker is resolved only when an
  executable is requested, so every other output kind works without a C compiler, as
  `leechc --emit` did.
- `emit(module, kind, path, opt_level)` writes one output kind through `ll_emit`.
- A build's intermediates live in a `tempfile.TemporaryDirectory`, deleted when the build
  ends, whether it succeeds or not.

`ll_emit.py` stays the thin llvmlite wrapper. Linking several LLVM modules
(`ll_emit.link`) is no longer needed by the build once there is one module per program; it
stays only while anything still produces several.

### Outputs (D12)

```text
leech build ROOT [--emit KIND[,KIND...]] [-o PATH] [-O{0,1,2,3}]
```

| Kind | Output | Default path |
| --- | --- | --- |
| `exe` (default) | A native executable, linked with `$CC` | `./<stem>` |
| `obj` | A PIC object file | `./<stem>.o` |
| `asm` | Assembly | `./<stem>.s` |
| `llvm-ir` | Textual LLVM IR | `./<stem>.ll` |
| `llvm-bc` | LLVM bitcode | `./<stem>.bc` |

- Every kind describes the whole program, optimized at `-O`, so `--emit llvm-ir,exe`
  writes the IR that was compiled into the executable.
- `--emit` takes a comma-separated list; repeating a kind is accepted and has no further
  effect. An unknown kind is a usage error (exit 2).
- `-o PATH` names the output and is accepted only when exactly one kind is requested (a
  usage error otherwise), as rustc requires. Without `-o`, each kind goes to its default
  path in the current directory. A relative `-o` is relative to the current directory, as
  every default path is.
- Nothing else is written: no `leech-out/`, no `.gitignore` or `CACHEDIR.TAG`, no per-module
  `.ll` files.
- The executable path is resolved to an absolute path for `leech run` and for reporting.

**Writing outputs: stage, then commit.** A build produces everything before it replaces
anything:

1. **Stage.** Generate every requested kind into the build's temporary directory, in a fixed
   order (`llvm-ir`, `llvm-bc`, `asm`, `obj`, `exe`). `exe` links an object in the temporary
   directory (the same object that is staged if `obj` was requested too). Any failure here
   (a link failure, an LLVM error) leaves every destination untouched.
2. **Commit.** Copy each staged output to a uniquely named temporary file beside its
   destination (`tempfile.mkstemp` in the destination's directory), then `os.replace` it
   over the destination, in the same fixed order. Copying first is needed because the
   temporary directory may be on another filesystem, where `os.replace` fails; replacing
   from the same directory is atomic per file. An executable keeps its mode bits.
3. **Failure model.** Each destination is either its old content or its complete new
   content, never partial. If committing one output fails (for example, the destination is
   a directory, or permission is denied), it is reported as `BuildOutputError`, outputs
   already committed in this build stay, later ones are not attempted, and any staged
   temporary beside a destination is removed. Atomic replacement of several files at once is
   not possible, so this is the strongest guarantee; it only arises when the file system
   rejects a write, not when compilation or linking fails.

A failed check or a failed link therefore writes nothing and leaves every existing output as
it was.

The default executable path is the root's stem in the current directory, as `go build` and
`rustc` do. A root `app.leech` in the current directory therefore builds `./app` beside it.
If the destination exists and is a directory, the build fails with `BuildOutputError`.

### `leech run`

`leech run` builds an executable that nobody asked to keep, then replaces the `leech`
process with it (`os.execv`), so the program's exit status and termination signal are its
own, as the build-tooling design requires. `execv` gives `leech` no chance to delete the
executable afterwards, so it cannot live in the temporary directory.

**Decision (D14).** The executable is a per-user cache file,
`<cache>/leech/run/<sha256 of the root's absolute path>/<stem>`:

- `<cache>` is `$XDG_CACHE_HOME` if it is set to an absolute path, and `~/.cache`
  otherwise (the XDG base directory specification says relative values are invalid and
  must be ignored). The directories are created as needed, with mode `0o700` for those
  `leech` creates.
- The executable is committed as any output is (stage, then copy to a uniquely named
  temporary file beside it and `os.replace`), so two concurrent runs of the same root never
  share a temporary name, and each `execv`s a complete executable: whichever replaced it
  last, or the one it replaced, which stays valid while running because replacement swaps
  the directory entry, not the file.
- Storage grows by one executable per distinct root path run, never per run; nothing evicts
  old entries (deleting `<cache>/leech` is always safe). A future build cache (#105) would
  live beside it and could own eviction.

This keeps `execv`'s exact exit and signal semantics. `zig run` similarly runs from its
cache.

The rejected alternative was `go run`'s: build in a temporary directory, run the program as a
child process, wait, delete the directory, then exit with the child's status, re-raising the
child's terminating signal on `leech` itself. It leaves nothing behind, but `leech` must
forward signals it receives (such as `SIGTERM`) to the child, ignore `SIGINT` and `SIGQUIT`
while waiting (as `system(3)` does), and its status only imitates the program's.

### `leech check` and `leech doctor`

- `leech check ROOT` is `Program(root, entry=True).check(session)`: no code generation, no
  files.
- `leech doctor` builds its test program through the same library calls as `leech build`,
  with `-o` in its own temporary directory.

### Command line (D9, D11)

```python
class Command(abc.ABC):
    name: ClassVar[str]
    help: ClassVar[str]
    option_groups: ClassVar[tuple[type[OptionGroup], ...]] = ()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...

    @abc.abstractmethod
    def run(self, args: argparse.Namespace, session: Session) -> int:
        """Do the command's work, returning the exit status."""


class OptionGroup(abc.ABC):
    """Options several commands share, and how they configure a Session."""

    @staticmethod
    @abc.abstractmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None: ...

    @staticmethod
    def configure(args: argparse.Namespace, session: Session) -> None: ...
```

- **Commands.** `CheckCommand`, `BuildCommand`, `RunCommand` (a build, then `execv`) and
  `DoctorCommand`. Adding a command, such as `leech explain` (#119), is one class in the
  command list.
- **Option groups.** `RootArgument` (validates `ROOT` as `cli._check_root` does today),
  `OptimizationOptions` (`-O`, configuring `session.opt_level`) and `OutputOptions`
  (`--emit`, `-o`, build only). #117, #120 and #121 each add or extend one group
  (`DiagnosticOptions`), and #108 adds `-g` to `OptimizationOptions` or its own group.
- **`main`** builds the parser from the command list, splits `run`'s arguments at the first
  `--` as today, constructs a `Session`, lets each of the command's option groups configure
  it, and runs the command inside `reporting_crashes`. It then renders the session's
  diagnostics and exits with the command's status.
- **Crash rendering (D9).** `cli/common.reporting_crashes(session)` renders the
  diagnostics collected so far, then the internal-compiler-error banner, and re-raises,
  exactly as `driver.reporting_crashes` does today. The `Command` machinery uses it for
  every command, so commands don't.
- **Exit status (D13).** Unchanged for `leech`: 0 on success including warnings, 1 on a
  build failure, 2 on usage errors. `leechc`'s severity-based status goes with it.
- **`version_text`** moves to `cli/common.py`.

### Test harness

The harness uses the library as `leech` does:

- `CompilerHarness.build` returns a checked program's root module; `load` is unchanged.
- `CompilerHarness.compile` compiles the whole program once and returns its single LLVM IR;
  per-module results (`CompiledProgram.mods[name].llvm_ir`) become one program IR. Tests
  that inspected `mods["main"].llvm_ir` read the program IR, which also contains `std`'s
  code.
- `CompilerHarness.run` emits an object from the program module and links it with the
  system linker, without the separately compiled `std` modules
  (`_bundled_mod_llvm_ir` is removed), because `std` is part of every program.
- Tests that call `driver.*` or `build.*` move to `Program`, `CheckedProgram` and the
  command classes.

### Removing `leechc`

`leechc FILE` compiled one module: it loaded and checked the module's whole program, then
generated that module's LLVM IR. It is removed because:

- nothing drives it except its own tests;
- every upcoming command-line feature would need adding to two tools;
- its severity-based exit status differs from `leech`'s;
- `--module-name` and `--entry` exist only because it compiled a module out of its
  program's context.

Compiling a single module (or function) may return later as an option to `leech`, designed
when an external build system needs it. The build-tooling design's rejection of a single
command ("the user explicitly wants a `leechc` compiler command") is superseded by the
owner's decision here.

## Compatibility and migration

- **Commands.** `leechc` is removed from `pyproject.toml`'s scripts; `leech` moves to
  `leech.cli.leech:main`. `leech build` gains `--emit`, writes to the current directory, and
  no longer creates `leech-out/`.
- **Existing `leech-out/` directories** are left alone; nothing reads or deletes them.
- **Library API.** `driver.py` and `build.py` are removed, with no compatibility shims: the
  test suite and `doctor` are their only users.
- **Documentation.** `README.md` and `AGENTS.md` describe `leech build --emit` instead of
  `leechc` and drop `leech-out/`. The build-tooling spec gets a note at its top pointing to
  this design for the superseded parts (`leechc`, `leech-out/`, per-module compilation).
  The diagnostics spec and plan stop naming `leechc`.
- **Issues.** #105 loses its "let one `ModLoader` codegen every module" bullet (done here)
  and gains B as a blocker; #108, #117, #119, #120 and #121 drop their `leechc` options;
  #106 notes that outputs now go to the current directory. B closes #28.

## Sequencing

The owner chose to do the restructure (A) first, then one compilation per program (B), then
remove `leechc` (C), before settling on one LLVM module per program (D2). With D2 settled,
B would change `leechc`'s output from one module's IR to the whole program's just before C
deletes `leechc`, and update `leechc`'s tests twice. The plan therefore orders the issues
**A, C, B**; reverting to A, B, C costs that churn and nothing else.

## Alternatives rejected

- **One LLVM module per Leech module** with instances in every using unit (`linkonce_odr`)
  and per-unit reachability recorded from lowering requests. It keeps per-module `.ll`
  files and a unit for #105 to cache, but costs an instance placement rule and a
  reachability graph now, for a cache that does not exist yet.
- **Keeping `leechc`** as a module compiler for external build systems: nothing uses it, and
  it doubles every option.
- **`Optional` results or `CompilationFailed` now** (D7): the first burdens every caller
  with `None` checks; the second pulls #115's test migration into this work.
- **Passing a `Diags` and nothing else** (D8): every other option would need its own
  parameter.
- **`Session` as the crash-reporting context manager** (D9): puts tool names and standard
  streams in the library.
- **rustc-style module names** (`interface.py`, a `driver/` package): "interface" is vague
  in Python, and "driver" keeps today's ambiguity.
- **`leech-out/` for outputs**: the owner chose the current directory, with intermediates in
  a temporary directory, now that there are no per-module intermediates to keep.
- **`KIND=PATH` in `--emit`**: no need yet; it can be added without breaking anything.
- **`go run`'s temporary directory and child process for `leech run`** (D14): leaves nothing
  behind, but only imitates the program's exit status and needs signal forwarding.

## Future work

- **Per-module units and a cache (#105).** Partitioning the program's instances into
  per-module LLVM modules is a step after discovery, as rustc's partitioning is. It needs a
  placement rule (every using unit with `linkonce_odr`, as C++ templates and Go
  instantiations are emitted), and each unit's reachable instances, which lowering can
  record as edges from the unit being lowered (`Ctx.unit_stack`) to the instances it
  requests. Requests made only while validating declarations then have no lowering
  requester, which also fixes #49.
- **Query-level incremental compilation.** Leech modules can import each other cyclically,
  so module-at-a-time compilation with interface files (Go, GHC) is not available.
  Incrementality within one compilation, as rustc's query system and Zig's InternPool
  provide, builds on analysis units: recording which units each unit reads, keying units by
  stable identities rather than object identity, and recomputing only invalidated units.
  One compilation per program is a prerequisite.
- **Compiling one module or function** as an option to `leech`, when a use case appears.

## Risks and open questions

- **Test churn.** About 30 tests read per-module IR (`mods["main"].llvm_ir`) and will read
  the program IR, which also contains `std`; assertions on absence ("no symbol named …")
  must be checked against `std`'s symbols.
- **Larger IR per test.** Every compiled test program now generates `std`'s code in its one
  module, where the harness used to link `std` IR compiled once per process
  (`_bundled_mod_llvm_ir`). Checking `std` is not new: today every per-module compilation
  already checks every loaded module, `std` included, and one compilation per program
  checks it once. The new cost is generating `std`'s code per test. Measure the suite's run
  time before and after B. A per-process cache of `std`'s generated code needs per-module
  units, so until then the fallback is to accept the cost.
- **Conflicting extern signatures** become an error where they used to compile (when the
  modules were compiled separately and only the linker saw both). That is the intended
  behaviour, since the program was calling one C function with two types; the error message
  names both declarations.
- **Discovery's roots.** Each module's compilation used to generate the code its own public
  functions reach. One discovery must start from the union of those roots, or code reachable
  only from another module's public function would be missed. A test covers a private
  function reached only through another module's public function.
