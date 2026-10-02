<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech user guide

Leech is a systems programming language that compiles to LLVM IR. This guide
targets programmers already familiar with a systems language such as C, C++, or
Rust.

- [Getting started](getting-started.md) installs the compiler and builds
  complete programs.
- [Language tour](tour/index.md) introduces Leech's syntax and features (in
  progress).

This guide describes the compiler as it exists today. Where an important
feature is not yet implemented, the relevant GitHub issue is linked inline.

## Tested examples for contributors

Every `leech` fence in the README or `docs/guide/` is collected by pytest. A case has a
page-local `test` ID, one `main.leech` root with a mode, and optional module and result
fences. For example, the following outer `markdown` fence illustrates the annotations
without creating another live test case:

````markdown
```leech test=hello file=main.leech mode=run
import std::io;

pub fn main() i32 {
    io::println("hello");
    return 0;
}
```

```text output=hello
hello
```
````

Use `mode=compile` when a case need not run. Expected compiler failures use
`mode=error error=UserErrorSubclass` plus a `text diagnostic=hello` fence containing one
stable message excerpt. A non-root source fence uses the same `test` ID and a distinct
relative `file`; add `module=qualified::name` only when path-derived qualification is not
correct. See `tests/doc.py` for the complete validation contract.
