<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Diagnostics: Collection, Recovery, and Reporting — Implementation Plan

For: #20, #93, #113, #72, #73, and the new issues #114–#122.

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
- CLI option names follow gcc/clang, then rustc/cargo. Both `leechc` and
  `leech check`/`build`/`run` accept every diagnostics option, through a shared argparse
  helper.

## Suggested order and parallelism

```text
#93 ┄┄► #122 ┄┄► #114
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
between #93 and #114.

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

1. **Unit decorator.** Add the `compilation.HasCtx` protocol (a `ctx` property) and
   `compilation.analysis_unit`, a *data* descriptor (it defines `__set__`, which raises)
   that stores `_Ok(value)` or `_Failed(reported)` under the private instance key `_unit_<name>`,
   so every access goes through it. On computing it pushes a frame on `ctx.unit_stack`,
   catches `diag.ReportedError` (memoizing without emitting) and legacy `errors.UserError`
   (reporting it with `ctx.diags.error`, then memoizing), and raises `ReportedError(reported)`. A memoized
   failure re-raises `ReportedError(reported)` without emitting. Other exceptions propagate
   untouched. Add `ctx` properties to the owners that lack one: `FnInstance` (via `_fn`),
   `StructField` (via `_env`), `ComptimeParamTyp` and `GenericTypTemplate` (via
   `_decl_env`), `StructTyp`/`UnionTyp`/`EnumTyp` (via their template or declaration
   environment), `ModVar`, `Trait` and `Impl`. Test the descriptor on one owner per context
   path, and test that a failed unit re-raises rather than returning its wrapper.
2. **Convert the units** listed in the design's unit table, replacing
   `functools.cached_property` (and `StructTypTemplate.validate_declaration` / union
   equivalent, `ComptimeParamTyp.check_declaration`, `Mod.designate_entry`, and `ir_traits`
   impl validation, which become units or call one). `src.SrcFile.src`/`lines` and
   `ir_values` `typ` stay `cached_property` (they report nothing).
3. **Cycles.** Each `detect_cycle` frame records `len(ctx.unit_stack)` when pushed. A
   caller that receives a cycle reports it once (`diags.fail`); before `ReportedError` unwinds,
   `Ctx` marks every unit frame above the depth recorded by the cycle's first participant to
   memoize `Failed(reported)` with that proof (design rule). Tests: mutually recursive
   initializers; mutually infinite structs `A { b: B }`/`B { a: A }` used from three
   functions and forced first through different units (a field access, a `size_of`, a
   literal); a cycle reached through a function signature. One diagnostic each, and an
   unrelated error elsewhere in a unit below the cycle is still reported.
4. **Module building: stage, then commit.** Restructure `_build_defn` and
   `_build_impl_defn` so each item is validated fully before anything shared is mutated:
   construct the symbol, check its name (reserved, duplicate via a non-mutating
   `Env.can_add`), and only then append to `src_fn_symbols` and call `_add_item`. For impls,
   build the `Impl` and its method symbols locally, run signature matching and
   `check_complete`, and only then register it with `ImplRegistry` (whose conflict check
   runs at registration, so a conflicting impl is rejected before insertion) and append its
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
5. **Recovery loop.** `ModLoader.check_declarations` and `Mod.check_declarations` force each
   unit inside `try/except diag.ReportedError: continue`; `driver.compile_to_ir` calls
   `designate_entry` (now a unit) inside the same loop. Parse errors still propagate, and
   `compile_to_ir` catches them as the design describes.
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
   and note, then re-raise. `compile_module` attaches the partial sink to the exception
   (`err.add_note`/an attribute) so the CLI can reach it.
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

## Task 3 (#113): Report every user error before code generation

**Files:** `ir_module.py`, `ir_loader.py`, `typs.py`, `mono.py`, `driver.py`, `codegen.py`,
`build.py`, `errors.py` (or `diag.py`); tests per #113 (`tests/test_structs.py`, `tests/test_unions.py`,
`tests/test_enums.py`, `tests/test_vars.py`, new guard in `tests/test_check.py`).

- `Mod.check_declarations` forces, as units: every module variable's `typ_check_results`,
  `cfg` and `initializer` (only lowering/evaluating an initializer whose check succeeded);
  every struct and union template's validation instance, field and payload types and
  layout; every enum's `variants` and `backing_typ`.
- Move `mono.discover` from `codegen.Compiler.compile` into checking:
  `driver.compile_to_ir` runs it after `check_declarations` and `designate_entry`, and
  stores the `MonoResult` on the module; codegen consumes it. Make discovery recover *per
  request*: in each of its work loops (module-variable CFGs, function-instance CFGs, struct
  fields, union payloads), wrap the forcing of one request in `try/except diag.ReportedError`,
  record it as failed, leave it out of `MonoResult`, and continue draining. Test with two
  independently failing generic instances (two infinite-size instances requested from
  different functions), one of which is requested only by an instance that itself fails
  later, and assert the expected set of diagnostics.
- Phase boundary: after checking, if `diags.has_errors`, stop (`compile_to_ir` raises per
  the current transitional rule; `compile_module` returns no IR).
- Remove the validation-only forcing from `codegen.Compiler.compile`. Codegen asserts
  `not diags.has_errors` on entry, and any `UserError`/`ReportedError` escaping codegen is
  converted to an internal error.
- Reclassify `LlvmVerificationError` (raised in `build._emit_object`) as an internal error:
  introduce `diag.InternalError(Exception)` and raise it there, so it takes #114's ICE path
  (diagnostics rendered, banner, traceback) rather than being a user diagnostic.
- `build.check` stops after `compile_to_ir` for every module (no IR generation); update its
  docstring.
- Tests: one per declaration kind in #113's table, raised by `driver.compile_to_ir`. The
  gap cannot reopen silently: codegen converts any user diagnostic it would raise into an
  internal error, so every test program that reaches code generation acts as the guard, and
  a unit test monkeypatches a codegen-time user error to prove the conversion.

**Acceptance:** #113's criteria.

## Task 4 (#115): Replace `UserError` classes with a diagnostic catalogue

Staged so every commit is green.

**Commit A — model and catalogue.** First write down the classification of all 114
`UserError` classes (in the commit message): user diagnostics become catalogue kinds;
`LlvmVerificationError` is already an `InternalError` after #113, and any other class whose
message says "internal compiler error" joins it. New `diag_kinds.py` with `lookup(name)`
(aliases); `diag.py` gains `DiagKind` (with `aliases`), `MsgKind`, `Msg`, `Label`, `Note`,
`Diag` (with `new`/`with_label`/`with_note` helpers and `promoted_by`), the `DiagArg`
protocol, and `CompilationFailed(diags)` with `.diags` and `.kinds`. Add one `DiagKind` per
user-diagnostic class and one `MsgKind` per distinct note, with names and **normalized**
templates. Give every legacy class a `kind` class attribute pointing at its entry.
`typs.Typ` and `ast.Ast` implement `diag_str()` (`Typ.name`; AST `diag_str` as today) and
`report_proof()` (always `None` until #116). Catalogue test: unique kebab-case names and
aliases, template fields parse, templates start with a literal lowercase word or `"`, no
trailing `.`.

