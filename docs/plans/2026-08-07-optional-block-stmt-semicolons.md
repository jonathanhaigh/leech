<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Optional Semicolons After Block-Like Statements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a statement's trailing `;` be omitted when its expression is block-like (`if`/`if`-`else`, `while`, or a bare `{ ... }` block) and something follows it in the enclosing block, while keeping `;` legal (and, at the very end of a block, still meaningful) everywhere it is today.

**Architecture:** Replace `block_expr`'s body grammar (`stmt* expr?`) with a pair of mutually recursive rules — one that may end the block, one that must be followed by more content — so a semicolon-less block-like statement can never land in the position immediately before `}` (which stays exclusively the tail-expression path, unchanged from today). `ast.py`'s `BlockExpr`/`Stmt`/`ExprStmt` classes are updated to build the same AST shape as before from the new, still-flat parse tree.

**Tech Stack:** Python 3.14, Lark (LALR(1) parser), pytest.

## Global Constraints

- Design doc: `docs/specs/2026-08-07-optional-block-stmt-semicolons-design.md`. Read it before starting — it has the full rationale.
- `let`, `return`, `break`, `continue`, and assignment statements are **not** affected: `;` stays mandatory for them regardless of their operand's shape.
- `struct_expr` is **not** block-like: its statement-position `;` stays mandatory.
- Backward compatible: every program that compiles today must keep compiling with an identical AST/behavior.
- Run `uv run ruff format .`, `uv run ruff check .`, and `uv run basedpyright` before each commit; run `uv run reuse lint` before the final commit (no new files are added by this plan, so this should be a no-op check).
- Every step's exact code has already been validated against the real grammar/AST in this codebase (compiled under Lark's strict LALR(1), full `uv run pytest` suite passing, `basedpyright` clean) — copy it as given.

---

### Task 1: Grammar change

**Files:**
- Modify: `src/leech/leech.lark:64-95` (the `stmt`/`block_expr` rules)
- Test: `tests/test_parsing.py`

