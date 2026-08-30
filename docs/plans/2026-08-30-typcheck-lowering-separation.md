<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# TypCheckResults as the Single Source of Truth for Lowering — Implementation Plan

**Goal:** Make `CfgBuilder` read type checking's recorded decisions and never re-run its
algorithms. Delete the `expected_typ` hints threaded through `ir_builder`; record expression,
place and local-binding types, a match's whole pattern analysis, and folded integer-literal
constants; move the fact vocabulary out of `typcheck.py` so `ir_builder` no longer imports
it; and fix #69, which stops being a special case once the `if` lowering reads its recorded
result type.

**Architecture:** Leech keeps its side-table design — facts keyed by AST-node identity,
written by `TypCheck`, read by `CfgBuilder` — rather than moving to a typed IR. The table
becomes complete and self-checking: `_build_expr` asserts every value it builds against the
type the checker recorded for that node, so a cross-phase disagreement fails loudly instead
of miscompiling. Match lowering stops deriving anything: `patterns.build_match_plan` runs the
usefulness and coverage analysis once, and both the checker's diagnostics and the lowerer's
comparison chain come from that one `MatchPlan`. The boundary becomes structural — after
Task 7, `ir_builder` imports `check_results` and not `typcheck` at all, and reaches into
`patterns` only for the constructor dataclasses it has to dispatch on, pinned by a test.

**Tech Stack:** Python 3.14, pytest, ruff, basedpyright, reuse. No new dependencies.

**Design doc:** `docs/specs/2026-08-30-typcheck-lowering-separation-design.md` — read it
first. It carries the measurement showing the hints are dead, the soundness argument for
dropping the ordering coupling, the compiler comparison (rustc, Swift, Clang, Zig), and the
reason a typed IR is deferred rather than rejected.

## Global Constraints

- Keep all changes unstaged and uncommitted until the user reviews and explicitly authorizes
  each commit.
- Every commit carries `For: #70: Make TypCheckResults the single source of truth for
  lowering` (Task 0 retitles the issue first). Task 5 additionally carries
  `For: #69: If/else with two void branches crashes codegen`.
- One coherent change per commit; the task boundaries below are the commit boundaries.
- Module-qualified imports, four-space indentation, Ruff's 100-column limit.
- `patterns.py` must not import `leech.ast` or `leech.typs`. `MatchPlan` names arm indices and
  `ConstructorKind`s only.
- No new IR instruction. A `SwitchInstr` is #68's job and needs `comptime.Interpreter` and
  `codegen` arms of its own.
- Docstrings follow AGENTS.md: no Sphinx roles, no caller lists, no restating the signature,
  and **no references to this plan, the design doc, or any issue number**.
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`, `uv run basedpyright` and
  `uv run reuse lint` must pass at every task boundary. New files need SPDX headers.

## Output-Identity Harness

Tasks 1, 2, 3, 4, 6 and 7 must not change generated LLVM IR. Set this up before Task 1 and
re-run it at the end of each of those tasks. Keep it in the scratch directory — it is a
development aid, not a repo artifact.

Write `ir_corpus.py` outside the repo:

```python
import json
import pathlib
import sys

from leech import driver, src

CORPUS = {...}  # name -> Leech source, see below

out = {}
for name, text in CORPUS.items():
    path = pathlib.Path(sys.argv[1]) / f"{name}.leech"
    path.write_text(text, encoding="utf-8")
    out[name] = driver.compile_to_llvm_ir(src.SrcFile(path), name)
