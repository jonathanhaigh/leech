<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Comptime `__size_of` via llvmlite Target Data Implementation Plan

**Goal:** Make `__size_of[T]()` fold at compile time for every type `T` can concretely be
(`bool`, any integer width, pointers, structs, arrays, enums), by asking
`llvmlite.binding.TargetData` for the real ABI size instead of hand-computing it in Python;
emit a real (non-empty) datalayout on every compiled module so the assumption is documented
in the output.

**Architecture:** A new leaf module, `ll_typs.py`, holds the Leech-type → LLVM-type mapping
(imports only `typs` and `llvmlite.ir`, so it can be imported from both `codegen.py` and
`comptime.py` without the cycle a direct `comptime.py` → `codegen.py` import would create).
`codegen.Compiler._ll_typ` delegates every non-`StructTyp` case to it but keeps its own
`StructTyp` handling (and its "already declared" assertion) untouched. `comptime.py`'s
`SizeOfInstr` case builds an LLVM type from scratch per query (its own fresh `ll.Context`),
round-trips it through a scratch module and `llvmlite.binding.parse_assembly` to get a real
type handle, and asks a module-level, lazily-built `TargetData` (`target.py`, built from a
hardcoded x86_64-linux-gnu datalayout string) for its ABI size.

**Tech Stack:** Python 3.14, llvmlite 0.47.0 (`llvmlite.ir` for building, `llvmlite.binding`
for `parse_assembly`/`TargetData` — the latter's first use in this codebase), pytest.

**Design doc:** `docs/superpowers/specs/2026-08-27-comptime-sizeof-design.md` — read it
first; it has the full rationale, including why `llvmlite.ir` types can't be queried
directly and why `StructTyp` is deliberately *not* shared between `codegen.py` and
`ll_typs.py`.

## Global Constraints

- Keep all changes unstaged and uncommitted until the user reviews and explicitly
  authorizes a commit.
- Use module-qualified imports, four-space indentation, Ruff's 100-column limit.
- `ll_typs.py` must not import `leech.ir_module` (directly or transitively) — that's the
  whole point of extracting it.
- `codegen.py`'s existing struct declare/compile behavior (ordering, the
  "struct was never declared" assertion, imported-vs-local structs) must not change.
