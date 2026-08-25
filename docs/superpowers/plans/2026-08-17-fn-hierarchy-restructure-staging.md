<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# `Fn` hierarchy restructure: staging

Six changes to the class/protocol hierarchy around `ir_module.Fn`, staged into
five independently shippable pieces. This document fixes the order and the
boundaries; each stage gets its own implementation plan when its turn comes.

It is **not** an implementation plan. Stage 1's is
[Reify impl blocks](2026-08-17-reify-impl-blocks.md).

## Why

`Fn` occupies two orthogonal hierarchies at once. Nominally it is a value —
`FnSpec` → `ComptimePtr` → `ComptimeValue` → `Value` — and structurally it
satisfies the `FnTemplate` protocol. For a non-generic `Fn` the value role is
honest. For a generic one, `calculate_typ()` returns `*fn(T) T`, a type
mentioning unsubstituted parameters that no expression may have.

`GenericFn` exists to hide that at binding sites, and it does not cover them
all: `add_assoc_fn` and `_build_impl_fns` bind raw `Fn`s, so the invariant
"a generic callable is never bound as a value" is maintained by convention at
two construction sites and already has a hole. The hole is currently
unreachable only because associated functions cannot be generic — which is
exactly what
[gap 4](../specs/2026-08-08-generics-traits-gap-assessment-design.md#generic-associated-functions--generic-methods)
changes.

Three further consequences of the same root cause:

- A non-generic `Fn` is its own instance while a generic one is never emitted,
  so "the thing codegen emits" is an unnamed subset of `FnSpec` that six
  separate sites re-derive.
- `Fn.trait_name` (a pre-flattened `str`) and `Fn.impl_siblings` (an aliased
  `list`) are denormalized stand-ins for a parent pointer that cannot exist,
  because inherent `impl` blocks are never reified.
- `typs.StructTyp` owns a member table and performs impl selection, which is
  what forces the `typs` → `ir_module` import edge and the local
  `# noqa: PLC0415` imports scattered through `typcheck`.

## Order, and why it is forced

Stages 1–3 are structural and strictly ordered. 4 and 5 depend only on 1–3
and may swap.

**Reification leads.** Flat instantiation arguments mean *impl arguments ++
method arguments*, which needs an owner for the impl-level parameters to be
positional against. Today they are recovered by
`recv_typ.infer_typ_args(self_typ, mapping)` inside `FnInstance.__init__`
precisely because no `Impl` object exists on the inherent side. Stage 2
cannot be done cleanly before Stage 1.

**The symbol/value split follows instantiation.** Stage 3 makes only
instances become values. That statement is only true once every emitted
function is an instance, which is Stage 2.

**Renames go last** so the sweep is a pure-rename diff with no behaviour
mixed in, and so no stage renames a class another stage is about to split.

## Global constraints

Every stage inherits these.

- Python 3.14. Four-space indent, Ruff 100-column limit.
- `uv run pytest` (1112 tests, ~42s at the time of writing), `uv run ruff
  check .`, `uv run ruff format .`, `uv run basedpyright` and `uv run reuse
  lint` all pass before any stage is considered done.
- Imports stay module-qualified (`from leech import typs`, then `typs.Typ`).
- Sphinx-style docstrings per `AGENTS.md`. Docstrings are self-contained:
  no references to this document, to plans, or to design docs.
- New files carry SPDX headers.
- No stage may change the language accepted by the compiler. Every stage is
  behaviour-preserving except where a defect fix is called out explicitly,
  and each such fix gets its own test and its own commit.

## Stage 1 — Reify impl blocks

Change #4. Plan: [2026-08-17-reify-impl-blocks.md](2026-08-17-reify-impl-blocks.md).

Give inherent blocks an `ir_traits.Impl` with an optional trait and register
them in `ImplRegistry` alongside trait impls, so one `find_impl` serves both.
Add `Fn.impl` as a parent pointer.

**Deletes:** `Fn.trait_name`, `Fn.impl_siblings`,
`StructTyp.get_assoc_fn`, `_find_generic_assoc_fn`, `_scan_generic_assoc_fn`,
`_generic_assoc_fn_cache`, and the `ir_module.Fn` half of `StructTyp._members`.

**Fixes two defects:** conflicting generic inherent impls currently trip a
bare `assert found is None` in `_scan_generic_assoc_fn`; and the
impl-selection recursion guard (`ImplRegistry._selection_depth`) currently
covers only the trait path.

**Import cycle:** breaks `typs` → `ir_module` (by moving `Access` to its own
module and removing `typs`' associated-function duties). `typs` ⇄ `ir_traits`
remains, because bound resolution genuinely needs `Trait`; that edge is out
of scope here.

**Exit criteria:** suite green; no `ir_module` import in `typs.py`; inherent
and trait impl selection share one code path.

## Stage 2 — Total and flat instantiation

Changes #1 and #2. One door:

```python
def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance: ...
```

`args` is flat — the impl's own type arguments followed by the function's —
and a non-generic function is `instantiate(())`, following rustc's
`Instance::mono`. Every emitted function is an instance; a declaration is
never an emission target.

**Deletes or collapses:** `Fn.for_recv_typ`, `Fn.impl_instance`, `Fn.instance`
(into `instantiate`), the `(Optional[Typ], tuple[Typ, ...])` cache key,
`FnInstance.__init__`'s re-inference of the impl's parameters,
`FnInstance.qualified_name`'s `_self_typ is not None` branch,
`FnInstance._siblings`' early return, `CfgBuilder._resolve_sibling`'s
`isinstance(self._fn, FnInstance)` check, `_build_impl_fns`' split between
`generic_fns.append(fn)` and `_add_item(...)`, and `mono`'s separate
`for item in mod.items: _ = item.value.cfg` seeding walk.

**Depends on:** Stage 1, for `Impl.typ_params` to make the argument list
positional.

**Exit criteria:** suite green; exactly one instantiation entry point;
`mono.discover`'s roots are `main` plus the public surface rather than a
special walk.

## Stage 3 — Symbol/value split

Change #3.

```
FnSymbol      declaration: parent impl/mod, name, span, access, typ_params, sig, body
FnInstance    FnSymbol + flat args
FnRef(Value)  a constant wrapping an FnInstance; typ is *fn(...)
```

`FnSpec` stops inheriting `ComptimePtr`, so the two `raise AssertionError`
overrides of `load`/`store` disappear — a Liskov violation currently baked
into the hierarchy. `ir_env` binds symbols rather than values, which turns
"is this usable as a value here?" into a question `typcheck` asks once,
rather than an invariant maintained by wrapping at construction.

**Deletes:** `GenericFn` entirely — about 20 sites across `ir_env`,
`ir_module`, `typcheck`, `ir_builder`, `ir_builtins`, `resolve`, `mono` and
`codegen` — and with it the enforcement hole described above.

**Depends on:** Stage 2.

**Exit criteria:** suite green; no class both is a `Value` and produces
instances; a generic symbol is structurally not a `Value`.

## Stage 4 — Renames

Change #5. Mechanical, with the related hierarchy renamed together in one commit.

| From | To | Why |
| --- | --- | --- |
| `FnSpec` / `Callable` | `FnSymbol` | names the semantic declaration found by resolution |
| `NonBuiltinFnSpec` | `ParsedFnSymbol` | owns behavior shared by parsed declarations |
| `FnDecl` | `ExternFnSymbol` | distinguishes an extern symbol from AST declarations |
| `Fn` | `SrcFnSymbol` | identifies a function whose implementation is Leech source |
| `BodyFnSpec` | `LowerableFn` | names the body-lowering capability rather than a specification |
| `GenericBuiltinFn` | `IntrinsicFnSymbol` | genericity is incidental; compiler implementation is fundamental |
| `FnSelection` | `ImplFnSelection` | records selection from an impl plus matched impl arguments |
| `*BuiltinFn` | `*IntrinsicFn` | distinguishes compiler intrinsics from other built-in items |
| `ast.FnSpec` | `ast.FnDecl` | all nodes in this AST hierarchy are declarations |
| `ast.FnDecl` | `ast.ExternFnDecl` | identifies the bodyless extern syntax specifically |
| `ast.TraitFn` | `ast.TraitFnDecl` | identifies the trait-method declaration syntax |
| `FnTemplate` (Protocol) | folded into `FnSymbol` | names a role, not a thing |
| `instance` / `impl_instance` | `instantiate` | already merged in Stage 2 |

Related grammar rules, attributes, methods, and locals follow the same declaration,
symbol, instance, reference, and intrinsic vocabulary in this rename.

`…Symbol` is preferred over `…Defn` for the IR layer because the latter suffix identifies
AST definitions such as `ast.FnDefn` and `ast.ImplDefn`. An AST definition is also a
declaration, so `ast.FnDefn` remains a concrete subclass of `ast.FnDecl`.

**Exit criteria:** suite green; diff contains no behaviour change.

## Stage 5 — Centralize the lazy caches

Change #6. Design: [Compilation-wide lazy state](../specs/2026-08-24-compilation-lazy-state-design.md).
Plan: [Compilation-wide lazy state](2026-08-24-compilation-lazy-state.md).

**Status:** Complete.

One compilation context owns function/struct instance request caches and request logs, so
monomorphization drains new work directly instead of polling every declaration. The same
context owns typed active-computation frames replacing independent recursion guards for:

- `typs._MAX_STRUCT_INSTANTIATION_DEPTH` and `_check_finite_size`'s
  `visiting` list
- `typs.MAX_BOUND_DEPTH` in `resolve_bound`
- `ir_traits.ImplRegistry._selection_depth`
- `ir_module.ModVar._resolving`

`mono._fixpoint` disappears as a consequence of the request logs rather than as a cycle
guard. Growing struct layouts, recursive declared bounds, and recursive impl selection each
receive a source diagnostic in place of a depth-limit error.

This deliberately stops well short of a rustc-style query engine, which a
10k-line compiler does not need. The goal is one place for cycle detection,
not a dependency graph. Ordinary immutable `cached_property` values and process-wide type
interning stay where they are.

**Exit criteria:** met. Monomorphization drains compilation-owned request logs; each
semantic recursion domain reports a cycle rather than a depth-limit
`NotImplementedError`; terminating impl-selection chains are no longer rejected by an
arbitrary depth cap; and the full 1191-test repository verification gate passes.

## Relationship to the generics/traits roadmap

Stage 1 is the roadmap's
[Reify inherent impl blocks](../specs/2026-08-08-generics-traits-gap-assessment-design.md#potential-future-work)
future-work item, promoted to a prerequisite. Its sibling item, *One producer
for an impl method's symbol name*, stays deferred: `Fn.impl` makes it
possible, but unifying `ir_traits.Impl.name` with
`ir_module.FnInstance.qualified_name` changes emitted symbol names, and both
producers are already collision-free. It is duplication, not a defect.

Gap 4 (generic associated functions and generic methods) is parked behind
Stages 1–3, which remove the three things its design would otherwise work
around:

| Gap 4 problem | Removed by |
| --- | --- |
| No slot for a method's own type arguments alongside an impl's | Stage 2 (flat args) |
| A generic associated function would be emitted abstractly | Stage 3 (only instances are values) |
| Lookup instantiates eagerly with `typ_args=()` | Stages 1–2 |

Gap 4's brainstorm reached two settled decisions before parking, which its
spec should keep: it covers **both** the inherent and trait-declared halves,
and an impl of a trait's generic method follows Rust — names free, arity
must match, and the impl's bounds must be a subset of the trait's
(leech's analogue of `E0276`).

## Risk

The suite is the safety net: 1191 tests in about a minute, covering the language end to end
including generated-program execution. Every stage is a refactor with no intended language
change except Stage 5's removal of the impl-selection depth cap, which deliberately admits
deeper terminating selections. A red suite is the signal.

The riskiest single change is Stage 3's move of `ir_env` from binding values
to binding symbols, which touches name resolution, type checking and
lowering together. If it needs splitting, the natural seam is to introduce
`FnRef` and make instances values first, leaving `ir_env` alone, then change
the binding in a second pass.
