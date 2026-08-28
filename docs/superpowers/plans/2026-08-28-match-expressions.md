<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# `match` Expressions with Exhaustiveness Checking Implementation Plan

**Goal:** Add `match` to Leech as both a statement and a value-producing expression, over
`_`, `let x` / `let mut x` bindings, bool literals, integer literals (including negative),
enum variant paths, and or-patterns; diagnose a non-exhaustive match with every uncovered
case named, and a redundant arm as a warning.

**Architecture:** `match_expr` joins the `?block_like_expr` grammar category, which supplies
statement position, the optional trailing semicolon and tail-expression behaviour for free.
Bindings are spelled `let x`, which dissolves the reduce/reduce collision issue #30
describes rather than resolving it semantically as Rust does. A new pure leaf module,
`patterns.py`, implements Maranget's usefulness algorithm — both exhaustiveness and
unreachability fall out of it — with a `Constructor` abstraction that is arity-0 throughout
this change but which struct patterns, tuples (#29) and tagged unions (#24) extend rather
than replace. Lowering is a cbranch chain plus an N-ary phi, structurally identical to
`_build_if_expr` and reusing `_build_if_arm` verbatim, which means **no new IR
instruction** — important because `comptime.Interpreter._eval_instr` raises on any
instruction it doesn't know, and module-level `let x = match (…);` runs through it.

**Tech Stack:** Python 3.14, lark 1.3.1 (LALR, `strict=True`), pytest. No new dependencies.

**Design doc:** `docs/superpowers/specs/2026-08-28-match-expressions-design.md` — read it
first; it carries the full rationale, the surveyed alternatives for binding syntax, the
verified-conflict-free grammar, and why `_` must become a reserved word.

## Global Constraints

- Keep all changes unstaged and uncommitted until the user reviews and explicitly
  authorizes a commit.
- Use module-qualified imports, four-space indentation, Ruff's 100-column limit.
- `patterns.py` must not import `leech.ast` or `leech.typs` — its purity is what makes it
  unit-testable in isolation and what keeps the constructor abstraction honest.
- Do not add a new IR instruction. If one seems necessary, stop: it also needs an arm in
  `comptime.Interpreter._eval_instr` and in `codegen._compile_instr`, and that is a
  separate filed issue.
- The checker and the builder must visit arms in the same order (see Task 5).
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`, `uv run basedpyright` and
  `uv run reuse lint` must all pass. This plan adds two new source files, so SPDX headers
  matter.

---

### Task 1: Grammar and keywords

**Files:**
- Modify: `src/leech/leech.lark`
- Modify: `src/leech/reserved.py`
- Modify: `tests/test_parsing.py`
- Modify: `tests/test_block_stmt_semicolons.py`

**Interfaces:**
- Produces the `match_expr`, `match_arm_list`, `match_arm` and pattern rules.
- `match` becomes a block-like expression, so its statement/tail/semicolon behaviour comes
  from the existing `_stmts`/`_req_stmts` rules with no change to them.
- `_` and `match` stop being usable as identifiers (semantically, not lexically).

- [ ] Add `| match_expr` to `?block_like_expr`.
- [ ] Add the `match_expr` / `match_arm_list` / `_match_arm_list` / `match_arm` rules and the
  `pattern` / `or_pattern` / `primary_pattern` / `wildcard_pattern` / `binding_pattern` /
  `int_lit_pattern` / `path_pattern` rules beside `while_expr`, exactly as given in the
  design doc.
- [ ] Add `"match"` and `"_"` to `reserved.KEYWORDS`.
- [ ] Confirm `tests/test_reserved.py::test_keyword_set_matches_grammar` passes **unchanged**
  — it derives the expected set from the grammar's string terminals, and both new keywords
  qualify.
- [ ] Confirm the five `let _ = …;` sites in `tests/test_generic_fns.py` (lines 918, 930,
  942, 1008, 1020) still raise their expected errors: `_check_let_stmt` checks the
  initializer before the reserved-name check, so they should be unaffected. If any
  regresses, rewrite it as a bare expression statement (`f[i32]();`).
- [ ] Add positive parse-tree assertions to `tests/test_parsing.py` using the existing
  `T`/`Tok` builder DSL: a basic match, or-patterns, `let mut` binding, negative int
  pattern, empty match, trailing comma, and `match` as a call argument.
- [ ] Add negative cases to `test_parse_fails`: a block-bodied arm with no following comma,
  and an unparenthesised scrutinee.
- [ ] Extend `tests/test_block_stmt_semicolons.py`'s existing `if`/`while`/`{}` coverage to
  `match`, in both the semicolon and semicolon-less statement forms.

---

### Task 2: AST nodes

**Files:**
- Modify: `src/leech/ast.py`

**Interfaces:**
- Produces `ast.MatchExpr` (`scrutinee: Expr`, `arms: tuple[MatchArm, ...]`) and
  `ast.MatchArm` (`pattern: Pattern`, `body: Expr`).
- Produces an `ast.Pattern` base with `WildcardPattern`, `BindingPattern`, `IntLitPattern`,
  `BoolLitPattern`, `PathPattern`, `OrPattern`, and a `Pattern.from_tree` dispatch.
- `BlockExpr`'s external interface (`stmts` / `expr`) is unchanged.

- [ ] Add the node classes, modelled on `IfExpr` and `WhileExpr`: class-level `Final`
  annotations assigned in `__init__`, `src.SrcSpan.from_lark_meta(file, tree.meta)`, and an
  `@override def diag_str`. Use `_as_tree`/`_as_token` and `opt_util.opt_map` for optional
  children, as the neighbouring nodes do.
- [ ] Add a `Pattern.from_tree` static dispatch keyed on rule name, mirroring the shape of
  `Expr._child_ctors` / `Stmt.from_tree`.
- [ ] Register `"match_expr": MatchExpr` in `Expr._child_ctors`. Verify no other change is
  needed in `BlockExpr`: registering the rule is what makes `_is_expr_tree` treat a trailing
  `match` as the block's tail expression, and `_stmt_from_tree` wrap a semicolon-less one in
  a synthetic `ExprStmt`.
- [ ] Add AST-shape assertions to `tests/test_ast.py` covering arms, patterns and or-pattern
  alternatives.

---

### Task 3: `patterns.py`

**Files:**
- Add: `src/leech/patterns.py`
- Add: `tests/test_patterns.py`

**Interfaces:**
- Produces `patterns.is_useful(matrix, row, space) -> bool` and
  `patterns.missing_patterns(matrix, space) -> list[Witness]`.
- Produces the `Constructor` / `Pat` / `ConSpace` / `Witness` types.
- Imports neither `leech.ast` nor `leech.typs`.

- [ ] Add the SPDX header.
- [ ] Define constructors — `VariantCon(discriminant, display_name)`, `BoolCon(value)`,
  `IntCon(value)` — each with an `arity` (0 throughout this change) and an equality that
  keys on the *discriminant/value*, not the display name. That is what makes
  `enum Alias { A = 1, B = 1 }` deduplicate correctly.
- [ ] Define patterns: `WildPat`, `ConPat(con, subpats)`, `OrPat(alternatives)`. Frozen
  dataclasses.
- [ ] Define `ConSpace`: the complete constructor set for a scrutinee type, and whether it is
  open. Finite for enums (deduped by discriminant) and bools; open for integers and for any
  type with no constructor patterns yet; empty for an uninhabited type.
- [ ] Implement `specialize(con, matrix)` and `default(matrix)`. With arity-0 constructors
  these are degenerate — `specialize` filters rows, `default` keeps wildcard rows — but write
  them as the general operations over sub-patterns so a later arity > 0 constructor is an
  extension.
- [ ] Implement `is_useful` and `missing_patterns` per Maranget, expanding an `OrPat` row
  into several rows.
- [ ] Implement `Witness` rendering as a source-like pattern string (`Color::Blue`, `true`,
  `_`), naming a variant by the first display name registered for its discriminant.
- [ ] Unit tests in `tests/test_patterns.py`, with no compiler involvement: exhaustive and
  non-exhaustive enum sets; aliased discriminants deduplicating; both bool values; an open
  integer space requiring a wildcard; an empty space being exhaustive with zero rows;
  or-pattern expansion; redundant rows detected at the right index; witness rendering.

---

### Task 4: Diagnostics

**Files:**
- Modify: `src/leech/errors.py`

**Interfaces:**
- Produces `NonExhaustiveMatchError`, `UnreachableMatchArmWarning`,
  `MatchArmTypMismatchError`, `PatternTypMismatchError`, `BindingInOrPatternError`,
  `NotAPatternError`.

- [ ] `NonExhaustiveMatchError` — primary span on the `match`, then one `NOTE` per uncovered
  witness. Model on `InfiniteSizeStructError`, which already takes a `Sequence` and emits a
  variable-length note list. All uncovered cases must be in this one error: errors are
  raised, so only one surfaces per compilation.
- [ ] `UnreachableMatchArmWarning` — `WARNING` level, reported via `errors.register_error`
  rather than raised, following `UnreachableCodeWarning` (including its
  `# noqa: N818 - a warning, not an error` comment).
- [ ] `MatchArmTypMismatchError` — model on `IfElsTypMismatchError`: `None` primary span,
  both arm types as notes with their own spans.
- [ ] `PatternTypMismatchError` — a literal pattern against an incompatible scrutinee type.
- [ ] `BindingInOrPatternError` — a `let` binding inside an or-pattern alternative.
- [ ] `NotAPatternError` — a path resolving to something that is not an enum variant. Word it
  so it stays accurate when constants become patterns.

---

### Task 5: Type checking

**Files:**
- Modify: `src/leech/typcheck.py`
- Modify: `src/leech/resolve.py`

**Interfaces:**
- Produces `TypCheck._check_match_expr` and `_check_pattern`.
- Produces new `TypCheckResults` tables for the scrutinee type and each arm's resolved
  constructor, so `CfgBuilder` never re-resolves a name.
- Extends `resolve.LocalDecl` with `ast.BindingPattern`.

- [ ] Add `case ast.MatchExpr():` to `_check_expr`.
- [ ] Write `_check_pattern(pat, scrutinee_typ, e)`: type-check each pattern against the
  scrutinee type and translate it to a `patterns.Pat`. Integer literals go through
  `_infer_int_lit_typ` with the scrutinee type as the hint, the `fits` check
  (`IntLitOverflowError`), and `results._set_int_lit_typ`. Variant paths resolve through
  `ir_env.Env.resolve_path`, following the `EnumTyp` arm of `_resolve_path_segment`. Reject a
  binding inside an `OrPattern`.
- [ ] Build the `patterns.ConSpace` for the scrutinee type: finite for `EnumTyp` (from
  `EnumTyp.variants`, deduped by discriminant) and `BoolTyp`; empty for `NeverTyp`; open
  otherwise.
- [ ] Per-arm scope: `e.new_child()`, with the binding added before the arm body is checked.
- [ ] Bindings as locals: extend `resolve.LocalDecl` with `ast.BindingPattern`; record
  `self._local_typs[binding_ast]`; add `ast.BindingPattern` to the isinstance tuples in
  `_check_var_expr`, `_check_place` and `_place_mut`; call `reserved.is_reserved` on the
  bound name and raise `ReservedNameError`, as `_check_let_stmt` does.
- [ ] Write the shared arm-ordering helper: visit non-flexible arms first, `resolve_peer_typ`
  over their types, then the flexible ones. Put it in `typcheck.py` at module level so
  `ir_builder` calls the *same* helper — do not duplicate the logic as `if` does.
- [ ] Unify arm types by exact equality with `never` absorption, exactly as `_check_if_expr`
  does; raise `MatchArmTypMismatchError` otherwise. The match's type is the first non-`never`
  arm type, or `never` if all arms diverge.
- [ ] Run the exhaustiveness and unreachability checks: raise `NonExhaustiveMatchError` when
  `missing_patterns` is non-empty; register `UnreachableMatchArmWarning` for each arm *i*
  where `not is_useful(rows[:i], rows[i])`.
- [ ] Confirm no void requirement is imposed in statement position — `ast.ExprStmt` discards
  any type, unlike `if` without `else`.

---

### Task 6: Lowering

**Files:**
- Modify: `src/leech/ir_builder.py`

**Interfaces:**
- Produces `CfgBuilder._build_match_expr`.
- Adds no new IR instruction, so `codegen.py` and `comptime.py` are untouched.

- [ ] Add `case ast.MatchExpr():` to `_build_expr`.
- [ ] Lower the scrutinee once into a value; if it is an enum, `enum_to_int` once before the
  chain and compare on the backing integer type.
- [ ] Create one test block and one arm block per arm, plus a shared `end_bb`, all before
  lowering any arm body so branches can target them.
- [ ] Reuse `_build_if_arm` unchanged for each arm body — its
  `(value, last_bb, reaches_end)` contract is exactly what the phi needs, and `last_bb` must
  be the block lowering *ended* in, not the arm's entry block.
- [ ] Bindings: `alloca` then `store` at the top of the arm block, recorded in
  `_local_values[binding_ast]`, mirroring `_build_let_stmt`.
- [ ] Merge: collect `{last_bb: value}` for arms reaching `end_bb`; emit a `phi` if more than
  one reaches it, return the single value if exactly one does, and `unreachable` if none
  does.
- [ ] Use `_branch` / `_cbranch` throughout, never `bb.branch(...)` directly — `codegen.py`
  orders blocks by DFS from `cfg.entry`, so a block never made a CFG successor is never
  emitted.
- [ ] Where the pattern space is fully covered before the last arm, branch unconditionally
  into the final arm rather than emitting a dead comparison.
- [ ] Use the shared arm-ordering helper from Task 5 — the visit order here must match the
  checker's, or recorded int-literal types will not line up.

---

### Task 7: Behaviour tests and gates

**Files:**
- Add: `tests/test_match.py`
- Modify: `tests/test_errors.py`

**Interfaces:**
- Every case has a runtime form; cases that can be evaluated at compile time also have a
  `test_comptime_*` form, following `tests/test_if_els.py`.

- [ ] Runtime cases: exhaustive enum match; enum match with `_`; or-patterns; bool exhaustion
  via `true`/`false`; integer patterns with a required wildcard; negative integer patterns;
  `let x` and `let mut x` bindings; block-bodied arms; `match` as a tail expression, as a
  statement with and without a semicolon, and as a call argument; nested `match`; arms that
  diverge, and `never` absorption; empty `match` on a `never` scrutinee.
- [ ] Comptime cases: bind at module scope (`let x = match (…) {…};`) to force evaluation
  through `comptime.Interpreter`, mirroring `test_comptime_if_true_else_expr_val`. This is
  the check that the no-new-instruction constraint held.
- [ ] Aliased discriminants: confirm `match (a) { Alias::A => … }` is exhaustive and that a
  following `Alias::B` arm produces the unreachable-arm warning.
- [ ] Peer typing of bare integer literals across three or more arms, in the spirit of
  `tests/test_peer_typs.py`.
- [ ] Error cases via `pytest.raises` around `util.compile_str`, one per new error class.
- [ ] Add message/span/note assertions for `NonExhaustiveMatchError` and
  `MatchArmTypMismatchError` to `tests/test_errors.py`, using `util.find_pos`.
- [ ] End-to-end sanity check by hand — compile and run:

  ```leech
  enum Color { Red, Green, Blue }
  pub fn main() i32 {
      let c = Color::Green;
      return match (c) {
          Color::Red | Color::Blue => 1i32,
          Color::Green => 0i32,
      };
  }
  ```

  `uv run leech main.leech -o main.ll` should emit the cbranch chain and phi; linked and run
  under `lli` it should exit 0. Then delete the `Color::Green` arm and confirm a
  `NonExhaustiveMatchError` naming `Color::Green`; add a duplicate `Color::Red` arm and
  confirm the unreachable-arm warning.
- [ ] Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`,
  `uv run basedpyright`, `uv run reuse lint`.
- [ ] Request independent review and leave all changes unstaged for user review. Do not close
  #30 — that is the repository owner's call.
