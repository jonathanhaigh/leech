<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Path Resolution Comptime Applications Implementation Plan

**For:** #83: Attach comptime arguments to path segments

**Goal:** Finish the path-segment comptime-argument change with one consistent semantic
boundary: `Env` consumes every explicit argument list exactly once, identifier lookup remains
separate from argument application, and resolving or type-checking a function path does not create
a concrete function instance.

**Scope:** This is a corrective follow-up to the current #83 work. The grammar and AST continue
to attach arguments to `ast.PathSeg`; `ast.Path.str()` continues to render them; generic struct
segments continue to work as intermediate scopes. This plan replaces the current hybrid in which
`Env` applies generic type arguments but returns raw trait and function symbols whose consumers
then re-read the AST.

**Non-goal:** Do not add inference for omitted generic owner arguments in this issue.
`MyStruct::f(1i32)` therefore continues to report `MissingComptimeArgsError` when `MyStruct` is
generic. Function arguments retain their existing call-site inference. Partial explicit argument
lists, overload resolution, generic associated functions, and generic-trait method calls remain
unchanged.

## Terms and invariants

Use the following terms consistently in code and documentation:

- **Lookup** maps an identifier and a namespace within an already usable scope to a declaration,
  value, module, or type template. It does not inspect `PathSeg.comptime_args`, infer arguments,
  substitute types, or instantiate anything.
- **Comptime application** resolves an explicit argument AST against an item's parameters, checks
  arity, kind, and bounds, and records the resulting semantic arguments. A type application may
  produce the cached semantic `StructTyp` needed for member lookup. A function application does
  not produce a `FnInstance`.
- **Instantiation** means asking `compilation.Ctx` for a concrete function or struct instance and,
  where applicable, adding it to monomorphization discovery. Function path references reach this
  operation only in lowering/materialization. Existing `StructTypTemplate.instantiate()`
  terminology and request behavior remain unchanged because changing struct discovery is a
  separate architectural concern.

The final implementation must maintain these invariants:

1. Every explicit `PathSeg.comptime_args` tuple is consumed by `Env`'s application step exactly
   once. No downstream consumer re-reads it.
2. `_resolve_path_seg` delegates identifier lookup to a helper whose `match` covers the closed
   scope union without a wildcard case.
3. A generic type segment must be semantically applied before it can become the scope for the
   following segment. An omitted generic owner argument list remains an immediate error.
4. A generic function with omitted arguments may remain a type-checking inference obligation;
   an explicit argument list is resolved before `Env` returns it.
5. Trait and function applications are first-class semantic records. Neither is represented by a
   raw declaration plus a later read of the syntax tree.
6. `FnSymbol.instantiate()` is not called by path lookup, comptime application, or type checking.
   A path reference reaches it only when lowering needs an `ir_module.FnRef`; existing
   declaration-discovery and ABI-root instantiations remain unchanged.
7. `ModLoader.resolve_import()` owns the rule that no import segment may have comptime arguments.

## Design

### Semantic records

Add small immutable records rather than conflating a declaration with an instance:

```python
@dataclasses.dataclass(frozen=True)
class FnCandidate:
    fn: FnSymbol
    impl_args: tuple[typs.Typ, ...]
    explicit_fn_args: Optional[tuple[typs.Typ, ...]]


@dataclasses.dataclass(frozen=True)
class AppliedFn:
    fn: FnSymbol
    args: tuple[typs.Typ, ...]
```

`FnCandidate` is the result of resolving a function path. `impl_args` are the arguments already
selected by an associated-function lookup. `explicit_fn_args` has three meaningful states:

- `()` for a non-generic function;
- a non-empty tuple for a generic function with an explicit argument list;
- `None` for a generic function whose omitted arguments must be inferred from its use.

