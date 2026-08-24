<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Compilation-wide lazy state design

## Purpose

Leech creates function and generic-struct instances lazily. The caches recording those
requests live on individual declarations, monomorphization repeatedly scans every
declaration to discover new entries, and recursive semantic computations are stopped by
four unrelated mechanisms:

- a struct-layout `visiting` list plus a generic-instantiation depth limit;
- a set and depth limit threaded through trait-bound resolution;
- an `ImplRegistry` selection-depth counter;
- a class-global stack for module-variable initializers.

These mechanisms solve variations of the same problem without sharing an owner or a
definition of an active computation. Growing recursive types and bounds never repeat their
fully instantiated keys, so they eventually report depth-limit implementation failures
rather than the declaration cycle causing the growth. Monomorphization is not recursive,
but its polling fixpoint exists only because requested instances have no central owner.

This stage introduces one compilation-owned context for request caches, request order, and
active semantic computations. It is deliberately not a general query engine: it records no
dependency graph, performs no incremental invalidation, and does not replace ordinary
`cached_property` values whose computation cannot recurse.

## Decisions

### One context per compilation

`compilation.Ctx` is created by `ModLoader`. Every root `Env` created by the
loader receives that context, and child environments inherit it. `ImplRegistry` receives
the same context. Focused tests that construct an `Env` directly must explicitly provide
the context, registry, and optional panic reference.

The context owns only state that must be shared across declarations or semantic layers:

- cached `FnInstance` objects, keyed by function-symbol identity and flat type arguments;
- cached generic `StructTyp` instances, keyed by template identity and type arguments;
- the insertion-ordered lists of requested function and struct instances; and
- one stack of active semantic computations.

Process-wide type interning in `Typ._cache`, parsed bundled-module caching, and ordinary
derived properties such as a function signature remain where they are. They are canonical
value caches, not compilation work requests, and moving them would add indirection without
improving cycle handling.

### Typed cache operations, not a heterogeneous public cache

The context exposes typed operations for the two request families:

```python
def instantiate_fn(
    self,
    fn_symbol: ir_module.FnSymbol,
    args: tuple[typs.Typ, ...],
) -> ir_module.FnInstance: ...


def requested_fn_instances(self) -> Sequence[ir_module.FnInstance]: ...


def instantiate_struct(
    self,
    template: typs.StructTyp,
    args: tuple[typs.Typ, ...],
) -> typs.StructTyp: ...


def requested_struct_instances(self) -> Sequence[typs.StructTyp]: ...
```

The implementation may share a private generic helper, but callers do not manipulate an
`object`-typed map or construct cache keys themselves. Equal requests in one compilation
return the same object.

`StructTyp.cache_key` additionally includes a per-compilation identity token owned by the
context. The token does not point back to the context, avoiding a retention cycle through
the process-wide weak type cache. Bundled ASTs are cached process-wide, so without this
distinction a bundled generic struct could retain the first compilation's declaration
environment and send a later compilation's requests to the wrong context. Primitive and
composite type interning otherwise remains process-wide.

Extern functions use the same function-instance cache even though only `()` is valid.
This removes their separate cached `_instance` field. Their `instances` property no longer
creates that instance merely by being inspected; it reports requests like every other
symbol. Source and intrinsic symbols also lose their per-symbol `_instance_cache`
properties. The declaration-level `FnSymbol.instances` and `StructTyp.instances` accessors
delegate during the cache-migration commit, then are removed when monomorphization stops
polling declarations; validated `FnSymbol.instantiate` and `StructTyp.instance` remain.

### Monomorphization drains request logs

The context appends an instance exactly once, when it first enters a request cache.
Monomorphization walks those insertion-ordered logs with integer cursors:

1. Seed root function instances and lower module-variable initializers.
2. While the function cursor has not reached the end of the function request log, process
   the next concrete instance. Lowering it may append more requests to either log.
3. After function discovery is exhausted, walk the struct request log. Resolving fields may
   append further struct requests, so the same cursor loop continues until it reaches the
   moving end.

Abstract instances requested while checking generic declarations stay in the cache but are
skipped by emission. Bodyless extern instances are also skipped: their existing
reachability-independent module-item declaration path remains their sole codegen owner.
Imported non-generic instances remain declaration-only leaves. Discovery changes from declaration
scan order within each fixpoint round to global first-request order; emitted symbol sets and
linkage do not change. `_fixpoint`, `_fn_symbols`, and whole-program template scans
disappear.

### One active-computation tracker

The context owns a stack of frames. A frame contains:

- a domain identifying the semantic operation;
- an identity used to decide whether the operation has recurred; and
- domain-specific display data used by diagnostics.

Entering a frame searches only the active suffix, never completed work. By default,
re-entering the same `(domain, identity)` returns the cycle beginning at the earlier frame.
The struct-layout domain supplies a stricter repetition predicate described below. A
context-manager API guarantees stack cleanup when computation succeeds or raises. Its cycle
result is generic in the detail type, so callers do not cast from `object`.

A caller receiving a cycle must raise its domain-specific diagnostic rather than continue
the guarded computation. This obligation is explicit in `enter`'s docstring.

Cycle identity intentionally differs from cache identity:

- struct layout uses the instantiated `StructTyp`, augmented by a structural-growth test;
- trait-bound resolution uses the bound's declaration-site AST identity;
- impl selection uses the `(Impl, Typ)` candidate-obligation identity;
- module-variable evaluation uses the `ModVar` identity.

