<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# User Documentation with Tested Leech Examples — Design

For: #23: Write user documentation with automatically tested code snippets

Implementation plan: [User documentation plan](../plans/2026-09-24-user-documentation.md).

## Revision

Revised on 2026-10-27, after the build tooling (#100 `leech build`, #101 `leech run`),
location-based module names and absolute imports (#99), the diagnostic catalogue, and native
test execution landed. The first version taught a manual pipeline: compile each module
separately with `--module-name`, link with `llvm-link`, run with `lli`, then build a native
executable with `llc` and `cc -no-pie`. That option landed (346eb22) and was removed again
when `leech build` began compiling a whole program in one compilation (c3a4d8c, 584c894).
The Markdown example collector landed in aecd743 and has since followed the diagnostic and
module changes. A quickstart written against the manual pipeline (6d60ff8, on the unmerged
branch `issue-23-quickstart-docs`) is superseded by this revision; its navigation pages are
reusable, but its getting-started page and smoke test are not.

## Outcome and audience

A systems programmer should be able to find the documentation from `README.md`, install
`leech` from a source checkout, build and run a complete program, and then read a concise
tour of the implemented language. The supported environment is x86-64 Linux:
`src/leech/target.py` fixes the output target to `x86_64-linux-gnu`, the bundled prelude
calls POSIX/C functions, and executables are linked by the host C compiler. Commands for
other hosts are not asserted to work.

The documentation is plain Markdown rendered by GitHub. `README.md` is a short orientation
that links to the guide; installation and command usage live in the guide, not in a second
copy in the README. The tour assumes experience with C, C++, Rust, Zig, or a similar
systems language. It explains Leech's current behavior without treating proposed syntax as
implemented.

## Scope

The first guide contains:

| Page | Reader outcome |
| --- | --- |
| `README.md` | Learn what Leech is and its status; follow links to install it and read the guide. |
| `docs/guide/index.md` | Choose the quickstart or a tour chapter; understand the guide's scope. Contributors find the example annotation contract. |
| `docs/guide/getting-started.md` | Install `leech` with uv, check the toolchain with `leech doctor`, build and run Hello World with `leech build` and `leech run`, build a program of several modules, find errors with `leech check`, see what `--emit` and `-O` produce, and fix common problems. |
| `docs/guide/language-at-a-glance.md` | Learn the language's style through careful comparisons with Rust, Zig, C++, and Swift, including the present limits of its ownership and standard library. |
| `docs/guide/tour/index.md` | See the chapter list. |
| `docs/guide/tour/01-basics.md` | Functions, `main` and its exit status, integer and boolean types, `let`, mutability, expressions, return values, and coercion examples. |
| `docs/guide/tour/02-control-flow.md` | Blocks, `if`, `while`, labeled control flow, `match`, `never`, enums, and tagged unions. |
| `docs/guide/tour/03-data-and-pointers.md` | Structs, arrays, indexing, pointer mutability, address/dereference, methods, and memory caveats. |
| `docs/guide/tour/04-modules-and-stdlib.md` | The program's package, absolute imports, visibility, `extern`, the prelude, `std::io`, and `std::mem`. |
| `docs/guide/tour/05-generics-traits-comptime.md` | Type and value parameters, trait bounds and impls, compile-time evaluation, and the available compiler builtins. |

Each tour chapter starts with what the reader can do afterward, then uses small **complete
programs** with explanations of the interesting lines. A complete program may have several
module files. Most examples should run; compile-only examples are useful for library
declarations, and expected-error examples teach diagnostics. The tour discusses the small
bundled library through real examples, without claiming a comprehensive library API.

The guide links from relevant sections to open issues for notable gaps, particularly pointer
member syntax [#12](https://github.com/jonathanhaigh/leech/issues/12), trait-method
disambiguation [#11](https://github.com/jonathanhaigh/leech/issues/11), ownership
[#34](https://github.com/jonathanhaigh/leech/issues/34), slices
[#26](https://github.com/jonathanhaigh/leech/issues/26), `Option`/`Result` in the
prelude [#88](https://github.com/jonathanhaigh/leech/issues/88), program arguments
[#107](https://github.com/jonathanhaigh/leech/issues/107), entry-point return types
[#110](https://github.com/jonathanhaigh/leech/issues/110), and `std::process::exit`
[#111](https://github.com/jonathanhaigh/leech/issues/111). A section must state the syntax
that works now before mentioning a future spelling. The `array[T, N]` spelling and
`.[index]` access are already implemented. Although
[#9](https://github.com/jonathanhaigh/leech/issues/9) remains open, the guide must not
present its array respelling as future work. Its still-missing motivation is #11.

The modules chapter teaches the rules in force since #99: the root module's directory is the
program's package; `import x::a;` names `<package>/x/a.leech` from every file, including
files in `x/`, and binds the last segment, `a`; `import std::...` names a module of the
bundled library; a file outside the package cannot be imported; and a module path whose
first segment is `std` is reserved for the bundled library. The prelude is the bundled module
`std::prelude`, whose public names every module sees without an import.

Not included: a full language reference, complete standard-library API documentation,
a generated website, release instructions (#109), support claims for other targets,
or implementation of the language features linked above. The owner has explicitly chosen
the public-guide test boundary over #23's original `docs/**/*.md` wording. Historical
specs/plans are design records, not maintained language teaching material; their old code
fences are intentionally outside syntax-migration coverage. The implementation review
should call out this narrower acceptance when evaluating #23.

## Getting started

The page follows the path a new reader takes:

1. **Requirements.** x86-64 Linux, [uv](https://docs.astral.sh/uv/) (which provides Python
   3.14 if needed), and a C compiler, `cc` or the command named by `CC`, for linking.
2. **Install.** From a source checkout: `uv tool install .`, `--editable` to track the
   checkout, `--reinstall` to pick up changes to a non-editable install,
   `uv tool uninstall leech`, and `uv tool update-shell` when the tool directory is not on
   `PATH`. `uv build` produces a wheel that installs the same way; the package carries the
   `Private :: Do Not Upload` classifier and is not published. Inside the checkout,
   `uv run leech` works without installing. This material moves here from `README.md`.
3. **Check the toolchain** with `leech doctor`.
4. **Hello World.** The reader makes a new directory, saves `hello.leech` there, runs
   `leech build hello.leech` to write `./hello`, and runs it. `leech run hello.leech` builds
   into a per-user cache and runs the result. `main` returns the process's exit status.
   `leech run` passes arguments after `--` to the program, which cannot yet read them (#107).
5. **Several modules.** The directory holding the root module is the program's package. A
   second example adds `greeting.leech` (and, if useful, a nested `text/greeting.leech`) to
   show that `leech build` finds and builds every imported module; there is no separate
   compile or link step.
6. **Errors.** `leech check` reports diagnostics without writing anything; a build that
   fails writes nothing. An expected-error example shows a real message. The full rendered
   layout is described in prose only, because it is expected to change with
   [#118](https://github.com/jonathanhaigh/leech/issues/118).
7. **Under the hood.** One short section: `--emit llvm-ir,asm` writes `hello.ll` and
   `hello.s` for inspection; `-O0`…`-O3` choose the optimization level; `-o` names the
   output when one kind is requested; and `--emit obj` followed by `cc hello.o -o hello`
   is the link that `leech build` performs.
8. **Troubleshooting.** `leech` not found (`uv tool update-shell`), a missing or broken C
   compiler (`leech doctor`, `CC`), a stale non-editable install (`--reinstall`), an import
   that names no file (imports are absolute from the root's directory), and a reserved
   `std` name.

Every command shown is copyable into a shell started in the reader's program directory; no
command refers to files inside the checkout except the install commands.

### Smoke test

Leech fences on the page are tested by the collector like any other. In addition,
`tests/test_doc_build.py` executes exactly one shell fence, the primary path, so the
commands a reader types first cannot drift from the compiler's behavior:

````markdown
```leech test=quickstart-hello file=hello.leech mode=run
...
```

```text output=quickstart-hello
Hello, world!
```

```bash test=quickstart-build
leech build hello.leech
./hello
```
````

The test parses the page, requires exactly one `bash` fence with a `test=` key and requires
its value to be `quickstart-build`; a missing, duplicate, or differently named tested Bash
fence fails. It selects the `test=quickstart-hello` case through the collector, writes that
case's single source file into a temporary directory under its `file=` name, and runs the
Bash fence with `bash -eu -o pipefail` in that directory, in a new session so that a 60-second timeout
can kill the whole process group, including the built program. `PATH` is prefixed with the
directory of the running Python interpreter, so `leech` is the checkout's console script
rather than any separately installed tool. The fence's stdout must equal the case's expected
output, and the command must succeed; a timeout fails the test with the fence's
output so far. Install, `leech doctor`, `leech run`, `leech check`
and `--emit` fences are explanatory and not executed: the install path needs a network and
writes outside the test's directories, and the rest exercise behavior the CLI's own tests
cover. The test never touches a reader's files. The whole suite already requires `cc`, so
the smoke test has no skip.

Precedent: the [Rust book](https://doc.rust-lang.org/book/ch01-00-getting-started.html)
and [Go's tutorial](https://go.dev/doc/tutorial/getting-started) put installation and a
working program built by the language's own driver before the language guide; the original
manual-pipeline quickstart was a stopgap for a driver Leech now has.

## Tested example contract

Collect `README.md` and `docs/guide/**/*.md`. Existing `docs/specs/` and `docs/plans/`
are historical planning material, not user-facing guide pages: many existing `leech`
fences represent future or intentionally invalid code. They are outside this collector.
Within the public scope, **every fenced block whose first info-string word is `leech`
must belong to a valid test case**. There is no silent skip flag. Use inline
code or a `text` fence to show incomplete or hypothetical syntax.

Use CommonMark fences. The first info-string word remains the language for highlighting;
subsequent space-separated `key=value` words are test metadata. Keys and values contain no
spaces or shell quoting. The contract is deliberately small:

- Every `leech` fence has `test=ID` and `file=RELATIVE.leech`; `ID` identifies one page-local
  case, and `file` is unique within it. Reject absolute paths, `..`, duplicate files, and
  unsupported keys during collection. A module's qualified name follows from its path
  relative to the case's directory, as `text/greeting.leech` is `text::greeting`; there is
  no override.
- **The root fence is the one fence of the case with `mode=`.** Its file is a single
  `NAME.leech` at the top of the case's directory, because the root's directory is the
  program's package; the root's module name is `NAME`. A case with no `mode=` fence, or more
  than one, is a collection error, as is a root file in a subdirectory. `NAME` must be an
  identifier other than `std`, as for `leech build`. The collector is deliberately stricter
  than the CLI in one respect: it also rejects a reserved word such as `if`, which
  `leech build if.leech` accepts, because the harness's `ModSrc` validates every module name
  that way and a guide has no reason to name a file after a keyword.
- The root has `mode=run`, `mode=compile`, or `mode=error`, and every mode constructs a
  `tests.harness.TestProgram`. `run` builds and executes the program as `leech build` would,
  so the root must define a valid entry point, `main`. `compile` and `error` check the
  program like a library, without requiring `main`: a compile-only case can show
  declarations alone, and an error case reports only the diagnostic it demonstrates.
  Entry-point diagnostics (such as `missing-main-function`) are therefore outside the
  guide's checked examples; the getting-started and basics pages describe the `main` rules
  in prose. The prelude is part of every program, and a bundled `std::` module is built
  whenever the program imports it, so there is no `std=` metadata.
- `mode=run` may specify `exit=N` (default `0`, with `N` from `0` through `255`) or
  `exit=SIGABRT` for an aborting program. An optional `text` fence with `output=ID` contains
  the exact expected output; no output fence means empty output. The `markdown-it-py` fence
  token includes the newline before the closing fence in its content. That structural final
  newline is normally part of expected output; extra blank lines remain extra newlines. Add
  `newline=no` on the output fence to remove exactly the last newline, allowing
  `std::io::print` examples. For a numeric exit, `output=` matches stdout exactly and stderr
  must be empty. For `SIGABRT`, `output=` matches **stderr exactly**, with the same
  `newline=no` rule, and stdout must be empty. The source must demonstrate intentional abort.
  A second output fence is an error.
- `mode=error` requires `error=<diagnostic name>` on the root and exactly one `text`
  fence with `diagnostic=ID`. It is a single-file case: no helper module fences.
  Remove the fence's one structural final newline from the diagnostic excerpt, require
  one nonempty line, and assert it occurs in the primary diagnostic message. The
  compilation must report exactly one diagnostic, of the named kind. The name is a
  diagnostic kind's current catalogue name, such as `let-type-mismatch`; collection
  rejects unknown names, former names (aliases), and kinds that are not errors. This tests
  the salient message without coupling the guide to source-path formatting, caret placement,
  labels, or notes. A wrong kind, an additional diagnostic, or a successful compile fails.
- By default `run` and `compile` require **no diagnostics**. To demonstrate a warning, a
  **single-file** case in either mode may put `warning=<diagnostic name>` on its root and
  exactly one `text diagnostic=ID` fence. Require exactly one diagnostic, a warning of that
  kind with the stated primary-message excerpt. Collection validates that the name is the
  current name of a warning kind. Each case collects its diagnostics in its own
  `diag.Diags`, so examples cannot affect one another or ordinary tests.
- Reject orphan output/diagnostic fences, missing roots, mixed modes, invalid exit values,
  and result fences on the wrong mode at collection time. Every result fence's ID must
  equal its source case's `test=ID`. Cases may contain prose between their file fences,
  and between source and result fences. Their fences cannot interleave with another
  case's fences, and all result fences must appear after their case's last source fence
  and before any fence belonging to a later case. Reject `std=` and `module=` everywhere,
  `exit=` outside `mode=run`, `warning=` in `mode=error`, and `newline=no` on a diagnostic
  fence; recognized keys in invalid positions are errors. IDs are page-local, so a tour can
  reuse a short name on another page. The contributor note in `docs/guide/index.md`
  demonstrates the metadata inside an outer `markdown` fence (as below) to avoid creating
  accidental live `leech` examples.

For example, the guide can present a standalone program and its actual output:

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

A multi-module case adds another `leech test=hello file=greeting.leech` fence. The root can
import it as `greeting`; the case builds as one program. A compile-only case has
`mode=compile` and no result fence. An expected error uses `mode=error error=NAME` and a
`text diagnostic=ID` fence. Test IDs and `path:line` appear in pytest item names and
failures; the temporary `.leech` path must not be the only location reported.

Exact `SIGABRT` matching replaces the first version's stderr *prefix*, which existed only
because `lli` appended its own crash report. A native program's stderr is exactly what it
wrote. The harness's `check_signal` changes the same way, so documentation cases and Python
tests share one rule.

## Tooling decision

Use [markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/using.html) to parse
Markdown fences; its fence tokens expose content, info strings, and source line maps. Use
pytest's documented [non-Python collection hook](https://docs.pytest.org/en/stable/example/nonpython.html)
to create one item per case. The collector is small Leech-specific glue over these stable
interfaces and constructs `tests.harness.ModSrc` and `TestProgram` objects for
build/run behavior. Tests of the collector itself cover fence parsing, grouping,
metadata validation, and error reporting. `markdown-it-py` is a direct development
dependency; ordinary builds do not use it.

[Sybil](https://sybil.readthedocs.io/en/latest/markdown.html) supports custom evaluators
for non-Python code fences, but this contract requires grouping files, pairing output and
diagnostic fences, and validating all Leech fences together. That still needs a custom
parser/evaluator. [Sphinx doctest](https://www.sphinx-doc.org/en/master/usage/extensions/doctest.html)
primarily models Python sessions and would introduce a site/markup stack for this guide;
[mdBook test](https://rust-lang.github.io/mdBook/cli/test.html) is tailored to Rust code
blocks. Neither directly runs Leech's compiler test harness. The decisive criterion is
one reliable source location and one pytest result per Leech case, not a new publishing tool.

The fence format follows [CommonMark's info-string convention](https://spec.commonmark.org/spec#fenced-code-blocks):
the first word identifies the language. Testing the examples as part of the normal suite
follows [Zig's language-reference practice](https://ziglang.org/documentation/master/),
while a short guided entry follows
[Swift's tour](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/guidedtour/).
These are presentation precedents; none determines Leech semantics.

## Acceptance and risks

- The default `uv run pytest` run collects all public-guide Leech cases. A broken fence
  fails with the Markdown path, opening line, and test ID. No public Leech fence is ignored.
- At least one checked example exercises each tour chapter, a program of several modules
  including a nested one, `std::io`, compile-only mode, an aborting program, and an
  expected compiler error. A representative positive example for every implemented feature
  named in #23 appears in the tour.
- The quickstart's `leech build` fence runs in the suite and produces the documented
  output. A manual check in the final task installs `leech` with `uv tool install` and
  follows the page from a fresh shell.
- The guide states when a feature is absent or limited and links its live issue. Links and
  syntax are reviewed against the repository before publishing.

The main risks are drift in the untested command fences, brittle metadata parsing, and
accidental claims of portability or memory safety. Executing the primary command fence,
strict collection validation, the CLI's own tests, and explicit target/ownership language
address these. Diagnostic rendering is still changing (#118, #119, #120), so the guide quotes
only primary-message excerpts.
