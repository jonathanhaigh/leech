<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Getting started

This page installs the `leech` command from a source checkout, then uses it to build and run
programs. The [user guide](index.md) lists the other pages.

## Requirements

- **x86-64 Linux.** It is the only target Leech supports.
- **[uv](https://docs.astral.sh/uv/).** `leech` is a Python package; uv installs it and
  downloads Python 3.14 if it is not already available.
- **A C compiler**, `cc` or the command named by the `CC` environment variable. `leech`
  uses it to link executables.

## Install

From the root of a checkout of this repository, install `leech` into an isolated tool
environment:

```bash
uv tool install .
```

If uv reports that its tool directory is not on `PATH`, run `uv tool update-shell` and
start a new shell.

That install is a copy of the checkout as it was. Other ways to manage it:

- `uv tool install --editable .` installs instead a `leech` that tracks the checkout, so
  source edits apply immediately.
- `uv tool install --reinstall .` picks up changes in a non-editable install.
- `uv tool uninstall leech` removes it.

`uv build` writes a wheel and a source distribution to `dist/`, and a wheel installs the
same way. When `dist/` holds only the wheel just built:

```bash
uv build
uv tool install dist/leech-*-py3-none-any.whl
```

The package is deliberately not published to any registry: it declares the
`Private :: Do Not Upload` classifier, so package indexes such as PyPI reject it.

Inside the checkout, `uv run leech` works without installing anything.

## Check the toolchain

```bash
leech doctor
```

`leech doctor` prints the versions of `leech`, Python and the C compiler, then builds and
runs a small program. If something is wrong, it explains how to fix it.

## Hello, world

Make a new directory anywhere and save this program in it as `hello.leech`:

```leech test=quickstart-hello file=hello.leech mode=run
import std::io;

fn main() i32 {
    io::println("Hello, world!");
    return 0;
}
```

From that directory, build the program and run it:

```bash test=quickstart-build
leech build hello.leech
./hello
```

```text output=quickstart-hello
Hello, world!
```

`leech build` writes the executable `hello`, named after the program's root module,
`hello.leech`, in the current directory. `import std::io;` makes the bundled standard
library's `io` module available as `io`. `main` takes no parameters and returns an `i32`,
which becomes the process's exit status; other entry-point signatures are tracked in
[#110](https://github.com/jonathanhaigh/leech/issues/110).

To build and run in one step, without writing anything to the current directory:

```bash
leech run hello.leech
```

`leech run` builds into a per-user cache directory, then runs the program. Its output and
exit status are the program's own. Arguments after `--` are passed to the program, though
a Leech program cannot read them yet
([#107](https://github.com/jonathanhaigh/leech/issues/107)).

## A program of several modules

The directory holding the root module is the program's *package*. An import names a
module by its path in the package, so `import greeting;` is the file `greeting.leech` next
to the root, and `import text::words;` is `text/words.leech`. Imports are absolute: they
mean the same file wherever they appear, even in a module inside `text/`.

This program has three modules. `greet.leech` is the root:

```leech test=several-modules file=greet.leech mode=run
import std::io;
import greeting;

fn main() i32 {
    io::println(greeting::message());
    return 0;
}
```

`greeting.leech` sits next to it:

```leech test=several-modules file=greeting.leech
import text::words;

pub fn message() *u8 {
    return words::hello();
}
```

and `text/words.leech` is in a subdirectory:

```leech test=several-modules file=text/words.leech
pub fn hello() *u8 {
    return "Hello from text::words";
}
```

```text output=several-modules
Hello from text::words
```

`leech build greet.leech` finds every module the root imports, directly or indirectly,
and builds them all into one executable, `greet`. There is no separate compile or link
step. `pub` makes a function visible to other modules; without it, a function is private
to its own module.

## Finding errors

`leech check` reports a program's diagnostics without writing any files:

```bash
leech check hello.leech
```

It prints nothing and exits successfully when the program is correct. For this program:

```leech test=type-error file=answer.leech mode=error error=let-type-mismatch
fn main() i32 {
    let answer: i32 = true;
    return answer;
}
```

it reports an error and exits with status 1:

```text diagnostic=type-error
cannot initialize "answer" of type "i32" with a value of type "bool"
```

Each diagnostic starts with its level and message, such as `ERROR:`, followed by the
source line with a marker under the code at fault. Notes, such as where a variable's type
was declared, follow in the same form. This layout will change as diagnostics improve
([#118](https://github.com/jonathanhaigh/leech/issues/118)). `leech build` and `leech run`
report the same diagnostics, and a build that fails writes nothing.

## Under the hood

`leech build` writes an executable by default. `--emit` takes a comma-separated list of
other outputs, each describing the whole program and named after the root module:

```bash
leech build hello.leech --emit llvm-ir,asm    # writes hello.ll and hello.s
leech build hello.leech -O2                   # optimize: -O0 (the default) to -O3
leech build hello.leech --emit obj -o out.o   # -o names the output of one --emit kind
```

The kinds are `exe` (the default), `obj`, `asm`, `llvm-ir` and `llvm-bc`. An executable is
an object file linked by the C compiler, so these two commands do what
`leech build hello.leech` does:

```bash
leech build hello.leech --emit obj
cc hello.o -o hello
```

## Troubleshooting

- **`leech: command not found`.** Run `uv tool update-shell`, then start a new shell; or
  run `uv run leech` from inside the checkout.
- **`cannot find C compiler`.** Install gcc or clang, or set `CC` to a C compiler.
  `leech doctor` checks the compiler `leech` will use.
- **An installed `leech` ignores changes to the checkout.** A plain `uv tool install .`
  copies the source; reinstall with `uv tool install --reinstall .`, or install with
  `--editable`.
- **`cannot find module`.** Imports are relative to the root module's directory, not to
  the importing file: `import text::words;` always means `text/words.leech` beside the
  root. `import std::...` names the bundled standard library.
- **`module name "std" ... is reserved`.** Module paths whose first segment is `std`
  always name the bundled library, so a root module cannot be called `std.leech`, and a
  program's own `std/` directory is never imported.
- **`has no "main" function`.** The root module must define `fn main() i32`. A module
  without one can be imported, but not built as a program.