`AppliedFn` is the type checker's completed semantic result. Its flat `args` tuple is in the order
already required by `FnSymbol.instantiate()`: impl arguments followed by the function's own
arguments. Arguments may still mention an enclosing generic body's comptime parameters; lowering
substitutes those before requesting the concrete `FnInstance`. Put both records beside `FnSymbol`
in `ir_module.py`, which owns their argument ordering and avoids a new cross-module abstraction.

Add an immutable `TraitApplication` beside `Trait` in `ir_traits.py`:

```python
@dataclasses.dataclass(frozen=True)
class TraitApplication:
    trait: Trait
    args: tuple[typs.Typ, ...]
```

It replaces the recurring pair of a raw `Trait` plus arguments recovered separately from the AST.
It is a semantic reference, not an instance.

### Resolution pipeline

Split `Env`'s current `_resolve_path_seg` work into two named operations:

1. `_lookup_path_seg(ns, scope, ident)` performs only namespace lookup, visibility checking,
   associated-function selection, and enum-variant selection. Its scope type is the closed union
   `Env | ir_module.Mod | typs.StructTyp | typs.EnumTyp`; its `match` has one arm for each member
   and no wildcard.
2. `_resolve_path_seg(ns, scope, seg)` calls the lookup helper, then applies `seg.comptime_args`.
   It returns a semantic path target: an applied type, `TraitApplication`, `FnCandidate`, module,
   value, or local declaration. Arguments on a non-generic item are rejected here.

An unapplied `GenericTypTemplate` is a lookup result but never a valid scope. Before advancing to
the next segment, the path walker applies its explicit arguments or raises
`MissingComptimeArgsError`. This removes `StructTypTemplate` from the match's scope union and makes
the exhaustiveness check meaningful. Any other intermediate result outside the usable-scope union,
including a scalar, array, type parameter, trait, function, or value, produces the new
`ItemCannotQualifyPathError` at the segment that cannot serve as a scope.

Replace the broadly typed public `resolve_path()` uses with result-specific entry points:

- `resolve_typ(path) -> typs.TypKind`;
- `try_resolve_trait(path) -> Optional[ir_traits.TraitApplication]`;
- `resolve_var(path) -> resolve.VarTarget`;
- a private or narrowly exposed container resolver for comptime-parameter kind discrimination.

The `try_resolve_trait()` optional result is important: a bound and a trait impl currently translate
a non-trait final item into different user-facing errors (`InvalidComptimeBoundError` and
`ImplForNonTraitError`). Lookup and comptime-argument errors still propagate; only the final
wrong-kind result becomes `None`, so each caller retains its existing diagnostic contract.

The shared walker may remain private and return a closed union. Remove `Any` from the path
resolution signatures. `resolve.VarTarget` contains `FnCandidate`, not `FnSymbol` or
`ImplFnSelection`; dot-member lookup may continue to use `ImplFnSelection` because dot calls are
not path-segment applications.

### Shared explicit-argument application

Replace the three copies of explicit argument handling with a single helper in `typs.py`:

```python
def resolve_explicit_comptime_args(
    item_name: str,
    params: Sequence[ComptimeParamTyp],
    args_ast: Sequence[ast.ComptimeArg],
    e: ir_env.Env,
    span: src.SrcSpan,
) -> tuple[Typ, ...]:
    ...
```

The helper enforces arguments-on-non-generic, missing arguments, exact arity, argument kind, and
bounds for arguments that exist as syntax. Inferred function arguments are already semantic
`typs.Typ` values, so they never pass through this helper; the existing
`check_comptime_arg_bounds()` validates them after inference. Rename `instantiate_generic_typ()`
to `apply_generic_typ()` so the syntax-to-semantic operation is not confused with function
instance creation; it uses the shared helper and then obtains the semantic type from the template.

### Function data flow

For `Box[i32]::make`:

1. Lookup finds `Box` as a template; application produces `Box[i32]`.
2. Associated lookup on that semantic type produces an `ImplFnSelection` containing `make` and the
   impl arguments `(i32,)`.
