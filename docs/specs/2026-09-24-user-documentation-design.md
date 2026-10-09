<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# User Documentation with Tested Leech Examples — Design

For: #23: Write user documentation with automatically tested code snippets

Implementation plan: [User documentation plan](../plans/2026-09-24-user-documentation.md).

## Outcome and audience

A systems programmer should be able to find the documentation from `README.md`, install Leech
from a source checkout, compile and run a complete program, and then read a concise tour of
the implemented language. The first supported walkthrough environment is x86-64 Linux:
`src/leech/target.py` fixes the output target to `x86_64-linux-gnu`, and the bundled prelude
calls POSIX/C functions. Commands for other hosts are not asserted to work.

The documentation is plain Markdown rendered by GitHub. `README.md` is the entry point and
links to `docs/guide/index.md`; it is a short orientation rather than a second copy of the
guide. The tour assumes experience with C, C++, Rust, Zig, or a similar systems language.
It explains Leech's current behavior without treating proposed syntax as implemented.

## Scope

The first guide contains:

| Page | Reader outcome |
| --- | --- |
| `docs/guide/index.md` | Choose a quickstart or tour chapter; understand the guide's scope. |
| `docs/guide/getting-started.md` | Install Python 3.14, uv, Leech, LLVM tools, and a C linker; compile a `std::io` Hello World, link the prelude and standard module, run with `lli`, and build a native executable. Show a second local-module example and common build failures. |
| `docs/guide/language-at-a-glance.md` | Learn the language's style through careful comparisons with Rust, Zig, C++, and Swift, including the present limits of its ownership and standard library. |
| `docs/guide/tour/01-basics.md` | Functions, integer and boolean types, `let`, mutability, expressions, return values, and coercion examples. |
| `docs/guide/tour/02-control-flow.md` | Blocks, `if`, `while`, labeled control flow, `match`, `never`, enums, and tagged unions. |
| `docs/guide/tour/03-data-and-pointers.md` | Structs, arrays, indexing, pointer mutability, address/dereference, methods, and memory caveats. |
| `docs/guide/tour/04-modules-and-stdlib.md` | Imports, visibility, `extern`, the prelude, `std::io`, `std::mem`, and separate compilation/linking. |
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
[#26](https://github.com/jonathanhaigh/leech/issues/26), and `Option`/`Result` in the
prelude [#88](https://github.com/jonathanhaigh/leech/issues/88). A section must state the
syntax that works now before mentioning a future spelling. The tour's tagged-union and
`match` coverage reflects their current implementation, even though #23 predates them.
The `array[T, N]` spelling and `.[index]` access are already implemented. Although
[#9](https://github.com/jonathanhaigh/leech/issues/9) remains open, the guide must not
present its array respelling as future work. Its still-missing motivation is #11.
The modules chapter must explain that `import pkg::a;` binds the last segment as `a`, and
that the file is resolved relative to the importing file before the bundled library.

Not included: a full language reference, complete standard-library API documentation,
a generated website, packaging/release instructions, support claims for other targets,
or implementation of the language features linked above. The owner has explicitly chosen
the public-guide test boundary over #23's original `docs/**/*.md` wording. Historical
specs/plans are design records, not maintained language teaching material; their old code
fences are intentionally outside syntax-migration coverage. The implementation review
should call out this narrower acceptance when evaluating #23.

## A CLI path for complete programs

The CLI currently passes the source filename's stem as its module name. Imports use their
qualified path as the module name, so compiling `src/leech/std/io.leech` through the CLI
emits `io::...` definitions where an importer expects `std::io::...`. The compiler test
harness already compiles this module under `std::io` when linking runnable tests. This makes
a documented `std::io` build impossible with the existing CLI alone.

Add `--module-name NAME` to the CLI and pass it to `compile_to_llvm_ir` as
`qualified_name`; preserve the stem default. `NAME` consists of one or more valid Leech
identifier segments separated by `::`. The final segment, which an import binds, cannot
be a reserved Leech name; intermediate filesystem path segments may be reserved. Reject
empty or invalid names through argparse with its normal usage message and exit code 2.
Parse the segments with Leech's Lark grammar and use `reserved.is_reserved` as the source
of truth for the final segment. The implicit filename stem keeps its current behavior,
including no new validation of reserved names. The Python `compile_to_ir` API keeps its
existing contract. The option changes only symbol
qualification, not import search paths or the output filename. A program entry point is
emitted only when the root's module name and function name are both `main`; the source
file must therefore be `main.leech` under the default, or be compiled with
`--module-name main`. The guide shows the following shape, with the actual example kept
in the guide rather than duplicated here:

```text
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
mkdir -p "$LEECH_BUILD_DIR"
# Save the displayed Hello World as "$LEECH_BUILD_DIR/main.leech" here.
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
uv run leech "$LEECH_BUILD_DIR/main.leech" -o "$LEECH_BUILD_DIR/main.ll"
uv run leech src/leech/std/io.leech --module-name std::io -o "$LEECH_BUILD_DIR/io.ll"
uv run leech src/leech/std/prelude.leech --module-name prelude -o "$LEECH_BUILD_DIR/prelude.ll"
llvm-link "$LEECH_BUILD_DIR/main.ll" "$LEECH_BUILD_DIR/io.ll" "$LEECH_BUILD_DIR/prelude.ll" -o "$LEECH_BUILD_DIR/program.bc"
lli "$LEECH_BUILD_DIR/program.bc"
LEECH_BUILD_DIR=${LEECH_BUILD_DIR:-build}
llc -filetype=obj "$LEECH_BUILD_DIR/program.bc" -o "$LEECH_BUILD_DIR/program.o"
cc -no-pie "$LEECH_BUILD_DIR/program.o" -o "$LEECH_BUILD_DIR/program"
"$LEECH_BUILD_DIR/program"
```

The guide must explain why the prelude and imported source modules need separate emitted
bodies: the compiler loads imports to resolve names, but ordinary imported functions are
only declared by the importing compilation. Reachable generic instances are instead
emitted in the instantiating module; `std::mem`'s generic functions illustrate that nuance.
This command path stays explicit until Leech has a build driver. It should be smoke-tested
on the supported target. Defaulting every CLI root to `main` would break library modules;
deriving names from a search root awaits #41, and a whole-program driver is wider work.
The explicit option is additive and remains an override if #41 later infers a module name.
The guide creates `build/` under the checkout, which `.gitignore` already ignores. It first
shows a setup Bash fence that creates the directory, then tells the reader to save the
`test=quickstart-hello file=main.leech` program there, then shows separate IR and native
Bash command fences. All three named fences default `LEECH_BUILD_DIR` independently, so
each can be copied into a fresh shell. The smoke test parses those exact fences, redirects
`LEECH_BUILD_DIR` to a temporary directory, writes the named Leech program between setup
and IR, and executes each shell phase from the checkout root. Only the three fixed phase
IDs `quickstart-setup`, `quickstart-ir`, and `quickstart-native` may be executed; missing,
duplicate, out-of-order, or unknown `bash test=...` fences on the quickstart page fail.
Other Bash fences are explanatory and never executed. The test does not transcribe commands
or touch a reader's `build/` contents. `cc -no-pie` is required because `llc` emits a
static-relocation object that a PIE-default C linker rejects; explain that in the guide.
The three executable fences use the literal info strings `bash test=quickstart-setup`,
`bash test=quickstart-ir`, and `bash test=quickstart-native`. They are unique, ordered, and
only valid on `docs/guide/getting-started.md`; unknown `test=` values on Bash fences there
are collection/test errors. The smoke test selects the root Leech fence by its literal
`test=quickstart-hello` ID, so the second multi-module example cannot be mistaken for the
Hello World source.

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
  case, and `file` is unique within it. `file=main.leech` is the required root file;
  other relative files are modules. Reject absolute paths, `..`, duplicate files, and
  unsupported keys during collection. A non-root fence may set `module=QUALIFIED_NAME`
  when its standalone compiled name differs from its path-derived name, notably for
  transitive imports relative to another module's directory. Convert each fence directly
  to `tests.harness.ModSrc`, passing both the explicit relative path and its path-derived or
  overridden qualified name so every emitted symbol matches the name assigned by import
  resolution. Validate `module=` through `ModSrc`, which uses the same Lark-backed
  qualified-name parser as `--module-name`; reject it on the root fence, whose name is
  always `main`.
- The root fence has `mode=run`, `mode=compile`, or `mode=error`. Other module fences omit
  `mode`. `run` and `compile` construct a `tests.harness.TestProgram`; each module is
  compiled independently as well as checked as an import. `run` automatically links the
  prelude and every unshadowed bundled `std::` module, then executes under `lli`. There is no
  `std=` metadata because documentation authors do not maintain the link set manually.
- `mode=run` may specify `exit=N` (default `0`, with `N` from `0` through `255`) or
  `exit=SIGABRT` for an aborting program. An optional `text` fence with `output=ID`
  contains the exact expected output for numeric exits; no output fence means empty
  output. The `markdown-it-py` fence token includes the newline before the closing fence
  in its content. That structural final newline is normally part of expected output;
  extra blank lines remain extra newlines. Add `newline=no` on the output fence to remove
  exactly the last newline, allowing `std::io::print` examples. For `SIGABRT`, the fence
  is an expected **prefix** because `lli` appends its own crash text; a nonempty output
  fence is required and `newline=no` is invalid. The source must demonstrate intentional
  abort. Explain `lli --disable-symbolication` beside aborting examples to avoid a
  crash-handler hang on affected hosts.
  For a numeric exit, `output=` matches stdout exactly and stderr must be empty. For
  `SIGABRT`, `output=` matches the stable prefix of stderr and stdout must be empty. A second
  output fence is an error.
- `mode=error` requires `error=<diagnostic name>` on the root and exactly one `text`
  fence with `diagnostic=ID`. It is a single-file case: no helper module fences.
  Remove the fence's one structural final newline from the diagnostic excerpt, require
  one nonempty line, and assert it occurs in the primary diagnostic message
  (`err.message.message`). The compilation must report exactly one diagnostic, of the named
  kind. Validate at collection time that `error=` is the current name of an error kind.
  This tests the salient message without coupling the guide to source-path formatting,
  caret placement, or secondary notes. A wrong error type, an unrelated registered
  diagnostic, or a successful compile fails.
- By default `run` and `compile` require **no registered diagnostics**. To demonstrate a
  warning, a **single-file** case in either mode may put `warning=<diagnostic name>` on
  its root and exactly one `text diagnostic=ID` fence. Require exactly one registered
  warning of that kind with the stated primary-message excerpt and no other diagnostics.
  Collection validates that the name is the current name of a warning kind. This
  single-file rule avoids counting the same warning twice when a helper module is checked
  as an import and as a standalone compilation. Isolate Leech's module-global diagnostic
  list and level around every case,
  including failures, so examples cannot affect one another or ordinary tests.
- Reject orphan output/diagnostic fences, missing roots, mixed modes, invalid exit values,
  and result fences on the wrong mode at collection time. Every result fence's ID must
  equal its source case's `test=ID`. Cases may contain prose between their file fences,
  and between source and result fences. Their fences cannot interleave with another
  case's fences, and all result fences must appear after their case's last source fence
  and before any fence belonging to a later case. Reject `std=` everywhere, `exit=` outside
  `mode=run`, `warning=` in `mode=error`, `newline=no` on a diagnostic fence, and
  `module=` on the root; recognized keys in invalid positions are errors. IDs are
  page-local, so a tour can reuse a short name on another page. An authoring note
  demonstrates the metadata inside an outer `markdown`
  fence (as below) to avoid creating accidental live `leech` examples.

For example, the guide can present a standalone program and its actual output:

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

A multi-module case adds another `leech test=hello file=helper.leech` fence. The root can
import it; both files are compiled and linked. A compile-only case has `mode=compile` and
no result fence. An expected error uses `mode=error error=...` and a
`text diagnostic=ID` fence. Test IDs and `path:line` appear in pytest item names and
failures; the temporary `.leech` path must not be the only location reported.

## Tooling decision

Use [markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/using.html) to parse
Markdown fences; its fence tokens expose content, info strings, and source line maps. Use
pytest's documented [non-Python collection hook](https://docs.pytest.org/en/stable/example/nonpython.html)
to create one item per case. The collector is small Leech-specific glue over these stable
interfaces and constructs `tests.harness.ModSrc` and `TestProgram` objects for
compile/link/run behavior. Tests of the collector itself cover fence parsing, grouping,
metadata validation, and error reporting.

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
Rust's [Getting Started](https://doc.rust-lang.org/book/ch01-00-getting-started.html)
similarly puts installation and a working program before the deeper language guide.
These are presentation precedents; none determines Leech semantics.

## Acceptance and risks

- The default `uv run pytest` run collects all public-guide Leech cases. A broken fence
  fails with the Markdown path, opening line, and test ID. No public Leech fence is ignored.
- At least one checked example exercises each tour chapter, multi-module linking, `std::io`,
  compile-only mode, and an expected compiler error. A representative positive example for
  every implemented feature named in #23 appears in the tour.
- The quickstart commands run successfully on x86-64 Linux using both `lli` and a native
  executable. A smoke test checks the commands' actual compiler/linker behavior.
- The guide states when a feature is absent or limited and links its live issue. Links and
  syntax are reviewed against the repository before publishing.

The main risks are drift in manually written shell commands, brittle metadata parsing, and
accidental claims of portability or memory safety. A command smoke test, strict collection
validation, and explicit target/ownership language address these. `markdown-it-py` already
arrives transitively through runtime dependency `rich`; the test collector should declare
it directly as a development dependency so that its use is explicit. Ordinary builds do
not use the Markdown parser.