print(json.dumps(out, indent=2, sort_keys=True))
```

The corpus must cover, at minimum:

- `if`/`else` with the flexible literal in the `then` arm, and in the `els` arm;
- the same two, with nested `if`/`else` inside the non-literal arm (this is what would move
  block *names* if the ordering change were observable);
- `match` with flexible-literal arm bodies first, last, and interleaved, over `bool`, `enum`
  and integer scrutinees;
- binary operators with the flexible literal on the left and on the right;
- `-128i8` and a bare negated literal in a peer position;
- a `let` with a declared type coercing `*mut T` to `*T`, and one coercing a narrow integer;
- an assignment of a `*mut T` value into a `*T`-declared place (Task 3's `StoreInstr` change);
- **an unreachable match arm whose body is non-trivial** — it must contain control flow that
  calls `_add_bb` (a nested `if`, say) *and* be followed by another arm, so that dropping its
  lowering would shift later block names and show up as a diff;
- **an or-pattern arm with a wildcard alternative** (`A | _ =>`), which is what exercises
  `_match_arm_is_wildcard`'s any-alternative rule;
- **an or-pattern arm with several constructor alternatives**, giving a multi-constructor test
  chain rather than a single comparison;
- **an enum with aliased discriminants** (`enum E { A = 1, B = 1 }`), which exercises
  `ConstructorSpace.from_constructors`' deduplication and first-name-wins witness rule;
- a match whose last arm covers the remainder without being a wildcard;
- a match with no reachable arms (a `never` scrutinee), exercising the `tests == ()` path.
  Do **not** try to add a "falls through" entry here: "no arm covers everything" is a
  non-exhaustive match, which `_check_match_expr` rejects before lowering, so it cannot be
  compiled to IR at all. That case belongs to the `build_match_plan` unit tests;
- a generic function instantiated at two types, with a match and an `if` inside its body;
- struct and array literals, method calls, and a module-level `let x = match (…) {…};`
  (which runs through `comptime.Interpreter`).

Capture the baseline at `2cb0d4b` before Task 1. After each of Tasks 1, 2, 3, 4, 6 and 7,
re-run and `diff` against the baseline; it must be empty, and a non-empty diff is a stop
condition, not something to eyeball and accept.

The corpus cannot contain Task 5's void-`if` program — at baseline it crashes the compiler, so
there is nothing to compare against. Add it to the corpus *after* Task 5. Since the harness
emits one JSON document, compare on the keys the baseline actually has rather than
re-baselining wholesale:

```bash
jq -S --slurpfile b baseline.json 'with_entries(select(.key | in($b[0])))' \
   current.json > current.filtered.json
diff <(jq -S . baseline.json) current.filtered.json
```

(Verified both ways: a key added after Task 5 is ignored, and a changed value on a key the
baseline *does* have still shows up as a diff.)

Equivalently, keep the new entry in a second file. What must not happen is regenerating
`baseline.json` in full after Task 5 — that would silently adopt any regression Tasks 6 and 7
then introduce.

---

### Task 0: Widen issue #70 and update the dependency graph

**Files:** none in the repo (GitHub only).

**Interfaces:** establishes the issue every later commit references.

- [ ] Retitle #70 from "Reconsider passing expected-type hints through `ir_builder`" to
  "Make `TypCheckResults` the single source of truth for lowering".
- [ ] Rewrite #70's body to cover the whole separation: the dead hints, the duplicated match
  analysis, the unrecorded local types, the folded-literal duplication, the facts module, and
  the `#69` fix that falls out. Link the design doc path.
- [ ] Note in #69 that it is fixed as part of #70's Task 5, and leave it open — closing is the
  owner's call.
- [ ] Update the dependency graph in #55. #70 has no hard blockers and blocks nothing, so per
  #55's own rules it needs no node. Add nothing; confirm no existing line mentions #70 or #69
  and state that in the session so the check is on record.
- [ ] **Ask the owner whether to file a new issue** for the two probe defects the design doc
  records: `_infer_comptime_args` double-reporting diagnostics, and its probe raising a false
  `IntLitOverflowError` that rejects a valid program (`f(5i64, 3000000000 + 0)` where the
  parameter resolves to `i64`). These are pre-existing and out of scope here, but they are
  real and currently untracked. Do not file it unprompted; if it is filed, update #55 in the
  same session.
- [ ] Do not close #70 or #69. Ask the owner when the work lands.

---

### Task 1: Delete the `expected_typ` hints

