<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Extract a `mono.py` module for generic-instance discovery

## Problem

`codegen.py` currently mixes two distinct jobs inside `Compiler`:

1. **Discovering which generic function/method and struct instantiations a
   program actually reaches.** This is a fixpoint search over the on-demand
   instance-creation machinery already owned by `ir_module.Fn`/`FnInstance`
   and `typs.StructTyp` (forcing `.cfg` / `.fields` triggers instantiation;
   discovery just keeps forcing newly-created instances until nothing new
   appears).
2. **Lowering each declared item and each discovered instance to LLVM IR**
   via `llvmlite`.

The discovery logic (`_fixpoint`, `_fn_templates`, `_generic_impl_method_fns`,
`_discover_fn_instances`, `_discover_struct_instances`, ~90 lines) has no
`llvmlite` dependency at all — it only touches `ir_module` and `typs` — but
today it's private methods on `codegen.Compiler`, entangled with the LLVM
lowering methods around it.

## Goal

Extract the discovery/fixpoint logic into a new module, `src/leech/mono.py`,
so that:

- Codegen's own responsibility narrows to "lower already-known declarations
  and instances to LLVM," matching what its module docstring already claims.
- The reachability algorithm becomes a standalone, LLVM-free unit that can be
  read, reasoned about, and (later, if ever wanted) tested in isolation from
  `llvmlite`.

Everything else that's part of "monomorphization" in the broader sense —
`Typ.substitute_typ_params` across the `typs.py` type hierarchy, `StructTyp`'s
and `Fn`'s instance caches, the `FnInstance` class itself, and
`ir_builder.py`'s `_resolve_fn_instance` — stays exactly where it is. Those
are core data-model behavior on their owning classes, used pervasively by
type checking and IR building, not just by codegen's reachability pass;
relocating them would mean breaking encapsulation (exposing private caches)
for no isolation benefit.

## Design

### New module: `src/leech/mono.py`

```python
"""Monomorphization: discovering every concrete generic instance a program reaches.

Generic function/method and struct instances are created on demand — see
ir_module.Fn.instance/impl_instance and typs.StructTyp.instance — whenever
type checking, or a previously discovered instance's own body, references
them. This module forces that on-demand machinery to a fixpoint and
collects whatever it produces.

The result over-approximates reachability: it includes any instance any
template's cache holds, however it got there — not only ones a call or
field use within a program's own code actually reaches. It never omits an
instance the program needs, but can include one requested only as a side
effect of validating an unrelated, never-instantiated generic template.
"""


@dataclasses.dataclass(frozen=True)
class MonoResult:
    """Every generic instance discovered reachable from a program, in discovery order."""

    fn_instances: tuple[ir_module.FnInstance, ...]
    struct_instances: tuple[typs.StructTyp, ...]


def discover(mod: ir_module.Mod) -> MonoResult:
    """Discover every concrete fn/method and struct instantiation reachable
    from ``mod``'s own items and its imports' public items.
    """
    # Fn instances first: forcing their bodies can request struct
    # instantiations that only a subsequent struct-discovery pass would catch.
    fn_instances = _discover_fn_instances(mod)
    struct_instances = _discover_struct_instances(mod)
    return MonoResult(tuple(fn_instances), tuple(struct_instances))
```

Followed by `_fixpoint`, `_fn_templates`, `_generic_impl_method_fns`,
`_discover_fn_instances`, and `_discover_struct_instances`, moved verbatim
from `codegen.Compiler` and turned into module-private free functions
(dropping `self`, taking `mod: ir_module.Mod` where the methods used
`self._mod`). Imports: `dataclasses`, `collections.abc.Callable`,
`ir_module`, `typs`.

### `codegen.py` changes

`Compiler.compile()` replaces its two discovery calls with one:

```python
result = mono.discover(self._mod)
for struct_inst in result.struct_instances:
    self._declare_struct_instance(struct_inst)
for struct_inst in result.struct_instances:
    self._compile_struct_instance(struct_inst)
for inst in result.fn_instances:
    self._declare_fn_instance(inst)
for item in self._mod.items:
    self._compile_mod_item(item)
for inst in result.fn_instances:
    self._compile_fn_instance(inst)
```

`MonoResult`'s field order (fn instances first, struct instances second)
reflects the *discovery*-time dependency (forcing fn bodies can surface new
struct instantiations). LLVM *emission* order — struct types declared and
compiled before fn instances that reference them — is unchanged and is now
expressed independently in `compile()`, rather than being implicit in one
shared method-call sequence.

