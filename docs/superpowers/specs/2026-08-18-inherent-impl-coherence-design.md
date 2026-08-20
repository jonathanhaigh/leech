<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Inherent impl coherence design

## Goal

Decouple inherent associated-function names from `StructTyp` while preserving
declaration-time rejection of conflicting functions. Fields and associated
functions become separate name domains, and inherent coherence becomes a
responsibility of `ImplRegistry` so future non-struct types can have inherent
impls without depending on struct internals.

## Language rules

- A field and an inherent associated function may have the same name.
- Field syntax continues to resolve fields: `value.name` and struct-literal
  `name: value` entries do not consult inherent impls.
- Method-call and associated-function syntax continue to resolve functions:
  `value.name()` and `Type::name` do not resolve fields.
- Multiple inherent impl blocks may apply to the same type.
- Two inherent impls are rejected at declaration time when their self types
  overlap and they declare at least one function with the same name.
- Structurally disjoint inherent impls may reuse function names.
- Bounds do not distinguish otherwise-overlapping impls. This matches Leech's
  conservative trait-impl coherence rule.
- Duplicate functions within one impl remain the responsibility of
  `Impl.add_fn`.

## Architecture

`StructTyp` stores fields only. Remove `_assoc_fn_spans` and
`reserve_assoc_fn_name`; `_add_field` checks only reserved and duplicate field
names. This leaves `typs` unaware of associated functions and inherent impl
coherence.

`ImplRegistry.add_impl` handles cross-block inherent conflicts. When adding an
inherent impl, it compares the new impl with inherent impls under the same head
shape. For every structurally overlapping pair, it compares the names declared
by their function ASTs. A shared name raises `DuplicateItemDefnError` using the
new and existing function spans. Different names do not make overlapping impl
blocks conflict.

The registry check uses `typs.typs_overlap`, which structurally unifies type
parameters from both impl declarations and therefore detects true partial
overlap even when neither declaration subsumes the other. It does not evaluate
bounds to prove impls disjoint. Trait-impl coherence uses the same helper, so it
also gains true partial-overlap detection.

`lookup_assoc_fn` and `lookup_member` become `ImplRegistry` methods because
both operations query registry-owned inherent and trait implementations. The
method forms are:

```python
registry.lookup_assoc_fn(typ, name, span)
registry.lookup_member(typ, name, span)
```

`disambiguate` remains a private module helper. Although declaration-time
coherence prevents newly registered conflicting inherent functions,
`lookup_assoc_fn` retains defensive ambiguity handling.

`Mod._build_impl_fns` retains its current construction and registration order.
Only the obsolete `StructTyp.reserve_assoc_fn_name` call is removed. Successful
inherent functions continue to be bound by bare name in their own impl block's
environment after `Impl.add_fn`.

## Diagnostics and ordering

Cross-block conflicts are reported while the later impl is registered, before
its functions are built. The diagnostic category remains
`DuplicateItemDefnError("associated function", ...)`, with the later
declaration as the primary span and the earlier declaration as the existing
span.

Field/function coexistence no longer produces a diagnostic. Duplicate fields,
same-block duplicate functions, trait-impl conflicts, visibility failures, and
lookup precedence retain their existing diagnostics.

## Tests

Tests will establish that:

- a field and receiver method with one name coexist and both syntaxes work;
- a field and receiverless associated function with one name coexist and both
  field construction/access and `Type::name` work;
- separate overlapping inherent impls with a shared function name fail during
  module construction, including generic overlap;
- disjoint inherent impls may reuse a function name;
- same-block duplicates still fail through `Impl.add_fn`;
- generic inherent lookup, bare sibling calls, visibility checks, finite-size
  checking, and inherent-before-trait lookup remain unchanged.

No emitted symbol names or runtime representations change.
