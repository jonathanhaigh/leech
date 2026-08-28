<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Calling methods through generic trait impls

Gap 3 of the
[generics/traits gap assessment](2026-08-08-generics-traits-gap-assessment-design.md),
including the architecture prerequisites that document folds into this spec
— less one it turns out not to need, and plus one it didn't foresee.

## Problem

A generic trait impl's methods can be declared and type-checked, but never
called. `ir_traits.lookup_member` finds the impl and the method by
structural matching, then gives up:

```python
if method.recv_typ is not typ:
    raise NotImplementedError("calling a method through a generic trait impl isn't supported yet")
```

The method it found was built against the impl's own *abstract* self type
(`Box[T]`), not the concrete type being called on (`Box[i32]`). Calling it
needs the impl's type parameters substituted throughout its body — exactly
what `Fn.impl_instance` already does for generic *inherent* impls.

A second site does the same thing for a call through a trait bound:
`ir_builder`'s `TraitMethod` branch re-finds the impl at lowering time and
raises the same error.

## Goal

`impl[T] Show for Box[T]` and `impl[T] Show for [T; 3]` produce callable
methods, reached either directly (`b.show()`) or through a bound
(`fn f[T: Show](x: T) { x.show(); }`), with correct, collision-free symbol
names.

## What the prerequisites actually cost

Each problem below was reproduced against a throwaway spike, not inferred.
The spike widened the self type and replaced both `NotImplementedError`s;
the full suite stayed green apart from the two tests that assert the
feature is unsupported.

| Case | Failure |
| --- | --- |
| `impl[T] Show for [T; 3]` | `AttributeError: 'ArrayTyp' object has no attribute 'qualified_name'` |
| Free-function call inside such a method | `AssertionError` — `sibling_instances`' `assert isinstance(recv_typ, StructTyp)` |
| `self.*.other()` between two methods of one generic trait impl | `KeyError`, then `unhandled comptime value <Fn>` |
| Two traits declaring `go`, both impl'd for `Box[T]`, both called | `llvmlite DuplicatedNameError: main::Box[i32]::go` |

The third differs from what the gap assessment predicted. It guessed the
`StructTyp` assert would fire; in fact `sibling_instances` returns an empty
map, because a trait impl's methods are stored on `ir_traits.Impl`, never on
`StructTyp._members`. The abstract `Fn` is then handed to codegen unchanged
and fails there. The assert is a real problem too, but a separate one: it
needs a *non-struct* self type to reach, which is why the free-function case
is listed on its own.

## Design

### Widening the self type

`Fn.impl_instance`, `Fn._get_instance`, `Fn._instance_cache`,
`FnInstance._self_typ`, `FnInstance.__init__` and
`FnInstance.substitute_typ_params` all take `typs.StructTyp` where they only
ever use operations defined on `Typ`. Widen the annotations; the two
`isinstance(..., StructTyp)` asserts become an `is not None` check and a
deletion respectively.

Nothing else in this section is needed: `infer_typ_args` and
`substitute_typ_params` are `Typ`-level, and `_head_shape` already falls back
to `type(typ)` for non-struct types.

### Symbol names

Two independent defects, one introduced by the widening and one already
present.

**Qualification isn't recursive.** `StructTyp.qualified_name` is
`f"{self.mod_name}::{self.name}"`, and `StructTyp.name` renders its type
arguments with `typ_arg.name`. So `Box[a::Foo]` and `Box[b::Foo]` already
mangle to the same `main::Box[Foo]` today, with no generic trait impl
involved. Give `Typ` a `qualified_name` property defaulting to `self.name`
— correct as-is for `i32`, `bool` and the like, which are declared in no
module — and override it wherever a type contains another:

| Type | `qualified_name` |
| --- | --- |
| `StructTyp` | `f"{mod_name}::{ident}[{args' qualified_names}]"` |
| `EnumTyp` | `f"{mod_name}::{name}"` |
| `PtrTyp` | `f"*{mut}{pointee.qualified_name}"` |
| `ArrayTyp` | `f"[{element.qualified_name}; {length}]"` |

`EnumTyp` is included because an enum can be a generic struct's type
argument, and so reach a symbol name that way, even though a trait impl for
an enum can't itself be generic. `FnTyp` needs no override: no type syntax
can spell a function type, so one can be neither a self type nor a type
argument.

