<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech

Leech is a systems programming language. This repository contains its compiler, written in
Python and targeting x86-64 Linux. Its `leech` command builds a whole program, its root module
and everything it imports, into a native executable, an object file, assembly, LLVM IR or
bitcode.

## Install

`leech` is installed from a source checkout as a [uv](https://docs.astral.sh/uv/)
tool, from the Python package named `leech`. uv downloads Python 3.14 if it is not already
available. Linking executables also needs a C compiler, `cc` or the one named by `CC`. From the
repository root:

```bash
uv tool install .                # install leech into an isolated tool environment
uv tool install --editable .     # or: track the checkout, so source edits apply immediately
uv tool install --reinstall .    # pick up changes in a non-editable install
uv tool uninstall leech          # remove it again (by package name)
```

If uv reports that its tool directory is not on `PATH`, run `uv tool update-shell`.

Run `leech doctor` to check that the toolchain can build and run programs. It prints the
versions in use, then builds and runs a test program, explaining how to fix any problem.

`uv build` writes a wheel and a source distribution to `dist/`. A wheel installs the same
way, with `uv tool install dist/leech-<version>-py3-none-any.whl`. The package declares the
`Private :: Do Not Upload` classifier, so package indexes such as PyPI reject it. The
package is deliberately not published to any registry yet.

Inside the checkout, `uv run leech` works without installing anything.

## Build and run a program

```bash
leech build hello.leech          # writes the executable ./hello
./hello
leech run hello.leech            # builds it into a per-user cache, then runs it
leech check hello.leech          # reports diagnostics only, writing nothing
leech build hello.leech --emit llvm-ir,asm   # writes ./hello.ll and ./hello.s
```

`--emit` takes a comma-separated list of `exe` (the default), `obj`, `asm`, `llvm-ir` and
`llvm-bc`; each output describes the whole program, and is written to the current directory
named after the root module, or to the path `-o` names when only one is requested. `-O0`…`-O3`
picks the optimization level. A build that fails to compile or link writes nothing.

The root module's directory is the program's package: `import x::a;` names `x/a.leech` in it,
and `import std::io;` names a module of the bundled standard library.