- Don't add multi-target configurability — `target.py` stays a single hardcoded
  x86_64-linux-gnu triple/datalayout, matching `codegen.py`'s existing hardcoded triple.
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`, and `uv run basedpyright`
  must pass. `uv run reuse lint` must pass (this plan adds one new source file).

---

### Task 1: Target data in `target.py`

**Files:**
- Modify: `src/leech/target.py`

**Interfaces:**
- Produces `target.TRIPLE: str`, `target.DATALAYOUT: str`, `target.target_data() -> llvmlite.binding.TargetData`.

- [ ] Add `import functools` and `from llvmlite import binding as llb`.
- [ ] Add `TRIPLE: Final[str] = "x86_64-linux-gnu"`.
- [ ] Add `DATALAYOUT: Final[str]` set to the literal string
  `"e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"`, with
  a comment recording how it was derived (`llvmlite.binding.Target.from_triple(TRIPLE).create_target_machine().target_data`
  under llvmlite 0.47.0) and that it must be re-verified by hand against
  `test_size_of_runtime_padded_typs` if `TRIPLE` or the installed llvmlite/LLVM version ever
  changes.
- [ ] Add `@functools.cache def target_data() -> llb.TargetData: return llb.create_target_data(DATALAYOUT)`.
- [ ] Confirm by hand (a scratch script, not a committed test) that
  `target_data().get_abi_size(...)` for `i32`/`i24`/`i3`/`{i32,i32}`/`{i8,i64}`/`[4 x i32]`/`ptr`
  reproduces the table in the issue, to catch a transcription error in `DATALAYOUT` before
  it's relied on elsewhere.

---

### Task 2: Extract `ll_typs.py` and wire it into `codegen.py`

**Files:**
- Add: `src/leech/ll_typs.py`
- Modify: `src/leech/codegen.py`

**Interfaces:**
- Produces `ll_typs.ll_typ(typ: typs.Typ, recurse: Callable[[typs.Typ], ll.Type]) -> ll.Type`
  (every `typs.Typ` case except `StructTyp`).
- Produces `ll_typs.declare_struct(ctx: ll.Context, cache: MutableMapping[typs.Typ, ll.Type], typ: typs.StructTyp) -> ll.Type`.
- `codegen.Compiler._ll_typ`'s observable behavior (including its struct-declared
  assertion) is unchanged.

- [ ] Add SPDX header (matching `target.py`'s) to the new file.
- [ ] Write `ll_typ`, moving the body of `codegen.Compiler._ll_typ`'s `IntTyp`/`BoolTyp`/
  `FnTyp`/`PtrTyp`/`ArrayTyp`/`VoidTyp`/`EnumTyp`/`NeverTyp` arms verbatim, replacing every
  `self._ll_mod_items.get(...)` call with `recurse(...)`. Drop the `StructTyp` arm entirely
  (not handled here).
- [ ] Write `declare_struct`: return `cache[typ]` if present; otherwise
  `ll_struct = ctx.get_identified_type(typ.qualified_name)`, store it in `cache` *before*
  building field types, then `ll_struct.set_body(*(...))` over `typ.fields.values()`,
  recursing into each field's `.typ` through the same struct-aware dispatch (a field that's
  itself a `StructTyp` calls `declare_struct` again; anything else calls `ll_typ`).
- [ ] In `codegen.py`, change `_ll_typ`'s `case typs.StructTyp():` arm to keep exactly its
  current body; change every other arm to a single `case _: return ll_typs.ll_typ(typ, self._ll_mod_items.get)`.
- [ ] Add `ll_typs` to `codegen.py`'s `from leech import ...` line.
- [ ] Run the full suite to confirm codegen output is byte-for-byte unchanged for every
  existing fixture (no test should need updating from this task alone).

---

### Task 3: Comptime `SizeOfInstr` via `TargetData`

**Files:**
- Modify: `src/leech/comptime.py`
- Modify: `src/leech/errors.py`

**Interfaces:**
- `__size_of[T]()` folds at comptime for every `T` the grammar can produce as a type
  argument (`bool`, any int width, pointers, structs, arrays, enums).
- Removes `errors.SizeOfNotComptimeEvaluableError`.

- [ ] In `comptime.py`, add `from llvmlite import binding as llb` and `from llvmlite import ir as ll`, plus `ll_typs`.
- [ ] Replace the `SizeOfInstr` match arm's body: build a fresh `ctx = ll.Context()` and
  `cache: dict[typs.Typ, ll.Type] = {}`; define a local `recurse(t)` that dispatches to
  `ll_typs.declare_struct(ctx, cache, t)` for a `StructTyp` and `ll_typs.ll_typ(t, recurse)`
  otherwise; get `ll_ty = recurse(instr.sized_typ)`.
- [ ] Build a scratch `ll.Module()` (triple `target.TRIPLE`), declare a throwaway
  `ll.Function` with `ll.FunctionType(ll.VoidType(), [ll_ty])`, render it with `str(...)`,
  and `parsed = llb.parse_assembly(...)`; pull the real `TypeRef` off
  `next(iter(parsed.get_function(<name>).arguments)).type`.
- [ ] Compute `size = target.target_data().get_abi_size(<that TypeRef>)`; keep the existing
  `self._registers[instr] = ir_values.ComptimeInt(typs.USIZE, size, instr.ast)` tail.
- [ ] Delete the `case typs.BoolTyp():`/`IntTyp()`/`PtrTyp()`/`case _:` structure entirely —
  replace the whole match with the single computation above (bool/pointer/int are no longer
  special-cased; they go through the same `ll_typs`/`TargetData` path as everything else).
- [ ] Change the fallback (now genuinely unreachable, per the grammar argument in the design
  doc) to `raise AssertionError(f"unexpected type for __size_of: {instr.sized_typ}")` — no
  `errors.SizeOfNotComptimeEvaluableError` reference remains in `comptime.py`.
- [ ] Delete `SizeOfNotComptimeEvaluableError` from `errors.py`.
- [ ] Search the tree for any remaining reference to `SizeOfNotComptimeEvaluableError` and
  confirm there are none outside the tests updated in Task 4.

---

### Task 4: Update and extend `tests/test_builtins.py`

**Files:**
- Modify: `tests/test_builtins.py`

**Interfaces:**
- No test still expects `SizeOfNotComptimeEvaluableError` for a concrete type.
- Comptime and runtime `__size_of` are checked to agree for structs, arrays, and enums, not
  just scalar types.

- [ ] Replace `test_size_of_padded_typ_at_comptime_raises` with a comptime-matches-runtime
  test over the same `["i3", "i17", "i24"]` parametrization, in the shape of the existing
  `test_size_of_at_comptime_matches_runtime` (bind `let comptime_size = __size_of[{typ}]();`
  at module scope, compare it against both the literal expected size and a fresh runtime
  `__size_of[{typ}]()` call inside `main`).
- [ ] Replace `test_size_of_struct_at_comptime_raises` the same way, using a `struct Point { x: i32, y: i32 }`
  fixture: bind `let comptime_size = __size_of[Point]();` at module scope, and assert it
  equals a fresh runtime `__size_of[Point]()` call (equality between the two calls, not a
  literal expected size — matching `test_size_of_struct_is_at_least_sum_of_field_sizes`'s
  existing reasoning that exact struct padding is legitimately platform-dependent and
  shouldn't be hand-encoded in the test).
- [ ] Add an equivalent comptime-matches-runtime test for an array type (e.g.
  `array[i32, 4]`) and for an enum type (e.g. `enum Color(u8) { Red, Green, Blue }`),
  following the same shape.
- [ ] Add a small test that compiles any fixture (e.g. reuse `test_size_of_ptr_typ_at_comptime_matches_runtime`'s
  source) and asserts the emitted `.ll` text contains a non-empty
  `target datalayout = "..."` line (not `target datalayout = ""`), next to
  `test_size_of_intrinsic_compiled_once_across_multiple_calls`, which already reads
  `ll_path.read_text()`.
- [ ] Remove the now-unused `errors` import from `test_builtins.py` only if nothing else in
  the file still references `errors.*` (it does — `WrongNumberOfComptimeArgsError`,
  `PtrCastNotComptimeEvaluableError` — so the import stays; just confirm
  `SizeOfNotComptimeEvaluableError` no longer appears anywhere in the file).
- [ ] Run `uv run pytest tests/test_builtins.py -q`, then the full suite, `ruff check .`,
  `ruff format .`, `basedpyright`, `reuse lint`.
- [ ] Request independent review and leave all changes unstaged for user review.