**A trait impl's methods aren't discriminated by trait.** Give `Fn` a
`trait_name: Optional[str]` — a plain string, `f"{trait.mod_name}::{trait.name}"`,
threaded through `_build_impl_fns` from `_build_trait_impl_defn`, and `None`
for an inherent impl. `FnInstance.qualified_name` then reconstructs the
`<SelfTyp as Trait>` shape `ir_traits.Impl.name` already uses for non-generic
impls:

```python
if self._self_typ is not None:
    prefix = self._self_typ.qualified_name
    trait_name = asserts.checked_cast(self._fn, Fn).trait_name
    if trait_name is not None:
        prefix = f"<{prefix} as {trait_name}>"
    return f"{prefix}::{self.name}"
```

Built per instance from the *concrete* self type, so `Box[i32]` and
`Box[bool]` differ — storing the precomputed `Impl.name` on `Fn` instead
would give every instantiation the one abstract string `<Box[T] as Show>`.
No impl-module prefix is needed on top of these two: the orphan rule and the
conflicting-impls check together guarantee at most one impl of a given
(trait, self type) pair program-wide.

The `<... as ...>` spelling is already emitted and already survives LLVM: a
non-generic trait impl produces `main::<i32 as Show>::show` today.

### Siblings

`FnInstance.sibling_instances` maps each method of the enclosing impl block
to that method's own instance, so that one method calling another doesn't
re-resolve from scratch. It currently finds those siblings through
`StructTyp.assoc_fns`, which a trait impl's methods never enter.

Base siblings on **the impl block a `Fn` was declared in**, uniformly for
inherent and trait impls. `_build_impl_fns` already constructs one block's
functions in a single loop; it keeps a `list[Fn]`, passes it to each `Fn` as
it goes, and appends. `Fn` holds the list; `sibling_instances` reads it.

This needs no reference from `Fn` to an `Impl` object, and no new collection
on `ir_traits.Impl`. It leaves `StructTyp.assoc_fns` with no callers, so
that property goes.

It is also behaviour-preserving for the inherent case, which is not obvious:
`assoc_fns` spans every method registered on one abstract `StructTyp`
instance, which sounds broader than one impl block. But two
`impl[T] Foo[T]` blocks intern *different* `TypParamTyp`s — they are keyed
by declaring AST node — and so produce two different abstract `Foo[T]`
instances that never shared a bucket. The only impl blocks that do share one
are non-generic, and a non-generic impl's methods never take this path at
all, since `sibling_instances` returns early unless `_self_typ` is set.

### Dispatch

Both sites replace their `NotImplementedError` with
`method.impl_instance(concrete_self_typ)`:

- `ir_traits.lookup_member`, for a direct call, where the concrete type is
  the one being looked up on.
- `ir_builder`'s `TraitMethod` branch, for a call through a bound, where it
  is the lowered receiver's pointee type.

Both are concrete at those points. The resulting `FnInstance` then travels
paths that already exist: `substitute_typ_params` handles the case where
type checking resolved against a still-abstract receiver, and `mono`
discovery already collects trait-impl methods, because `_build_impl_fns`
appends to `generic_fns` for any generic impl block regardless of which
branch built it.

### Bound-aware impl selection

`impl[T: Show] Show for Box[T]` says nothing today: `find_impl` matches on
the self type's shape alone and ignores the impl's own bounds. Instantiating
it at `Box[bool]` reaches lowering and dies on a bare
`assert impl is not None` in `ir_builder`. This is not new — the inherent
form, `impl[T: Show] Box[T]`, fails identically today — but gap 3 opens a
second route to it.

Treat an unsatisfied bound as **the impl not applying**, not as an error:
`Box[bool]` simply has no `show`. That is what a bound on an impl means, and
it is the only reading that composes — two impls distinguished only by their
bounds are still conflicting impls, so this changes selection without
touching coherence.

`TypParamTyp` carries its own `bounds`, so a single helper driven by the
inferred bindings serves every site:

```python
def unsatisfied_bound(
    bindings: Mapping[TypParamTyp, Typ], e: ir_env.Env
) -> Optional[tuple[TypParamTyp, ir_traits.Trait]]: ...
```

The bindings carry both halves — parameters as keys, arguments as values —
so the helper can keep `check_typ_arg_bounds`' existing rule of binding
every parameter before resolving any bound, which is what lets one bound's
type arguments name a sibling parameter declared either side of it.

`check_typ_arg_bounds` is refactored onto it and keeps raising
`UnsatisfiedBoundError`. Two selection sites consult it and *skip* the
candidate instead:

