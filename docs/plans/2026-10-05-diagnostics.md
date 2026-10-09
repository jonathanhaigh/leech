<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Diagnostics: Collection, Recovery, and Reporting — Implementation Plan

For: #20, #93, #113, #72, #73, #56, and the new issues #114–#122.

Design: [Diagnostics design](../specs/2026-10-05-diagnostics-design.md). Design rationale lives
there and is not repeated here.

Only the planning documents are written in this planning session. The tasks below are for
the later implementation, after manual approval and a separate instruction to implement.

## Global constraints

- Each task is one issue and one or more commits. Each commit references its issue with a
  `For: #N: Title` body line (see `AGENTS.md`). Filing #114–#121 updates the #55 dependency
  graph in the same session (see [Issue tracking](#issue-tracking)).
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run basedpyright`, and `uv run reuse lint` before each commit. Every commit leaves the
  suite green; migrations are staged so that no commit needs a test to be skipped.
- Follow `AGENTS.md` style: module-qualified imports (`from leech import diag`, then
  `diag.Diags`), `Optional[T]`, no multi-line conditional expressions, assertions via
  `asserts`, and docstrings that do not point at plans, specs or issues.
- New files carry SPDX headers. `diag_docs/*.md` files are covered by `REUSE.toml` (or
  carry HTML-comment headers, as `docs/` Markdown does).
- CLI option names follow gcc/clang, then rustc/cargo. `leech check`/`build`/`run` accept
  every diagnostics option, through a shared `DiagnosticOptions` option group (see Task 3b).

## Suggested order and parallelism

```text
#93 ┄┄► #122 ┄┄► #114 ┄┄► #56 ┄┄► #113
#93 ──► #114 ──► #116 ──► #20 closes (with #114)
#93 ──► #115 ──► #116
#114 ──► #72/#73      #114 ──► #117      #93 ──► #113      #114 ┄┄► #113
#115 ──► #118, #119, #120, #121               #118 ┄┄► #119, #121
```

Solid arrows are hard dependencies; dotted arrows are sequencing preferences, which the #55
graph (hard prerequisites only) deliberately omits (#119 and #121 reuse
#118's label layout decisions and its summary line). #113 needs only #93 (for `diag.ReportedError` in discovery's per-request recovery); it does not strictly need #114, but forcing
every declaration during checking is far more useful once a failing declaration no longer stops
the others, so it is ordered after #114. #114 and #115 both touch most raise sites; landing #114 first
keeps #115's codemod purely mechanical. #122 (Task 1b) is a pure refactor with no hard
dependencies, but it rewires the same `Ctx` access paths that #114 builds on, so it goes
between #93 and #114. #56 (Task 2b) uses #114's analysis units and should land before #113,
which forces every declaration, the bundled library's generics included, during checking.

## Task 1 (#93): Move accumulated diagnostics into per-compilation state

**Files:** new `src/leech/diag.py`; `src/leech/errors.py`, `compilation.py`, `ir_loader.py`,
`driver.py`, `build.py`, `doctor.py`, `cli.py`, `typcheck.py`, `ir_values.py`,
`ir_builder.py`, `ir_module.py`; `tests/harness.py`, `tests/doc.py`, `tests/test_cli.py`,
`tests/test_build.py`, `tests/test_doc.py`, `tests/test_match.py`, `tests/test_unions.py`,
new `tests/test_diag.py`.

- Create `diag.py` with `Level` (moved from `errors.py`, re-exported there for now),
  `ReportProof` (constructor guarded by a private module key), `ReportedError(Exception)`
  carrying one, and `Diags`. In this task `Diags` stores `errors.UserError` values:
  `error(err) -> ReportProof` and `warn(err) -> None` (each asserting the diagnostic's
  level; there is no public severity-agnostic `emit`), `merge`, `has_errors`, `any_error()`,
  `level`, and
  `all()` in emission order. Move `build._Diags`' structural key into `Diags` so recording
  deduplicates.
- `ModLoader.__init__` takes a `Diags` and stores it on `compilation.Ctx` (`ctx.diags`).
  `driver.compile_to_ir` and `driver.compile_to_llvm_ir` take an optional caller-owned
  `diags`, creating one when it is omitted, before `load_root`. In this task the first error
  still escapes them as today; `compile_module` is what records it.
- Replace the two `errors.register_error` calls: `typcheck.py` reaches `e.ctx.diags`;
  `ir_values` basic blocks get the sink from `ir_builder.CfgBuilder`, which receives it from
  whoever builds the CFG (`SrcFnSymbol`/`ModVar` pass `self.env.ctx.diags`).
- `driver.compile_module` creates the sink, catches the escaping `UserError`, emits it into
  the same sink, and returns `Compilation(diags=sink.all())`. Behaviour and ordering are
  unchanged (emission order; sorting comes in #114).
- `ReportProof.diag` is the error it proves. The sink keeps each error's proof, and
  `Diags.merge` records the other sink's diagnostics with their proofs (an earlier
  duplicate's proof wins).
- `build.py` keeps one `Diags` per compilation and merges each into a build-level `Diags`
  with `Diags.merge` (deduplicating), replacing `_Diags`. The root compilation is merged
  first.
- Delete `register_error`, `all_errors`, `error_level`, `take_errors` and the module globals.
  Delete `harness.isolated_diags` and `harness.register_error_after_lowering`; the tests that
  used them inspect `Compilation.diags` or a `Diags` passed in explicitly.
  `tests/doc.py` reads warnings from the compilation's sink: `harness.CompilerHarness.compile`
  gains an optional `diags` argument and returns the warnings on `CompiledProgram`.
- Tests (`tests/test_diag.py`): two sequential compilations and two interleaved ones (one
  warning-producing, one clean) have independent diagnostics; a fatal error caught by
  `compile_module` follows warnings emitted before it; deduplication by structural key;
  `ReportProof` cannot be constructed outside `diag` (constructor asserts on the key).

**Acceptance:** #93's criteria; no code reads or writes private `errors` globals; existing
diagnostic ordering, rendering and exit statuses unchanged.

## Task 1b (#122): Make `compilation.Ctx` the root of a compilation's state

Numbered 1b so the task numbers cited in issues stay valid. Design: the spec's
[Compilation state ownership](../specs/2026-10-05-diagnostics-design.md#compilation-state-ownership).
No behaviour change, with one deliberate exception: the prelude's `panic` is now requested
only when something needs it (a synthesized runtime check, or compile-time evaluation),
rather than as a side effect of constructing every module, so a module that never panics no
longer declares `std::prelude::panic` in its IR.

**Files:** `compilation.py`, `ir_loader.py`, `ir_env.py`, `ir_module.py`, `ir_builtins.py`,
`typcheck.py`, `typs.py`, `mono.py`, `codegen.py`, `build.py`, `driver.py`; tests that
construct a `ModLoader`, `Ctx` or `Env` directly (`tests/harness.py`, `test_loader.py`,
`test_builtins.py`, `test_call.py`, `test_diag.py`, `test_generic_fns.py`,
`test_generic_structs.py`, `test_impl.py`, `test_traits.py`, `test_typs.py`, `test_vars.py`).

- `Ctx` creates and owns `diags`, `impl_registry` (`ir_traits.ImplRegistry(self)`), `loader`
  (`ir_loader.ModLoader(self)`) and `builtins`, a small `compilation.Builtins` (or
  `ir_builtins.Builtins`) holding the four intrinsics and the prelude's `panic_ref`.
  Imports that would cycle stay local, as `Ctx.instantiate_fn` does today.
- Two-phase construction: `Ctx.__init__` creates its parts without loading anything. The
  prelude, and the builtin environment that the intrinsics need, are created by an explicit
  `ctx.loader.load_prelude()` (called by `driver.compile_to_ir` before `load_root`) or on
  first access; choose whichever keeps `Mod.__init__`'s prelude injection simplest, and keep
  the existing rule that building the prelude itself sees no prelude.
- `ModLoader` keeps packages, parsing, import resolution and the module table, and takes
  its `Ctx` as a constructor argument; it loses its intrinsic attributes, `impl_registry`
  and `prelude_panic_ref` (which moves to `Builtins`).
- `Env(ctx, parent=None)`: drop the `impl_registry` and `panic_ref` fields and the
  consistency assert; callers use `e.ctx.impl_registry` and `e.ctx.builtins.panic_ref`.
- Drop `Mod.loader`; `Mod` uses `self.env.ctx.loader`. `mono.py`, `codegen.py` and
  `build.py` reach modules through `mod.env.ctx.loader.mods` (or a `Ctx.mods` convenience).
- `driver.compile_to_ir` builds a `Ctx(diags)` and loads through `ctx.loader`.
- Tests: update construction sites; add a test that one compilation's loader, registry,
  builtins and every module's environment share a single `Ctx`, and that `compilation.Ctx()`
  alone does not load the prelude.

**Acceptance:** #122's criteria; the full suite passes with only construction-site test
changes, apart from no longer expecting the unused `panic` declaration.

## Task 2 (#114): Recover from errors at analysis-unit boundaries

**Files:** `compilation.py`, `diag.py`, `ir_module.py`, `typs.py`, `ir_traits.py`,
`ir_env.py`, `ir_loader.py`, `driver.py`, `build.py`, `cli.py`, `errors.py`; tests in
`tests/test_errors.py`, new `tests/test_recovery.py`.

1. **Units.** Add `Ctx.unit(owner, name, compute)`, which memoizes a `UnitResult` (the value,
   or the proof of the error) on the `Ctx`, keyed by a `UnitId` (the owner, compared by
   identity and kept alive, and the unit's name). `UnitResult.capture`, `get` and `failure`
   own the conversion between exceptions and results, with `capture` built on
   `Ctx.recovering`. On computing it pushes a frame on `ctx.unit_stack`, catches
   `diag.ReportedError` (memoizing without emitting) and legacy `errors.UserError`
   (reporting it with `ctx.diags.error`, then memoizing), and raises
   `ReportedError(reported)`. A memoized failure re-raises without emitting. Other
   exceptions propagate uncached. Each unit is a `@property` over a method decorated with
   `@compilation.unit`, a plain `functools.wraps` wrapper calling
   `owner.ctx.unit(owner, method.__name__, ...)` for owners satisfying the
   `compilation.HasCtx` protocol. Add `ctx` properties to the owners
   that lack one: `FnSymbol` (abstract, via `env` in its subclasses), `FnInstance` (via
   `_fn`), `ModVar`, `StructField`, `StructTyp`, `UnionVariantTemplate`, `UnionVariant`,
   `UnionTyp` and `EnumTyp`. Test `Ctx.unit` directly, that results belong to one
   compilation, and that every kind of owner reaches its compilation's `Ctx`.
   The context manager `Ctx.recovering()` is the one place user errors are caught: it
   reports a legacy `UserError` or passes on a `ReportedError`, suppresses it, and yields a
   `Recovery` whose `failure` holds the proof afterwards.
2. **Convert the units** listed in the design's unit table, replacing
   `functools.cached_property`. What the design lists as deliberately not a unit stays as
   it is: state computed while building an item, comptime parameter objects (interned
   process-wide for the bundled library, so a memoized failure would leak between
   compilations), `Mod.designate_entry`, and layout validation.
3. **Cycles.** `detect_cycle` gives each `Cycle` a key of its domain and participant set,
   and every cycle error goes through `Ctx.fail_cycle(cycle, err)`, which reports only the
   first error per key and raises `ReportedError` with its proof every time. (This replaces
   an earlier idea of marking unit frames above the cycle: with every unit unwinding on
   `ReportedError`, the frames on a cycle memoize their failure by propagation, and the
   problem that remains is the same cycle found from another participant.) Tests: mutually
   recursive initializers alongside an unrelated error; mutually infinite structs;
   mutually recursive trait bounds. One diagnostic per cycle.
4. **Module building: stage, then commit.** Restructure `_build_defn` and
   `_build_impl_defn` so each item is validated fully before anything shared is mutated:
   each check-then-commit pair is a context manager that checks on entry and commits only on
   a clean exit. `Env.binding(ns, name, span)` checks that a name can be bound in a scope
   and binds the value the block passes to `bind`. `Mod._binding_item(ns, name, span)`
   builds on it, adding the reserved-name check and the module's item record, so a name is
   checked before the value is even built; `src_fn_symbols` is appended to only after.
   `ImplRegistry.registering(impl)` checks coherence and conflicts, the block builds the
   method symbols and runs `check_complete`, and the impl is registered on exit, re-checked
   first if another impl was registered meanwhile (the registry counts its impls). Then append its
   methods. Record comptime parameters against their owning item (`Ctx.record_comptime_param`
   gains an owner) and add `Ctx.discard_comptime_params(owner)` for rejected items. Wrap
   each item in `try/except (diag.ReportedError, errors.UserError)`: report a legacy error with
   `diags.error` (never re-report a `ReportedError`); a duplicate is dropped; an item rejected for another reason
   with a valid-but-reserved or otherwise bindable name is committed as
   `ir_module.PoisonedItem(reported, kind, span)`, which `ir_env` resolution turns into
   `ReportedError(reported)`. Tests: a duplicate function whose body has an independent error (the
   duplicate's body is not checked: one error); a reserved-name function used by a caller
   (one error); an impl missing a method followed by a call to one of its other methods and
   by a second, overlapping impl (the first impl's error only, then the second impl is
   accepted); a rejected generic item leaves no comptime parameter to validate.
5. **Recovery loop.** `ModLoader.check_declarations` and `Mod.check_declarations` run each
   check in a `with ctx.recovering():` block, and `driver.compile_to_ir` runs `designate_entry` the same
   way. A parse error in the root module propagates, and `compile_to_ir` reports and raises
   it; one in an imported module rejects the `import` item.
6. **Sorted output.** Add `Diags.sorted()` with the design's key and `Diags.merge`; the
   loader calls `diags.note_file(path)` in `ModLoader.load` *before* parsing.
   `Compilation.diags` and `BuildResult.diags` are sorted. Test a multi-module build where a
   later per-module compilation contributes a diagnostic the root compilation did not, and
   pin the build-level order.
7. **Transitional raise.** `driver.compile_to_ir` (and so `compile_to_llvm_ir` and the
   harness) raises the first error of `diags.sorted()` as its original `UserError`. Update
   any test whose program now has a different first error.
8. **ICE rendering.** `driver.main` and `cli.main` wrap the compilation: on a non-`ReportedError`
   exception, render the collected diagnostics, print the `internal compiler error` banner
   and note, then re-raise. The CLIs own the sink: `compile_module`, `build.check` and
   `build.build` accept a caller-owned `diags`, and `build` merges each module's sink even
   when its compilation crashes, so the CLI can render what was found.
9. **Tests** (`tests/test_recovery.py`): independent errors in two functions are both
   reported in source order; a broken struct used by three functions reports once; a broken
   function signature used by several callers reports once; a variable initializer error and
   a function body error both report; an error in an imported module and in the root both
   report, imported module first only if it loaded first; a duplicate definition reports once
   and uses of the name report nothing more; an internal error after a user error renders the
   user error and then re-raises (monkeypatched crash).

**Acceptance:** independent errors in separate function bodies and declarations are all
reported once each, in sorted order; no unit's error is reported twice; #20's two-variable
example still reports one error (that is #116).

## Task 2b (#56): Intern declaration-derived types per compilation

Numbered 2b so the task numbers cited in issues stay valid. #56's body holds the full scope;
in brief, `Typ`s split by whether their identity depends on a source declaration, and each
kind gets its own mechanism instead of one cache on `Typ` that only some subclasses use:

- **Structural** types (`IntTyp`, `BoolTyp`, `VoidTyp`, `NeverTyp`, `PtrTyp`, `ArrayTyp`,
  `FnTyp`, `ComptimeValueTyp`, `EnumBackingTyp`) derive from `typs.InternedTyp`, whose
  metaclass makes construction itself intern: `PtrTyp(t, MUT)` returns the process-wide
  instance for those arguments, so interning cannot be bypassed. Compound ones can't alias
  across compilations, because their keys hold their components' identities.
- **Declaration-derived** types are not interned at all. Each is constructed once by what
  declares it, and compared by identity: struct and union instances by their template,
  which owns its instance cache, as a function owns its instances (`Ctx` keeps only the
  request logs that `mono` drains); an `EnumTyp` by its module; a comptime parameter by
  its declaring item, `ARRAY_TEMPLATE` or an intrinsic.

This is a step towards #14: every applied type is cached by whatever defines its shape, for
as long as that shape lives.

**Files:** `typs.py`, `compilation.py`, `ir_module.py`, call sites constructing structural
types; tests in `test_typs.py`.

- Add `typs._InterningMeta` and `typs.InternedTyp`. Remove `Typ._cache`, `create`, `get`,
  `get_or_create` and `cache_key`; call sites construct types directly. Interned
  constructors take positional arguments without defaults, which the metaclass checks.
- Build comptime parameters directly. `ParsedFnSymbol` builds its parameters once, rather
  than `SrcFnSymbol.comptime_params` rebuilding them and relying on the global cache to
  return the same objects. Parameters no longer need an index.
- Build `EnumTyp` with its constructor in `Mod._build_defn`.
- Move struct and union instances from `Ctx` to their templates
  (`typs.NominalTypTemplate`), and function instances to their `FnSymbol`. `Ctx` keeps
  only the request logs.
- Make `ValueParamTyp`'s written value type and `ComptimeParamTyp.check_declaration`
  units; give `ComptimeParamTyp` a `ctx`. Remove the spec's temporary exception for
  comptime parameter objects.
- Document the interning rule in `Typ`'s docstring.
- Tests: structural types are interned by their arguments; an interned type with a default
  argument is rejected; a function's parameters are built once; two live compilations
  sharing a bundled generic declaration get distinct parameters belonging to their own
  compilations; two `Ctx`s sharing one `EnumDefn` get distinct enums (#56's reproducer); a
  failing comptime parameter check is reported once and memoized.

**Acceptance:** #56's criteria.

## Task 3 (#113): Report every user error before code generation

**Files:** `ir_module.py`, `ir_loader.py`, `typs.py`, `mono.py`, `driver.py`, `codegen.py`,
`build.py`, `errors.py` (or `diag.py`); tests per #113 (`tests/test_structs.py`, `tests/test_unions.py`,
`tests/test_enums.py`, `tests/test_vars.py`, new guard in `tests/test_check.py`).

- Every module item's value implements `compilation.Checkable`, a `check()` unit, and
  `ModItem.check` delegates to it (skipping imports). This replaces
  `NominalTypTemplate.validate_declaration` and `ComptimeParamTyp.check_declaration`.
  `Ctx.unit` asserts a unit never re-enters itself; `ModVar.initializer` detects its cycle
  outside the unit that evaluates it.
- `Mod.check_declarations` forces, as units: every module variable's `typ_check_results`,
  `cfg` and `initializer` (only lowering/evaluating an initializer whose check succeeded);
  every struct and union template's validation instance, field and payload types and
  layout; every enum's `variants` and `backing_typ`.
- Move `mono.discover` from `codegen.Compiler.compile` into checking:
  `driver.compile_to_ir` runs it after `check_declarations` and `designate_entry`, and
  stores the `MonoResult` on the module (`Mod.discover_instances`, read through
  `Mod.instances`); codegen consumes it. Make discovery recover *per
  request*: in each of its work loops (module-variable CFGs, function-instance CFGs, struct
  fields, union payloads), wrap the forcing of one request in `try/except diag.ReportedError`,
  record it as failed, leave it out of `MonoResult`, and continue draining. Test with two
  independently failing generic instances (two infinite-size instances requested from
  different functions), one of which is requested only by an instance that itself fails
  later, and assert the expected set of diagnostics.
- Phase boundary: after checking, which includes discovery (run even when declarations
  have errors, since it recovers per request), if `diags.has_errors`, stop
  (`compile_to_ir` raises per the current transitional rule; `compile_module` returns no
  IR).
- Remove the validation-only forcing from `codegen.Compiler.compile`. Codegen asserts
  `not diags.has_errors` on entry, and any `UserError`/`ReportedError` escaping codegen is
  converted to an internal error.
- Reclassify `LlvmVerificationError` (raised in `build._emit_object`) as an internal error:
  introduce `diag.InternalError(Exception)` and raise it there, so it takes #114's ICE path
  (diagnostics rendered, banner, traceback) rather than being a user diagnostic.
- `build.check` stops after `compile_to_ir` for every module (no IR generation); update its
  docstring.
- Tests: one per declaration kind in #113's table, raised by `driver.compile_to_ir`
  (parametrized in `tests/test_check.py`). Lowering raises no user errors, so the errors
  that depend on an instance are its struct or union layout and member types; the
  discovery test therefore drives `mono.discover` on a module that is loaded but not
  checked (`CompilerHarness.load`), so its declaration errors are first found there. The
  gap cannot reopen silently: codegen converts any user diagnostic it would raise into an
  internal error, so every test program that reaches code generation acts as the guard, and
  a unit test monkeypatches a codegen-time user error to prove the conversion.

**Acceptance:** #113's criteria.

## Task 3b: Build pipeline and CLI restructure

A sub-plan, [Build pipeline and CLI restructure](2026-10-08-build-pipeline-restructure.md),
with its own [design](../specs/2026-10-08-build-pipeline-restructure-design.md) and three
issues (#124, #125, #126). It lands before Task 4: it restructures the pipeline into a `Session` and
`Program`/`CheckedProgram` stages, removes `leechc` in favour of `leech build --emit`, and
checks and generates each program in one compilation. Later tasks' references to
`driver.compile_to_ir`, `leechc` and per-module compilation are read in its terms: the
transitional "raise the first error" rule that Task 4 replaces lives in `Program.check`, and
command-line options are added through its shared option groups. Later tasks below already
name the restructured modules (`program`, `session`, `cli/`).

## Task 4 (#115): Replace `UserError` classes with a diagnostic catalogue

Staged so every commit is green.

**Commit A — model and catalogue.** First write down the classification of all 114
`UserError` classes (in the commit message): user diagnostics become catalogue kinds;
`LlvmVerificationError` is already an `InternalError` after #113, and any other class whose
message says "internal compiler error" joins it. New `diag_kinds.py` with `lookup(name)`
(aliases); `diag.py` gains `DiagKind` (with `aliases`), `MsgKind`, `Msg`, `Label`, `Note`,
`Diag` (with `new`/`with_label`/`with_note` helpers and `promoted_by`), the `DiagArg`
protocol, and `CompilationError(diags)` with `.diags` and `.kinds`. Add one `DiagKind` per
user-diagnostic class (except that the too-many and too-few argument classes share
`argument-count-mismatch`) and one `MsgKind` per distinct note, with names following the
design's naming rules and **normalized** templates. Give every legacy class a `kind` class
attribute pointing at its entry. `typs.Typ` implements `diag_str()` (`Typ.name`) and
`report_proof()` (always `None` until #116); a syntax node is passed to a message as its
`diag_str()`. Catalogue test: unique kebab-case names and
aliases, template fields parse, templates start with a literal lowercase word or `"`, no
trailing `.`.

**Commit B — `CompilationError` and test codemod.** `program.Program.check` raises
`CompilationError` with all diagnostics in sorted order (replacing #114's transitional raise).
A one-off codemod script (kept out of the repo, or under `scripts/` and deleted in the same
commit) rewrites `with pytest.raises(errors.X) as exc_info:` blocks to
`pytest.raises(diag.CompilationError)` plus `assert exc_info.value.kinds == (kinds.X_KIND,)`,
and rewrites `exc_info.value.message.span`/`.extra` accesses to `exc_info.value.diags[0]...`.
Sites without `as exc_info` gain one. Tests whose programs report more than one diagnostic
are fixed by hand. `tests/doc.py` accepts `error=<name>`/`warning=<name>` (rejecting unknown names at
collection), and any fences in `README.md`/`docs/guide/` and the `tests/test_doc.py` fixtures
are rewritten. The raised diagnostics are still `UserError`s, with their existing message
text, so message-text assertions are revisited as each module's raise sites move to the
catalogue in Commit C: an assertion on a whole message becomes a check that the message
contains its key terms, such as the item's name, and a note is identified by its `MsgKind`. Only sites that compile through
`program.Program.check` are rewritten; tests that call the parser, loader or registry directly
still expect the `UserError` class until their module is migrated.

**Commit C… — migrate raise sites, one module per commit** (`parse`, `ir_env`, `typs`,
`ir_traits`, `ir_module`, `ir_loader`, `comptime`, `typcheck`, `toolchain`/`program`/`cli`):
`raise errors.X(...)` becomes `e.ctx.diags.raise_error(kinds.X, span, ...)` (or `error`/`warn`
where control continues), building a `Diag` explicitly when it has labels or notes. Decide per
diagnostic whether a spanned note becomes a `Label` or stays a `Note` (design rule), and give
every diagnostic that has any source location a primary span (for example
`conflicting-branch-types` and `conflicting-match-arm-types` take the whole `if`/`match`
expression, with the branches as labels). A commit migrates every raise site of each class its
module raises, in whichever module, and deletes the class, so a kind is never reported in both
forms or with two wordings. `Diags.error`/`warn` accepts only `Diag` when the last class is
gone. The first of these commits (`parse`) makes the sink, `ReportProof`, `CompilationError`
and the text renderer accept a `Diag` beside a `UserError`; the next adds `Diags.raise_error`
and lets the reporting methods build the `Diag`. The renderer shows a `Diag`'s
message and notes in the existing layout; the first module whose diagnostics have labels
decides how that layout shows them. Tests read a not-yet-migrated diagnostic's `message` and
`extra` through `harness.user_error`, which goes away with the last class.

**Final commit.** Delete `errors.py` and `TextErrorRenderer`'s dependence on it (the
existing renderer moves to `diag_text.py` unchanged in layout, using catalogue messages);
add the emitted-kinds hook in `conftest.py`: the sink records kinds in a test-only
registry, and at session end the hook fails if a catalogue kind was never emitted. It is
active only for an unfiltered run (no node IDs, `-k` or `-m` given), so running one test file
does not fail.

**Acceptance:** no `UserError` remains; every user-visible message comes from the catalogue;
tests assert full ordered kind lists; documentation fences use names.

## Task 5 (#72, #73): Discard diagnostics from speculative probes

**Files:** `diag.py`, `compilation.py`, `typcheck.py`; tests in `tests/test_generic_fns.py`,
`tests/test_unions.py`, `tests/test_match.py`.

- `Diags.transaction()` buffers diagnostics emitted while the *current unit frame* is on top
  of `ctx.unit_stack`; diagnostics from frames pushed later (other units forced by the
  probe) bypass the buffer. Each proof records the transaction it was issued in; once
  that transaction closes uncommitted the proof is void, and `Ctx.unit` failure
  memoization, a `ReportedError` caught outside the transaction, and (after #116) recorded facts
  and the sink's poison suppression assert they never see a void proof. `txn.commit()`
  records the buffered diagnostics with their existing proofs (as `Diags.merge` does), so
  proofs already held inside the transaction stay valid.
- Add `TypCheck._probe_arg_typs` (design contract): inside `self._speculative()` and one
  transaction, check each non-literal argument in its own `try/except diag.ReportedError`;
  failures contribute no bindings and set `probe_failed`. Use it from both
  `_infer_comptime_args` (function calls) and `_variant_union_typ` (generic union-variant
  constructors), removing the latter's comment about inheriting #72/#73. Unbound parameter
  with `probe_failed`: suppress `uninferable-comptime-argument` and re-check the arguments
  authoritatively without expected types, letting the first failure's `ReportedError` propagate
  (#116 later turns this into a poisoned result).
- Tests: #72's and #73's reproducers; variant-constructor analogues of both (a generic
  union variant `Some(3000000000 + 0)` with an `i64`-typed sibling argument, and a variant
  payload containing a `match` with an unreachable arm); a probe whose argument forces a
  broken struct declaration reports that struct's error exactly once; a call whose only
  argument is undefined reports `unknown-name` once and nothing about inference.

**Acceptance:** #72 and #73's reproducers, and their union-variant analogues, behave
correctly; a probe never loses or duplicates another unit's diagnostics.

## Task 6 (#116): Poison type and expression-level recovery

**Files:** `typs.py`, `typcheck.py`, `check_results.py`, `ir_env.py`, `patterns.py`,
`ir_builder.py`, `comptime.py`, `codegen.py`, `diag.py`; tests in new
`tests/test_poison.py` plus updates across feature tests.

- `typs.ErrorTyp`, interned per proof, and `typs.error_typ(reported)`.
  `report_proof()` on `Typ` and its compound subclasses (first poisoned component's
  proof). `coerces_to`, `unify`, `infer_typ_args` treat poison as compatible with
  everything and binding nothing. `check_comptime_arg_bounds` and comptime-argument kind and
  value-type checks skip poisoned arguments; instances with poisoned arguments are never
  recorded as monomorphization requests.
- `Diags.error`/`warn` drop any diagnostic with an argument whose `report_proof()` is not
  `None`; `error` returns that proof (asserting it is not void).
- `TypCheck._error(kind, span, **args) -> typs.Typ` emits and returns poison. Convert raise
  sites in `typcheck.py` to it following the design's rules, by construct: names and `let`;
  operators; coercions and returns; calls and comptime-argument inference; literals; field
  access and indexing; `if`/`while`/`match` (exhaustiveness and unreachable-arm analysis
  skipped on a poisoned scrutinee). `_check_block_expr` catches `ReportedError` per statement.
- Declarations: unresolvable field, payload, parameter and return types become poison with
  one diagnostic (`Typ.from_ast` gains a reporting variant used by declaration units).
  Layout queries on a poisoned component fail their unit with the component's proof.
- Assert no `ErrorTyp` reaches `ir_builder`, `comptime` or `codegen` (`asserts` helper
  `assert_not_poisoned`).
- Tests: #20's acceptance example (two undefined variables: two errors, nothing else);
  `let c: i32 = a + true` after a poisoned `a` reports nothing; two bad arguments in one
  call report two errors; a struct literal with two bad fields reports two errors; a
  poisoned `match` scrutinee reports no exhaustiveness error; a function with an unknown
  parameter type reports once and its callers report nothing about that parameter; an
  explicit undefined comptime argument and an inferred poisoned one report nothing about
  bounds; poison produced inside a discarded probe never reaches a recorded fact; a layout
  query over a poisoned field fails with that field's proof when other unrelated errors
  exist.
  **Poison-injection test:** for each expression position in a fixed corpus of valid
  programs, replace one expression with an undefined name and assert exactly one
  diagnostic (`unknown-name`) is reported.

**Acceptance:** #20's acceptance criteria; the poison-injection test passes; no lowering or
codegen path observes poison.

## Task 7 (#117): Limit reported errors with `-fmax-errors`

**Files:** a new `DiagnosticOptions` option group in `cli/common.py`, `cli/leech.py`,
`diag_text.py` (or `errors.TextErrorRenderer` if #118 has not landed);
`tests/test_cli.py`, `tests/test_check.py`.

- `-fmax-errors=N` (non-negative int, default 20) on `leech check`/`build`/`run`,
  passed to the text renderer. Rendering stops after the Nth error; the withheld-count note
  is printed; `0` disables the cap. Exit status is unaffected.
- The final summary counts every collected diagnostic; when the cap withholds any, the
  withheld-count note precedes the summary.
- Tests: 25 independent errors print 20 plus the note and a summary counting 25;
  `-fmax-errors=0` prints all;
  `-fmax-errors=3` prints three; warnings before the cutoff are shown, after it withheld and
  counted.

## Task 8 (#118): Render diagnostics in the rustc layout

**Files:** `diag_text.py`, `cli/common.py`, `cli/leech.py`, `cli/doctor.py`; tests in new
`tests/test_diag_text.py`, updates to CLI golden output tests.

- Implement the design's layout: header with name, `-->` location (cwd-relative when
  possible), gutter, `^` primary and `-` secondary underlines across the full span, inline
  and connector-line labels, multi-line spans, spanless `= note:` and spanned `note:`
  sub-snippets, the summary line, and the `leech explain` pointer (only once #119 has added
  explanations; until then omitted).
- `-fdiagnostics-color=auto|always|never` with `NO_COLOR` and TTY detection; colours only
  wrap level words, names and underlines.
- `leech doctor` output uses the same renderer.
- Tests: golden-text tests for a single-line span, two labels on one line, overlapping
  labels, a multi-line span, a spanless diagnostic, a spanned note in another file, tabs in
  source lines (expanded to four spaces consistently in line and underline), wide line
  numbers, the summary line forms (`1 previous error`, `N previous errors`, warnings only),
  and colour on/off.

## Task 9 (#119): Explain diagnostics with `leech explain`

**Files:** new `src/leech/diag_docs/*.md`, `cli/leech.py` (an `ExplainCommand`), `diag_text.py`,
`pyproject.toml` (package data, if not already included by `uv_build`), `REUSE.toml`,
`tests/doc.py`, new `tests/test_explain.py`, `tests/explain_allowlist.txt`.

- `leech explain NAME` prints the file; unknown names exit 2 with
  close matches (`difflib.get_close_matches`). Aliases for renamed kinds resolve.
- Seed explanations for the most common kinds (name resolution, type mismatches, calls,
  unreachable code), and record every other kind in the allowlist; a test fails if a kind
  lacks an explanation and is not allowlisted, or is allowlisted but has one.
- Extend `tests/doc.py` collection to `src/leech/diag_docs/*.md`; each erroneous example
  must produce exactly the explained kind.
- Renderer adds the final `for more information ...` line naming explained kinds that were
  shown.

## Task 10 (#120): Control warnings with `-W` options

**Files:** `diag.py` (`WarningPolicy`), `session.py` (the session's policy), the
`DiagnosticOptions` option group, `cli/leech.py`,
`diag_kinds.py` (`UNKNOWN_WARNING_OPTION`); tests in new `tests/test_warning_options.py`.

- Parse `-w`, `-W<name>`, `-Wno-<name>`, `-Werror`, `-Werror=<name>`, `-Wno-error=<name>` in
  order into a `WarningPolicy`; argparse handles them as an `action="append"` group that keeps
  relative order. Unknown or non-warning names emit `unknown-warning-option` into the sink
  before compilation.
- `Diags.warn` applies the policy and records the promoting option in `Diag.promoted_by`;
  promoted warnings render with the `(-Werror)` / `(-Werror=<name>)` suffix, count as
  errors, block codegen and set the exit status.
  `-W` names resolve through `diag_kinds.lookup`, so aliases work.
- Tests: each option alone; both suffixes; `-Werror -Wno-error=unreachable-code`;
  later-wins ordering (`-Werror=x -Wno-error=x` and the reverse); `-w` overriding `-Werror`;
  unknown name; an error kind named in `-Wno-`; promoted warning blocks `leech build` output
  and keeps its suffix.

## Task 11 (#121): Emit diagnostics as SARIF

**Files:** new `diag_sarif.py`, the `DiagnosticOptions` option group, `cli/leech.py`; tests in new
`tests/test_diag_sarif.py`.

- `-fdiagnostics-format=text|sarif`; `sarif` writes one SARIF 2.1.0 log to stderr with the
  design's mapping (including `primary_label` as the primary location's `message`).
  `-fmax-errors` is ignored for SARIF. `leech run` writes the log before running the program,
  only when there are diagnostics.
- Tests: validate output against the SARIF 2.1.0 JSON schema (vendored under `tests/` with
  its licence, checked with `jsonschema` added as a dev dependency) for an error with a label
  and a spanned note, a spanless tool error, and a warnings-only build; check `ruleId`,
  `level`, regions, and that both the primary label's and each secondary label's text are
  preserved (primary location `message`, `relatedLocations` messages).

## Documentation

- After #118, update `README.md` and any user-guide pages that show diagnostic output to the new
  layout; documentation fences already use names after #115.
- After #117, #120 and #121, document the options in `leech --help` text and in the
  user guide's getting-started troubleshooting section, if it exists by then.
- Update `AGENTS.md` (in #115, extended by #116 and #119) with a short diagnostics convention: add
  a `DiagKind` to `diag_kinds.py` following the message style; report with the sink rather
  than raising; in `TypCheck`, report with `_error` and continue with poison; add an
  explanation in `diag_docs/` or an allowlist entry; tests assert full kind lists.

## Issue tracking

Done on 2026-10-05, when the plan was approved:

- Filed #114–#121 (and, on 2026-10-06, #122) with the titles, scopes and acceptance
  criteria above, each with a `Sequencing` section stating its prerequisites.
- Extended #93's scope with `ReportProof`, `ReportedError`, structural deduplication and
  `Diags.merge`; the first error still escapes `compile_to_ir` in #93.
- Made #20 the umbrella for multi-error reporting, closing when #114 and #116 land, and
  answered its design questions.
- Commented on #72, #73 and #113 with their planned fixes and dependencies.
- Replaced #55's `93 --> 20` edge with the edges in the design's issue breakdown. #122 has
  no hard prerequisites, so #55 records it only as a soft note (after #93, before #114).