3. Because `make` is non-generic, application returns `FnCandidate(make, (i32,), ())`.
4. Type checking completes the candidate to `AppliedFn(make, (i32,))`, records that fact for
   the callee `VarExpr`, and uses the substituted `FnTyp`.
5. Lowering substitutes any enclosing abstract arguments and calls `make.instantiate((i32,))`.

For `id[bool](value)`, resolution puts `(bool,)` in `explicit_fn_args`. For `id(value)`, it
instead returns a candidate with `explicit_fn_args=None`; the existing structural call inference
supplies the function arguments in step 4. For the uncalled value `id`, type checking still raises
`MissingComptimeArgsError` because it has no inference context.

Replace `TypCheckResults._generic_calls` and `_generic_var_refs` with one mapping from the function
path's `ast.VarExpr` to `AppliedFn`. Lowering reads only that completed fact. This removes raw
function-symbol branching and AST argument recovery from `_resolve_generic_call()`,
`_generic_var_ref_typ()`, and `CfgBuilder._build_var_expr()`.

### Trait data flow

`Env.try_resolve_trait()` applies the final segment and returns `TraitApplication` or `None` for a
final item of another kind. Migrate:

- `typs.resolve_bound()`;
- trait-impl construction in `Mod._build_trait_impl_defn()`;
- bound-method lookup in `TypCheck._resolve_bound_method()`;
- comptime-parameter bound classification and recursive bound checks.

Consumers use `.trait` and `.args`; none reads `bound.path.segs[-1].comptime_args`. The existing
`NotImplementedError` for calling through a valid generic trait bound remains, but its condition is
the already resolved `.args`, so arguments cannot be silently ignored. This intentionally improves
diagnostic precedence for malformed generic bounds: arity, kind, and bound errors are reported
before the unsupported generic-trait method-call error.

### Future owner inference seam

Do not add an inference callback, placeholder type, or constraint solver now. The split between
`_lookup_path_seg()` and comptime application is the seam for that work: a future application
policy may replace omitted owner arguments with inference variables, produce a provisional owner
type, and ask member lookup for candidates. Until Leech specifies ambiguity and specialization
rules, adding a callback would freeze an interface without a correct implementation behind it.

The present rule intentionally resembles Go's split: generic types require all arguments, while
generic functions may infer arguments from their use. It also preserves a path representation
similar to rustc's without adopting rustc's inference machinery prematurely.

## Compiler precedents

