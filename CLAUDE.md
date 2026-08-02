# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Leech (package/module name `leech`) is a compiler for a small, statically-typed,
Rust/Go-flavored systems language. Source files use the `.leech` extension. The
compiler is written in Python (>=3.14, uses modern generic syntax like
`class Foo[T]:` and `def f[T](...)`) and lowers Leech source all the way down to
textual LLVM IR via `llvmlite`. It does not itself invoke `llc`/`clang`/`lli` —
those are external tools tests shell out to.

The CLI entry point is `leech` (see `[project.scripts]` in `pyproject.toml`),
implemented by `leech.main.run`.

## Commands

Install/sync the dev environment:
```
uv sync
```

Run the full test suite (uses real subprocess calls to the installed `leech`
console script, so use `uv run`, not `.venv/bin/pytest` directly — the latter
breaks `PATH` resolution for the `leech` binary that `tests/test_cli.py` and
`tests/util.py` invoke):
```
uv run pytest
```

Run a single test file / test:
```
uv run pytest tests/test_generic_fns.py
uv run pytest tests/test_generic_fns.py::test_some_case_name
```

Integration tests that actually run compiled output (`tests/util.py`'s
`check_prog_output`) require `llvm-link` and `lli` on `PATH`.

Lint / format (ruff is the formatter — see `[tool.ruff]` in `pyproject.toml`,
100-col line length):
```
uv run ruff check .
uv run ruff format .
```

Type-check (configured via `[tool.basedpyright]` in `pyproject.toml`):
```
uv run basedpyright
```

Compile a `.leech` file by hand (useful for manually inspecting generated IR):
```
uv run leech path/to/file.leech      # writes path/to/file.ll
uv run leech path/to/file.leech -o out.ll
```

## Architecture

The compilation pipeline, in order, is:

1. **Parse** (`parse.py` + `leech.lark`): a Lark LALR grammar turns source text
   into a Lark parse tree. Grammar errors are converted to `UnexpectedCharacterError`
   / `UnexpectedTokenError`.
2. **AST** (`ast.py`): one class per grammar rule, built directly from the Lark
   tree (`Ast.__init__` methods walk `tree.children`). This is a fairly literal
   syntactic representation — no name resolution or typing happens here.
3. **Module loading** (`ir_loader.py`): `ModLoader` parses and builds one
   `ir_module.Mod` per source file reached (directly or via `import`), keyed by
   resolved path so a diamond-imported file is only parsed/built once. It also
   owns the program-wide `ir_traits.ImplRegistry`, since it's the one object
   that can see every module regardless of which one a lookup starts from.
4. **Module building** (`ir_module.py`): `Mod.build()` turns each AST `Defn`
   into an IR item (`Fn`/`FnDecl`/`ModVar`/`StructTyp`/`Trait`/imported `Mod`)
   and binds it into the module's `Env` (`ir_env.py`). `impl` blocks are built
   last, after every struct is declared. Everything is otherwise built lazily
   via `cached_property` — signatures, struct fields, and initializers resolve
   only when something asks for them.
5. **Type checking** (`typcheck.py`) and **type representation** (`typs.py`):
   `Typ` subclasses (`IntTyp`, `PtrTyp`, `StructTyp`, `FnTyp`, ...) are
   *interned* — structurally-equal types are the same Python object, so types
   compare/hash by identity. `TypCheck` walks a function body's AST against its
   declared parameter/return types, producing `TypCheckResults`, a side table
   consulted by lowering (not embedded back into the AST).
6. **Lowering to IR** (`ir_builder.py` + `ir_values.py`): `CfgBuilder` lowers a
   checked function body (or module-var initializer) into a `Cfg` — a NetworkX
   digraph of `BasicBlock`s containing `Instr`s (`AddInstr`, `LoadInstr`,
   `CallInstr`, `PhiInstr`, ...). This is Leech's own SSA-ish IR, independent
   of LLVM.