**Files:**
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/typcheck.py`
- Modify: `tests/test_peer_typs.py` (a stale comment only — no assertion changes)

**Interfaces:**
- `CfgBuilder._build_expr`, `_build_block_expr`, `_build_if_expr`, `_build_if_arm`,
  `_build_match_expr`, `_build_bin_op_expr` and `_build_unary_op_expr` lose their
  `expected_typ` parameter. `_build_let_initializer` keeps its signature but stops computing a
  hint from `let_declared_typ` — it still reads that fact to decide whether to coerce.
- `typcheck.is_flexible_int_lit`, `resolve_peer_typ` and `match_arm_check_order` become
  `_`-private. `match_constructor_space` stays public until Task 6.
- No behaviour change; IR byte-identical.

- [ ] Remove the `expected_typ` parameter and every pass-through from the seven methods above,
  and drop the hint argument from all eight call sites that supply one (`build_fn`,
  `_build_ret_stmt`, `_build_call_expr`, `_build_array_access_expr`, `_build_struct_lit_expr`,
  `_build_array_lit_expr`, `_build_assignment_stmt`, `_build_let_initializer`).
- [ ] In `_build_if_expr`, delete the ordering branch entirely: lower `then` then `els`,
  unconditionally, both through `_build_if_arm`.
- [ ] In `_build_match_expr`, delete the `match_arm_check_order` call and lower arms in source
  order (`for i, arm_ast in enumerate(match_ast.arms)`).
- [ ] In `_build_bin_op_expr`, delete the swap: lower `lhs`, `_propagate_never`, lower `rhs`,
  `_propagate_never`. Rewrite the two comments that justified the swap — the new one should
  say the operand types are already settled and lowering is left-to-right, nothing more.
- [ ] In `_build_call_expr`, `param_typs` and `offset` become unused; delete them and iterate
  `for arg_ast in call_ast.args`. Keep the callee validation as unbound assertions so it is
  not silently lost:
  ```python
  fn_ptr_typ = asserts.checked_cast(callee.typ, typs.PtrTyp)
  asserts.checked_cast(fn_ptr_typ.pointee_typ, typs.CallableTyp)
  ```
  `arg_asts` and the `enumerate(args)` coercion loop are unchanged — their indexing never
  depended on `offset`.
- [ ] Drop the now-stale hint sentences from `_build_bin_op_expr`'s, `_build_unary_op_expr`'s
  and `_build_assignment_stmt`'s docstrings. `_build_assignment_stmt`'s place-before-value
  paragraph stays, minus its final `expected_typ` clause — the evaluation-order rule it
  documents is still real.
- [ ] Rename `is_flexible_int_lit` → `_is_flexible_int_lit`, `resolve_peer_typ` →
  `_resolve_peer_typ`, `match_arm_check_order` → `_match_arm_check_order`, updating their
  `typcheck.py` call sites.
- [ ] `tests/test_peer_typs.py` is the suite that pins this task, and every assertion in it
  must pass **unchanged** — `test_if_arm_typ_independent_of_order`,
  `test_binop_operand_typ_independent_of_order` and `test_if_expected_typ_still_wins` are
  precisely the behaviours at risk. One comment does go stale:
  `test_binop_peer_resolution_preserves_effect_order` says "its operand is lowered second",
  which stops being true — after this task the literal is lowered first, and the test holds
  for the simpler reason that lowering is now plain left-to-right. Update the comment; touch
  nothing else in the file.
- [ ] `uv run ruff check .` must report no `F841`; that is the signal every hint-only local is
  gone.
- [ ] Confirm `grep -n expected_typ src/leech/ir_builder.py` returns nothing.
- [ ] Full suite passes and the IR corpus diff is empty.

---

### Task 2: Extract `check_results.py`

**Files:**
- Add: `src/leech/check_results.py`
- Modify: `src/leech/typcheck.py`, `src/leech/ir_builder.py`, `src/leech/ir_module.py`

**Interfaces:**
- `check_results.TypCheckResults`, `check_results.Coercion`, `NeverDiverge`, `PtrMutRelax`,
  `IntExt`, `Invalid`.
- `typcheck.py` keeps only `TypCheck` and its module-private helpers.
- Pure move: no logic changes at all in this task.

- [ ] Create `src/leech/check_results.py` with an SPDX header and a module docstring saying
  what it holds — the decisions type checking records for lowering, keyed by AST-node
  identity — beside `resolve.py`'s name-resolution facts.
- [ ] Move `Coercion`, `NeverDiverge`, `PtrMutRelax`, `IntExt`, `Invalid` and
  `TypCheckResults` (including `_pattern_constructor_candidates`, which only
  `match_arm_constructors` uses) into it, unchanged.
- [ ] Keep the `TYPE_CHECKING`-only `ir_module` import that `TypCheckResults`' generic-call
  annotations need, with the same explanatory comment.
- [ ] Update `typcheck.py` to import and use `check_results.*`; `TypCheck.results` is now
  `check_results.TypCheckResults`, and `check_fn` / `check_var_initializer` return it.
- [ ] Update **every** `typcheck.TypCheckResults` reference — do this as a mechanical global
  rename and then verify with `grep -rn "typcheck\.TypCheckResults" src/`, which must come
  back empty. The sites are more numerous than a spot-check suggests:
  - `ir_module.py`: six return annotations (lines 216, 268, 273, 583, 588, 662) **and a
    runtime constructor call `typcheck.TypCheckResults()` at line 590**. The constructor call
    is the one that fails at run time rather than under basedpyright — it builds an
    intrinsic's empty results — so it must not be missed.
  - `ir_builder.py`: the class attribute annotation at line 53 and the `__init__` parameter
    annotation at line 69, in addition to the `_coerce` match arms
    (`check_results.Invalid()`, `NeverDiverge`, `IntExt`, `PtrMutRelax`).
- [ ] `ir_builder.py` still imports `typcheck` after this task, for `match_constructor_space`,
  and `patterns` for the usefulness calls — Task 6 removes the first and the calls.
- [ ] The SPDX header is all that is needed: `REUSE.toml` annotates only `uv.lock` and
  `.python-version`, so a new source file needs no entry there.
- [ ] Full suite, lint, type check pass; IR corpus diff empty.

---

### Task 3: Record local binding types

**Files:**
- Modify: `src/leech/check_results.py`, `src/leech/typcheck.py`, `src/leech/ir_builder.py`,
  `src/leech/ir_values.py`
- Modify: `tests/test_vars.py` (or `tests/test_coercions.py`, whichever is the closer home)

**Interfaces:**
- `TypCheckResults.local_typ(decl: resolve.LocalDecl) -> typs.PtrTyp` — the bound type and the
  binding's mutability, for a `Param`, `Receiver`, `LetStmt` or `BindingPattern`.
- `CfgBuilder` allocates local storage from it.

- [ ] Add `_local_typs` storage plus `local_typ` / `_set_local_typ` to `TypCheckResults`.
- [ ] Have `TypCheck` record into `results` at the three sites that populate its own
  `_local_typs` today (`check_fn`'s parameter loop, `_check_binding_pattern`,
  `_check_let_stmt`), and have its three readers (`_check_var_expr`, `_check_place`,
  `_place_mut`) read back through the recorded map, so there is one dict rather than two.
- [ ] `_build_let_stmt`: allocate `results.local_typ(let_ast)`'s pointee and mutability,
  substituting `_comptime_arg_mapping`, instead of `expr.typ` and
  `typs.Mutability.from_ast(let_ast.mut)`.
- [ ] `_build_match_binding`: same, from the `BindingPattern`, instead of
  `scrutinee_value.typ` and a re-read `mut`.
- [ ] `build_fn`'s parameter loop: allocate from the recorded parameter type rather than
  `param.typ` with a hardcoded `typs.CONST`, so the two phases agree on parameter mutability
  too.
- [ ] **Relax `StoreInstr` first — otherwise this task crashes on its own regression test.**
  `_coerce`'s `PtrMutRelax` case returns the value untouched, so a coerced `&a` still has type
  `*mut i32` while the recorded local type is `*i32`. Allocating from the recorded type makes
  `StoreInstr.__init__`'s `asserts.assert_eq(value.typ, dest_typ.pointee_typ)`
  (`src/leech/ir_values.py:667`) fail for exactly the case this task exists to fix. Before
  changing any alloca:
  - Widen `StoreInstr`'s check to accept a value whose type differs from the destination
    pointee *only* by a mut-to-const pointer relaxation: both `typs.PtrTyp`, equal
    `pointee_typ`, source `MUT`, destination `CONST`.
  - Do **not** express this as `value.typ.coerces_to(dest_typ.pointee_typ)` — that also admits
    `IntExt`, where the representations genuinely differ and an instruction is required. The
    assertion would then stop catching a real class of bug.
  - Give it a comment saying why it is sound: `PtrMutRelax` exists precisely because the two
    share a representation and nothing is emitted.
  - This affects `_build_assignment_stmt` too, which stores into a place whose pointee now
    comes from the recorded type.
  - `StoreInstr` is the only affected assertion — `CallInstr` does not check arguments against
    parameter types, and the remaining `assert_eq`s in `ir_values.py` (`BinOpInstr` operands,
    `PhiInstr` incoming values, `ComptimeArray` elements) are not on a coercion path that can
    produce this mismatch. Re-confirm that before widening anything else.
- [ ] Where the stored value must be coerced before the store, keep the existing `_coerce`
  call; only the *storage* type changes.
- [ ] Add a regression test for the divergence this fixes:
  ```leech
  pub fn main() i32 {
      let mut a = 5i32;
      let p: *i32 = &a;
      return p.*;
  }
  ```
  asserting it compiles and returns 5. Add a sibling with a widening integer coercion
  (`let x: i64 = 1i32;`) so the narrow-to-wide storage type is pinned too.
- [ ] Full suite, lint, type check pass; IR corpus diff empty. The `typs`-level types change
  here (`*mut i32` → `*i32` for the alloca's pointee) but the *emitted* types do not: LLVM
  erases pointer mutability, and every other coercion — `IntExt`, `NeverDiverge` — has already
  produced a value of the declared type before the store. A non-empty diff means one of those
  assumptions is wrong; stop and investigate rather than accepting it.

---

### Task 4: Record expression and place types, and assert them

**Files:**
- Modify: `src/leech/check_results.py`, `src/leech/typcheck.py`, `src/leech/ir_builder.py`

**Interfaces:**
- `TypCheckResults.expr_typ(node) -> Optional[typs.Typ]`, written by `TypCheck._check_expr`.
- `TypCheckResults.place_typ(node) -> Optional[typs.PtrTyp]`, written by `_check_place`.
- `TypCheck._speculative()` — a context manager suppressing recording.
- `CfgBuilder._build_expr` asserts every value it builds against the recorded type.
- No behaviour change; IR byte-identical to the baseline.

- [ ] Add both maps to `TypCheckResults`, with `Optional`-returning accessors (a node may have
  either, both, or neither).
- [ ] Record in `TypCheck._check_expr` (its single return path — wrap the existing `match` in
  a helper so there is exactly one recording site) and in `_check_place` (all return paths,
  including the `VarExpr` early returns).
- [ ] **Give `PtrMutRelax` and `Invalid` value equality before adding any assertion.** They are
  plain classes today, so `PtrMutRelax() == PtrMutRelax()` is `False`, while their siblings
  `NeverDiverge` and `IntExt` are frozen dataclasses. `_record_coercion` builds a fresh marker
  on every visit, so without this the re-record assertion fails on an ordinary idempotent
  re-visit. Make both `@dataclasses.dataclass(frozen=True)`, matching the siblings; the class
  patterns in `_coerce`'s `match` are unaffected.
- [ ] Add `_recording: bool` and the `_speculative()` context manager, and wrap
  `_infer_comptime_args`' probe loop in it. Two requirements:
  - **Suppress only context-dependent facts.** `expr_typ`, `place_typ`, `coercion`,
    `folded_int_lit`, `brace_expr_typ`, `let_declared_typ`, `struct_field_index`,
    `match_plan` and `match_scrutinee_typ` no-op during a probe. `local_typ` and everything in
    `resolve.Resolutions` **keep recording**. A blanket suppression breaks the checker:
    `f({ let y = 1i32; y })` compiles today, and a probe that drops `y`'s local type then
    raises `KeyError` reading it back for the block's tail expression. A match binding used in
    its arm body fails the same way.
  - **Save and restore the previous flag** rather than setting it to `True` on exit. A probe
    can contain a nested generic call, opening a nested `_speculative()` scope; unconditional
    re-enabling would re-arm recording for the rest of the outer probe.
- [ ] **Do not suppress diagnostics.** `_speculative` governs fact recording only. The probe
  currently double-reports warnings and can raise a false error (both documented in the design
  doc); leaving that untouched is what keeps this task's "no accepted or rejected program
  changes" contract true. Confirm no diagnostic output changes — including that the duplicate
  unreachable-arm warning is still duplicated. If it stops, something suppressed more than
  recording.
- [ ] Add regression tests for generic arguments that introduce bindings — a block local and a
  match binding used in its own arm body, both inside an argument whose comptime parameter is
  inferred. These pass today and must keep passing.
- [ ] Make every suppressible `_set_*` assert that a re-record agrees with the stored value.
  Expect this to fire during development; each firing is a finding, not noise.
- [ ] In `CfgBuilder`, rename the dispatching body to `_build_expr_uncheck` and make
  `_build_expr` the checking wrapper:
  ```python
  def _build_expr(self, expr_ast: ast.Expr, ctx: _ExprContext) -> ir_values.Value:
      value = self._build_expr_uncheck(expr_ast, ctx)
      self._assert_recorded_typ(value, expr_ast, ctx)
      return value
  ```
- [ ] `_assert_recorded_typ` compares against `expr_typ` in `VALUE` context and `place_typ` in
  `PLACE` context, substituting `_comptime_arg_mapping` into the recorded type first, and
  skipping when nothing was recorded for that node and context. It exempts a `never` value
  (`PLACE`: a `never` pointee), with a comment giving the reason from the design doc — lowering
  discovers divergence structurally, so `g() + 1i32` is recorded `i32` and lowered `never`.
  Use `asserts.assert_eq` so the check is stripped under `-O`.
- [ ] Run the suite. For every assertion that fires, decide and record which phase is wrong.
  The default is that the checker is right and the lowerer is corrected. If a case is neither
  — the checker is wrong, or both are defensible — stop and raise it rather than adding an
  exemption.
- [ ] Full suite, lint, type check pass; IR corpus diff against the baseline is empty.

---

### Task 5: Fix #69 — `if`/`else` with two void branches

**Files:**
- Modify: `src/leech/ir_builder.py`
- Modify: `tests/test_if_els.py`

**Interfaces:**
- `_build_if_expr` returns a `VoidValue` when the recorded result type is `void`, instead of
  building a phi over two `VoidValue`s.
- The only behaviour change in this plan: a program that crashed the compiler now compiles.

- [ ] In `_build_if_expr`'s two-armed path, after both arms are lowered, decide in this order —
  the order matters:
  1. neither arm reaches `end_bb` → `self._curr_bb.unreachable(if_ast)`, exactly as today.
     This must stay *first*: `end_bb` has no predecessors, so returning a `VoidValue` here
     would leave it with no terminator.
  2. the recorded result type is `typs.VOID` → `ir_values.VoidValue(if_ast)`, no phi.
  3. both arms reach → the phi, as today.
  4. one arm reaches → that arm's value, as today.
  Step 2 is the fix; steps 1, 3 and 4 are the existing branches, reordered around it.
- [ ] `_set_position(end_bb)` still runs before returning in every case.
- [ ] The single-armed `if` path already returns a `VoidValue` and needs no change; confirm
  that and say so rather than touching it. Confirm `_build_match_expr`'s all-void case is
  unaffected.
- [ ] Add the issue's reproducer as a test asserting it compiles and runs:
  ```leech
  pub fn main() i32 {
      if (true) {
          let x = 1;
      } else {
          let y = 2;
      };
      return 0;
  }
  ```
- [ ] Add a variant where one branch diverges (`return 0;`) and the other is void, so the
  mixed case is pinned as well.
- [ ] Full suite, lint, type check pass. This commit references both #70 and #69.

---

### Task 6: Record the match plan

**Files:**
- Modify: `src/leech/patterns.py`, `src/leech/check_results.py`, `src/leech/typcheck.py`,
  `src/leech/ir_builder.py`
- Modify: `tests/test_patterns.py`, `tests/test_match.py`

**Interfaces:**
- `patterns.ArmTest`, `patterns.MatchPlan`, `patterns.build_match_plan(rows, space)`.
- `TypCheckResults.match_plan(node: ast.MatchExpr) -> patterns.MatchPlan`.
- `TypCheckResults.match_arm_constructors` and `_pattern_constructor_candidates` are deleted.
- `ir_builder` stops importing `typcheck`. It keeps importing `patterns` for the constructor
  dataclasses `_match_case_value` dispatches on, and stops calling any function in it.
- No behaviour change; IR byte-identical.

- [ ] Add `ArmTest` and `MatchPlan` to `patterns.py` as frozen dataclasses, exactly as the
  design doc gives them. `patterns.py` must still import neither `leech.ast` nor `leech.typs`.
- [ ] Add `build_match_plan(rows, space) -> MatchPlan`, moving in, unchanged, the logic
  currently split across `TypCheck._check_match_expr`'s usefulness loop and exhaustiveness
  call, and `CfgBuilder._build_match_tests`, `_match_arm_is_wildcard` and
  `_match_arm_covers_remaining`. It returns `tests`, `reachable_arms`, `missing` and
  `falls_through`.
- [ ] **Move the constructor-candidate extraction into `patterns.py` as a private helper.**
  `ArmTest.constructors` is what `TypCheckResults.match_arm_constructors` produces today via
  `typcheck._pattern_constructor_candidates` (dropping the `None` entries). `patterns.py`
  cannot import `typcheck`, so that logic has to live here — deleting both without recreating
  it leaves `build_match_plan` unable to populate its own field.
- [ ] Assert the `MatchPlan` invariants inside `build_match_plan`, and test each directly:
  exactly one `ArmTest` per arm in `reachable_arms`, in ascending `arm_index`, and none for
  any other arm; `constructors == ()` only as the final entry, meaning "entered
  unconditionally"; an or-pattern with a wildcard alternative yielding `constructors == ()`;
  `falls_through` true exactly when there is no unconditional final entry; `tests == ()`
  meaning no arm is reachable.
- [ ] Do **not** write an invariant saying `tests` may be a proper prefix of `reachable_arms`.
  It cannot be: once an arm is entered unconditionally, every later arm fails `is_useful` and
  so is absent from `reachable_arms` entirely. Arms after the unconditional one are unreachable
  arms whose bodies are still lowered, not reachable arms lacking a test. Truncating `tests`
  would be solving a problem that cannot arise.
- [ ] Cover `falls_through` **only** in the `build_match_plan` unit tests, on a hand-built
  non-exhaustive matrix. It is unreachable for any accepted program: an exhaustive match over a
  closed space ends with an arm covering the remainder, and an open space can only be exhausted
  by a wildcard, so the final entry is always unconditional. The zero-reachable-arm case (a
  `never` scrutinee) goes through `tests == ()` instead. This matches the existing comment in
  `_build_match_tests` calling its own fallback unreachable.
- [ ] `TypCheck._check_match_expr` calls it once. Raise `NonExhaustiveMatchError` when
  `plan.missing` is non-empty; emit `UnreachableMatchArmWarning` for each arm index not in
  `plan.reachable_arms`; record the plan only after both. Delete the separate `is_useful` loop
  and `missing_patterns` call.
- [ ] `CfgBuilder._build_match_expr` reads `results.match_plan(match_ast)`: branch to `end_bb`
  when `plan.tests` is empty; otherwise walk `plan.tests`, emitting an unconditional branch
  for an empty `constructors` tuple and a comparison chain otherwise, and an `unreachable` at
  the end when `plan.falls_through`. Delete `_build_match_tests`, `_match_arm_is_wildcard` and
  `_match_arm_covers_remaining`.
- [ ] **Keep lowering every arm's body, including unreachable arms', exactly as today.** It
  looks like free dead-code elimination to lower only `plan.reachable_arms`, and it is not:
  `_add_bb` draws names from a per-basename counter (`naming.VarNamer`), so blocks an
  unreachable body would have created shift the names of every block created after it, and the
  IR text changes. The phi still takes incoming values only from arms that reach `end_bb`.
  Only the *tests* are driven by the plan.
- [ ] Keep `match_scrutinee_typ`: `_match_comparison_value` and `_match_case_value` still need
  the *substituted* scrutinee type. Confirm no other substituted-type input feeds a coverage
  decision any more.
- [ ] Delete `match_arm_constructors` and `_pattern_constructor_candidates`.
- [ ] `TypCheckResults.match_arm_pattern` becomes unread by `ir_builder` (it only fed `rows`;
  `_build_match_binding` works from the *AST* pattern). Delete it and keep the translated
  patterns local to `TypCheck._check_match_expr`. #65's sub-pattern bindings will need a
  recorded fact, but it will be a different one — do not keep this against that day.
- [ ] Rename `typcheck.match_constructor_space` → `_match_constructor_space` now that
  `ir_builder` no longer calls it.
- [ ] Remove `typcheck` from `ir_builder.py`'s imports. `patterns` stays, but only for
  `VariantConstructor` / `BoolConstructor` / `IntConstructor` / `ConstructorKind` /
  `MatchPlan` / `ArmTest`; confirm no `patterns.<function>` call remains.
- [ ] Add `tests/test_patterns.py` cases for `build_match_plan` directly — no compiler, just
  rows and spaces: a wildcard-terminated chain, a chain whose last arm covers the remainder, a
  chain that falls through, an unreachable arm, an or-pattern with a wildcard alternative, and
  an empty arm list.
- [ ] Confirm `tests/test_match.py` passes unchanged; if any test needed editing, the
  behaviour was not preserved — stop.
- [ ] Full suite, lint, type check pass; IR corpus diff empty.

---

### Task 7: Fold negative integer literals, and pin the boundary

**Files:**
- Modify: `src/leech/check_results.py`, `src/leech/typcheck.py`, `src/leech/ir_builder.py`
- Add: `tests/test_module_boundaries.py`
- Modify: `tests/test_arith.py`, `tests/test_compilation.py`

**Interfaces:**
- `TypCheckResults.folded_int_lit(node) -> tuple[typs.IntTyp, int]`, keyed by the
  `ast.IntLit`, or by the `ast.UnaryOpExpr` when a unary minus folded into it. Replaces
  `int_lit_typ`.
- `CfgBuilder._build_int_lit` is deleted.
- A test pinning that `ir_builder` does not import `typcheck` and uses `patterns` for data
  only.

- [ ] Add the map and accessors and delete `int_lit_typ`. Record from `TypCheck._check_expr`'s
  `IntLit` case (value as written) and from `_check_neg_expr`'s literal branch (negated value,
  keyed by the `UnaryOpExpr`), at the points that already range-check the value.
- [ ] Drop `_check_int_lit_pattern`'s recording rather than porting it. It is write-only:
  `int_lit_typ` has exactly one reader, `_build_int_lit`, and a pattern literal is never
  lowered as an expression — match lowering works from the plan's `IntConstructor` and the
  substituted scrutinee type. Re-confirm the single reader before deleting.
- [ ] `_build_expr_uncheck`'s `IntLit` case and `_build_unary_op_expr`'s `-` case both look the
  fact up and build a `ir_values.ComptimeInt` directly. Delete `_build_int_lit` and the
  `isinstance(op_ast.operand, ast.IntLit)` test; keep the comment explaining *why* the fold
  exists (`-128i8`'s positive half does not fit), since that reason is not visible from the
  code.
- [ ] Keep today's attribution: for the negated form the fact is *keyed* by the `UnaryOpExpr`,
  but the `ComptimeInt` is attributed to the operand `ast.IntLit`, which is what
  `_build_int_lit(op_ast.operand, True)` does now. Key and attribution node differ here on
  purpose; changing the attribution would move a diagnostic span for no reason.
- [ ] Add `tests/test_module_boundaries.py` (SPDX header) parsing source with Python's own
  `ast` module and asserting: `src/leech/ir_builder.py` does not import `leech.typcheck`;
  every name it reaches through `patterns.` is in a data-only allowlist
  (`VariantConstructor`, `BoolConstructor`, `IntConstructor`, `ConstructorKind`, `MatchPlan`,
  `ArmTest`); and `src/leech/patterns.py` imports neither `leech.ast` nor `leech.typs`. Give
  each assertion a message naming the invariant, so a future reader learns the rule from the
  failure rather than from this plan.
- [ ] Add a test in `tests/test_compilation.py` pinning codegen's block ordering, which Task
  1's soundness argument relies on: compile a function with nested `if`/`else` and assert the
  emitted block labels appear in reverse-postorder from entry, not creation order. This is a
  codegen property, not a module-boundary one — keep it out of the new boundaries file.
- [ ] Extend `tests/test_arith.py` with `-128i8`, `-2147483648i32` and a negated literal in a
  peer position, if not already covered.
- [ ] Full suite, lint, type check pass; IR corpus diff empty.

---

### Task 8: Documentation and close-out

**Files:**
- Modify: `src/leech/ir_builder.py`, `src/leech/typcheck.py`, `src/leech/check_results.py`
  (docstrings only)

**Interfaces:** none.

- [ ] Update `ir_builder.py`'s module docstring: it currently claims lowering "trusts and
  reuses the decisions recorded in `TypCheckResults`", which only becomes true here. Say what
  it now does — walk the AST in source order, emit instructions, and read every semantic
  decision from `check_results`.
- [ ] Update `typcheck.py`'s module docstring to say the checker is the sole decider and that
  its results are the interface to lowering.
- [ ] Re-read every docstring and comment touched across all tasks against AGENTS.md's review
  guidance: no Sphinx roles, no caller lists, no `:raises:`, no restating signatures, no
  references to plans or issues, and no docstrings on non-public items or overrides that do
  not diverge from their base contract.
- [ ] Confirm the whole diff touches no grammar, no error text, and no test expectations other
  than the additions listed above.
- [ ] Report to the owner that #70 appears complete and #69 fixed, and **ask** whether to close
  them. Do not close either unprompted.
- [ ] If either is closed, update #55's graph in the same session per its rules.

---

## Sequencing and parallelism

Tasks 1 and 2 are independent of each other and of everything else; either can go first, but
doing the deletion (1) before the move (2) keeps the move diff purely mechanical.

Task 3 must precede Task 4 — the local-type divergence makes Task 4's `place_typ` assertion
fail. Task 5 depends on Task 4 for the recorded result type it reads.

Task 6 depends only on Task 2 (for where the fact lives) and can be developed in parallel with
Tasks 3–5, merging last.

Task 7 depends on **both** Task 4 and Task 6, and cannot be developed fully in parallel with
Tasks 3–5. Its folded-literal half edits `_build_expr_uncheck`'s `IntLit` case, and
`_build_expr_uncheck` does not exist until Task 4 splits it out of `_build_expr`; its boundary
test needs Task 6, which is what removes `ir_builder`'s `typcheck` import and its last
`patterns` function call. If Task 7 is started early, it must merge on top of Task 4.

## Acceptance criteria

- `grep -n "expected_typ" src/leech/ir_builder.py` is empty.
- `src/leech/ir_builder.py` does not import `leech.typcheck`, and reaches into `patterns` only
  for constructor and plan dataclasses — both pinned by a test.
- `typcheck.py` exposes no flexible-literal or peer-type helper: `_is_flexible_int_lit`,
  `_resolve_peer_typ`, `_match_arm_check_order` and `_match_constructor_space` are all private.
- `CfgBuilder` calls no function from `patterns`, and derives no type it could have read.
- Every value `_build_expr` produces is asserted against a recorded type, or is `never`.
- `patterns.build_match_plan` is the only place usefulness or coverage is computed.
- Every IR corpus entry that existed at the `2cb0d4b` baseline is byte-identical to it. The
  one entry added after Task 5 is the void-`if` program that could not compile at baseline,
  and it is named in that commit's message.
- Full suite, `ruff check`, `ruff format --check`, `basedpyright` and `reuse lint` pass.
