<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Build Tooling: `leechc`, `leech`, and Local Installation — Design

For: #95–#104 (see [Issue breakdown](#issue-breakdown)), plus
[#41](https://github.com/jonathanhaigh/leech/issues/41): Wire up a module search path.

Implementation plan: [Build tooling plan](../plans/2026-10-02-build-tooling.md).

## Outcome

A user with Python 3.14, uv and a C toolchain can install `leechc` and `leech` from a source
checkout with one `uv tool install` command. They can then build and run a multi-module
program with one command each:

```bash
uv tool install --editable /path/to/leech   # or a non-editable path/wheel install
leech run hello.leech                       # build to an executable, then execute it
leech build hello.leech                     # writes leech-out/hello next to hello.leech
leech check hello.leech                     # diagnostics only, no output files
leechc hello.leech --emit=obj -o hello.o    # low-level, one module at a time
```

The standard library and prelude are found, compiled and linked automatically. No system
LLVM installation is needed. The only external tool on the build path is a C compiler
driver (`cc`), which links the object file and supplies libc and the C runtime start files.

## Current state

These facts were checked against the repository on 2026-10-02:

- `pyproject.toml` declares one console script, `leech = "leech:main"`, which compiles one
  source file to textual LLVM IR (`src/leech/driver.py`). The `uv_build` backend already
  produces a wheel containing `leech.lark` and `std/*.leech`. The sdist has no license file,
  and the metadata declares no license.
- **Compilation is separate per module.** Each module is compiled to its own `.ll`.
  Imported modules are loaded for declarations only. Imported non-generic function bodies
  are emitted only by their owning module. Generic instances are emitted with
  `linkonce_odr` linkage in every module that uses them (`codegen.Compiler._declare_fn_instance`).
  Symbol names are prefixed with the module's qualified name, so each module must be
  compiled under the qualified name its importers use.
- **The executable entry point depends on the module's name.** `SrcFnSymbol.is_main`
  holds only for `main` in a module named `main`, and `FnInstance.qualified_name` emits
  that function unqualified as `@main`.
- `ir_loader.ModLoader` already loads the whole import graph, including the implicit prelude
  and any reached `std::*` modules. It searches, in order: the importing file's directory,
  the bundled root, and `extra_search_roots`, which is always empty (#41).
- The documented manual workflow (the paused #23 quickstart branch) runs three `leech`
  invocations with `--module-name`, then `llvm-link`, `lli`, or `llc` plus `cc -no-pie`.
  The test harness (`tests/harness.py`) does the same with `llvm-link` and `lli`.
- llvmlite 0.47.0 bundles LLVM 20.1.8. It can parse textual IR, link modules
  (`Module.link_in`), run the new pass manager (`create_pass_builder`), and emit objects or
  assembly from a `TargetMachine`. A spike on 2026-10-02 compiled `main`, `std::io`,
  `std::mem` and `prelude` separately, linked them in-process, and emitted a PIC object at
  `-O2`. Plain `cc` then linked it into a working PIE executable without `-no-pie`, with
  stdout and the exit status intact. The four compilations plus object emission took
  0.65 s.
- The bundled standard library is 60 lines in three files. Compiling all three takes about
  0.5 s, which is dominated by Python start-up and imports.

## Goals and non-goals

Goals:

1. Build, install and version the compiler as a Python package with uv, following uv and
   PyPA practice, without uploading it to any registry.
2. Rename the compiler command to `leechc`.
3. Add a `leech` command with subcommands that build a whole program to a native executable,
   build and run it, or only check it.
4. Provide the prelude and standard library to every build automatically.
5. Remove system LLVM tools from the build path.
6. Name modules by location with absolute imports (#99), so that separately compiled modules
   agree and a package system stays possible.
7. Report missing or broken toolchains clearly.

Non-goals, recorded so they are not reopened by accident:

- **No package manifest (`leech.toml`), project scaffolding (`leech new`) or dependencies.**
  A "project" is a root file plus everything it imports, which follows #41's reasoning.
- **No persistent build cache or incremental rebuilds.** Every `leech build` recompiles every
  reached module (see [Standard library provisioning](#standard-library-and-prelude-provisioning)).
- **No program arguments.** `main` takes no parameters today. `leech run` forwards arguments
  after `--` to the process so the CLI does not change later, but Leech code cannot read
  them yet.
- **No script mode.** There is no `leech FILE` shorthand and no `#!` shebang support.
- No git tags, release process, or registry publishing. The version stays static in
  `pyproject.toml`.
- No new targets. `target.TRIPLE` remains `x86_64-linux-gnu`.
- No debug information, sanitizers, or cross-compilation.

## Decisions at a glance

| Question | Decision |
| --- | --- |
| Command split | `leechc` compiles one module, like `cc -c` or `rustc --emit`. `leech` is the user-facing driver with `build`, `run`, `check`, and `doctor` subcommands. |
| What is built | A root file and its transitive import graph, discovered by the loader. There is no manifest. |
| Entry point | The explicit `leechc --entry` flag replaces the "module named `main`" rule. The root keeps its own qualified name. |
| Toolchain | Link, optimize, and emit objects in-process with llvmlite, then link with `$CC` (default `cc`). There is no `llvm-link`, `lli`, or `llc`. |
| `leech run` | Build the native executable, then `exec` it. |
| Standard library | Ship it as source in the wheel and compile it on demand in every build. Nothing is prebuilt or cached. |
| Outputs | `leech-out/` next to the root file. The executable is `leech-out/<stem>`, and intermediates are under `leech-out/<stem>.obj/`. |
| Optimization | `-O0` by default. `-O1`, `-O2`, and `-O3` are optional. |
| Versioning | A static version in `pyproject.toml`, changed with `uv version --bump`. There are no tags yet. |
| Upload guard | The `Private :: Do Not Upload` classifier. |
| Extras | `leech check`, `leechc --emit`, `-O`, `--version`, `$CC`, toolchain errors, and `leech doctor`. `-I` and `LEECH_PATH` (#41) were added and then removed (see [Import paths](#import-paths-41-superseded)). |
| Option names | gcc/clang names where one fits, rustc/cargo names otherwise (see [Option naming](#option-naming)). |
| Test harness | A later, separately deferrable task moves `tests/harness.py` onto the same native pipeline. |
| Docs | The #23 quickstart waits for `leech run` and `leech build`, then documents them instead of the manual pipeline. |

## Precedents

### Compiler and build-tool split

| Toolchain | Low-level compiler | User-facing build/run | How the unit graph is found | Output location |
| --- | --- | --- | --- | --- |
| Rust | `rustc` compiles one crate (a crate root plus its `mod` tree) and can link an executable. | `cargo build` / `cargo run` / `cargo check` orchestrates `rustc` per crate. | `Cargo.toml` manifest | `target/debug`, `target/release` per profile ([Cargo build cache](https://doc.rust-lang.org/cargo/reference/build-cache.html)) |
| Zig | `zig build-exe` / `build-obj` compile a root file and its imports. | `zig run` builds and runs a file. `zig build` runs a `build.zig` program. | Imports from the root file, or `build.zig` | `zig-out/` install prefix, plus `.zig-cache` and a global cache ([Zig build system](https://ziglang.org/learn/build-system/)) |
| Swift | `swiftc` is a driver that runs per-file frontend jobs and links. | `swift build` / `swift run` (SwiftPM) | `Package.swift` | `.build/` ([SwiftPM](https://www.swift.org/documentation/package-manager/)) |
| C/C++ (Clang) | `clang -c` compiles one translation unit. The same driver also links (`clang a.c -o a`). | External tools such as Make, CMake, and Ninja | Build scripts; no module graph | Wherever the build script says ([Clang driver](https://clang.llvm.org/docs/DriverInternals.html)) |
| Go | `go tool compile` is internal and rarely used directly. | One `go` command: `go build`, `go run file.go`, `go vet` | Imports, plus `go.mod` | `go build` writes to the working directory. `go run` uses a temporary directory ([cmd/go](https://pkg.go.dev/cmd/go)). |
| Haskell | `ghc -c` compiles one module. | `ghc --make Main.hs` follows imports. Cabal or Stack add packages. | Imports from the root module | Next to the sources by default ([GHC make mode](https://downloads.haskell.org/ghc/latest/docs/users_guide/using.html#make-mode)) |

**What the precedents support.** Every listed toolchain separates "compile one unit" from
"build a program". The split shows up in the interface (Rust, Swift, Go), in the driver
(Clang, Swift), or both. Leech already compiles one module at a time and links with LLVM
tools, so it has Clang's `-c` shape. A dedicated program builder is the missing layer,
which matches `rustc`/`cargo` and `swiftc`/`swift build`.

Go, Zig and GHC show that a builder can find the whole program by following imports from a
root file, with no manifest. Leech's file-path imports have the same property, and
`ModLoader` already does the traversal. This lets the build tool ship before the manifest
question (#41's "Not this issue") is settled. A future manifest replaces how the build finds
the root and its search paths, not the build pipeline itself.

Leech diverges from `rustc` and `zig build-exe` in one respect: `leechc` does **not** gain a
whole-program `-o exe` mode. Leech's compilation unit is one module, not a crate, so a
whole-program `leechc` would duplicate `leech build`. That would give two entry points to
test and document. A Clang-style "driver that also links" was considered and rejected for
the same reason.

### Standard library distribution

| Toolchain | Ships | Built how |
| --- | --- | --- |
| Rust | A prebuilt `rust-std` per target via rustup. `rust-src` is a separate optional component used by `-Zbuild-std` and tools ([rustup components](https://rust-lang.github.io/rustup/concepts/components.html)). | Ahead of time by the Rust project |
| Swift | Prebuilt stdlib binaries plus module interfaces | Ahead of time |
| C/C++ | Prebuilt `libc`, `libstdc++`, or `libc++` plus headers | Ahead of time by the system |
| Go 1.20+ | Source only | On demand, cached in the build cache. The Go 1.20 release notes say: "packages in the standard library are built as needed and cached in the build cache… This change reduces the size of the Go distribution and also avoids C toolchain skew" ([Go 1.20](https://go.dev/doc/go1.20#go-command)). |
| Zig | `lib/std` as source; `compiler_rt` and libc shims built on demand | On demand, cached in the global cache |

The tradeoffs for Leech:

| Option | Pros | Cons |
| --- | --- | --- |
| **Source, compile every build (chosen)** | Works with `uv_build` as-is. One artifact for every target and flag combination. The stdlib can never be stale against the compiler. Generic stdlib code (`std::mem`) is instantiated in user modules anyway. Type checking needs the source regardless, because Leech has no interface files. | Repeats work in every build. That costs about 0.5 s today and grows with the stdlib. |
| Source plus a persistent cache | Repeated builds are fast. This is Go and Zig's current design. | Needs cache keys (compiler version, source hash, target, flags), invalidation, and concurrency safety. It pays off only once the stdlib or user programs are large. |
| Prebuilt objects in the wheel | No stdlib compile cost. | `uv_build` has no build hooks, so this needs a different backend. Artifacts are per target and per flag, but Leech has one target, no ABI stability, and no interface format. It saves only the non-generic codegen of 60 lines. |

Go moved *from* prebuilt archives *to* source plus cache. That supports treating the cache
as the later optimization and not shipping prebuilt artifacts. The cache is a recorded
[future issue](#future-issues-not-in-this-plan).

## Packaging and installation

### `pyproject.toml`

- Two console scripts: `leechc = "leech.driver:main"` and `leech = "leech.cli:main"`.
  Remove `leech.main` from `src/leech/__init__.py`.
- License metadata per PEP 639 ([PEP 639](https://peps.python.org/pep-0639/)): set
  `license = "MPL-2.0"` and `license-files = ["LICENSE", "LICENSES/*.txt"]`. `uv_build`
  copies `project.license-files` into the sdist and into the wheel's `.dist-info`
  ([uv build backend](https://docs.astral.sh/uv/concepts/build-backend/)).
- Add `classifiers` including `"Private :: Do Not Upload"`. PyPI rejects any distribution
  with a classifier beginning `Private ::` ([PyPI classifiers](https://pypi.org/classifiers/)),
  so accidental uploads are blocked. Also add `Programming Language :: Python :: 3.14` and
  `Operating System :: POSIX :: Linux`.
- The version stays static. `uv_build` supports no dynamic versions or build hooks, and
  dynamic VCS versions would need a backend switch for no current benefit. Bump it with
  `uv version --bump patch|minor|major` ([uv packaging guide](https://docs.astral.sh/uv/guides/package/)).
  Under pre-1.0 SemVer, any minor bump may break compatibility. Git tags and a release
  process are deferred until something is distributed.
- Keep the `uv_build` version bound in step with the uv in use. It is `<0.12` today, while
  uv 0.12.3 is installed. Bump it in the same change only if `uv build` warns.

### Install workflows

From a checkout:

```bash
uv build                                   # dist/leech-<v>.tar.gz and dist/leech-<v>-py3-none-any.whl
uv tool install .                          # isolated tool env, leech and leechc on PATH
uv tool install --editable .               # development: source edits take effect without reinstalling
uv tool install --reinstall .              # pick up changes in a non-editable install of the same version
uv tool install dist/leech-<v>-py3-none-any.whl
uv tool uninstall leech
uv tool update-shell                       # if uv's tool bin directory is not on PATH
```

uv downloads Python 3.14 if needed (`requires-python = ">=3.14"`). Inside the repository,
`uv run leechc …` and `uv run leech …` keep working without installing.

### Version reporting

`leechc --version` and `leech --version` print one line, for example
`leechc 0.0.1 (LLVM 20.1.8 via llvmlite 0.47.0; target x86_64-linux-gnu)`.
The version comes from `importlib.metadata.version("leech")`, so it always matches the
installed package metadata.

### llvmlite compatibility

`target.DATALAYOUT` was derived by hand under llvmlite 0.47.0, and `ll_emit` relies on
llvmlite's binding and pass-manager APIs. An installed wheel resolves dependencies from its
metadata, not from `uv.lock`, so the dependency becomes `llvmlite>=0.47.0,<0.48`. Moving to
a new llvmlite series is a deliberate change. It re-derives the data layout (as
`target.py` already instructs), runs the full suite, and widens the bound.

## `leechc`: the module compiler

```text
leechc FILE [-o OUT] [--module-name NAME] [--entry]
            [--emit {llvm-ir,llvm-bc,asm,obj}] [-O{0,1,2,3}]
            [--version]
```

- `--emit` defaults to `llvm-ir`, which is today's behavior. At `-O0`, `llvm-ir` output is
  the codegen string written unchanged, not a parse-and-reprint round trip through
  llvmlite. The textual IR names its source with `source_filename`, so assembly and objects
  carry the real file name. `FILE` must end in `.leech` (otherwise a usage error, exit 2),
  and the default output path replaces that suffix with the chosen format's (`.ll`, `.bc`,
  `.s` or `.o`), so it can never overwrite the input.
- `-O` defaults to `0`. With a non-zero level, the module runs through LLVM's default
  per-module pipeline at that level before emission, including for `llvm-ir` (like
  `clang -O2 -emit-llvm`).
- Objects and assembly use PIC relocation (`reloc="pic"`, `codemodel="default"`), so the
  output links into the default PIE executables of modern Linux toolchains.
- `--module-name` is unchanged.
- `--entry` is described in [Program entry](#program-entry).
- Exit status is unchanged: the highest diagnostic severity, so 0 for none or notes, 1 for
  warnings and 2 for errors. Argument errors also exit 2. A consequence is that `leechc`
  exits 1 for the nonexistent-search-directory warning, while `leech build` exits 0 for it.

The LLVM-side work of parsing, linking, optimizing and emitting lives in one new module,
`src/leech/ll_emit.py`, which follows the existing `ll_typs.py` and `ll_layout.py` naming.
`leech build` uses the same functions, so the two commands cannot diverge.

## Program entry

`--entry` (leechc) marks the module being compiled as the program root:

- The module must declare `main` as a non-generic function **with a body** (an ordinary
  source function, not an `extern fn`), with no parameters, returning `i32`. Either access
  is accepted. The call is made from inside the module, so a private `main` works (as in
  Rust). New `UserError`s cover these cases:
  - no `main` at all (spanless, naming the file);
  - `main` that is not a function, such as a module variable or a type (span at the item);
  - an `extern fn main`, a generic `main`, or a wrong signature (span at `main`'s
    signature).
- Codegen emits the user function under its ordinary qualified name (for example
  `@"app::main"`). It also emits an externally visible C-ABI `define i32 @main()` that calls
  it and returns its result.
- `main` becomes a mono-discovery root because of `--entry`, not because of its name.
- The `main::main` special case is **removed**: `SrcFnSymbol.is_main`, the
  unqualified-name branch in `FnInstance.qualified_name`, and the `qualify_name` exception in
  `Mod` item registration. A module named `main` is now an ordinary module, and its `main`
  is `@"main::main"`. Exactly one mechanism decides the entry point.
- Without `--entry`, a module's `main` function is an ordinary function.

Rejected alternative: always compile the root under the qualified name `main`. That needs no
codegen change, but the root could then never be imported (its name would conflict), and a
filename would keep a hidden meaning.

## Module names and import resolution (#99)

Each module is compiled separately under its qualified name, which prefixes its symbols, so
every compilation must agree on every module's name. A module's name therefore comes from
**where its file is**, never from how an import spells it:

- A module's identity is a *package* plus its path within the package. The file
  `<package>/x/a.leech` is the module `x::a`.
- **The root package** is the directory implied by the compiled module's name:
  `src/x/b.leech` named `x::b` makes `src/` the root package. A name that doesn't match the
  file's location is an error (`ModNameLocationMismatchError`). `leechc` defaults the name to
  the file stem. Every module of one program shares the root module's package, so
  separately compiled modules resolve and name every module alike.
- An import that is a link to a file outside the root package and the bundled library is an
  error (`ModOutsidePackagesError`).
- **The bundled library is the package `std`**, so its modules are named `std::io` and so
  on. The prelude is `std::prelude`, loaded into every compilation and imported implicitly.
  Names starting with `std` are reserved: a file outside the bundled library cannot be named
  `std::...` (`StdModNameReservedError`).
- **Imports are absolute.** `import x::a` names `<root package>/x/a.leech` from every file,
  including from `x/b.leech` itself. `import std::...` resolves in the bundled library.
  Any other import resolves in the root package. Imports are no longer relative to the
  importing file.

Because a name is a function of a file's location, and resolution is a function of the
name, one file can't have two names and two files can't share one. The loader asserts both
as internal invariants.

This doesn't design a package system, but it keeps one open. Go, Gleam, Rust and Python also
name modules by location. Absolute imports keep the first segment of a path free to name a
dependency later (`some-package::x::a`), as `std` does now, without the ambiguity that
Python 2's implicit relative imports had. Keeping the package separate from the path inside
it leaves room for named packages, and for multiple versions of a package with a richer
symbol prefix, without renaming modules.

## Import paths (#41, superseded)

`-I DIR` / `--import-path DIR` and a `LEECH_PATH` variable were implemented for #41 as
anonymous search roots sharing the root package's namespace. They were then removed, before
any build tool used them, and #41 was closed as superseded:

- With a package system planned, every use for them is better served by something else.
  Sharing code between projects needs manifest dependencies (#106). A build system driving
  `leechc` directly would need an explicit named mapping such as rustc's `--extern name=PATH`
  or Zig's `-M name=path`, which fits `package-name::x::a` imports. Monorepos and vendored
  code need path dependencies.
- They carried most of the resolution machinery's complexity: ordered precedence between
  anonymous roots, naming a file after the first root containing it, and requiring every
  separate compilation to receive the same ordered roots.
- `LEECH_PATH` made builds depend on the environment.

When packages are designed, a named mapping can be added to `leechc` as their low-level hook.

## Option naming

`leechc` and `leech` take gcc/clang option names where one fits, so C and C++ users can guess
them, and rustc/cargo names where gcc/clang have no applicable precedent. Long options must be
spelled in full: like gcc, clang and rustc, neither command accepts abbreviations.

| Option | Precedent |
| --- | --- |
| `-o FILE`, `-O0`…`-O3`, `--version`, `-h`/`--help` | gcc/clang |
| `--emit {llvm-ir,llvm-bc,asm,obj}` | rustc `--emit`, with the same values. clang's `-S`/`-c`/`-emit-llvm` assume a default of linking, which `leechc` never does. |
| `--module-name NAME` | rustc `--crate-name` |
| `--entry` | none that fits: gcc and ld's `-e`/`--entry SYMBOL` names an entry *symbol*, and rustc's analogue is `--crate-type=bin`. |
| `build`, `run`, `check`, `run ROOT -- ARGS` | cargo |
| `$CC` | make and the Rust `cc` crate |

## `leech`: the program driver

```text
leech build ROOT [-o EXE] [-O{0,1,2,3}]
leech run   ROOT [-O{0,1,2,3}] [-- ARGS...]
leech check ROOT
leech doctor
leech --version
```

`ROOT` must be an existing `.leech` file whose stem is a single Leech identifier, so it
cannot be a qualified name containing `::`. The stem is the root's qualified name.
Reserved words are accepted, matching `leechc`'s unvalidated default stem
(`test_cli_defaults_module_name_to_source_stem` compiles `array.leech`). A reserved stem
only means the root cannot be imported by that name. A nonexistent `ROOT`, a
non-`.leech` `ROOT`, or an invalid stem is a usage error: an error message that names the
problem, and exit 2.

For `run`, `leech` splits its own argv at the first literal `--` before argparse sees it.
Everything after the `--` goes to the program untouched, even tokens that look like
`leech` options. The left side is parsed normally, so options may come before or after
`ROOT`. `argparse.REMAINDER` is not used, because it swallows `-O`
when they follow `ROOT`.

### Pipeline

1. **Discover.** Create a `ModLoader`, load `ROOT` under its stem, and check
   declarations. `loader.mods` then holds every reached
   module, including the prelude and std modules, with its location-based qualified name
   (see [Module names and import resolution](#module-names-and-import-resolution-99)).
2. **Compile each module.** Compile every module in discovery order under its qualified
   name. `--entry` is set for the root only. Each compilation uses a fresh `ModLoader`,
   which is how `leechc` works and how the harness works today. Bundled ASTs are already
   process-cached, and the root's discovery result is reused as its own compilation. The
   IR is written to `leech-out/<stem>.obj/<qualified/name/as/path>.ll`.
3. **Link** the IR in-process (`ll_emit`), and verify the linked module. A verification
   failure is an internal compiler error that points at the saved `.ll` files.
4. **Optimize** at the requested `-O` level. Optimizing after linking lets LLVM inline across
   modules.
5. **Emit** a PIC object to `leech-out/<stem>.obj/<stem>.o`.
6. **Link the executable** with `$CC` (default `cc`):
   `<cc> <obj> -o <exe>`. `<exe>` is `-o` if given, otherwise `leech-out/<stem>`, and it
   is always resolved to an absolute path. One resolver turns `$CC` into an argv, and
   `doctor` shares it. An unset, empty or whitespace-only `CC` means `cc`. A `shlex.split`
   failure, such as unmatched quotes, is a driver error that names `CC` and quotes its
   value. So is an executable that `shutil.which` cannot find. None of these produce a
   traceback.

The first build creates `leech-out/` with two regular files, if they are absent:

- a `.gitignore` containing `*`;
- a `CACHEDIR.TAG` ([cache directory tagging](https://bford.info/cachedir/)). The
  specification requires the file to start with exactly
  `Signature: 8a477f597d28d172789f06886806bc55`. Leech writes that line, followed by a
  `#` comment naming Leech and the specification's URL.

That keeps build outputs out of version control and backups without the user editing
anything.

The `<stem>.obj` directory name cannot collide with an executable name: a module name is an
identifier, so it cannot contain `.`. This also keeps intermediates of different roots in the
same directory apart. The approved layout said `leech-out/obj/…`, but a root named `obj`
would collide with that.

### Diagnostics and exit status

- Compiler diagnostics render exactly as `leechc` renders them.
- Each module compilation re-loads the graph, so one warning can be reported by several
  compilations, each with fresh `SrcFile` and `SrcSpan` objects. Those classes compare by
  identity, so `leech` deduplicates by a structural key. The key covers:
  - the diagnostic class;
  - for the primary message and every extra message, in order: the level, the text, and
    the resolved file path with the start and end line and column (or `None` when
    spanless).

  Two reports with the same primary but different notes are therefore both printed.
  Nothing useful is discarded. Until per-compilation diagnostics (#93)
  land, the driver resets the process-global registry between compilations through a small
  public `errors` API. #93 then replaces that API.
- Compilation stops at the first module with an error. It does not link, and it does not
  write an executable.
- Exit status:
  - 0 on success, including when there are warnings.
  - 1 on any build failure: a compile error, a missing or misconfigured
    toolchain, or a failed link.
  - 2 on a usage error, including an invalid `ROOT`.

  This differs deliberately from `leechc`'s severity-based status. `leech` reports whether
  the build succeeded, not the highest severity.

### `leech run`

`run` builds exactly as `build` does, without `-o`, then replaces the `leech` process with
the program (`os.execv(exe, [str(exe), *ARGS])`). `exe` is absolute, so this works however
`ROOT` was spelled relative to the current directory, and `argv[0]` is that absolute path.
An `OSError` from `execv`, for example on a `noexec` mount, is reported as a driver error
with exit 1. The program's stdout, stderr, exit status and
termination signal are therefore exactly the program's own, as with `cargo run` and
`go run`. Arguments after `--` are passed through. Build diagnostics go to stderr before the
exec.

### `leech check`

`check` runs steps 1–2 in memory and writes nothing. `leech-out` is not created. Many Leech
errors only appear during lowering and monomorphization, which happen in codegen, so `check`
runs all of codegen except object emission. Its exit status matches `build`'s. Like
`cargo check`, it is the quick editor or CI loop.

### `leech doctor`

`doctor` prints the leech version, the Python version, the llvmlite and LLVM versions, the
target triple, and the resolved `$CC` command and its version. It then builds and runs a
one-line program in a temporary directory, and reports success, or the failing step with a
fix (install `gcc` or `clang`, or set `CC`). It exits 0 when every check passes and 1
otherwise.

## Standard library and prelude provisioning

Bundled sources stay in `src/leech/std/` and ship in the wheel. The loader already resolves
the prelude and `import std::…` against the package directory, so an installed `leech`
finds them with no configuration. `leech build` compiles and links exactly the bundled
modules the program reaches. That is always the prelude, plus each imported `std::*`
module. A user's own module never shadows them via a search path. Nothing about the stdlib
is prebuilt or cached (see [Precedents](#standard-library-distribution) for the rationale).

## Toolchain requirements and errors

| Need | Provided by | Failure handling |
| --- | --- | --- |
| Python 3.14 | uv | uv installs it |
| LLVM (parse, link, optimize, emit) | llvmlite wheel (LLVM 20.1.8) | n/a |
| Linking an executable, libc, and C runtime | `$CC` or `cc` (gcc or clang) | Not found: "C compiler `cc` not found; install gcc or clang, or set CC". Link failure: the full command, its exit status, and its stderr. |

`leechc` itself never needs `cc`. After the harness migration, the test suite needs only
`cc`, not `llvm-link` or `lli`.

## Testing

- `leechc`: `--emit` for each format (an object starts with the ELF magic number and links
  with `cc`), the output-path derivation, and `-O` changing the output
  IR. Also `--entry` positive and negative cases, location-based names and absolute imports
  including the reserved `std` names, and `--version`.
- `leech`: build or run of a single-file program, a multi-module program with a nested
  package, a program importing `std::io` and `std::mem`, and a private `main`. Also the
  exit status forwarding of `run`, argument forwarding, and signal termination (a `panic`
  aborts). Cover `-o`, the `leech-out` layout plus `.gitignore` and `CACHEDIR.TAG`, and
  duplicate-warning suppression for a warning in a *user* module across fresh loaders. Cover `check` writing nothing, a missing, empty or malformed `CC`,
  a failing link, options after `ROOT` versus program arguments after `--`, and running a
  root given by a path outside the current directory.
- Packaging: a test runs `uv build --wheel` into `tmp_path` and checks the wheel's contents
  (grammar, std sources, both entry points, license files, the `Private` classifier, and
  the bounded llvmlite requirement). A manual acceptance check covers the non-editable path:
  - install the built wheel with `uv tool install` into isolated temporary
    `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` directories;
  - from outside the checkout, run `leechc --version` and then `leech run` on a
    hello-world program.

  The resolution path the wheel exercises is not the locked one.
- Existing tests are updated for the rename (`run_cli` invoking `leechc`) and for the entry
  change (`define i32 @"main"` assertions become `--entry` / `@main` wrapper assertions).

## Documentation

- `AGENTS.md`'s command list switches to `uv run leechc …`, `uv run leech run …`, and the
  install commands. Its tool requirements change from `llvm-link`/`lli` to `cc` once the
  harness migrates.
- `README.md` gains a short "Install" section. The #23 quickstart (paused on branch
  `issue-23-quickstart-docs`) is rewritten on top of `leech run`/`leech build`. Manual
  `leechc` plus linking moves to an optional "under the hood" section that uses
  `--emit=obj` and `cc`.
- `docs/specs/2026-09-24-user-documentation-design.md` and its plan describe the old manual
  pipeline. #23's own work updates them when it resumes. This plan does not edit them.

## Compatibility and migration

Pre-1.0, single developer, nothing published. The breaking changes are accepted:

- `leech FILE` (compile) becomes `leechc FILE`. `leech FILE` then fails with argparse's
  "invalid choice" usage error.
- A module named `main` no longer emits `@main`. Manual pipelines must pass `--entry`.

## Alternatives rejected

- **Whole-program `leechc -o exe`** (like `rustc` or `zig build-exe`): duplicates `leech build`
  for a compiler whose unit is one module.
- **A single `go`-style command with a hidden frontend**: the user explicitly wants a
  `leechc` compiler command.
- **A manifest now**: premature (#41), and it is not needed to find the program.
- **`leech run` via an in-process JIT**: a crash or `abort()` would kill the driver, and exit
  or signal semantics would differ from the built binary.
- **`leech run` via `lli`, or shelling out to `llvm-link`/`llc`**: needs a system LLVM whose
  version must accept llvmlite's IR, and does not exercise the native link.
- **Prebuilt stdlib, or a cache now**: see [Precedents](#standard-library-distribution).
- **Dynamic VCS versioning**: needs a backend switch, and the user chose no tags yet.
- **Output in the current directory, or in an XDG cache**: the user chose a project-local
  directory beside the root, so the same program always gets the same output location.
- **`target/` or `.leech/` naming**: `target/`'s debug/release split is unnecessary while
  every build is a full rebuild, and a hidden directory is less discoverable.

## Risks and open questions

- **Quadratic loading.** Each module compilation re-loads the whole graph. That is fine at
  current program sizes. One loader codegenning every module is the fix, but
  `mono.discover` drains a request log shared across the compilation, so this is not a
  drop-in change. Recorded as a future issue alongside the build cache.
- **llvmlite's LLVM is the only LLVM.** IR or objects are tied to LLVM 20.1.8. A system
  `cc` that runs LTO on these objects is not supported. Plain linking is
  version-independent.
- **Global diagnostics state** (#93) needs a temporary reset API, which #93 later removes.
- **Open question:** when the harness moves to native linking, how much does a `cc` link
  per test cost compared with `lli`? Measure it in that task. The task can be deferred
  without affecting the user-facing work.

## Issue breakdown

Existing issues touched:

- **#41** Wire up a module search path: implemented as `-I`/`LEECH_PATH`, then removed in
  favour of absolute, location-based module names (#99) and a future package system. Closed as
  superseded.
- **#23** Write user documentation: its quickstart becomes blocked by the `leech build` and
  `leech run` issues. Add the edges to #55.
- **#93** Per-compilation diagnostics: not blocking. The driver's temporary reset API is
  removed when #93 lands.

New issues:

| Issue | Title | Blocked by |
| --- | --- | --- |
| #95 | Package the compiler for local installation with uv | none |
| #96 | Rename the compiler command to `leechc` | none |
| #97 | Replace the `main`-module entry rule with `leechc --entry` | none |
| #98 | Emit bitcode, assembly, and objects from `leechc`, with optimization levels | none |
| #99 | Name modules by location, with absolute imports and a `std` package | none |
| #100 | Add `leech build` to build a program and its imports into an executable | #97, #98, #99 |
| #101 | Add `leech run` to build and execute a program | #100 |
| #102 | Add `leech check` for diagnostics without output | #100 |
| #103 | Report toolchain problems clearly and add `leech doctor` | #100 |
| #104 | Run compiler tests through the native build pipeline | #97, #98, #100 |

#96 should land before #97 and #98 because they all edit the same CLI, but this is a
sequencing preference, not a hard dependency.

### Future issues (not in this plan)

Suggested for filing now or later, at the owner's discretion:

- A persistent build cache and incremental rebuilds (Go `GOCACHE` / Zig global cache style),
  including one loader codegenning every module.
- A package manifest (`leech.toml`), `leech new`, and project-root discovery (per #41's
  "Not this issue").
- Program arguments: `main(argc, argv)` or a `std::env`, so `leech run -- ARGS` becomes
  meaningful.
- DWARF debug information, so Leech executables are debuggable with gdb or lldb.
- A release process: tags, changelog, and distributing wheels, when the first external
  user appears.
