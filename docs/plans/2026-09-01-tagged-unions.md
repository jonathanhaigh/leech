<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Tagged Unions (`union`) Implementation Plan

**Goal:** Add `union` as a tagged-union declaration form — generic over comptime parameters,
with positional variant payloads — that can be constructed, destructured through arbitrarily
nested `match` patterns with full exhaustiveness and reachability checking, laid out
correctly across mixed payload sizes and alignments, monomorphized, evaluated at compile
time, and given inherent and trait `impl` blocks. `enum` behaviour is entirely unchanged.

**Architecture:** `union` follows the template/instance split #3 established for structs
(`UnionTypTemplate` / `UnionTyp` / `UnionVariant`), participating in the same
`compilation.Ctx` instance cache and the same `mono.discover` drain. Layout is
discriminant-then-payload: an identified LLVM struct `{ tag_ty, [K x iA] }` sized and
aligned from the widest payload, with each variant's payload projected through its own
literal struct type. `patterns.py` gains real constructor arity and per-column constructor
spaces — the extension #30 built it to absorb — and `MatchPlan` gets *smaller*, losing
`ArmTest` in favour of a per-arm sequential test emitter that can express nesting. Three new
IR instructions (`UnionMake`, `UnionTag`, `UnionPayload`) keep lowering high-level and, more
importantly, keep unions usable in the compile-time interpreter, which reusing
`ptr_cast` would not.

**Tech stack:** Python 3.14, lark 1.3.1 (LALR, `strict=True`), llvmlite 0.47.0, pytest. No
new dependencies.

**Design doc:** `docs/specs/2026-09-01-tagged-unions-design.md` — read it first. It carries
the language comparison behind the path spelling, the verified-conflict-free grammar, the
layout derivation, and why the compile-time constant needs its own LLVM type.

## Prerequisite

**Task 0 is a separate issue, not part of #24.** Moving comptime arguments from the end of a
path onto path segments (`path_seg: ident [comptime_args]`, deleting the trailing
`[comptime_args]` from `basic_typ` and `var_expr`) is right on its own merits and has
nothing to do with tagged unions. File it, add a `NEW --> 24` edge to #55, and do it first.

**It must land before Task 4**, not merely before the first task that constructs a variant:
Task 4 already asserts a parse tree for a path pattern carrying comptime arguments, and
`Option[i32]::Some(let x)` cannot parse under the trailing-argument grammar — that
impossibility is the prerequisite's entire premise.

Tasks 4, 6, 7 and 8 all consume the post-refactor path API, so the prerequisite issue must
settle and document it: `ast.Path` holds segments rather than idents; a segment carries an
optional comptime-argument list; `ast.BasicTyp` and `ast.VarExpr` no longer carry one of
their own; and `Env.resolve_path` applies each segment's arguments as it walks. Everything
below assumes that shape.

## Global constraints

- Keep all changes unstaged and uncommitted until the user reviews and explicitly authorizes
  a commit.
- Module-qualified imports, four-space indentation, Ruff's 100-column limit.
- `patterns.py` must not import `leech.ast` or `leech.typs`. Its purity is what makes the
  constructor abstraction honest and lets `tests/test_patterns.py` exercise arity without a
  compiler.
- Every new IR instruction needs three arms, not one: `ir_values`, `codegen._compile_instr`
  and `comptime.Interpreter._eval_instr`. The interpreter raises on anything it does not
  know, and module-level `let` runs through it.
