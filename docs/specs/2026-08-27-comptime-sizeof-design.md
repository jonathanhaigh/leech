<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Comptime `__size_of` via llvmlite Target Data — Design

For: #4: Use llvmlite target data for compile-time type sizes

## Problem

`comptime.Interpreter`'s `SizeOfInstr` case hand-computes sizes for `bool`, pointers, and
integer widths that are both a power of two and at least 8 bits. Everything else (`i3`,
`i24`, structs, arrays, enums) raises `SizeOfNotComptimeEvaluableError`, because the
compiler doesn't replicate LLVM's target-dependent ABI padding/alignment rules in Python.
llvmlite already has those rules, via `llvmlite.binding.TargetData.get_abi_size`.

Separately, `codegen.py` never sets `ll_mod.data_layout`, so emitted modules carry
`target datalayout = ""`.

## What can appear as `T` in `__size_of[T]()`

`leech.lark`'s `typ` rule only ever produces `basic_typ` (a path plus optional comptime
args — resolving to `bool`/an integer type/a struct/an enum/the builtin `array[T, N]`
generic) or `ptr_typ`. There is no function-type or `void`/`never` literal syntax, so `T`
can only ever concretely resolve to `BoolTyp`, `IntTyp`, `PtrTyp`, `ArrayTyp`, `StructTyp`,
or `EnumTyp` — exactly the types this change makes comptime-sizeable. `errors.SizeOfNotComptimeEvaluableError`
therefore becomes unreachable and is deleted; its match arm in `comptime.py` becomes an
`AssertionError`, matching the "should never get here" idiom already used elsewhere in that
file (e.g. `UnreachableInstr`).

## Key technical fact: `llvmlite.ir` types aren't queryable directly

`llvmlite.binding.TargetData.get_abi_size` needs a real LLVM type handle
(`llvmlite.binding.TypeRef`), not one of the Python-side builder objects `codegen.py`
constructs via `llvmlite.ir` (`ll.IntType`, `ll.LiteralStructType`, etc. — confirmed by
testing directly, `ctypes.ArgumentError: expected LP_LLVMType instance instead of IntType`).
The only bridge is a text round-trip: build a scratch module containing the type in
question, render it to text, and reparse it with `llvmlite.binding.parse_assembly` to get a
real `TypeRef`.

**Use `llvmlite.ir.Type.get_abi_size(target_data, context=...)` directly - don't hand-roll
this round trip.** llvmlite already ships this exact bridge as a method on every
`llvmlite.ir.Type`: internally it builds a scratch `Module` (in `context` if given, a fresh
one otherwise), adds a `GlobalVariable` of the type, parses it back with `parse_assembly`,
and reads the parsed global's `global_value_type` - the identical workaround this design
originally hand-rolled with a throwaway function parameter instead of a global variable
(needed because a plain global's own `TypeRef.type` comes back as a bare, uninformative
`ptr` under LLVM's opaque-pointer rules; `global_value_type` is what avoids that). Found by
searching the web for this exact complaint after the repository owner asked whether the
hand-rolled version was really necessary - it wasn't. Verified directly (not just from
llvmlite's docs) against the same table as above, including a self-referential
`ctx.get_identified_type("Node")` with a `*Node` field, which requires passing
`context=ctx` (the *same* context the type was built in - `get_abi_size`'s `context`
parameter defaults to `None`, which builds an unrelated fresh module/context and fails to
parse for any named/identified type: `LLVM IR parsing error: ... use of undefined type
named 'Node'`).

## Decisions

### 1. Sharing the Leech-type → LLVM-type mapping

**Superseded during implementation.** The plan below originally called for `ll_typs.py` to
expose two functions - a leaf-type `ll_typ(typ, recurse)` taking a caller-supplied recursion
callback, plus a separate `declare_struct(ctx, cache, typ)` for structs, with `cache` an
explicit parameter every caller had to construct - and for `codegen.Compiler._ll_typ` to keep
its own `StructTyp` arm and "already declared" assertion untouched, calling `ll_typs.ll_typ`
only for every other arm. That shipped, passed the full suite, and was then reworked at the
repository owner's request: threading an explicit `recurse` callback and cache parameter
through every call site was judged unnecessarily awkward.

The shipped design instead has `ll_typs.py` expose one function,
`ll_typ(ctx: ll.Context, typ: typs.Typ) -> ll.Type`, handling every `typs.Typ` case
including `StructTyp`, purely self-recursive (no caller-supplied callback). A struct's
identified type is deduplicated by `ll.Context.get_identified_type` itself (confirmed
idempotent by name), and `IdentifiedStructType.is_opaque` tells the function whether a
struct's body still needs building.

That alone is **not** sufficient for a self-referential struct (e.g. `Node { next: *Node }`)
or two mutually-recursive structs: `is_opaque` stays `True` for the *entire* duration of
building a struct's body (built by recursing into field types, one argument at a time,
before the single `set_body` call that flips it to non-opaque), so a field that refers back
to the same struct mid-build sees `is_opaque == True` again and tries to rebuild it -
confirmed empirically to stack-overflow with a minimal repro before this was caught. The fix
is a small amount of state tracking which struct qualified names are currently mid-build,
scoped per `ll.Context` via a module-level `weakref.WeakKeyDictionary` internal to
`ll_typs.py` - not exposed to callers, not something they construct or pass in. A reentrant
call for a name already mid-build returns the already-registered (still-opaque, which is all
a pointer or array-element reference ever needs) identified type instead of recursing again.