A bound's AST identity ignores substituted type arguments deliberately. Re-entering one
bound declaration means its fixed syntax is driving the recursion, even when substitution
grows an argument (`Foo[T] -> Foo[*T]`). Separate bound declarations involving the same
trait, and sequential uses of one declaration, remain distinct.

The tracker is shared infrastructure, not a shared diagnostic. Each domain converts its
cycle frames into the appropriate user error.

## Struct layout cycles

`StructTyp.fields` still triggers finite-size checking before exposing fields. The recursive
walk enters one struct-layout frame per instantiated by-value struct and records the field
edge leading to it. Pointer fields remain terminal; arrays continue to be unwrapped because
LLVM rejects recursive identified layouts even through zero-length arrays.

An exact repeated `StructTyp` is a cycle as today. A different instance of the same
declaration is a growing cycle only when each earlier type argument occurs structurally in
the corresponding later argument. Thus `L[i32] -> L[[i32; 1]]` is rejected immediately,
while finite nesting `Box[Box[i32]] -> Box[i32]` continues. A new general
`contains_typ(container, contained)` helper shares the existing structural traversal used
by type-parameter occurrence checks.

Both exact recursion (`A -> B -> A`) and growing generic recursion
(`L[T] -> L[[T; 1]] -> ...`) now raise `InfiniteSizeStructError` with the cycle's field
edges. `_MAX_STRUCT_INSTANTIATION_DEPTH` and `TypInstantiationDepthExceededError` are
removed. This is an intentional diagnostic correction: the latter program is an infinite
by-value declaration cycle, not merely a deeply nested valid type.

## Trait-bound and impl-selection cycles

`unsatisfied_bound` enters a trait-bound frame for each declaration-site bound and keeps it
active across both `resolve_bound` and the subsequent proof that the argument implements
the resolved trait. This scope matters: impl selection happens after `resolve_bound`
returns. The context is reached through the environment, so `in_progress` parameters no
longer need to be threaded through `resolve_bound`, `unsatisfied_bound`, and
`check_typ_arg_bounds`.

Impl selection has a separate active domain. `_impl_bounds_hold` guards the concrete
obligation `(impl, matched self type)` before checking the impl's bounds. Repeating an
obligation identifies direct or mutual blanket-impl recursion. Selection may recurse on a
strict subterm and terminate; those different obligations remain legal. This replaces
`ImplRegistry._selection_depth` without conflating a trait declaration's bounds with an
impl whose applicability is recursively conditional.

`RecursiveTraitBoundError(UserError)` reports that a declaration-site trait bound is
recursive and adds notes for the cycle. `RecursiveImplSelectionError(UserError)` reports a
recursive implementation obligation and identifies the participating impl declarations.
Recursive trait bounds and recursively conditional impls remain unsupported; this stage
changes internal `NotImplementedError` or depth-limit failures into stable source
diagnostics. Generic-argument transformations in declared bounds do not evade detection.

This is detection only. Coinductive trait solving, recursive trait semantics, and generic
traits remain out of scope.

## Module-variable initializer cycles

`ModVar.initializer` enters a module-variable frame through its environment's context.
`ModVar._resolving` is removed. `CircularVarInitializerError` retains its current wording,
primary span, and per-variable notes, including cycles that cross modules. Unlike the
diagnostic corrections above, this is behavior-preserving.

## Error and cleanup behavior

Every active frame is removed in `finally` logic owned by the context manager. A failed
computation is not cached. A later independent request may retry it and receive the same
user diagnostic. Successfully cached instances are immutable identities and remain in their
request log for the compilation's lifetime.

The context asserts stack discipline: exiting a frame must remove the same frame from the
top. A cycle object is internal compiler plumbing; it never escapes to users without being
translated by the struct, trait-bound, or module-variable layer.

Layout checks do not force another `StructTyp.fields` property while a layout walk is
active; recursion stays within the one explicit walk. This prevents an unrelated nested
property evaluation from inheriting layout frames accidentally.

## Testing

Tests will establish:

- function, intrinsic, extern, and struct instantiation identity is stable within one
  compilation, and bundled struct templates are context-isolated;
- monomorphization discovers recursively requested function and struct instances without
  scanning declaration caches, while preserving symbols and linkage;
- direct and mutual by-value struct cycles still report `InfiniteSizeStructError`;
- a growing generic struct cycle now reports `InfiniteSizeStructError`, with field notes,
  rather than `TypInstantiationDepthExceededError`;
- a legal by-value `Box[Box[i32]]` layout is not mistaken for a growing cycle;
- direct, mutual, and growing recursive trait bounds now report
  `RecursiveTraitBoundError` rather than `NotImplementedError`;
- direct and mutual recursive blanket-impl selection report
  `RecursiveImplSelectionError`;
- a finite nested trait-bound chain and sequential uses of one trait at different arguments
  are not mistaken for cycles;
- direct, mutual, three-way, and cross-module module-variable cycles retain their existing
  diagnostics; and
- recursive functions remain legal and monomorphize once, demonstrating that semantic
  recursion is not confused with an active compiler-computation cycle.

The full pytest, Ruff, basedpyright, REUSE, and diff-check gates remain mandatory.

## Out of scope

- Incremental compilation, dependency graphs, invalidation, parallel query execution, or a
  rustc-style query engine.
- Moving process-wide type interning or harmless immutable derived-property caches.
- Accepting recursive trait bounds through coinduction.
- Generic traits and matching a bound's own type arguments against an impl.
- Changing reachability roots, symbol spelling, linkage, or accepted programs.