`codegen.py` deletes `_fixpoint`, `_fn_templates`,
`_generic_impl_method_fns`, `_discover_fn_instances`, and
`_discover_struct_instances` from `Compiler`, adds `from leech import mono`,
and drops the now-unused `Callable` import (`Iterator` stays — it's still
used by `_program_items`, which is unaffected: it's used directly by
`compile()` for the declare/validate passes, not by the discovery methods,
so it doesn't move).

## Known imprecision

This existed in `codegen.py` before this extraction and is unchanged by it —
it's called out here because pulling the logic into a named module with a
`discover()` entry point makes it a more prominent, documented contract, so
the docstring above needs to be honest about it rather than implicitly
promising exact reachability.

`compile()` validates every generic struct *template* (never instantiated
anywhere) by forcing its own `.fields`, purely to catch infinite-size or
runaway-recursive field cycles regardless of whether the program ever
instantiates the struct:

```python
for item in self._program_items():
    if isinstance(item.value, typs.StructTyp) and item.value.is_generic_template:
        _ = item.value.fields
```

If that template declares a field whose type is a *different*, already-concrete
generic instantiation — e.g. `struct Foo[T] { x: Pair[i32, i32], y: T }` —
resolving `Foo`'s field types resolves `Pair[i32, i32]` too, through the same
`resolve_named_type` → `StructTyp.instance()` path every other type annotation
uses (`typs.py`, `resolve_named_type`/`StructTyp.instance`). That call
populates `Pair`'s instance cache with a concrete `Pair[i32, i32]`, even
though `Foo` — the only thing that mentions it — is never instantiated by any
real code. `mono.discover()` (like today's `_discover_struct_instances`) scans
every template's instance cache, so it reports `Pair[i32, i32]` as
"discovered," and codegen emits an unused LLVM struct definition for it.

Consequence: `mono.discover()`'s output is a safe over-approximation, not a
minimal reachable set. Nothing is ever under-discovered (no correctness
risk — an actually-used instance can never be missing), but a small amount
of dead LLVM struct-type bloat is possible in narrow cases like the one
above. This is called out explicitly rather than silently fixed as part of
this refactor — see the rejected alternative below for why.

## Non-goals

- No change to `typs.py`, `ir_module.py`, or `ir_builder.py`.
- No change to emitted LLVM IR, symbol names, or linkage — this is a pure
  code-motion refactor.
- No new public API beyond `mono.discover()` / `mono.MonoResult`.

## Testing

This is a behavior-preserving refactor (pure code motion, no logic changes).
Verification is running the full existing test suite before and after,
confirming no change in outcomes — coverage comes transitively through
`tests/test_generic_fns.py`, `tests/test_generic_structs.py`, and the rest of
the end-to-end suite, which already exercises the discovery fixpoint via
real compiled-and-run programs. No new test file is added as part of this
change.

## Rejected alternative: explicit worklist over the CFG/field graph

An adversarial review of this design raised the imprecision documented above
and suggested redesigning discovery around an explicit worklist: seed it
from the declared roots (local items, public imports), then walk each
instance's `.cfg` (for fn instances) or `.fields` (for struct instances) by
hand, following `CallInstr` callees and every `Typ` an instruction or
comptime value touches, adding newly-referenced instances to the worklist as
they're found — rather than re-scanning each template's instance cache for
whatever it happens to already contain. This is roughly how e.g. rustc's
`mono_item` collector works, and would eliminate the over-approximation
above: an instance would only ever be discovered because something reachable
genuinely calls or uses it.

This is feasible, but was rejected for this change:

- **It duplicates an already-exhaustive surface, less safely.**
  `codegen.py`'s `_compile_instr`/`_ll_typ`/`_compile_comptime_value` already
  pattern-match every `ir_values.Instr`/`ComptimeValue`/`typs.Typ` variant
  exhaustively, each with an `AssertionError` fallback for anything
  unhandled. An explicit worklist walker would need a second, independent
  traversal covering that same surface, kept in sync by hand as IR node
  kinds are added over time. Falling out of sync doesn't fail loudly the way
  the existing lowering code does — it silently *under*-discovers, which
  means an instance that's genuinely used gets skipped, producing a hard
  crash or a miscompile downstream. That's a strictly worse failure mode
  than today's harmless over-inclusion.
- **The imprecision it would fix is cosmetic, not a correctness bug.** The
  worst outcome demonstrated above is an unused LLVM struct declaration in
  the output — dead weight that any downstream LLVM tooling would ignore or
  strip, not something that changes program behavior.
- **It's out of scope for this change.** This refactor is pure code motion
  (see Non-goals); redesigning the discovery algorithm is a separate,
  larger, and riskier change that shouldn't ride along with it.

If the imprecision above is ever observed to matter in practice (e.g. someone
notices meaningful `.ll` bloat from it), the recommended fix at that point is
narrower than a full worklist rewrite: stop the template-validation
`.fields` forcing loop in `compile()` from feeding the *same* instance cache
`mono.discover()` scans — e.g. by validating a generic template's own field
cycles/sizes without going through `StructTyp.instance()` for concrete
nested references — rather than replacing the whole discovery mechanism.