- Do not change `enum` behaviour. `tests/test_enums.py` must pass untouched.
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`, `uv run basedpyright` and
  `uv run reuse lint` must all pass. This plan adds test files, so SPDX headers matter.
- Tasks 1–5 deliberately land the `match` and layout groundwork *before* anything union-shaped
  depends on it, so a regression in delivered `match` behaviour surfaces on its own.

---

### Task 1: Generalise `patterns.py` to real constructor arity

**Files:**
- Modify: `src/leech/patterns.py`
- Modify: `tests/test_patterns.py`

**Interfaces:**
- `Constructor.arity` becomes an instance field; `Constructor.field_spaces()` returns one
  `ConstructorSpace` per sub-column.
- `is_useful(matrix, row, spaces)` and `missing_patterns(matrix, spaces)` take a tuple of
  spaces, one per column.
- `MatchPlan` and `ArmTest` are **unchanged** by this task; they change in Task 2, together
  with the lowering that reads them, so this task ends with the suite green.

- [ ] Replace the `arity: ClassVar[int] = 0` on `Constructor` with a real field.
  `BoolConstructor` and `IntConstructor` keep 0; `VariantConstructor` takes it from the
  variant.
- [ ] Add `field_spaces()` to `Constructor`, returning `()` by default. On
  `VariantConstructor` the lazily invoked provider goes in a **separately named** field —
  a dataclass field cannot share a name with the base method it would otherwise shadow — and
  `field_spaces()` invokes it. Mark the provider `dataclasses.field(compare=False)` so
  constructor identity stays on the discriminant, which is what makes
  `enum Alias { A = 1, B = 1 }` still deduplicate.
- [ ] Give **both** the new `arity` and the provider defaults. Every existing call in
  `tests/test_patterns.py` is `VariantConstructor(discriminant, name)` — two positional
  arguments — so making either required contradicts this task's own "existing tests pass
  unchanged" gate.
- [ ] Keep the laziness rationale accurate: it is an optimisation, not a termination
  requirement. By-value layout cycles are rejected before any space is built and pointer or
  array payloads give open spaces, so eager construction would terminate for every
  constructible type. Do not write a test asserting that eager building diverges — it cannot
  be built from a legal program. Test instead that a pointer-recursive union yields a finite
  space.
- [ ] Thread `spaces: tuple[ConstructorSpace, ...]` through `_is_useful` and `_missing`.
  Specialising on `c` replaces the head space with `c.field_spaces()`; `default` drops it.
  `_missing`'s `width` parameter becomes `len(spaces)`.
- [ ] Delete the three comments in `_is_useful` and `_missing` that apologise for reusing the
  outer space at arity 0 — they are the exact thing this task fixes.
- [ ] Extend `_render_pattern` to render sub-patterns: `Option::Some(_)`,
  `Result::Ok(Option::None)`.
- [ ] Leave `build_match_plan(rows, space)`'s signature alone — there is still exactly one
  scrutinee column, so it takes one space and wraps it. `MatchPlan`'s own shape changes in
  Task 2, together with the lowering that reads it, so this task ends with the suite green.
  `typcheck` calls `build_match_plan` and nothing else in this module, so no checker change
  belongs here.
- [ ] Update the module docstring: it currently states the arity-0 limitation as a fact and
  names tagged-union payloads as what would need per-column spaces.
- [ ] Tests, still with no compiler involvement: arity-2 constructors; nested constructor
  patterns; a nested column exhausted independently of its parent; an open nested column
  (an integer payload) needing a wildcard; witnesses rendered with payloads; a
  pointer-recursive union yielding a finite space; every existing arity-0 test passing
  unchanged.

---

### Task 2: Re-lower `match` onto a sequential per-arm emitter

**Files:**
- Modify: `src/leech/patterns.py`, `src/leech/ir_builder.py`, `src/leech/typcheck.py`

**Interfaces:**
- `MatchPlan` becomes `(reachable_arms, missing)`; `ArmTest` is deleted.
- `CfgBuilder._emit_pattern_test(pattern_ast, value, fail_bb)` emits an arm's test,
  branching to `fail_bb` on failure and leaving the builder positioned in a block reached
  only on success.
- `_build_match_expr` drives one test chain per reachable arm.
- No new IR instruction yet; enum, bool and integer patterns only.

- [ ] Delete `MatchPlan.tests`, `ArmTest` and `_arm_constructors`, and the `test_bb`-shaped
  bookkeeping they existed for.
- [ ] Assert, in `build_match_plan`, the invariant lowering now relies on: when `missing` is
  empty and `reachable_arms` is non-empty, `_covers_remaining` holds for the last reachable
  arm. Arms after it are covered by earlier arms, so arms `0..last` cover everything;
  therefore arm `last` covers whatever `0..last-1` leave. Keep `_is_wildcard_row` and
  `_covers_remaining` — they are what the assertion is written in terms of.
- [ ] Rewrite `_build_match_expr` to walk `plan.reachable_arms`, giving each arm a test block
  whose `fail_bb` is the next arm's test block, and entering the last reachable arm
  unconditionally per the Task 1 invariant.
- [ ] Write `_emit_pattern_test`, dispatching on the AST pattern: wildcard and binding emit
  nothing; integer and bool literals compare and `cbranch`; an enum variant path compares
  the once-computed `enum_to_int` value. Reuse `_build_match_compare` and `_match_case_value`
  as-is.
- [ ] Or-patterns: each alternative but the last gets its own `alt_fail` block and branches
  to a shared `cont` on success; the last alternative branches to the real `fail_bb`.
- [ ] Keep using `_branch`/`_cbranch`, never `bb.branch(...)` — `codegen.py` orders blocks by
  DFS from `cfg.entry`, so a block never made a CFG successor is never emitted.
- [ ] Move binding stores to the top of the arm block, walking the pattern to each binding.
  With bindings barred from or-alternatives the path is unambiguous, so no "which
  alternative matched" bookkeeping is needed. This replaces `_build_match_binding`.
- [ ] Keep the merge unchanged: `_build_if_arm`'s `(value, last_bb, reaches_end)` contract,
  the single-incoming and all-void shortcuts, and the N-ary phi.
- [ ] Adjust `typcheck._check_match_expr` for the new `MatchPlan` shape only. Its checking
  logic — arm ordering, peer typing, `never` absorption, exhaustiveness, reachability — must
  not change.
- [ ] Rewrite the six `tests/test_patterns.py` tests that assert on `plan.tests` — they are
  the only tests coupled to the deleted field, at lines 174, 188, 201, 213, 223 and 231.
  Each should assert `reachable_arms` and `missing` instead. Deleting `MatchPlan.tests`
  without this leaves the suite red, so "test_patterns.py passes unchanged" holds for Task 1
  but explicitly **not** for this task.
- [ ] `tests/test_match.py`, `tests/test_enums.py`, `tests/test_if_els.py` and
  `tests/test_errors.py` must pass **unchanged** — they exercise behaviour, not plan shape.
  If any needs editing, that is a behaviour change and wants explaining rather than editing.

---

### Task 3: Rename and abstract the layout cycle check — structs only

`UnionTyp` does not exist until Task 5, so this task cannot mention it and stay a green
checkpoint. It does the renaming and the abstraction with one implementation; Task 5 adds the
union arm to the abstraction it leaves behind.

**Files:**
- Modify: `src/leech/typs.py`, `src/leech/compilation.py`, `src/leech/errors.py`
- Modify: `tests/test_structs.py`, `tests/test_errors.py`, `tests/test_generic_structs.py`,
  `tests/test_compilation.py`

**Interfaces:**
- `CycleDomain.STRUCT_LAYOUT` → `CycleDomain.TYPE_LAYOUT`, its identity widened to a
  nominal-layout type of which `StructTyp` is for now the only member.
- `errors.StructLayoutHop` → `TypLayoutHop`, carrying which kind of edge it is.
- `errors.InfiniteSizeStructError` → `InfiniteSizeTypError`.

- [ ] Rename the domain, the hop and the error. Widen the hop to carry its edge kind, so the
  note can read `Field "f" of struct "S" contains "T" by value` now and
  `Payload 0 of variant "V" of union "U" contains "T" by value` after Task 5. Struct wording
  must not change.
- [ ] Factor the by-value traversal out of `StructTyp._check_finite_size` — unwrap `ArrayTyp`
  to its element, recurse into a nominal type — into a shape a second nominal type can join
  without duplicating it.
- [ ] Factor `_layout_repeats` the same way, keeping the structurally-growing-arguments rule
  (`contains_typ` over each pair of comptime arguments) byte-for-byte identical.
- [ ] Update **every** site these renames break. A repository-wide search finds four test
  files, not the two an obvious guess would list: `tests/test_generic_structs.py` (5),
  `tests/test_structs.py` (4), `tests/test_compilation.py` (3 — it asserts
  `CycleDomain.STRUCT_LAYOUT` by name), `tests/test_errors.py` (1). Re-run the search rather
  than trusting these counts. The two `impl`-error renames are Task 12's, in different files.
- [ ] Struct behaviour must be unchanged: same diagnostics, same spans, same notes.

---

### Task 4: Grammar, keywords and AST

**Files:**
- Modify: `src/leech/leech.lark`, `src/leech/reserved.py`, `src/leech/ast.py`
- Modify: `tests/test_parsing.py`, `tests/test_ast.py`

**Interfaces:**
- `union_defn` / `union_variant_list` / `union_variant` / `_union_payload_list` rules.
- `path_pattern: path ["(" _pattern_list ")"]` and `_pattern_list`.
- `ast.UnionDefn`, `ast.UnionVariantDefn`; `ast.PathPattern` gains an optional payload tuple;
  `ast.DefnKind` gains `UnionDefn`.

- [ ] Add the grammar rules exactly as the design doc gives them, beside `enum_defn` and
  `path_pattern` respectively. Both were spiked conflict-free against the real grammar under
  `strict=True`; if lark reports a conflict, stop and re-spike rather than working around it.
- [ ] Add `"union"` to `reserved.KEYWORDS`. Confirm
  `tests/test_reserved.py::test_keyword_set_matches_grammar` passes **unchanged** — it
  derives the expected set from the grammar's string terminals, and `"union"` qualifies.
- [ ] Add the AST nodes modelled on `EnumDefn`/`EnumVariantDefn`: class-level `Final`
  annotations assigned in `__init__`, `src.SrcSpan.from_lark_meta`, an `@override def
  diag_str`, `_as_tree`/`_as_token` and `opt_util.opt_map` for optional children.
- [ ] Register `UnionDefn` in the `Defn` dispatch so `Mod` sees it.
- [ ] Parse-tree assertions in `tests/test_parsing.py` using the existing `T`/`Tok` DSL:
  unit-only, payload, mixed, multi-payload, generic, `pub`, empty `{}`, trailing commas; and
  in patterns, payload sub-patterns, nesting, or-patterns inside a payload, and a path
  pattern carrying comptime arguments.
- [ ] Negative parse cases: `union U { A() }` and `Option::Some()` — an empty payload list is
  a parse error in both positions, because a unit variant has exactly one spelling.
- [ ] AST-shape assertions in `tests/test_ast.py` for variants, payload types and pattern
  payloads.

---

### Task 5: `UnionTypTemplate`, `UnionTyp`, `UnionVariant`

**Files:**
- Modify: `src/leech/typs.py`, `src/leech/compilation.py`, `src/leech/ir_module.py`,
  `src/leech/ir_env.py`, `src/leech/errors.py`
- Add: `tests/test_unions.py`

**Interfaces:**
- `typs.UnionTypTemplate(GenericTypTemplate)`, `typs.UnionTyp(Typ)`, `typs.UnionVariant`.
- `typs.TypKind` gains `UnionTyp`.
- `compilation.Ctx.instantiate_union` / `requested_union_instances`.
- `errors.DuplicateVariantInUnionDefnError`.

- [ ] Write `UnionTypTemplate` against `StructTypTemplate`, member for member: declaration
  AST and environment, comptime parameters bound before variants so parameter-name errors
  take priority, reserved-name and duplicate-variant checks at construction, `instantiate`,
  `_validation_instance`, `validate_declaration`, `module_instance`.
- [ ] Write `UnionTyp` against `StructTyp`: `cache_key` raising (instances are owned by the
  compilation context), `name`, `qualified_name` qualifying its comptime arguments,
  `is_concrete`, `substitute_typ_params`, `infer_typ_args`, `span`, `_check_finite_size`
  through Task 3's shared traversal.
- [ ] Join `UnionTyp` to Task 3's abstraction: add the union arm to the shared by-value
  traversal (recursing through variant payload types), widen `CycleDomain.TYPE_LAYOUT`'s
  identity and `_layout_repeats` to `StructTyp | UnionTyp`, and emit the payload-edge wording
  of `TypLayoutHop`. This is the half Task 3 could not write.
- [ ] Add the `UnionTyp` arm to `typs._contains_typ` **here**, not in Task 12. It looks like
  coherence machinery, but `_layout_repeats` calls `contains_typ` on each pair of comptime
  arguments, so layout checking needs it first. Without it,
  `union L[T] { Next(L[L[T]]) }` walks `L[T] → L[L[T]] → L[L[L[T]]] → …` forever:
  `_contains_typ` falls through on a `UnionTyp`, `_layout_repeats` never sees that `L[T]`
  contains `T`, and validation recurses instead of raising `InfiniteSizeTypError`.
- [ ] Test growing cycles whose growth runs **through a union comptime argument**, both
  directly and mutually with a struct. Generic-identity and tag-inference tests do not
  exercise this contract.
- [ ] `UnionVariant`: declaration index, AST, instance environment, lazily resolved
  `payload_typs`, `arity`, and a tag value equal to the declaration index. **Instance-scoped**
  — it holds an instance's environment, so it cannot represent a variant of an
  uninstantiated template.
- [ ] Add the template-level **variant descriptor** the instance-scoped `UnionVariant` cannot
  serve as: name, declaration index, declaration AST, owned by `UnionTypTemplate` and
  independent of any instance. Task 6's variant references hold one of these; the concrete
  `UnionVariant` is looked up by index on the instance once inference settles. Payload types
  needed *for* inference resolve in the template's own parameter-bound environment — the
  validation instance exists to check the declaration and its environment must not leak into
  checking uses of it.
- [ ] `UnionTyp.tag_typ`: the smallest unsigned builtin integer fitting `n - 1`, choosing
  from widths 8/16/32/64 the way `EnumTyp.backing_typ` does for an inferred type. A
  zero-variant union uses `u8`.
- [ ] Add `_union_instances`, `instantiate_union` and `requested_union_instances` to
  `compilation.Ctx`, mirroring the struct versions including the `record_request` flag and
  its documented meaning.
- [ ] Add the `ast.UnionDefn` case to `ir_module.Mod._build_defn`, binding the template when
  it has comptime parameters and its `module_instance` otherwise — exactly as `StructDefn`
  does.
- [ ] Add `UnionTyp`/`UnionTypTemplate` to `ir_module.ModItemValue` and to
  `ir_env.Env._private_item_diag_info` as `("type", span)`.
- [ ] Unit-level tests: variant order and arity, tag type inference at 2 / 256 / 257
  variants, generic instantiation identity (`Option[i32] is Option[i32]`), and
  `qualified_name` distinguishing same-named unions from different modules.

---

### Task 6: Name resolution for variants

**Files:**
- Modify: `src/leech/ir_env.py`, `src/leech/typs.py`, `src/leech/resolve.py`
- Modify: `tests/test_unions.py`

**Interfaces:**
- `typs.UnionVariantRef` — an owner (`UnionTypTemplate` or `UnionTyp`) plus the Task 5
  template-level variant **descriptor**, never an instance-scoped `UnionVariant`.
- `Env._resolve_path_segment` accepts `UnionTypTemplate` and `UnionTyp` as path scopes.

- [ ] Add `UnionVariantRef` to `typs.py` (not `ir_values` — it is not a value) and to
  `ir_env`'s `Container`/`Var` type aliases and `resolve.VarTarget`.
- [ ] `UnionTypTemplate` as a mid-path scope resolves a variant name in the VARS namespace to
  a `UnionVariantRef` with a template owner. This deliberately *relaxes* the
  `MissingComptimeArgsError` that `StructTypTemplate` raises there, because a variant's
  comptime arguments are inferable and a struct's associated function's are not. Say so in a
  comment; it is the one place the two templates behave differently.
- [ ] `UnionTyp` as a mid-path scope resolves a variant name first, then falls back to
  `impl_registry.lookup_assoc_fn`, mirroring the `StructTyp` case including its
  private-associated-function check.
- [ ] Variant visibility follows the union's own access — no per-variant check, exactly as
  the `EnumTyp` case documents.
- [ ] Reject comptime arguments on the *variant's* own segment. The post-refactor grammar
  admits them on every segment, so `Option::Some[i32]` parses and must be turned away at
  resolution with `ComptimeArgsOnNonGenericItemError`: the arguments belong to `Option`, and
  `Option[i32]::Some` is how to say so.
- [ ] Tests: `Option::Some`, `Option[i32]::Some`, `mod::Option::Some`, an unknown variant
  giving `ItemNotFoundError`, and a private union's variant giving `PrivateItemAccessError`.

---

### Task 7: Checking variant construction

**Files:**
- Modify: `src/leech/typcheck.py`, `src/leech/check_results.py`, `src/leech/errors.py`
- Modify: `tests/test_unions.py`

**Interfaces:**
- `TypCheck._check_variant_ref` and `_resolve_variant_callee`.
- `TypCheckResults` records the resolved `(UnionTyp, variant index)` per construction site.
- `errors.VariantConstructorNotAValueError`, `errors.UnitVariantCalledError`.

- [ ] Thread `expected_typ` into **both** `_check_var_expr` and
  `_check_call_expr`/`_resolve_callee`. `_check_expr_inner` currently drops it on both arms
  (`case ast.VarExpr(): return self._check_var_expr(expr_ast, e)` and the `CallExpr` arm
  alongside it), and a unit variant is a `VarExpr`, not a call — so threading only the call
  path leaves `let b: Option[i32] = Option::None;`, the design's headline example, still
  unable to see its expected type.
- [ ] Write the shared comptime-argument resolution for a variant reference, in this order:
  explicit arguments on the path; then, if the owner is a template, seeding from
  `expected_typ` when it is a `UnionTyp` of the same template; then structural inference from
  payload argument types through `Typ.infer_typ_args`, inside the existing `_speculative()`
  probe. Anything unbound is `CannotInferComptimeArgError` naming the union and parameter.
  Run `typs.check_comptime_arg_bounds` on the result.
- [ ] Unit variant in value position: `_check_var_expr` resolves the instance and returns its
  `UnionTyp`. A payload variant in value position is `VariantConstructorNotAValueError`.
- [ ] Payload variant in call position: `_resolve_callee` returns a synthesized
  `FnTyp(ret=<the union instance>, params=<substituted payload types>)`, so
  `_check_call_expr`'s existing coercion recording and `InvalidArgTypError` apply unchanged.
  A unit variant in call position is `UnitVariantCalledError`.
- [ ] **Preflight the arity check against the template-level descriptor's payload AST count,
  before running inference.** `_check_call_expr` resolves the callee *before* comparing
  argument and parameter counts, and for an unapplied generic variant the parameter types
  inference needs may depend on the very arguments that are missing. Left in the existing
  order, `Option::Some()` raises `CannotInferComptimeArgError(T)` instead of the
  `NotEnoughArgsError` the design promises, and a parameter unrelated to any payload masks
  both too-few and too-many diagnostics. The descriptor knows the arity without inference, so
  check it first.
- [ ] Test that ordering specifically: missing and extra arguments on an **inferred** generic
  owner with neither explicit nor expected comptime arguments. An `Option[i32]::Some()` test
  passes under the broken order and proves nothing.
- [ ] Record the resolved union instance and variant index in `check_results` so `CfgBuilder`
  never re-resolves a name, following `_set_generic_call`'s shape — a plain dict write rather
  than `_set_fact`, so a speculative probe does not discard it.
- [ ] Add `UnionVariantRef` arms to `_check_place_inner` and `_place_mut`, covering **both**
  variant kinds. A unit variant has no address, so it takes the const-temporary path exactly
  as `ir_values.ComptimeEnum` already does; a payload variant in place position (`&Option::Some`)
  is `VariantConstructorNotAValueError`, the same as in value position. Both sites list their
  cases explicitly and will otherwise fall through wrongly.
- [ ] Tests: inference from a payload argument; explicit arguments; wrong payload arity;
  wrong payload type; each new error.
- [ ] Test expected-type inference in **every** context that already computes an expected
  type, not just the one `let` the design doc uses: a typed `let`, an assignment, a `return`,
  a block tail expression, and a call argument. Threading through only the context the
  example happens to name is exactly the bug this bullet exists to catch.
- [ ] Pin the deferred peer-context boundary with a test, so it is a decision rather than an
  accident: `let o = if (c) { Option::Some(1i32) } else { Option::None };` raises
  `CannotInferComptimeArgError`, and the annotated form `let o: Option[i32] = …` compiles.
  Do **not** widen the flexible-literal peer machinery here — that is the deferred follow-on.
- [ ] Test the `Result::Err(e)` case precisely: `Err(E)` infers `E` from its payload and
  leaves **`T`** unbound, so the diagnostic must name `T`. Then confirm
  `Result[i32, MyErr]::Err(e)` and an expected type each fix it.
- [ ] Note in passing, do not fix: this path inherits #72/#73, since `_infer_comptime_args`'
  speculative probe lets its diagnostics escape.

---

### Task 8: Checking payload patterns

**Files:**
- Modify: `src/leech/typcheck.py`, `src/leech/errors.py`
- Modify: `tests/test_unions.py`, `tests/test_match.py`

**Interfaces:**
- `_check_pattern` threads a *column* type rather than the scrutinee type.
- `_match_constructor_space` gains a `UnionTyp` case with lazy sub-column spaces.
- `errors.WrongNumberOfPayloadPatternsError`, `errors.PayloadPatternOnNonVariantError`.

- [ ] Rename `_check_pattern`'s `scrutinee_typ` parameter to the column type it now is, and
  pass each payload sub-pattern its variant's substituted payload type.
- [ ] Extend `_check_path_pattern`: a `UnionVariantRef` requires a `UnionTyp` column type of
  the same template; explicit comptime arguments on the pattern path must produce exactly
  the column type, `PatternTypMismatchError` otherwise; the payload sub-pattern count must
  equal the variant's arity, `WrongNumberOfPayloadPatternsError` otherwise — including the
  zero-given case for a bare `Option::Some =>`.
- [ ] A payload list on a non-variant path, or on an enum variant, is
  `PayloadPatternOnNonVariantError`.
- [ ] Build a `patterns.VariantConstructor` per union variant with its real arity and a lazy
  `field_spaces` closing over the substituted payload types, memoised per union instance so
  repeated matches on one type do not rebuild it.
- [ ] Extend the or-pattern binding prohibition to any depth: a `BindingPattern` anywhere
  under an `OrPattern` is `BindingInOrPatternError`. Today's check only inspects immediate
  alternatives.
- [ ] Confirm — do not add code for — that two bindings of one name in an arm already raise
  `DuplicateItemDefnError`, since an arm's bindings share one `e.new_child()` scope.
- [ ] Tests: exhaustive `Option` match; `Option::Some(_)` plus `Option::None`; nested
  `Result[Option[T], E]`; a literal inside a payload leaving the column open so a wildcard is
  still required; or-patterns inside a payload; non-exhaustive diagnostics naming payload
  witnesses; unreachable-arm warnings; every new error.

---

### Task 9: IR instructions, lowering and the interpreter

**Files:**
- Modify: `src/leech/ir_values.py`, `src/leech/ir_builder.py`, `src/leech/comptime.py`
- Modify: `tests/test_unions.py`

**Interfaces:**
- `UnionMakeInstr(union_typ, variant_index, values)`, `UnionTagInstr(operand)`,
  `UnionPayloadInstr(operand, variant_index, field_index)`, and the matching
  `BasicBlock.union_make` / `union_tag` / `union_payload` builders.
- `ir_values.ComptimeUnion(typ, variant_index, payload)`.

- [ ] Add the three instructions with their `calculate_typ` implementations: the union type,
  the union's tag `IntTyp`, and the named payload field's type.
- [ ] Add `ComptimeUnion`. It is not a `ComptimeAggregate` — a union has one active variant,
  not indexable elements. Implement `copy`.
- [ ] Give `Interpreter._check_not_temporary` an **explicit `ComptimeUnion` arm**, or a
  shared payload-container base that the check actually tests for. Its recursion is gated on
  `isinstance(value, ComptimeAggregate)`, so adding payload accessors to a class outside that
  hierarchy changes nothing: a temporary comptime pointer wrapped in a union would escape a
  check written precisely to catch it.
- [ ] Negative test for exactly that: a module-level initializer producing a union whose
  active payload holds a pointer into a temporary comptime allocation must raise
  `CannotTakeAddressOfComptimeValueError`.
- [ ] Interpreter arms: `UnionMakeInstr` builds a `ComptimeUnion`; `UnionTagInstr` returns
  `ComptimeInt(tag_typ, variant_index)`; `UnionPayloadInstr` asserts the operand's variant
  matches and returns the payload value. The assertion is sound because Task 10's tests
  short-circuit — a payload is only ever projected after its tag has been compared.
- [ ] Lower unit-variant references and payload-variant calls to `UnionMakeInstr` in
  `_build_var_expr` and `_build_call_expr`, reading the recorded instance and variant index
  and substituting `self._comptime_arg_mapping`, the way `_build_struct_lit_expr` does.
- [ ] Tests at this stage are comptime-only and IR-only; runtime execution waits for Task 11.

---

### Task 10: Union patterns in the match emitter

**Files:**
- Modify: `src/leech/ir_builder.py`
- Modify: `tests/test_unions.py`

- [ ] Extend `_emit_pattern_test` with the union case: `union_tag` the value once per test,
  compare against the variant index, `cbranch`, then recurse into each payload column with
  `union_payload(value, variant_index, i)`.
- [ ] Tests **must** short-circuit — emit each payload column's test only in a block reached
  when the tag already matched. Projecting a payload from the wrong variant reads storage
  that was never written for it, and the interpreter has nothing to return.
- [ ] Extend the binding walk to descend through payload patterns, projecting with
  `union_payload` and storing into the binding's `alloca` at the top of the arm block.
- [ ] Tests: nested destructuring; a binding under two levels of payload; or-patterns in a
  payload; an arm reached only through the fall-through chain; a `match` whose scrutinee is a
  call result rather than a place.

---

### Task 11: Codegen — types, instructions and constants

**Files:**
- Modify: `src/leech/ll_typs.py`, `src/leech/codegen.py`, `src/leech/mono.py`
- Modify: `tests/test_unions.py`, `tests/test_builtins.py`

**Interfaces:**
- `ll_typs.ll_typ` maps `UnionTyp` to `{ tag_ty, [K x iA] }`.
- `mono.MonoResult.union_instances`; `codegen` declares them with struct instances.
- `_compile_comptime_value` handles `ComptimeUnion` via an ad-hoc packed constant type.

- [ ] `_union_ll_typ`: an identified struct named by `qualified_name`, sharing `_building`
  with `_struct_ll_typ` so a union payload holding a struct that points back to the union
  gets the still-opaque type rather than recursing forever. Body is `(tag_ll_ty, payload)`
  where `A` = max ABI alignment over the variants' payload literal-struct types (1 when
  there are none), `S` = max ABI size, `K = ceil(S / A)`, payload = `[K x i(A*8)]`.
- [ ] Make struct and union discovery a **joint fixed point**, not two sequential drains.
  The two logs are mutually recursive: forcing a struct field can request a union, and
  forcing that union's payload can request a further struct. Whichever pass ran second would
  append to the other's already-drained log, and `MonoResult` would silently omit reachable
  instances. Keep a persistent cursor per log and alternate until neither advances. The
  existing functions-then-structs comment explains the one-directional case; extend it rather
  than leaving it describing a rule that no longer holds. Add `union_instances` to
  `MonoResult`.
- [ ] Test both directions and an alternating chain — struct → union → generic struct →
  union — asserting every instance reaches `MonoResult`. A single-direction test passes under
  the broken sequential drain, so it proves nothing.
- [ ] Declare union instances in `codegen.compile`'s types-first pass, beside struct
  instances, and add `UnionTyp`/`UnionTypTemplate` cases to `_declare_mod_item` and
  `_compile_mod_item`. Force `validate_declaration` on union templates in the same pass that
  forces it on struct templates and `backing_typ` on enums, so an invalid declaration is
  rejected whether or not the program uses it.
- [ ] `UnionTagInstr` → `extract_value 0`. `UnionMakeInstr` for an arity-0 variant →
  `insert_value` of the tag constant into `undef`. `UnionMakeInstr` for a payload variant and
  `UnionPayloadInstr` → `alloca` / store / `bitcast` the payload field pointer to the
  variant's payload literal-struct pointer / `gep` / store or load.
- [ ] `_compile_comptime_value` for `ComptimeUnion`: build a **packed** literal struct
  constant `<{ tag, [pad x i8], payload_struct, [pad x i8] }>` whose padding is computed from
  the real element offsets and ABI size of the union's own LLVM type via `target_data()`.
  Unpacked, LLVM re-pads it and the constant disagrees with the type the rest of the program
  uses.
- [ ] Make the construction recursive, to a stated contract rather than "pack the container
  too": a helper returning `(constant, ad_hoc_type)` that reproduces the **original** target
  layout at every aggregate level — each struct field at its original offset, each array
  element at its original stride, the original tail padding — with explicit `i8` padding.
  An array whose elements are different variants has differing ad-hoc element types, so it
  becomes a packed literal struct of per-element constants each padded to the original
  stride, not an `ArrayType`.
- [ ] Trigger the ad-hoc path on **representation mismatch, not top-level source type**:
  `_declare_mod_var` uses the initializer's ad-hoc LLVM type whenever recursive construction
  returns a type differing from the declared one. A global whose declared type is a *struct
  or array containing* a union has exactly the same mismatch, and a rule written for
  "union-typed global" would send it down the ordinary path and reject its initializer.
- [ ] Take the explicit `align` from the ABI alignment of the variable's **declared outer
  pointee type**, not from the nested union — packing drops the type's own alignment to 1,
  and a struct holding both an `i128` and a weakly aligned union would be under-aligned by
  the union's figure. Test exactly that struct.
- [ ] This **is** an ordering change, so make it deliberately. `var.typ` forces `initializer`
  only as a `ComptimeValue`; the ad-hoc LLVM type exists only after that value is lowered to
  an LLVM constant, which today happens later in `_compile_mod_var`. A global's LLVM type is
  fixed when the `GlobalVariable` is constructed, so `_declare_mod_var` must lower the
  initializer (or compute its ad-hoc type) during the declare pass. Note the knock-on: a
  constant whose payload points at another module variable forces that variable's global to
  be declared early too.
- [ ] Fix the bookkeeping the bitcast breaks. `_compile_mod_var` looks the item's mapped value
  back up and assigns `.initializer` to it; a bitcast constant expression is not a
  `GlobalVariable` and cannot take one, so registering the bitcast as the mapped value breaks
  every union-typed module variable at compile time. Retain the raw global separately for
  initializer, linkage and alignment, and expose the bitcast only as the value other code
  consumes.
- [ ] Test the early-declaration path directly: a module-level union constant whose active
  payload is a pointer to another module variable, compiled and run.
- [ ] Add `__size_of` cases to `tests/test_builtins.py` covering unit-only, single-payload,
  mixed-size and mixed-alignment unions, and a union nested in a struct — the case a byte-array
  payload would silently under-align.
- [ ] Assert that a module-level union global's emitted ABI size equals `__size_of` for the
  same type. This is the one place two independently built LLVM types must agree.
- [ ] Size agreement is necessary but not sufficient — it cannot see a wrong field offset or
  array stride. Add running tests that read fields and elements back out of module-level
  struct and array constants containing mixed union variants, including a pointer-bearing
  payload, and check the values.

---

### Task 12: `impl` blocks on unions

**Files:**
- Modify: `src/leech/ir_module.py`, `src/leech/ir_traits.py`, `src/leech/typs.py`,
  `src/leech/errors.py`
- Modify: `tests/test_unions.py`, `tests/test_impl.py`, `tests/test_traits.py`,
  `tests/test_generic_structs.py`

- [ ] Accept `UnionTyp` in `_build_inherent_impl_defn`. Rename
  `ImplForNonStructTypError` → `ImplForNonNominalTypError` and
  `ImplForNonLocalStructTypError` → `ImplForNonLocalTypError`, updating their messages and
  every test asserting them — `tests/test_impl.py` (2) and `tests/test_generic_structs.py`
  (1), the second of which is easy to miss.
- [ ] Add `UnionTyp` arms to the four structural helpers that currently special-case
  `StructTyp`. Each mirrors its struct arm exactly, and each fails **silently** rather than
  loudly if missed, which is why they are listed individually:
  - `typs._contains_typ` — already added in Task 5, because layout checking needs it first;
    confirm it, do not re-add it.
  - `typs.typs_overlap`'s `unify` — same template, then unify arguments pairwise.
  - `ir_traits._head_shape` — return the template.
  - `ir_traits._is_local` — compare `mod_name`.
- [ ] Confirm trait impls need no separate change: `_build_trait_impl_defn` already resolves
  any self type. The orphan and overlap rules come from the helpers above.
- [ ] Tests: an inherent method on a generic union called through a value and through a
  pointer receiver; an associated function reached as `Option[i32]::make()`; a trait impl for
  a union; two overlapping impls for one union rejected; an orphan trait impl for a
  non-local union rejected; an inherent impl for a non-local union rejected.

---

### Task 13: End-to-end behaviour, gates and follow-on issues

**Files:**
- Modify: `tests/test_unions.py`, `tests/test_errors.py`

- [ ] Runtime cases executed under `lli`, all of them over *inhabited* unions: `unwrap_or`
  over `Option[i32]`; a `Result` carrying a struct payload; a multi-payload variant; a union
  stored in a struct field; a union in an array; a recursive
  `union List[T] { Nil, Cons(T, *List[T]) }` walked to a length.
- [ ] The zero-variant union is a **compile-only** acceptance test —
  `pub fn absurd(e: Empty) i32 { return match (e) {}; }` type-checks and lowers, and is
  never called. It **must** be `pub`: `mono` only forces a non-generic body when it is `main`
  or public, so a private `absurd` would never be type-checked or lowered and the test would
  pass vacuously. Assert its body appears in the emitted IR. It is uninhabited and has no constructor, so there is no value to run it with;
  manufacturing one from uninitialised storage would be testing undefined behaviour rather
  than the language.
- [ ] Comptime cases forcing `comptime.Interpreter`: `pub let G = Option::Some(1i32);` and a
  module-level `let` whose initializer `match`es a union down to an integer, mirroring
  `test_comptime_if_true_else_expr_val`.
- [ ] Message, span and note assertions in `tests/test_errors.py` for
  `NonExhaustiveMatchError` over a union (payload witnesses),
  `WrongNumberOfPayloadPatternsError` and `InfiniteSizeTypError` over a union, using
  `util.find_pos`.
- [ ] Confirm `enum` is untouched: `tests/test_enums.py` passes with no edits.
- [ ] End-to-end sanity check by hand — compile, link and run:

  ```leech
  union Option[T] { None, Some(T) }

  pub fn main() i32 {
      let o = Option::Some(0i32);
      return match (o) {
          Option::Some(let x) => x,
          Option::None        => 1i32,
      };
  }
  ```

  `uv run leech main.leech -o main.ll` should emit the tagged struct type and the tag
  comparison; under `lli` it should exit 0. Then delete the `Option::None` arm and confirm a
  `NonExhaustiveMatchError` naming `Option::None`.
- [ ] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`,
  `uv run basedpyright`, `uv run reuse lint`.
- [ ] Raise the follow-on issues the design doc defers, and add their edges to #55:
  niche filling; struct-like variants; explicit discriminant values and an explicit tag type;
  variant constructors as values; `Option`/`Result` in the prelude; and peer-context
  inference across `if`/`match` branches. The path/comptime-argument refactor issue (Task 0)
  is raised before any of this work starts, not here.
- [ ] Request independent review and leave all changes unstaged for user review. Do not close
  #24 — that is the repository owner's call.
