<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# `match` Expressions with Exhaustiveness Checking — Design

For: #30: Add match expressions with exhaustiveness checking

## Problem

Leech has no pattern matching. `match` appears in neither `leech.lark` nor
`reserved.KEYWORDS`. This blocks other work rather than merely being missing: tagged unions
(#24) name `match` an absolute prerequisite, because without it there is no safe way to
read a union payload and the alternative — unchecked accessors — gives up the guarantee
that distinguishes a tagged union from an untagged one. Tuples (#29) are deliberately
sequenced behind it so positional pattern support isn't built twice.

The only closed constructor set the language has today is the C-style `enum`: a fixed,
named set of integer discriminants (`typs.EnumTyp.variants`). The sole way to consume one
is `__enum_to_int` plus integer comparison, which is unchecked and discards the fact that
the variant set is finite. `match` makes that consumption checked, and lands the
exhaustiveness machinery #24 and #29 will extend.

## The binding-versus-reference ambiguity, and why `let` dissolves it

Issue #30 records a reduce/reduce collision between `<binding_pattern : ident>` and
`<path : ident>`: a bare identifier in a pattern is genuinely ambiguous — `None` could bind
a fresh variable or reference a unit variant — and no lookahead resolves it. The issue's
proposed resolution was Rust's: drop the binding rule, parse every bare identifier as a
path, and decide binding-versus-reference semantically afterwards. It also flags the
hazard that approach carries: misspelling a variant name silently turns an intended
reference into a catch-all binding.

Requiring `let` on bindings removes the ambiguity at its source instead. `let` is already a
keyword, so `binding_pattern: "let" mut ident` cannot collide with `path`. No semantic
post-pass is needed, and a misspelled variant becomes a hard "not found" error rather than
a silent catch-all — strictly better than the language the syntax is modelled on.

The spelling has direct precedent in Swift, which writes `case .some(let x)`. Surveying the
alternatives:

| Language | Binding | Hazard |
|---|---|---|
| Rust | bare `x` | typo'd variant silently becomes a catch-all; mitigated only by lints |
| Swift | `let x` inside the pattern | none — `let` is unambiguous |
| Scala | lowercase binds, `Uppercase` references | subtle; needs backticks to force a reference |
| Elixir | bare binds, `^x` pins to an existing value | inverted, but explicit on the rarer case |
| Zig | `.a => \|v\|` — capture after the arrow | patterns stay pure; doesn't scale to nesting |
| C# | `Circle c` / `var x` | type-directed |

`let mut x` comes free from reusing the existing `"let" mut ident` shape, and is what
mutable bindings will need once destructuring exists.

## Grammar: verified LALR(1)-clean

`parse.py` builds an LALR parser with `strict=True`, so a grammar addition that introduces
a conflict is a hard build failure, not a silently-resolved ambiguity. The rules below were
spiked against the real `leech.lark` and confirmed conflict-free:

```lark
?block_like_expr: block_expr
  | if_expr
  | while_expr
  | match_expr

match_expr: "match" "(" expr ")" "{" match_arm_list "}"
match_arm_list: _match_arm_list?
_match_arm_list: match_arm ("," match_arm)* ","?
match_arm: pattern "=>" expr

?pattern: or_pattern
?or_pattern: or_pattern "|" primary_pattern
  | primary_pattern
?primary_pattern: wildcard_pattern
  | binding_pattern
  | int_lit_pattern
  | bool_lit
  | path_pattern
wildcard_pattern: "_"
binding_pattern: "let" mut ident
!int_lit_pattern: ["-"] int_lit
path_pattern: path
```

Cases confirmed to parse: `match` in value and statement position, the semicolon-less
statement form, or-patterns, `let`/`let mut` bindings, negative and bool literal patterns,
block-bodied arms, empty `match (x) {}`, a trailing comma, nesting, and `match` as a call
argument. A block-bodied arm with no following comma is correctly rejected.

Three details carry the design:

- **`?block_like_expr` is the extension point.** Joining that category yields statement
  position, the optional trailing semicolon, tail-expression behaviour, and exclusion from
  callee/index/field-access position, all from the existing `_stmts`/`_req_stmts` rules and
  `BlockExpr`'s tail-versus-statement split. The optional-block-statement-semicolons design
  doc already names `match` as the intended next member of this category.
- **The scrutinee is parenthesised.** This matches `if (…)` and `while (…)`, and avoids the
  collision with `brace_expr` (`basic_typ "{" … "}"`) that an unparenthesised `match Foo {`
  would create — the same ambiguity Rust patches with a grammar-level "no struct literal
  here" restriction.
- **`!int_lit_pattern: ["-"] int_lit`** reuses the `enum_variant_value` trick. There is no
  negative-literal token, and `factor`'s unary minus is not reachable from a pattern.

## `_` becomes a reserved word

A `"_"` literal in the grammar creates an anonymous string terminal, and
`test_keyword_set_matches_grammar` derives `KEYWORDS` from every string terminal whose
value `.isidentifier()` — which `"_"` does. So `"match"` and `"_"` both join
`reserved.KEYWORDS` and the drift test stays as written.

Reserving `_` costs less than it appears, because lark's LALR **contextual lexer** only
offers a terminal in parser states that accept it. Verified against the spike grammar:
`let _ = f();`, `enum E { _ }`, `fn f(_: i32)` and even `let match = 1i32;` all still
*parse* — `_` and `match` lex as `IDENT` outside the states that want the keyword. They are
rejected semantically by `reserved.is_reserved`, which is exactly the split
`tests/test_reserved.py` already documents: the lexer catches keyword misuse only
inconsistently, so the semantic check must exist independently.

The five existing `let _ = …;` sites in `tests/test_generic_fns.py` are expected to keep
passing unchanged, because `_check_let_stmt` checks the initializer *before* the
reserved-name check and all five already raise from the initializer. This must be confirmed
during implementation; if any regresses, it becomes a bare expression statement
(`f[i32]();`).

`let _` in pattern position needs no special case: `binding_pattern` runs the same
`reserved.is_reserved` check and produces `ReservedNameError`.

## Decisions

### 1. Bindings are explicit; `_` is the wildcard

`let x` and `let mut x` bind; `_` matches without binding. A bare path is therefore
*always* a reference, never a binding. In the first version a binding can only bind the
whole scrutinee, since no pattern destructures. Bindings copy — Leech is copy-only today,
and binding modes are #34's business.

### 2. Comma between arms is required

`pat => expr,` uniformly, with a trailing comma allowed. Block-bodied arms are covered
because a block is an expression: `pat => { … },`. Rust's rule — comma optional after a
block — reads better for large arms but adds a grammar wrinkle of the same family as the
`_stmts`/`_req_stmts` split. Relaxing this later is backwards-compatible, so it is deferred
to a follow-up issue rather than paid for now.

### 3. Or-patterns ship, without bindings inside alternatives

`A | B => …` is nearly free in a usefulness-based checker: one row expands into several.
Forbidding bindings inside alternatives is what keeps it free — it sidesteps the "every
alternative must bind the same names at the same types" rule entirely. Allowing them later
is backwards-compatible.

### 4. Coverage is computed over discriminant values, not variant names

`enum Alias(u8) { A = 1, B = 1 }` is deliberately legal, "matching C's own enum semantics"
for a C-ABI-facing feature. So:

```leech
match (a) {
    Alias::A => 1i32,   // covers value 1
    Alias::B => 2i32,   // warning: unreachable
}

match (a) { Alias::A => 1i32 }   // exhaustive
```

This is honest about runtime behaviour. The lowering compares integers, so the `Alias::B`
arm genuinely could never execute; reporting it as live code would be a lie, and no
alternative dispatch could make it true for a C-ABI enum. A missing-case witness is named
using the first variant declared with that discriminant.

### 5. Exhaustiveness and unreachability share one algorithm

A new leaf module, `patterns.py`, implements Maranget's usefulness algorithm with a
`Constructor` abstraction carrying an arity:

```python
# Constructors:  VariantCon(discriminant, display_name) | BoolCon(value) | IntCon(value)
# Patterns:      WildPat() | ConPat(con, subpats) | OrPat(alternatives)
# ConSpace:      the complete constructor set for a scrutinee type
#                  enum  -> finite, deduped by discriminant
#                  bool  -> {true, false}
#                  int   -> open (a wildcard or binding arm is required)
#                  never -> empty (so `match (x) {}` is exhaustive)
#                  other -> open (only wildcards match)

def is_useful(matrix, row, space) -> bool: ...
def missing_patterns(matrix, space) -> list[Witness]: ...
```

Both checks fall out of the one algorithm: arm *i* is unreachable iff
`not is_useful(rows[:i], rows[i])`, and the match is exhaustive iff `missing_patterns` is
empty over all rows. A `Witness` renders as a source-like pattern string (`Color::Blue`,
`true`, `_`) for the diagnostic.

Every constructor in the first version has arity 0, so `specialize` and `default` are
degenerate and the matrix is one column wide. Writing the general algorithm anyway costs
roughly a hundred lines over flat per-type coverage sets, and buys the property that struct
patterns, tuples (#29) and tagged unions (#24) *extend* the module rather than replace it —
and those are the issues #30 exists to unblock. The module is pure, importing neither `ast`
nor `typs`, so it is unit-testable in isolation.

The `never` case falls out for free: an uninhabited type has an empty constructor space, so
an empty matrix over it has no missing patterns.

### 6. Non-exhaustive is an error; a redundant arm is a warning

A redundant arm is reported through `errors.register_error` as a warning, alongside the
existing `UnreachableCodeWarning`. This tolerates a defensive trailing `_` after full enum
coverage, and tolerates enum evolution in another module.

A non-exhaustive match is a raised error. Because errors are raised and only one surfaces
per compilation (#20 is still open), the single diagnostic must name *every* uncovered
case, as a variable-length list of notes — the shape `InfiniteSizeStructError` already
uses.

### 7. Lowering is a cbranch chain, with no new IR instruction

Per arm: `EnumToIntInstr` once before the chain when the scrutinee is an enum, then
`IcmpInstr` plus `CbranchInstr` per test, then an N-ary phi at the merge block. This is
structurally identical to `_build_if_expr`, and reuses `_build_if_arm`'s
`(value, last_bb, reaches_end)` contract verbatim.

Needing no new instruction matters more than it first appears. `PhiInstr` is already
N-ary, and `comptime.Interpreter._eval_instr` ends in
`raise AssertionError(f"Invalid instruction {instr}")` — so a new instruction would break
module-level `let x = match (…) {…};` until it also got an arm there. Emitting a real
`switch` is a four-file change (`ir_values.py`, `codegen.py`, `comptime.py`, plus CFG edge
bookkeeping) for a transformation LLVM's SimplifyCFG performs anyway; it is deferred to a
follow-up issue.

The scrutinee is evaluated exactly once, into a value reused by every test. Where the
pattern space is fully covered before the last arm, the final test branches
unconditionally rather than emitting a dead comparison.

### 8. N-arm peer typing, and the visit-order constraint

`_check_if_expr` and `_build_if_expr` duplicate their flexible-int-literal ordering dance
verbatim, because `_set_int_lit_typ` is keyed by AST-node identity and the checker and
builder must agree on what each literal's type was decided to be. Generalised to N arms:

1. Visit every arm whose body is not `is_flexible_int_lit`, collecting types.
2. Call `resolve_peer_typ` over those — it already takes a list, so it needs no change.
3. Visit the flexible arms with that result as the hint.

The checker and the builder must use the identical order. This design factors the ordering
into one shared helper in `typcheck.py` that both call, rather than duplicating it as `if`
does.

`never` absorption follows `if`: a diverging arm is skipped both for the mismatch check and
for the result type, so the match's type is the first non-`never` arm type, or `never` if
all arms diverge. Arms must be exactly equal — there is no unification engine and no
inter-arm coercion.

In statement position there is no void requirement, unlike `if` without `else`:
`ast.ExprStmt` discards a value of any type. Arms must still unify with each other.

## Changes by file

- `src/leech/leech.lark` — add `| match_expr` to `?block_like_expr`; add the `match_expr`,
  arm-list and pattern rules beside `while_expr`.
- `src/leech/reserved.py` — add `"match"` and `"_"` to `KEYWORDS`.
- `src/leech/ast.py` — `MatchExpr` and `MatchArm` nodes, a `Pattern` base with
  `WildcardPattern` / `BindingPattern` / `IntLitPattern` / `BoolLitPattern` /
  `PathPattern` / `OrPattern`, a `Pattern.from_tree` dispatch mirroring `Expr.from_tree`,
  and a `"match_expr": MatchExpr` entry in `Expr._child_ctors`.
- `src/leech/patterns.py` — **new.** Maranget usefulness, constructors, witnesses. Pure.
- `src/leech/typcheck.py` — `_check_match_expr`, `_check_pattern`, the shared arm-ordering
  helper, new `TypCheckResults` tables for the scrutinee type and each arm's resolved
  constructor, and `ast.BindingPattern` added to the local-binding isinstance sites.
- `src/leech/resolve.py` — extend `LocalDecl` with `ast.BindingPattern`.
- `src/leech/ir_builder.py` — `_build_match_expr`, reusing `_build_if_arm` unchanged.
- `src/leech/errors.py` — `NonExhaustiveMatchError`, `UnreachableMatchArmWarning`,
  `MatchArmTypMismatchError`, `PatternTypMismatchError`, `BindingInOrPatternError`,
  `NotAPatternError`.
- Tests — new `tests/test_match.py` and `tests/test_patterns.py`; additions to
  `tests/test_parsing.py`, `tests/test_block_stmt_semicolons.py` and `tests/test_errors.py`.

## Non-goals

Each of the following is deferred, with a filed follow-up issue unless noted:

- Guards (`pat if cond => …`). They defeat exact exhaustiveness: a guarded row must be
  treated as covering nothing, as Rust does.
- Range patterns (`1..=5`). Needs range syntax Leech does not have. This is what would let
  an integer match be exhaustive without a wildcard, which is why `patterns.py` should model
  an integer pattern in a way a range can extend.
- Struct patterns, and `..` rest patterns.
- `@` bindings — only meaningful once destructuring exists.
- Constants as patterns. Needs comptime evaluation of a `ModVar` during type-checking and
  its value folded into coverage. Until then a bare path that is not an enum variant is a
  clean "not a pattern" error.
- Irrefutable patterns in `let`, i.e. destructuring bindings.
- `SwitchInstr` lowering.
- Optional comma after a block-bodied arm.
- **Bindings inside or-pattern alternatives — no issue filed.** This is the restriction that
  makes or-patterns nearly free; lifting it requires the "every alternative binds the same
  names at the same types" rule.

Tuple and tagged-union pattern support is not tracked separately: adding tuple patterns is
part of delivering #29, and union patterns part of delivering #24. The existing `30 --> 29`
and `30 --> 24` edges in the dependency graph already express the sequencing.

Binding modes — whether a pattern binding a payload copies or references it — belong to
#34, which already names its interaction with #30 and #24.