**Commit B — `CompilationFailed` and test codemod.** `driver.compile_to_ir` raises
`CompilationFailed` with all diagnostics in sorted order (replacing #114's transitional raise).
A one-off codemod script (kept out of the repo, or under `scripts/` and deleted in the same
commit) rewrites `with pytest.raises(errors.X) as exc_info:` blocks to
`pytest.raises(diag.CompilationFailed)` plus `assert exc_info.value.kinds == (kinds.X_KIND,)`,
and rewrites `exc_info.value.message.span`/`.extra` accesses to `exc_info.value.diags[0]...`.
Sites without `as exc_info` gain one. Tests whose programs report more than one diagnostic
are fixed by hand. `tests/doc.py` accepts `error=<name>`/`warning=<name>` (rejecting unknown names at
collection), and any fences in `README.md`/`docs/guide/` and the `tests/test_doc.py` fixtures
are rewritten. Message-text assertions are updated to the normalized wording.

**Commit C… — migrate raise sites, one module per commit** (`parse`, `ir_env`, `typs`,
`ir_traits`, `ir_module`, `ir_loader`, `comptime`, `typcheck`, `build`/`doctor`/`driver`):
`raise errors.X(...)` becomes `e.ctx.diags.fail(diag.Diag.new(kinds.X, span, ...))` (or `error`/
`warn` where control continues), constructing labels and notes explicitly. Decide per
diagnostic whether a spanned note becomes a `Label` or stays a `Note` (design rule), and
give every diagnostic that has any source location a primary span (for example
`if-else-typ-mismatch` and `match-arm-typ-mismatch` take the whole `if`/`match`
expression, with the branches as labels). Delete each class once unused. `Diags.error`/`warn`
accepts only `Diag` when the last class is gone.

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
  that transaction closes uncommitted the proof is void, and `analysis_unit` failure
  memoization, a `ReportedError` caught outside the transaction, and (after #116) recorded facts
  and the sink's poison suppression assert they never see a void proof. `txn.commit()`
  records the buffered diagnostics with their existing proofs (as `Diags.merge` does), so
  proofs already held inside the transaction stay valid.
- Add `TypCheck._probe_arg_typs` (design contract): inside `self._speculative()` and one
  transaction, check each non-literal argument in its own `try/except diag.ReportedError`;
  failures contribute no bindings and set `probe_failed`. Use it from both
  `_infer_comptime_args` (function calls) and `_variant_union_typ` (generic union-variant
  constructors), removing the latter's comment about inheriting #72/#73. Unbound parameter
  with `probe_failed`: suppress `cannot-infer-comptime-arg` and re-check the arguments
  authoritatively without expected types, letting the first failure's `ReportedError` propagate
  (#116 later turns this into a poisoned result).
- Tests: #72's and #73's reproducers; variant-constructor analogues of both (a generic
  union variant `Some(3000000000 + 0)` with an `i64`-typed sibling argument, and a variant
  payload containing a `match` with an unreachable arm); a probe whose argument forces a
  broken struct declaration reports that struct's error exactly once; a call whose only
  argument is undefined reports `item-not-found` once and nothing about inference.

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
  diagnostic (`item-not-found`) is reported.

**Acceptance:** #20's acceptance criteria; the poison-injection test passes; no lowering or
codegen path observes poison.

## Task 7 (#117): Limit reported errors with `-fmax-errors`

**Files:** new shared option helper (e.g. `src/leech/diag_options.py`), `driver.py`,
`cli.py`, `diag_text.py` (or `errors.TextErrorRenderer` if #118 has not landed);
`tests/test_cli.py`, `tests/test_check.py`.

- `-fmax-errors=N` (non-negative int, default 20) on `leechc` and `leech check`/`build`/`run`,
  passed to the text renderer. Rendering stops after the Nth error; the withheld-count note
  is printed; `0` disables the cap. Exit status is unaffected.
- The final summary counts every collected diagnostic; when the cap withholds any, the
  withheld-count note precedes the summary.
- Tests: 25 independent errors print 20 plus the note and a summary counting 25;
  `-fmax-errors=0` prints all;
  `-fmax-errors=3` prints three; warnings before the cutoff are shown, after it withheld and
  counted.

## Task 8 (#118): Render diagnostics in the rustc layout

**Files:** `diag_text.py`, `driver.py`, `cli.py`, `doctor.py`; tests in new
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

**Files:** new `src/leech/diag_docs/*.md`, `cli.py`, `driver.py`, `diag_text.py`,
`pyproject.toml` (package data, if not already included by `uv_build`), `REUSE.toml`,
`tests/doc.py`, new `tests/test_explain.py`, `tests/explain_allowlist.txt`.

- `leech explain NAME` and `leechc --explain NAME` print the file; unknown names exit 2 with
  close matches (`difflib.get_close_matches`). Aliases for renamed kinds resolve.
- Seed explanations for the most common kinds (name resolution, type mismatches, calls,
  unreachable code), and record every other kind in the allowlist; a test fails if a kind
  lacks an explanation and is not allowlisted, or is allowlisted but has one.
- Extend `tests/doc.py` collection to `src/leech/diag_docs/*.md`; each erroneous example
  must produce exactly the explained kind.
- Renderer adds the final `for more information ...` line naming explained kinds that were
  shown.

## Task 10 (#120): Control warnings with `-W` options

**Files:** `diag.py` (`WarningPolicy`), shared option helper, `driver.py`, `cli.py`,
`diag_kinds.py` (`UNKNOWN_WARNING_OPTION`); tests in new `tests/test_warning_options.py`.

- Parse `-w`, `-W<name>`, `-Wno-<name>`, `-Werror`, `-Werror=<name>`, `-Wno-error=<name>` in
  order into a `WarningPolicy`; argparse handles them as an `action="append"` group that keeps
  relative order. Unknown or non-warning names emit `unknown-warning-option` into the sink
  before compilation.
- `Diags.warn` applies the policy and records the promoting option in `Diag.promoted_by`;
  promoted warnings render with the `(-Werror)` / `(-Werror=<name>)` suffix, count as
  errors, block codegen and set the exit status. `Diags.merge` preserves `promoted_by`.
  `-W` names resolve through `diag_kinds.lookup`, so aliases work.
- Tests: each option alone; both suffixes; `-Werror -Wno-error=unreachable-code`;
  later-wins ordering (`-Werror=x -Wno-error=x` and the reverse); `-w` overriding `-Werror`;
  unknown name; an error kind named in `-Wno-`; promoted warning blocks `leech build` output
  and keeps its suffix after build-level merging.

## Task 11 (#121): Emit diagnostics as SARIF

**Files:** new `diag_sarif.py`, shared option helper, `driver.py`, `cli.py`; tests in new
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
- After #117, #120 and #121, document the options in `leechc --help`/`leech --help` text and in the
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