- `ImplRegistry.find_impl`, after its structural match succeeds, resolving
  the bounds in `Impl.env`.
- `StructTyp._scan_generic_assoc_fn`, the inherent-impl equivalent,
  resolving them in the candidate instance's own `env` — fixing that case
  too.

A parameter absent from the bindings is left unchecked; see
[Non-goals](#non-goals).

### Two ways to discharge a bound

Nothing above works against an *abstract* type, and Leech has only ever had
one way to discharge a bound: find a registered impl. Every abstract case is
then some approximation, and the two that existed approximated in opposite
directions:

- `check_typ_arg_bounds` against an abstract argument assumed **nothing**
  holds, so `struct Box[T: Show]` rejected `Box[U]` inside
  `fn wrap[U: Show]` — where `U: Show` is declared right there.
- An earlier version of this design skipped the check whenever the type was
  abstract, assuming **everything** holds. That let
  `fn f[U](x: Box[U]) { x.show() }` compile against
  `impl[T: Show] Show for Box[T]` with no `U: Show` in sight, and turned
  `f[bool]` into a bare `assert impl is not None` at lowering — a compiler
  crash where a type error belongs.

Both are the same missing concept: a bound can also be discharged by an
**assumption in scope**. This is rustc's model, where selection assembles
candidates from impls (`assemble_candidates_from_impls`) *and* from the
item's own where-clauses (`assemble_candidates_from_caller_bounds`, yielding
a `ParamCandidate` out of `ParamEnv::caller_bounds`). Inside a generic body,
`U: Show` is proved by a param candidate; with no such clause there is no
candidate and selection fails at the definition.

Leech already stores that param environment: `TypParamTyp.bounds`. So one
predicate replaces both approximations:

```python
def implements(self, trait: Trait, typ: typs.Typ) -> bool:
    if isinstance(typ, typs.TypParamTyp) and typ.declares_bound(trait):
        return True
    return self.find_impl(trait, typ) is not None
```

