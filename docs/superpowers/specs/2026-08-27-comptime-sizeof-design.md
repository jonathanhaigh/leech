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

`TargetData.get_abi_size` needs a real LLVM type handle
(`llvmlite.binding.TypeRef`), not one of the Python-side builder objects `codegen.py`
constructs via `llvmlite.ir` (`ll.IntType`, `ll.LiteralStructType`, etc. — confirmed by
testing directly, `ctypes.ArgumentError: expected LP_LLVMType instance instead of IntType`).
The only bridge is a text round-trip: build a scratch `llvmlite.ir.Module` containing a
throwaway function declared to take the type in question as its one parameter, render it to
text, and reparse it with `llvmlite.binding.parse_assembly` to get a real `TypeRef` off the
parsed function's argument. This was verified end to end against the exact table in the
issue (`i32`→4, `i24`→4, `i3`→1, `{i32,i32}`→8, `{i8,i64}`→16, `[4 x i32]`→16, `ptr`→8).

## Decisions

### 1. Sharing the Leech-type → LLVM-type mapping

New leaf module `src/leech/ll_typs.py` (imports only `typs` and `llvmlite.ir` — no
`leech.ir_module`, so nothing here can cycle back into `comptime.py`). It exposes:

- `ll_typ(typ: typs.Typ, recurse: Callable[[typs.Typ], ll.Type]) -> ll.Type` — the
  leaf-type switch (`IntTyp`, `BoolTyp`, `FnTyp`, `PtrTyp`, `ArrayTyp`, `VoidTyp`,
  `EnumTyp`, `NeverTyp`), calling `recurse` for any nested type instead of recursing on
  itself. **Does not handle `StructTyp`** — see below.
- `declare_struct(ctx: ll.Context, cache: MutableMapping[typs.Typ, ll.Type], typ: typs.StructTyp) -> ll.Type`
  — builds an identified struct type from scratch: `ctx.get_identified_type(typ.qualified_name)`,
  registers it in `cache` *before* recursing into field types (so a field that points back
  at this same struct sees an already-declared, still-opaque pointee instead of recursing
  forever), then `set_body(...)`.

`codegen.Compiler._ll_typ` keeps its own `StructTyp` arm exactly as today — including its
`assert typ in self._ll_mod_items` guard — and delegates every other arm to
`ll_typs.ll_typ(typ, recurse=self._ll_mod_items.get)`. This preserves 100% of codegen's
existing struct declare/compile behavior (multi-module ordering, monomorphization,
imported-vs-local structs) untouched; `ll_typs.declare_struct` is exercised only by
`comptime.py`, which has no such phased pipeline to reuse and needs to build a type
from nothing, on demand, per query.

`comptime.py`'s `SizeOfInstr` case builds a fresh `ll.Context()` and an empty
`cache: dict[typs.Typ, ll.Type]` per query (no cross-query memoization for now — see
Non-goals), and a `recurse` closure that dispatches `StructTyp` to `declare_struct` and
everything else to `ll_typ`.

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

## Changes by file

- `target.py`: add `TRIPLE`, `DATALAYOUT` constants and `target_data()`.
- `ll_typs.py` (new): `ll_typ`, `declare_struct`, as above.
- `codegen.py`: set `self.ll_mod.data_layout = target.DATALAYOUT` alongside the existing
  `self.ll_mod.triple = target.TRIPLE` (replacing today's inline literal) in `compile()`;
  `_ll_typ` delegates its non-`StructTyp` arms to `ll_typs.ll_typ`.
- `comptime.py`: `SizeOfInstr` case builds the LLVM type via `ll_typs`/a fresh context, then
  round-trips it through a scratch module + `llvmlite.binding.parse_assembly` to get a real
  `TypeRef`, then asks `target.target_data().get_abi_size(...)`. Falls back to
  `raise AssertionError(...)` for the (now unreachable for any concrete type) `case _`.
- `errors.py`: delete `SizeOfNotComptimeEvaluableError`.
- `tests/test_builtins.py`: `test_size_of_padded_typ_at_comptime_raises` and
  `test_size_of_struct_at_comptime_raises` become positive comptime-matches-runtime tests
  (mirroring `test_size_of_at_comptime_matches_runtime`'s shape), extended to cover arrays
  and enums too. Add a check that emitted IR carries a non-empty `target datalayout`.

## Non-goals / out of scope

- No cross-query caching of comptime `__size_of` results or of built LLVM types across
  separate `SizeOfInstr` evaluations — each evaluation pays its own scratch-module/parse
  round trip. Comptime evaluation already isn't optimized for repeated work elsewhere in
  this file (e.g. every nested function call gets a fresh `Interpreter`), and `__size_of`
  calls are expected to be few and small. Worth revisiting only if it shows up as a real
  compile-time cost.
- No multi-target support. `target.py` stays a single hardcoded x86_64-linux-gnu triple and
  datalayout, matching `codegen.py`'s existing hardcoded triple.
- Doesn't touch `codegen.py`'s runtime `SizeOfInstr` case (the GEP idiom) — that stays the
  ground truth both new tests check the comptime path against.
