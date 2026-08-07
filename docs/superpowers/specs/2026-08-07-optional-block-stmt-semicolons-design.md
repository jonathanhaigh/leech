<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Optional Semicolons After Block-Like Statements Design

## Goal

Let the trailing `;` after a statement be omitted when the statement's expression is
"block-like" (`if`/`if`-`else`, `while`, or a bare `{ ... }` block), as long as more code
follows it in the enclosing block. Today l0 requires the `;` unconditionally, e.g.
`while (f()) { do_something(); };`. Existing code that keeps the `;` must keep working
unchanged.

## Scope: block-like expressions

l0 currently has exactly three "expression with a block body" forms: `if_expr`,
`while_expr`, and `block_expr`. These become a named grammar/AST category, "block-like
expression". Any future construct with a block body (`match`, `loop`, `unsafe {}`, ...)
should join this category and inherit the same optional-semicolon treatment without
further grammar surgery.

`struct_expr` also ends in `}` but is a value literal, not a control-flow construct, and
is excluded — its statement-position semicolon stays mandatory.

## Rule

A statement whose expression is block-like may omit its trailing `;` **as long as
something follows it** before the enclosing block's closing `}` — another statement, or a
tail expression. The `;` remains legal everywhere it is legal today; this only relaxes a
requirement.

`let`, `return`, `break`, `continue`, and assignment statements are unaffected: `;` stays
mandatory for these regardless of their operand's shape, matching Rust
(`let x = if (c) { 1 } else { 2 };` still needs the `;`).

The `;` is still required when a block-like expression is the *last* thing in a block and
its value should be discarded rather than become the block's tail value:

```
{ if (x) { 1 } }   // block's value is the if-expression's value (unchanged today)
{ if (x) { 1 }; }  // block's value is void; the if-expression's value is discarded
```

Without the `;` in the second form, there would be no way to express "evaluate this
block-like expression for effect only, as the last thing in a block, and don't let it
determine the block's type" — so this is the one case where the semicolon keeps doing
real work rather than being pure legacy compatibility.

## Grammar

Replace `block_expr`'s body, `stmt* expr?`, with a pair of mutually recursive rules:

- one rule that may end the block (empty, a tail `expr`, a `;`-terminated statement
  followed by more of itself, or a block-like statement followed by the "must continue"
  rule below),
- one rule with the same shape but that must always be followed by more content (so it
  cannot itself end directly at `}`).

A semicolon-less block-like statement is only reachable through the "must continue" path,
so it can never land in the ambiguous position immediately before `}` — that position is
reachable only through the existing tail-`expr` path, preserving today's meaning exactly.

This has been validated against a minimal grammar mirroring l0's structure: it compiles
under Lark's LALR(1) parser (`parser="lalr"`) with no shift/reduce or reduce/reduce
conflicts, and produces the intended parse trees for both the "ends the block" and "more
follows" cases.

`if_expr`, `while_expr`, and `block_expr` are not reachable from `atom_expr`, so they can
never be the target of a call, index, field access, or deref. This means l0 does not have
the "statement-like expression followed by `(` or `[` gets parsed as a call/index"
ambiguity that affects automatic-semicolon-insertion in some other languages; no grammar
work is needed to guard against it.

## AST, typecheck, and codegen

`ast.py`'s `BlockExpr` builder walks the new parse-tree shape but keeps exposing the same
`stmts: tuple[Stmt, ...]` / `expr: Optional[Expr]` interface, so nothing outside `ast.py`
needs to change. The semicolon-less block-like statement reuses the existing `ExprStmt`
AST node. `typcheck.py`'s `_check_stmt` already discards an `ExprStmt`'s value regardless
of its type, so no typecheck or codegen changes are needed.

## Testing

Update `tests/test_parsing.py`'s parse-tree assertions for the new grammar shape, and add
coverage for:

- a block-like statement with the `;` omitted, followed by another statement — tested
  separately for `if_expr`, `while_expr`, and a bare `block_expr` (not just one
  representative form),
- the same with the `;` still present (still valid), again for each of the three forms,
- two block-like statements back-to-back with no separator between them,
- `;` required to force discard-at-tail-position (`{ if (x) { 1 }; }` typechecks as void),
- the negative case: `{ if (x) { 1 } }` (no `;`) still means the block's value is the
  `if`-expression's value, unchanged from today.

Add parser-rejection tests confirming the `;` is still mandatory (omitting it is a parse
error) for every excluded statement category, including when its operand/initializer is
itself block-like:

- `let x = if (c) { 1 } else { 2 }` (missing `;`),
- `return if (c) { 1 } else { 2 }` (missing `;`),
- `break`/`continue` (missing `;`),
- an assignment whose right-hand side is block-like, e.g. `x = if (c) { 1 } else { 2 }`
  (missing `;`),
- a `struct_expr` in statement position, e.g. `Foo { x: 1 }` (missing `;`).

## Non-goals

- `let`/`return`/`break`/`continue`/assignment statements do not gain optional
  semicolons, even when their operand is block-like.
- `struct_expr` statement semicolons are unaffected.
- No new block-like construct (`match`, `loop`, etc.) is being added by this change; the
  "block-like expression" category is just named so a future one slots in cleanly.