`codegen.Compiler._ll_typ` becomes a one-line delegation to `ll_typs.ll_typ` for every case,
including `StructTyp` - its old "was never declared" assertion is gone, but its failure mode
is now impossible by construction rather than merely unchecked: `ll_typs.ll_typ` is safe to
call for any struct, at any point, in any order, any number of times. This uncovered a real
consequence for codegen.py's four struct-declare/compile helper methods
(`_declare_struct_instance`, `_compile_struct_instance`, `_declare_mod_item`'s `StructTyp`
arm, `_compile_mod_struct`): they used to hand-roll `get_identified_type`+`set_body`
directly, unaware of `ll_typs.py`'s new internal "mid-build" bookkeeping - once
`ll_typs.ll_typ`'s own recursion could independently reach and *fully* build the same struct
(e.g. while resolving another struct's pointer field), those methods' own later
`set_body` call crashed with `RuntimeError: ... is already defined` (confirmed by the full
test suite: `test_struct_with_ptr_to_same_struct` and friends). All four now delegate to
`self._ll_mod_items.get(...)` (which reaches `ll_typs.ll_typ` through the same idempotent
path) instead of touching `get_identified_type`/`set_body` themselves. `compile()`'s pass
*order* (declare structs, then everything else, then compile struct bodies, then
monomorphization discovery) is unchanged - only made safe to call redundantly, since each
struct's type is now fully built by the first of the two calls that reaches it and the
second is a deliberate no-op.

`comptime.py`'s `SizeOfInstr` case now just builds a fresh `ll.Context()` per query and calls
`ll_typs.ll_typ(ctx, sized_typ)` directly - no local cache or recursion closure of its own.

### 2. Datalayout source

Hardcoded as a literal string constant in `target.py`, derived once by hand:

```
e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128
```

(from `llvmlite.binding.Target.from_triple("x86_64-linux-gnu").create_target_machine().target_data`
under llvmlite 0.47.0 — the version pinned in `pyproject.toml`/`uv.lock`. This matches the
issue's own confirmed sizes.) `llvmlite.binding.create_target_data(layout_string)` needs no
target-registry initialization at all (verified: no `initialize_all_targets()`/`Target.from_triple`/
`create_target_machine()` call needed just to construct a `TargetData` from a literal layout
string and query it) — so this keeps `llvmlite.binding` usage in this codebase to exactly
`create_target_data` and `parse_assembly`, both stateless.

Trade-off accepted: if `target.py`'s triple or the installed LLVM's default layout for it
ever changes, this string must be re-derived by hand and re-verified against
`test_size_of_runtime_padded_typs`. That's judged acceptable because the triple is already
a hardcoded literal in `codegen.py` today (single-target compiler, no configurability).

### 3. Where `TargetData` lives

A lazily-constructed, `functools.cache`d module-level accessor in `target.py`,
`target_data() -> llb.TargetData`, next to the existing bare `ADDR_SIZE` constant. No
constructor anywhere changes shape. This matches how the test suite already treats
`target.ADDR_SIZE` as one fixed global rather than something threaded per-compilation.

### 4. `codegen.py`'s runtime `SizeOfInstr` case, revisited

**Added during review, beyond the original plan.** `instr.sized_typ` is just as concrete at
codegen time as it is at comptime-evaluation time (codegen only ever compiles fully
monomorphized instances), so there was no longer a reason for the *runtime* `SizeOfInstr`
case to still emit the GEP-idiom instruction sequence (index one `T` past a null `T*`, read
the resulting address) instead of a plain compile-time constant, once both paths could ask
the same target data for the same answer.

Both call sites use `llvmlite.ir.Type.get_abi_size(target.target_data(), context=ctx)`
directly (see the "Key technical fact" section above - no hand-rolled round trip anywhere in
this codebase). `comptime.py`'s `_size_of` uses its own fresh `ll.Context` per query;
`codegen.py`'s `SizeOfInstr` case uses the compiler's own long-lived `self.ll_mod.context`,
so a struct type built for a size query is the same object already used elsewhere in the
real output module. `codegen.py`'s case is now just
`ll.Constant(usize_ll_typ, ll_typ.get_abi_size(target.target_data(), context=self.ll_mod.context))`
- confirmed by inspecting emitted IR that `__size_of[Padded]()`'s body folds to a bare
`ret i64 16`, no `getelementptr`/`ptrtoint` left anywhere.

## Changes by file

- `target.py`: add `TRIPLE`, `DATALAYOUT` constants and `target_data()`. No round-trip helper
  of its own - every caller uses `llvmlite.ir.Type.get_abi_size(target.target_data(),
  context=...)` directly (see the "Key technical fact" section above).
- `ll_typs.py` (new): single `ll_typ(ctx, typ)`, self-recursive, including `StructTyp`, plus
  a private per-context "mid-build" set - see Decision 1's "Superseded during implementation"
  note. Stays free of `llvmlite.binding` - only `target.py` touches that.
- `codegen.py`: set `self.ll_mod.data_layout = target.DATALAYOUT` alongside the existing
  `self.ll_mod.triple = target.TRIPLE` (replacing today's inline literal) in `compile()`;
  `_ll_typ` becomes `return ll_typs.ll_typ(self.ll_mod.context, typ)`, with no case of its
  own. Its struct-declare/compile helper methods (which are now unconditionally no-ops after
  the first of the two calls per struct) delegate to `self._ll_mod_items.get(...)` instead of
  hand-rolling `get_identified_type`/`set_body`; the two literal no-op methods
  (`_compile_struct_instance`, `_compile_mod_struct`) and their call sites were removed
  entirely rather than kept as dead pass-throughs. The runtime `SizeOfInstr` case (see
  Decision 4) is now a plain constant instead of the GEP idiom.
- `comptime.py`: `SizeOfInstr` case builds `ll_typ = ll_typs.ll_typ(ctx, sized_typ)` on a
  fresh `ctx`, then `ll_typ.get_abi_size(target.target_data(), context=ctx)`. The `case _`
  fallback for a type `ll_typs.ll_typ` can't handle lives inside `ll_typs.ll_typ` itself
  (`raise AssertionError(...)`) rather than duplicated in `comptime.py`.
- `errors.py`: delete `SizeOfNotComptimeEvaluableError`.
- `tests/test_builtins.py`: `test_size_of_padded_typ_at_comptime_raises` and
  `test_size_of_struct_at_comptime_raises` become positive comptime-matches-runtime tests
  (mirroring `test_size_of_at_comptime_matches_runtime`'s shape), extended to cover arrays,
  enums, and a struct with a real padding gap (`{i8, i64}`, not just a same-alignment
  `{i32, i32}` a naive sum-of-fields would also get right). Add a check that emitted IR
  carries a non-empty `target datalayout`.

## Non-goals / out of scope

- No cross-query caching of comptime `__size_of` results or of built LLVM types across
  separate `SizeOfInstr` evaluations — each evaluation pays its own scratch-module/parse
  round trip. Comptime evaluation already isn't optimized for repeated work elsewhere in
  this file (e.g. every nested function call gets a fresh `Interpreter`), and `__size_of`
  calls are expected to be few and small. Worth revisiting only if it shows up as a real
  compile-time cost.
- No multi-target support. `target.py` stays a single hardcoded x86_64-linux-gnu triple and
  datalayout, matching `codegen.py`'s existing hardcoded triple.