- **rustc:** HIR `PathSegment` stores a resolved `Res`, optional `GenericArgs`, and a separate
  `infer_args` flag. Its comment explicitly describes `Vec::new` as an inferred
  `<Vec<..>>::new::<..>`. This supports keeping declaration resolution, attached argument syntax,
  and inference state distinct rather than returning a concrete instance directly.
  [rustc HIR source](https://github.com/rust-lang/rust/blob/main/compiler/rustc_hir/src/hir.rs)
- **Swift:** declaration and member references introduce type variables and constraints; after
  solving, solution application inserts `SpecializeExpr` nodes for generic declarations and
  members. That validates a semantic application layer after lookup, but Swift's coupled overload
  and constraint solver is deliberately beyond this issue.
  [Swift type checker design](https://github.com/swiftlang/swift/blob/main/docs/TypeChecker.md)
- **Go:** generic functions may have arguments inferred from calls or expected function types,
  while generic types always require all arguments explicitly. This is the closest user-visible
  policy to the deliberately limited first step here.
  [Go specification: instantiation and inference](https://go.dev/ref/spec#Instantiations)
- **Zig:** generic function parameter types are inferred at the call and generic data structures
  are comptime functions returning types. Its integrated comptime model confirms that application
  belongs in semantic analysis, but it does not offer a direct nominal-path representation for
  Leech to copy.
  [Zig language reference](https://ziglang.org/documentation/master/#Function-Parameter-Type-Inference)
- **Clang/C++ and GCC:** C++ lookup distinguishes dependent names and may defer their lookup until
  template instantiation. That machinery solves a more general problem than #83 and warns against
  pretending an omitted generic owner can always be resolved before its type is known. Leech will
  keep owners explicit until it has defined comparable candidate and ambiguity rules.
  [Clang template semantic analysis](https://clang.llvm.org/doxygen/SemaTemplate_8cpp_source.html),
  [GCC two-stage name lookup](https://gcc.gnu.org/onlinedocs/gcc/Name-lookup.html)

## Implementation tasks

### Task 1: Put import validation at the loader boundary

**Files:** `src/leech/ir_loader.py`, `src/leech/ir_module.py`, `tests/test_packages.py`

- [ ] At the start of `ModLoader.resolve_import()`, scan `path.segs` in source order and raise
  `ComptimeArgsOnNonGenericItemError` for the first segment carrying arguments. Use that segment's
  identifier and span.
- [ ] Perform the check before constructing filesystem candidates so an invalid argument list wins
  over `ModDoesNotExistError`, even when the named module does not exist.
- [ ] Delete the duplicate check from `Mod._build_defn()`; it should only ask the loader to resolve
  and load the import.
- [ ] Keep the existing three import spellings and add or strengthen a missing-module case that
  pins diagnostic precedence and the offending intermediate segment's span.
- [ ] Run `uv run pytest tests/test_packages.py -q` and `uv run basedpyright`.

### Task 2: Add semantic application records and one explicit-argument resolver

**Files:** `src/leech/ir_module.py`, `src/leech/ir_traits.py`, `src/leech/typs.py`

- [ ] Add `FnCandidate`, `AppliedFn`, and `TraitApplication` with the invariants above. Keep their
  docstrings about their own contract; do not describe caller lists.
- [ ] Add `resolve_explicit_comptime_args()` and migrate type, trait, and function explicit-
  argument validation to it without changing diagnostic classes, spans, or arity/kind/bound
  order.
- [ ] Rename `instantiate_generic_typ()` to `apply_generic_typ()` and update its documentation and
  repository references. Do not rename `GenericTypTemplate.instantiate()` or change compilation
  request behavior in this issue.
- [ ] Add focused tests for the records' argument ordering and for the shared helper's empty,
  non-generic, wrong-arity, wrong-kind, and failed-bound cases where existing end-to-end coverage
  does not already pin the behavior.
- [ ] Run the focused generic function, generic struct, and trait tests plus Basedpyright.

### Task 3: Split path lookup from application and make scope matching exhaustive

**Files:** `src/leech/ir_env.py`, `src/leech/errors.py`, `src/leech/resolve.py`,
`src/leech/typs.py`, `src/leech/typcheck.py`, `src/leech/ir_module.py`,
`tests/test_errors.py`

- [ ] Introduce the closed usable-scope and path-target aliases. Remove `Any` from the path
  resolution methods.
- [ ] Extract `_lookup_path_seg()` with exactly the `Env`, `Mod`, `StructTyp`, and `EnumTyp` match
  arms. Do not add a wildcard or catch-all arm. Keep visibility and diagnostic behavior unchanged.
- [ ] Make `_resolve_path_seg()` call lookup then a separately named application helper. Preserve
  the current type-application behavior in this checkpoint; Tasks 4 and 5 extend the same helper
  to function and trait records without changing lookup again.
- [ ] Before advancing the walker, require the resolved intermediate result to be a usable scope.
  Raise `MissingComptimeArgsError` on an unapplied generic type at its own segment.
- [ ] Add `ItemCannotQualifyPathError`, reported at the offending segment, for every other
  non-scope intermediate result. Its message identifies the item kind and name, e.g.
  `Type "i32" cannot qualify a path`; do not preserve today's misleading next-segment
  `ItemNotFoundError` fallback. Cover scalar, array, type-parameter, trait, function, and value
  intermediates as applicable.
- [ ] Remove `resolve_typ()`'s now-unreachable final `GenericTypTemplate` check after the walker
  guarantees that every generic type is applied or rejected at its own segment.
- [ ] Keep the generic walker temporarily for compatibility, but give it a closed result union and
  remove `Any`. Add result-specific entry points as their consumers migrate in Tasks 4 and 5;
  delete or privatize the broad surface only after the last migration.
- [ ] Add direct resolution tests for `Box[i32]::make`, cross-module owner application, arguments
  on module/intermediate/non-generic segments, missing owner arguments, and type/value comptime
  arguments forwarded from an enclosing generic context. Add negative tests for `i32::x`,
  `array[i32, 3]::x`, and another non-scope item kind to pin the new diagnostic and span.
- [ ] Run the focused package, generic struct, generic function, impl, trait, pattern, and enum
  tests plus Basedpyright. Basedpyright passing is an acceptance criterion for the exhaustive
  closed-union match, not a reason to restore a wildcard.

### Task 4: Apply function arguments in `Env` and complete candidates in type checking

**Files:** `src/leech/check_results.py`, `src/leech/typcheck.py`,
`src/leech/ir_builder.py`, `src/leech/resolve.py`, `tests/test_generic_fns.py`,
`tests/test_generic_structs.py`, `tests/test_traits.py`

- [ ] Replace the two generic-function result maps with one `VarExpr -> AppliedFn` fact and assert
  stable repeat writes through the existing `_set_fact()` mechanism.
- [ ] Extend `_resolve_path_seg()`'s application helper to return `FnCandidate` for both free and
  associated path functions. Add `Env.resolve_var()` and update `resolve.VarTarget`; dot-member
  `Callee` resolution continues to use `ImplFnSelection`.
- [ ] Refactor generic call checking to use `FnCandidate.explicit_fn_args`; infer only when it is
  `None`. Resolve and check explicit arguments nowhere else.
- [ ] For a function used as a value, require the candidate to be complete, record `AppliedFn`, and
  derive its substituted pointer type. Preserve the existing missing-arguments diagnostic when
  inference context is absent.
- [ ] Normalize associated path functions and free functions through the same candidate-to-applied
  path. Preserve impl-before-function flat argument ordering.
- [ ] Make `CfgBuilder._build_var_expr()` consume only `AppliedFn` for path function references,
  substitute enclosing arguments, and then call `FnSymbol.instantiate()`. Delete the old generic
  call/reference branches made redundant by the unified fact. Leave dot-call selection lowering
  alone.
- [ ] Migrate every dispatch affected by removing `FnSymbol` and `ImplFnSelection` from
  `VarTarget`: `TypCheck._resolve_callee()`, `_check_var_expr()`, `_check_place_inner()`, and
  `_place_mut()`, plus `CfgBuilder._build_call_expr()` and `_build_var_expr()`. Run an `rg` sweep
  for both old target types rather than assuming this list remains complete. Factor shared
  candidate classification if it keeps the four checker paths consistent.
- [ ] Add an IR-level regression test that resolving and type-checking an explicitly applied
  function records its semantic arguments without adding a `requested_fn_instances()` entry;
  lowering the reference or call must add the correctly keyed instance.
- [ ] Preserve end-to-end behavior for inferred calls, explicit calls, bare function pointers,
  address-of (including `&f` and `&Box[i32]::make`), recursive generic calls, cross-module calls,
  impl siblings, and abstract arguments later substituted during lowering.
- [ ] Run `uv run pytest tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py
  tests/test_traits.py -q` and `uv run basedpyright`.

### Task 5: Apply trait arguments in `Env` and remove syntax recovery

**Files:** `src/leech/typs.py`, `src/leech/ir_module.py`, `src/leech/typcheck.py`,
`src/leech/ir_traits.py`, `tests/test_traits.py`, `tests/test_errors.py`

- [ ] Extend `_resolve_path_seg()`'s application helper to return `TraitApplication`, add
  `Env.try_resolve_trait()`, and make `resolve_bound()` return that record. Update bound checking
  and `TypParamTyp.declares_bound()` to use `.trait` and `.args`.
- [ ] Make trait impl construction call `Env.try_resolve_trait()` once and pass the resolved trait
  and arguments onward. Delete its separate `resolve_trait_comptime_args()` call.
- [ ] Preserve caller-specific wrong-kind diagnostics: `resolve_bound()` translates `None` to
  `InvalidComptimeBoundError`; trait impl construction translates it to `ImplForNonTraitError`.
- [ ] Make bound-method lookup use the already applied trait. Retain the existing loud
  `NotImplementedError` for a valid non-empty generic-trait application until generic trait
  methods are implemented. Add tests proving malformed applications report arity/kind/bound
  diagnostics first.
- [ ] Search for every read of `path.segs[-1].comptime_args`. After this task, such reads are
  permitted only inside `Env`'s application step or syntax-oriented AST/parser tests; no semantic
  consumer may recover arguments from the path AST.
- [ ] Migrate the remaining container consumers, then delete or privatize the broad
  `resolve_path()` API so each public entry point advertises its result type.
- [ ] Preserve full-path diagnostic text now that `Path.str()` includes arguments, including
  recursive trait-bound messages. Verify each changed message remains truthful rather than
  mechanically accepting snapshots.
- [ ] Run `uv run pytest tests/test_traits.py tests/test_errors.py -q` and
  `uv run basedpyright`.

### Task 6: Repository-wide cleanup and validation

**Files:** all touched #83 source, tests, plans, and specs

- [ ] Search for stale `Segment`/`segment` identifiers and finish the requested `Seg`/`seg` rename
  without changing prose where the English word is not an identifier.
- [ ] Search for stale `instantiate_generic_typ`, raw function-symbol path targets,
  `resolve_trait_comptime_args`, semantic reads of final-segment argument AST, and wildcard matches
  over the path scope union.
- [ ] Review every changed docstring and comment against `AGENTS.md`; remove caller narration,
  historical notes, redundant parameter descriptions, and comments that merely restate code.
- [ ] Run `uv run ruff format .`, then all required gates:

  ```text
  uv run pytest
  uv run ruff check .
  uv run basedpyright
  uv run reuse lint
  git diff --check
  ```

- [ ] Inspect the final diff to ensure issue #83 contains no owner-inference solver, no new generic
  associated-function support, no generic-trait method implementation, and no change to import or
  comptime-argument syntax.

## Acceptance criteria

- `Env` never silently ignores a path segment's comptime arguments.
- No semantic caller outside `Env` reads a path segment's comptime arguments to finish resolution.
- The scope lookup match is exhaustive and has no wildcard case; Basedpyright passes.
- `Box[i32]::make(...)`, qualified equivalents, explicit generic function calls, inferred generic
  function calls, and applied generic function values retain their current behavior.
- `Box::make(...)` remains invalid when `Box` is generic; this issue does not infer `Box`.
- Trait references carry their resolved arguments as a semantic value and retain current
  diagnostics and unsupported-feature failures.
- A non-container item used before the end of a path reports `ItemCannotQualifyPathError` at that
  item rather than a misleading not-found error for the following segment.
- Non-trait bounds and non-trait impl targets retain their distinct caller-owned diagnostics;
  malformed generic trait applications report argument errors before unsupported method use.
- Import arguments are rejected by `ModLoader.resolve_import()` before filesystem resolution.
- Function path resolution and type checking do not create a `FnInstance`; downstream
  lowering/materialization does.
- The full validation suite passes.

## Deferred work

Owner-argument inference needs a separate language-design issue. It must define candidate lookup
for multiple disjoint inherent impls, ambiguity diagnostics, interaction with expected result types
and ordinary call arguments, and whether specialization may change the associated declaration
selected. That work can extend the comptime-application seam with inference variables or a policy
object without undoing the lookup/application/instantiation boundary established here.