**Interfaces:**
- Produces: a `block_expr` parse tree whose children are still a **flat** list — some `stmt` subtrees (the existing `;`-terminated forms, unchanged shape), zero or more bare `if_expr`/`while_expr`/`block_expr` subtrees appearing directly (the new semicolon-less form, valid only when *not* last), and optionally a final bare expression subtree of any kind (the existing tail-expression form, unchanged meaning: it becomes the block's value). Task 2 consumes this shape.

- [ ] **Step 1: Write the new parser test cases**

Add these to the `rule,src,expected` parametrize list in `tests/test_parsing.py` (near the existing `while_expr`/`block_expr` cases, e.g. after the `"{}"` case around line 703):

```python
(
    (
        "expr",
        "{ if (x) { 1 } y }",
        T("block_expr").cs(
            T("if_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ if (x) { 1 }; y }",
        T("block_expr").cs(
            T("stmt", "expr_stmt", "if_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ while (x) { 1 } y }",
        T("block_expr").cs(
            T("while_expr").cs(
                None,
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ while (x) { 1 }; y }",
        T("block_expr").cs(
            T("stmt", "expr_stmt", "while_expr").cs(
                None,
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ { 1; } y }",
        T("block_expr").cs(
            T("block_expr", "stmt", "expr_stmt", "int_lit", Tok("1")),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ { 1; }; y }",
        T("block_expr").cs(
            T("stmt", "expr_stmt", "block_expr", "stmt", "expr_stmt", "int_lit", Tok("1")),
            T("var_expr").cs(T("path", "ident", Tok("y")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ if (x) { 1 } while (y) { 2 } z }",
        T("block_expr").cs(
            T("if_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
            T("while_expr").cs(
                None,
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
                T("block_expr", "int_lit", Tok("2")),
            ),
            T("var_expr").cs(T("path", "ident", Tok("z")), None),
        ),
    ),
)
(
    (
        "expr",
        "{ if (x) { 1 } }",
        T("block_expr", "if_expr").cs(
            T("var_expr").cs(T("path", "ident", Tok("x")), None),
            T("block_expr", "int_lit", Tok("1")),
        ),
    ),
)
(
    (
        "expr",
        "{ if (x) { 1 }; }",
        T("block_expr", "stmt", "expr_stmt", "if_expr").cs(
            T("var_expr").cs(T("path", "ident", Tok("x")), None),
            T("block_expr", "int_lit", Tok("1")),
        ),
    ),
)
```

Add these to the `rule,src` parametrize list in `test_parse_fails` (near the other `"block_expr"`/`"stmt"` fail cases, e.g. after line 1250):

```python
(("expr", "{ let x = if (true) { 1 } else { 2 } y }"),)
(("expr", "{ return if (true) { 1 } else { 2 } y }"),)
(("expr", "{ x = if (true) { 1 } else { 2 } y }"),)
(("expr", "{ Foo { a: 1 } y }"),)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py -v -k "test_parse or test_parse_fails"`
Expected: the 9 new `test_parse` cases FAIL (today's grammar requires `;` after a block-like statement that isn't last, so e.g. `{ if (x) { 1 } y }` doesn't parse). The 4 new `test_parse_fails` cases PASS already (today's grammar already rejects them, for unrelated reasons) — that's fine, they're regression guards for Task 1's grammar change, not new failures to fix.

- [ ] **Step 3: Replace the grammar rule**

In `src/leech/leech.lark`, replace:

```
stmt: expr_stmt
  | ret_stmt
  | let_stmt
  | assignment_stmt
  | break_stmt
  | continue_stmt

expr_stmt: expr ";"
ret_stmt: "return" [expr] ";"
break_stmt: "break" [ident] ";"
continue_stmt: "continue" [ident] ";"
let_stmt: "let" mut ident [":" typ] "=" expr ";"
!mut: ["mut"]

assignment_stmt: place "=" expr ";"

?place: var_expr
  | array_access_expr
  | field_access_expr
  | deref_expr

?expr: block_expr
  | if_expr
  | while_expr
  | or_expr

block_expr: "{" stmt* expr? "}"
```

with:

```
stmt: expr_stmt
  | ret_stmt
  | let_stmt
  | assignment_stmt
  | break_stmt
  | continue_stmt

expr_stmt: expr ";"
ret_stmt: "return" [expr] ";"
break_stmt: "break" [ident] ";"
continue_stmt: "continue" [ident] ";"
let_stmt: "let" mut ident [":" typ] "=" expr ";"
!mut: ["mut"]

assignment_stmt: place "=" expr ";"

?place: var_expr
  | array_access_expr
  | field_access_expr
  | deref_expr

?expr: block_like_expr
  | or_expr

?block_like_expr: block_expr
  | if_expr
  | while_expr

block_expr: "{" _stmts "}"

// May end the block: empty, a tail expr (unchanged meaning: becomes the
// block's value), a ";"-terminated stmt (then more of the same), or a
// block-like statement with no ";" (then _req_stmts, which cannot be empty).
_stmts:
  | expr
  | stmt _stmts
  | block_like_expr _req_stmts

// Must be followed by more content. Keeps a semicolon-less block-like
// statement out of the position immediately before "}", so that position
// stays exclusively reachable through the tail-expr alternative above.
_req_stmts: expr
  | stmt _stmts
  | block_like_expr _req_stmts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_parsing.py -v -k "test_parse or test_parse_fails"`
Expected: all `test_parse` and `test_parse_fails` cases PASS, including the 9 new and 4 new ones from Step 1.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `uv run pytest -q`
Expected: all 998+ tests pass (this grammar change is purely additive — every existing `;`-terminated program parses to an identical tree).

- [ ] **Step 6: Lint and format**

Run: `uv run ruff format . && uv run ruff check . && uv run basedpyright`
Expected: no changes needed, no errors (this task doesn't touch Python source).

- [ ] **Step 7: Commit — grammar change plus the design doc**

```bash
git add src/leech/leech.lark tests/test_parsing.py docs/specs/2026-08-07-optional-block-stmt-semicolons-design.md
git commit -m "Make block_expr's grammar allow semicolon-less block-like statements"
```

---

### Task 2: AST changes

**Files:**
- Modify: `src/leech/ast.py` (the `Stmt`, `ExprStmt`, and `BlockExpr` classes)
- Test: `tests/test_ast.py`

**Interfaces:**
- Consumes: the `block_expr` parse-tree shape from Task 1 — flat children, each either a `stmt` subtree, a bare `if_expr`/`while_expr`/`block_expr` subtree (not last), or a bare expr subtree (last, the tail).
- Produces: `BlockExpr.stmts: tuple[Stmt, ...]` and `BlockExpr.expr: Optional[Expr]` — unchanged public shape. A semicolon-less block-like statement becomes an `ast.ExprStmt` wrapping the block-like `ast.Expr`, exactly like today's `;`-terminated form.

- [ ] **Step 1: Write the failing AST test**

Add to `tests/test_ast.py`:

```python
def test_block_stmt_block_like_no_semicolon(tmp_path):
    src = """
    fn f() {
        if (true) { 1 }
        while (true) { 2 };
        3;
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    if_stmt, while_stmt, int_stmt = fn.block.stmts
    assert isinstance(if_stmt, ast.ExprStmt)
    assert isinstance(if_stmt.expr, ast.IfExpr)
    assert isinstance(while_stmt, ast.ExprStmt)
    assert isinstance(while_stmt.expr, ast.WhileExpr)
    assert isinstance(int_stmt, ast.ExprStmt)
    assert isinstance(int_stmt.expr, ast.IntLit)
    assert fn.block.expr is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ast.py::test_block_stmt_block_like_no_semicolon -v`
Expected: FAIL with `AssertionError: not(lhs == rhs)` from `Stmt.from_tree`'s `asserts.assert_eq(tree.data, "stmt")` — the parse tree now contains a bare `if_expr` node where `BlockExpr` still unconditionally expects a `stmt` node.

- [ ] **Step 3: Update `ExprStmt` to support both tree shapes**

In `src/leech/ast.py`, replace:

```python
class ExprStmt(Stmt):
    """An expression evaluated for its side effects, followed by ``;``."""

    expr: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "expr_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        self.expr = Expr.from_tree(file, _as_tree(child))

    @override
    def diag_str(self) -> str:
        return "expression statement"
```

with:

```python
class ExprStmt(Stmt):
    """An expression evaluated for its side effects, with or without a trailing ``;``."""

    expr: Final[Expr]

    def __init__(self, span: src.SrcSpan, expr: Expr) -> None:
        super().__init__(span)
        self.expr = expr

    @staticmethod
    def _from_expr_stmt_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> ExprStmt:
        asserts.assert_eq(tree.data, "expr_stmt")
        span = src.SrcSpan.from_lark_meta(file, tree.meta)
        (child,) = tree.children
        return ExprStmt(span, Expr.from_tree(file, _as_tree(child)))

    @staticmethod
    def _synthetic(expr: Expr) -> ExprStmt:
        """Construct a statement for a block-like expression used without a trailing ``;``."""
        return ExprStmt(expr.span, expr)

    @override
    def diag_str(self) -> str:
        return "expression statement"
```

(This mirrors the existing `Ident`/`Ident.from_tree`/`Ident._synthetic` split in this same file.)

- [ ] **Step 4: Update `Stmt.from_tree`'s dispatch**

Replace:

```python
    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Stmt:
        """Build the statement selected by a parse tree's grammar rule."""
        asserts.assert_eq(tree.data, "stmt")
        (child,) = map(_as_tree, tree.children)
        child_classes = {
            "expr_stmt": ExprStmt,
            "ret_stmt": RetStmt,
            "let_stmt": LetStmt,
            "assignment_stmt": AssignmentStmt,
            "break_stmt": BreakStmt,
            "continue_stmt": ContinueStmt,
        }
        return child_classes[child.data](file, child)
```

with:

```python
    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Stmt:
        """Build the statement selected by a parse tree's grammar rule."""
        asserts.assert_eq(tree.data, "stmt")
        (child,) = map(_as_tree, tree.children)
        if child.data == "expr_stmt":
            return ExprStmt._from_expr_stmt_tree(file, child)
        child_classes = {
            "ret_stmt": RetStmt,
            "let_stmt": LetStmt,
            "assignment_stmt": AssignmentStmt,
            "break_stmt": BreakStmt,
            "continue_stmt": ContinueStmt,
        }
        return child_classes[child.data](file, child)
```

- [ ] **Step 5: Update `BlockExpr` to handle bare block-like children**

Replace:

```python
        self.stmts = tuple(Stmt.from_tree(file, stmt) for stmt in stmts)

        if expr:
            self.expr = Expr.from_tree(file, expr)
        else:
            self.expr = None

    @override
    def diag_str(self) -> str:
        return "block expression"
```

with:

```python
        self.stmts = tuple(BlockExpr._stmt_from_tree(file, stmt) for stmt in stmts)

        if expr:
            self.expr = Expr.from_tree(file, expr)
        else:
            self.expr = None

    @staticmethod
    def _stmt_from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Stmt:
        """Build a block-body statement, which may be a ``;``-terminated ``stmt``
        or a bare block-like expression used without one."""
        if tree.data == "stmt":
            return Stmt.from_tree(file, tree)
        return ExprStmt._synthetic(Expr.from_tree(file, tree))

    @override
    def diag_str(self) -> str:
        return "block expression"
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_ast.py::test_block_stmt_block_like_no_semicolon -v`
Expected: PASS

- [ ] **Step 7: Run the full suite and static checks**

Run: `uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run basedpyright`
Expected: all tests pass, no lint/format changes, no type errors.

- [ ] **Step 8: Commit**

```bash
git add src/leech/ast.py tests/test_ast.py
git commit -m "Build AST nodes for semicolon-less block-like statements"
```

---

### Task 3: End-to-end behavior tests

**Files:**
- Create: `tests/test_block_stmt_semicolons.py`

**Interfaces:**
- Consumes: `util.check_prog_output(tmp_path, src, stdin, expected_return_code)` and `util.compile_str(tmp_path, src)` from `tests/util.py` (both already exist and are used the same way throughout the test suite, e.g. `tests/test_while.py`).

No implementation changes in this task — Tasks 1 and 2 already make these programs compile and run correctly. This task adds regression coverage at the compile-and-run level, confirming the feature works end-to-end (not just at the parse-tree level) and confirming the one semantically load-bearing case: the `;` still being required to force a block-like expression to be discarded when it's the last thing in a block.

The tail-position test cases use `if (true) { 1 } else { 2 }`, not a bare `if (true) { 1 }` with no `else`: an `if` without `else` must *already* be void-typed regardless of context (see `errors.IfTypNotVoidError` in `typcheck.py`), so it wouldn't exercise the block-tail ambiguity rule at all — it would just fail that unrelated, pre-existing check instead.

- [ ] **Step 1: Write the end-to-end tests**

Create `tests/test_block_stmt_semicolons.py`:

```python
# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_while_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        }
        return i;
    }
    """
    util.check_prog_output(tmp_path, src, "", 10)


def test_if_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        } else {
            2
        }
        return 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 5)


def test_block_expr_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 1;
        }
        return 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_semicolon_still_required_to_discard_tail_value(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 }
        }
        return 0;
    }
    """
    with pytest.raises(errors.WhileTypNotVoidError):
        util.compile_str(tmp_path, src)


def test_semicolon_discards_tail_value(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 };
            return 0;
        }
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

- [ ] **Step 2: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_block_stmt_semicolons.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 3: Run the full suite and static checks**

Run: `uv run pytest -q && uv run ruff format . && uv run ruff check . && uv run basedpyright && uv run reuse lint`
Expected: all pass, no changes needed, REUSE-compliant (new file already has the SPDX header).

- [ ] **Step 4: Commit**

```bash
git add tests/test_block_stmt_semicolons.py
git commit -m "Add end-to-end tests for semicolon-less block-like statements"
```
