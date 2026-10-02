<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Getting started

This guide builds Leech from a source checkout and uses LLVM directly to run
and link its output. The only currently supported target is x86-64 Linux.

## Requirements

Leech requires Python 3.14 and [uv](https://docs.astral.sh/uv/). If a matching
Python is not already installed, uv can download and manage it. Install the
locked Python dependencies from the repository root:

`uv sync`

The complete build also needs `llvm-link`, `lli`, `llc`, and a C linker
installed as `cc`. Check that they are available with:

```bash
uv run python --version
uv --version
llvm-link --version
lli --version
llc --version
cc --version
```

## Hello World

Create the ignored build directory from the checkout root:

```bash test=quickstart-setup
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
mkdir -p "$LEECH_BUILD_DIR"
```

Save this complete program as `build/main.leech`, or as
`$LEECH_BUILD_DIR/main.leech` if you set that variable to another directory:

```leech test=quickstart-hello file=main.leech mode=run
import std::io;

pub fn main() i32 {
    io::println("Hello, world!");
    return 0;
}
```

```text output=quickstart-hello
Hello, world!
```

Compile the root program, `std::io`, and the ambient prelude separately; link their LLVM
IR; then run the linked bitcode:

```bash test=quickstart-ir
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
uv run leech "$LEECH_BUILD_DIR/main.leech" -o "$LEECH_BUILD_DIR/main.ll"
uv run leech src/leech/std/io.leech --module-name std::io -o "$LEECH_BUILD_DIR/io.ll"
uv run leech src/leech/std/prelude.leech --module-name prelude -o "$LEECH_BUILD_DIR/prelude.ll"
llvm-link "$LEECH_BUILD_DIR/main.ll" "$LEECH_BUILD_DIR/io.ll" "$LEECH_BUILD_DIR/prelude.ll" -o "$LEECH_BUILD_DIR/program.bc"
lli "$LEECH_BUILD_DIR/program.bc"
```

The root is named `main` because its filename is `main.leech`; that combination makes the
compiler emit the process entry point. `--module-name std::io` makes the separately emitted
library symbols match the qualified names expected by `import std::io`. Imports let the
compiler resolve declarations, but ordinary imported function bodies still come from the
separately compiled module. The prelude provides the external declarations and routines
used by `std::io`. Generic functions are the exception: reachable instances are emitted by
the module that instantiates them, so a generic-only module such as `std::mem` may not add a
separately linked function body.

To produce a native executable from the same bitcode:

```bash test=quickstart-native
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
llc -filetype=obj "$LEECH_BUILD_DIR/program.bc" -o "$LEECH_BUILD_DIR/program.o"
cc -no-pie "$LEECH_BUILD_DIR/program.o" -o "$LEECH_BUILD_DIR/program"
"$LEECH_BUILD_DIR/program"
```

The current LLVM output uses static relocations, while many Linux C toolchains default to
position-independent executables. `-no-pie` prevents that default from rejecting the
object.

## A local module

A case can contain multiple Leech files. Here `main.leech` imports `greeting.leech`; both
modules must be compiled and linked in a manual build.

```leech test=quickstart-mod file=main.leech mode=run
import std::io;
import greeting;

pub fn main() i32 {
    io::println(greeting::message());
    return 0;
}
```

```leech test=quickstart-mod file=greeting.leech
pub fn message() *u8 {
    return "hello from another module";
}
```

```text output=quickstart-mod
hello from another module
```

Compile the helper separately, include its output in the `llvm-link` command, and keep its
filename stem aligned with the name used by `import greeting`. For example, after saving
the two displayed files under `build/local/`, the complete IR build is:

```bash
uv run leech build/local/main.leech -o build/local/main.ll
uv run leech build/local/greeting.leech -o build/local/greeting.ll
uv run leech src/leech/std/io.leech --module-name std::io -o build/local/io.ll
uv run leech src/leech/std/prelude.leech --module-name prelude -o build/local/prelude.ll
llvm-link build/local/main.ll build/local/greeting.ll build/local/io.ll build/local/prelude.ll -o build/local/program.bc
lli build/local/program.bc
```

Package search paths and a whole-program build driver are planned in
[GitHub issue #41](https://github.com/jonathanhaigh/leech/issues/41).

## Troubleshooting

- A missing-command error means the corresponding LLVM tool or `cc` is not on `PATH`.
- An unresolved symbol usually means an imported module, `std::io`, or the prelude was not
  included in the `llvm-link` command.
- If an imported function remains unresolved despite linking its file, compile that file
  with the qualified `--module-name` expected by the import.
- Compiler diagnostics name the source file and location that must be corrected before an
  `.ll` output is written.
- A native linker complaint about PIE relocations means the final `cc` command needs
  `-no-pie` on the supported target.
- For an intentionally aborting program, use `lli --disable-symbolication program.bc` to
  avoid LLVM's crash symbolizer waiting for input on affected hosts.
