<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech

Leech is a systems programming language. This repository contains its tools, written in
Python and targeting x86-64 Linux:

- `leech` builds a whole program, its root module and everything it imports, into a native
  executable;
- `leechc` compiles one Leech module at a time to LLVM IR, bitcode, assembly or an object
  file.

## Install

`leech` and `leechc` are installed from a source checkout as a [uv](https://docs.astral.sh/uv/)
tool, from the Python package named `leech`. uv downloads Python 3.14 if it is not already
available. Linking executables also needs a C compiler, `cc` or the one named by `CC`. From the
repository root:

```bash
uv tool install .                # install leech and leechc into an isolated tool environment
uv tool install --editable .     # or: track the checkout, so source edits apply immediately
uv tool install --reinstall .    # pick up changes in a non-editable install
uv tool uninstall leech          # remove it again (by package name)
```

If uv reports that its tool directory is not on `PATH`, run `uv tool update-shell`.

`uv build` writes a wheel and a source distribution to `dist/`. A wheel installs the same
way, with `uv tool install dist/leech-<version>-py3-none-any.whl`. The package declares the
`Private :: Do Not Upload` classifier, so package indexes such as PyPI reject it. The
package is deliberately not published to any registry yet.

Inside the checkout, `uv run leech` and `uv run leechc` work without installing anything.

## Build a program

```bash
leech build hello.leech          # writes the executable leech-out/hello beside hello.leech
./leech-out/hello
```

The root module's directory is the program's package: `import x::a;` names `x/a.leech` in it,
and `import std::io;` names a module of the bundled standard library.