`unsatisfied_bound` asks this instead of `find_impl(...) is None`, and both
abstract-type skips go. The case that motivated them still works, now for
the right reason: matching `impl[T: Show] Show for Box[T]` against its own
`Box[T]` binds `T` to itself, and `T` genuinely declares `Show`. This is
also where [supertraits](2026-08-08-generics-traits-gap-assessment-design.md#supertraits)
will need rustc's *elaboration* — `T: Ord` supplying `T: Eq` — in one place
rather than scattered.

### Where a type parameter's bounds resolve

`implements` has to turn `TypParamTyp.bounds` — unresolved AST paths — into
traits, which needs the scope they were *written* in.
`typcheck._resolve_bound_method` already faces this and uses the call site's
env instead, which is fine within one module and wrong across them.

`TypParamTyp` therefore gains a `decl_env`, threaded through
`typ_params_from_ast` from the five call sites that each already hold one.
It isn't part of the cache key — the owner alone determines it, since a
parameter is declared in exactly one place. Without it, a bound spelled
`a::Show` on a parameter declared in `main` would be resolved in the *impl's*
module, where `a` may not be imported at all.

This earns back the `assert impl is not None` that fails today. A bound call
site checks `find_impl(Show, Box[bool])` through `check_typ_arg_bounds` and
reports `UnsatisfiedBoundError` there — the right message at the right
place — and lowering later asks the registry the same question and gets the
same answer. The two agree by construction rather than by luck.

Bound checking and impl selection are now mutually recursive.
It terminates because each step moves from a self type to one of its proper
subterms, and Leech types are finite. Rather than rest on that argument, add
a recursion-depth guard raising `NotImplementedError`, mirroring
`typs._MAX_BOUND_DEPTH`, which guards the analogous recursion in
`resolve_bound`.

### Not needed: shared call-site type-argument resolution

The gap assessment lists this as the second architecture prerequisite and
folds it into this spec. It doesn't belong here. A method reached through a
generic trait impl introduces no type parameters of its own —
`_build_impl_fns` still rejects those, which is gap 4 — and the impl's own
parameters are inferred from the self type inside `FnInstance.__init__`, not
at the call site. There is no call-site inference to share yet, and
extracting a shared helper against a single caller would be designing it
blind. It moves wholly to gap 4, which has both callers that motivate it.

## Non-goals

- **Generic methods.** A method introducing its own type parameters stays
  rejected; that is gap 4.
- **Generic traits.** The trait need not be generic here; only the impl is.
- **Unconstrained impl parameters.** `impl[T, U] Box[T]` is accepted
  silently today and stays so. `U` is never inferred, so it is never bound
  and never bound-checked; a body that names it would leak an abstract type.
  Rust rejects these outright. Recorded as future work.
- **Diagnostics for a method lost to an unsatisfied bound.** `b.show()` on
  a `Box[bool]` now reports what any unknown method reports — a fallthrough
  to field access, ending in `NotCallableError` against `void`. That message
  is poor, but it is poor for every unknown method, and improving it is a
  diagnostics change, not this one.
- **Unifying inherent and trait impl selection.** `ImplRegistry.find_impl`
  and `StructTyp._scan_generic_assoc_fn` now gate on an impl's own bounds
  the same way, in two modules. The matching half is shared as
  `typs.match_typ_args`; sharing the rest needs inherent blocks to become
  real `Impl` objects, which is a refactor in its own right — recorded,
  with its knock-on for symbol naming, in the
  [gap assessment](2026-08-08-generics-traits-gap-assessment-design.md#potential-future-work).
- **Trait impl method visibility.** These methods default to private, so
  calling one from another module is rejected by `is_accessible_from` and
  its symbol is never declared there. Writing `pub` on the method works
  around it. Pre-existing and untouched here; found while testing, and
  recorded with its resolution — visibility should follow the trait's — in
  the [gap assessment](2026-08-08-generics-traits-gap-assessment-design.md#potential-future-work).
- **Order-dependent bound checking.** `check_typ_arg_bounds` runs eagerly
  during module building for a type reference, so it can consult the impl
  registry before every impl is registered. Pre-existing, unchanged here,
  and the same deferred whole-program pass the gap assessment proposes for
  supertraits is where it belongs.

## Testing

Both `..._not_supported_yet` tests become positive tests: a direct call
through a generic trait impl, and a call through a trait bound.

**Instantiation and naming.** Two instantiations of one generic trait impl's
method, both called, checking they run and emit distinct symbols. A
non-struct self type (`impl[T] Show for [T; 3]`). Two traits declaring the
same method name, both impl'd for the same generic struct and both called —
this is what collides without the trait discriminator. Two modules declaring
same-named traits, and two modules declaring same-named structs used as one
generic struct's type arguments — the latter fails today, before any of this
work.

**Siblings.** One method of a generic trait impl calling another on `self`,
for a struct self type and a non-struct one. A *free* function called from
inside a generic trait impl method with a non-struct self type — the case
that reaches the `sibling_instances` assert, and which no sibling call is
needed to trigger. Regression coverage for inherent impls is the existing
suite, which exercises `assoc_fns`-based sibling resolution throughout.

**Bound-aware selection.** `impl[T: Show] Show for Box[T]` used at `Box[i32]`
(satisfied, runs), at `Box[bool]` reached through a bound (reports
`UnsatisfiedBoundError` at the call site), and at `Box[bool]` called
directly (no such method). The inherent equivalent, `impl[T: Show] Box[T]`
at `Box[bool]`, which crashes on a bare assert today. A bound satisfied by a
nested type (`Box[Box[i32]]`) to exercise the recursion between selection
and bound checking.

**Assumptions in scope.** For each of the trait-impl and inherent paths: a
bounded impl reached from a caller that declares the matching bound
(`fn f[U: Show](x: Box[U])`, applies) and from one that doesn't
(`fn f[U](x: Box[U])`, no such method). A bounded impl's own method calling
a sibling on `self`, where the impl's bound is its own premise. A struct's
bound discharged by the caller's declared bound (`struct Box[T: Show]` used
inside `fn wrap[U: Show]`), which fails today.

The two paths need separate tests and are not interchangeable. A bounded
impl's sibling call reaches `find_impl` with the impl's abstract self type;
the inherent equivalent does *not*, because those methods are registered on
the very `StructTyp` instance being looked up, so the members table answers
directly and the generic scan never runs. Reaching that scan abstractly
takes a *different* abstract instantiation — a generic free function taking
`Box[U]`.

**Bound resolution scope.** A parameter declared in `main` whose bound is
spelled `a::Show`, matched against an impl written in `a`, which does not
import itself. Resolving in the impl's env fails with `ItemNotFoundError`;
resolving in the parameter's own `decl_env` succeeds. The bound trait's
*method* is deliberately not called in that test: cross-module trait method
calls are blocked by `is_accessible_from`, since a trait impl's methods can't
be `pub` — a separate pre-existing gap (see [Non-goals](#non-goals)).
