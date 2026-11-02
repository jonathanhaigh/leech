<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech user guide

Leech is a statically typed systems programming language. This guide is for programmers who
already know a systems language such as C, C++, Rust or Zig.

- [Getting started](getting-started.md) installs `leech` and builds complete programs with
  it.
- [Language tour](tour/index.md) introduces Leech's syntax and features (in progress).

The guide describes Leech as it is implemented today, on its one supported target, x86-64
Linux. Where an important feature is missing or limited, the text links the GitHub issue
that tracks it.

## Tested examples, for contributors

Every `leech` fence in `README.md` and `docs/guide/` is compiled, and usually run, by
`uv run pytest`. Fence metadata after the language word groups the fences of one example
and states what it should do. The outer `markdown` fence below shows the annotations
without itself being tested:

````markdown
```leech test=hello file=hello.leech mode=run
import std::io;

fn main() i32 {
    io::println("hello");
    return 0;
}
```

```text output=hello
hello
```
````

- `test=ID` names the example; it is unique within its page. Every fence of the example
  carries it.
- `file=PATH` is the fence's file, relative to the example's directory. A module's name
  follows from its path, so `text/words.leech` is `text::words`.
- The root module is the one fence with `mode=`, and must not be in a subdirectory.
  `mode=run` builds and runs the program; `mode=compile` only checks it, without requiring
  `main`; `mode=error` expects it to fail.
- `mode=run` may add `exit=N` for a nonzero exit status, or `exit=SIGABRT` for a program
  that aborts. A following `text output=ID` fence holds the exact output: stdout, or stderr
  for an aborting program. No output fence means no output. Add `newline=no` when the
  output does not end with a newline.
- `mode=error error=NAME` names the expected diagnostic, such as
  `error=let-type-mismatch`, and a following `text diagnostic=ID` fence holds one line of
  its message. The example must have a single file. A single-file `run` or `compile`
  example may instead show a warning with `warning=NAME` and a `diagnostic=` fence.

`tests/doc.py` implements and documents the complete contract. Use a `text` fence or
inline code for anything that should not be tested, such as syntax that is not yet
implemented.