7. **Compile-time evaluation** (`comptime.py`): `Interpreter` tree-walks a
   `Cfg` to evaluate module-level `let` initializers at compile time (they must
   be knowable without running generated code).
8. **Codegen** (`codegen.py`): `Compiler` walks every module reached from the
   root module (via `ModLoader.mods`) and lowers IR to LLVM IR using
   `llvmlite`. Declares types before functions/variables (so forward
   references across modules resolve), and only compiles bodies belonging to
   the root module — imported modules are left as declarations, resolved at
   link time. Generic function instances and generic struct instantiations
   have no `ModItem` of their own; they're discovered by a fixpoint search
   (`_discover_fn_instances` / `_discover_struct_instances`) over whatever
   instantiations lowering the root module's own bodies requested, since
   that's the only place they're created.

Cross-cutting pieces:

- **Generics**: a generic `Fn`/`StructTyp` is checked once, eagerly, against
  its own type parameters left opaque as `TypParamTyp`. Each concrete use
  (`Fn.instance` / `StructTyp.instance`) builds and caches a monomorphized
  `FnInstance` / instantiated `StructTyp`, substituting concrete types via
  `Typ.substitute_typ_params`.
- **Traits** (`ir_traits.py`): `Trait` declarations and `impl Trait for
  SomeTyp` blocks are tracked in `ImplRegistry`, keyed by a cheap structural
  "head shape" (`head_shape`) then verified structurally
  (`Typ.infer_typ_args`). Enforces an orphan rule (trait or self-type must be
  local to the impl's module) and rejects overlapping impls. Method
  resolution for a concrete type goes through `lookup_member`, which checks
  inherent members first, then applicable trait impls.
- **Scoping** (`ir_env.py`): `Env` is a chained scope (`ChainMap`) with two
  separate namespaces — `VARS` (values, including `GenericFn` placeholders for
  unapplied generic functions) and `CONTAINERS` (types, traits, and imported
  modules, which deliberately share one namespace so none of the three can
  collide by name).
- **Comptime vs. runtime values** (`ir_values.py`): `ComptimeValue` subclasses
  (`ComptimeInt`, `ComptimeStruct`, `ComptimePtr`, ...) represent
  compile-time-known values (used both by the comptime interpreter and by
  constant-folding during lowering); `Instr` subclasses represent
  runtime-computed values within a `Cfg`.
- **Diagnostics** (`errors.py`): every user-facing compiler error/warning is a
  `UserError` subclass carrying a primary `Message` plus optional extra notes,
  each with a `SrcSpan`. `TextErrorRenderer` renders them with a caret
  pointing at the offending source. The CLI's exit code is the worst
  `Level` seen (`NOTE`/`WARNING`/`ERROR`).

## Conventions specific to this codebase

- Method receivers are `*self`/`*mut self` only (pointer receivers) — there is
  no by-value `self`; this is a deliberate language design choice, not a gap.
- Extensive use of `opt_map`/`opt_or_else`/`opt_or_default`/`opt_unwrap`
  (`opt_util.py`) in place of manual `is None` ternaries.
- `assert_eq`/`assert_gt`/`assert_in`/... (`asserts.py`) are preferred over
  bare `assert` where they exist, since they pretty-print both operands on
  failure.
- Docstrings are Sphinx-style (`:param:`, `:return:`, `:raises:`) and are
  often the authoritative explanation of *why* code is structured a certain
  way (e.g. import-cycle workarounds, two-phase construction) — read them
  before assuming something is incidental.
- `Typ` and `ast.Typ` are different things: `ast.Typ` is a parsed type
  *expression*; `typs.Typ` is the resolved, interned type it's turned into via
  `typs.Typ.from_ast`.
- Nullable Python type annotations use `Optional[X]`, not `X | None` — ruff's
  `UP045` (which would rewrite `Optional[X]` to `X | None`) is disabled in
  `[tool.ruff.lint]` for this reason. Plain (non-nullable) unions still use
  `X | Y` PEP 604 syntax as normal.
