<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# TypCheckResults as the Single Source of Truth for Lowering — Design

For: #70: Make `TypCheckResults` the single source of truth for lowering

> Issue #70 was filed as "Reconsider passing expected-type hints through `ir_builder`".
> This design widens it to the whole checker/lowerer boundary, so every commit below can
> reference one issue. Retitling #70 is part of the work (see the implementation plan's
> Task 0).

## Problem

`typcheck.py` and `ir_builder.py` are supposed to be a checker and a lowerer: the checker
decides, records its decisions in `TypCheckResults` keyed by AST-node identity, and the
lowerer reads them. `ir_builder`'s module docstring already claims exactly that — *"Lowering
trusts and reuses the decisions recorded in `TypCheckResults`"*.

That is not what the code does. `CfgBuilder` re-runs pieces of the checker's own algorithms:

| Site | What lowering re-derives | What the checker already knew |
|---|---|---|
| `_build_expr` and six more methods | an `expected_typ` hint threaded through the whole expression walk | every literal's type, recorded per node |
| `_build_if_expr`, `_build_match_expr`, `_build_bin_op_expr` | which operand/arm to lower first, via `typcheck.is_flexible_int_lit`, `resolve_peer_typ` and `match_arm_check_order` | the peer type it settled on |
| `_build_match_expr` | `typcheck.match_constructor_space` and a per-arm `patterns.is_useful` loop | the identical space and usefulness results, computed for the unreachable-arm warning |
| `_build_match_tests` | `patterns.missing_patterns(rows[:i], space)` per arm | — (genuinely lowering-only, but it is still pattern analysis in the lowerer) |
| `_build_let_stmt`, `_build_match_binding`, `build_fn` | each local's storage type and mutability, from the lowered initializer's type | `TypCheck._local_typs`, which is never recorded |
| `_build_unary_op_expr` | that `-<int literal>` folds into one constant, via `isinstance(operand, ast.IntLit)` | the same test, in `_check_neg_expr`, where the folded value was range-checked |
| `_build_if_expr` | the `if`'s result type, from the two arms' lowered values | the peer type it settled on |
| `_build_call_expr` | `param_typs` off the callee value's type | the parameter types it checked the arguments against |

Three things follow. The lowerer imports the checker's *algorithms*, not just its *data*, so
the boundary is a convention rather than a structure. The two phases can disagree, and
already do (below). And the checker's visit order is load-bearing for the lowerer — a
coupling nothing enforces and nothing tests.

### The two phases already disagree

For

```leech
pub fn main() i32 {
    let mut a = 5i32;
    let p: *i32 = &a;
    return p.*;
}
```

`TypCheck._check_let_stmt` binds `p` at the declared type `*i32`. `CfgBuilder._build_let_stmt`
allocates storage for `expr.typ`, and `expr` is the *coerced* initializer — but the coercion
is `PtrMutRelax`, which by design emits nothing and returns the value untouched, so `expr.typ`
is still `*mut i32`. The checker and the lowerer hold different types for the same local.
(Verified by instrumenting `_build_let_stmt` to compare the two.) Nothing observable breaks
today, because LLVM erases mutability and every later decision about `p` is read from
recorded facts — but that is luck, not design.

### Why now

The pattern-matching issues queued behind #30 make the match half of this actively dangerous.
Guards (#62) invalidate the "this arm covers everything remaining, so branch unconditionally"
shortcut that `_build_match_tests` derives on its own; a guarded arm must be treated as
covering nothing for coverage purposes while still being tested. Ranges (#63) turn
`IntConstructor` into a range and change what "covers the remaining witnesses" means. Struct
patterns (#65) give constructors arity, so `_build_match_binding`'s single top-level
`BindingPattern` test stops being sufficient. Each of those is a change that must land
identically in two places for as long as both phases compute coverage. That is the argument —
it is about removing duplication, not about the recorded plan already being the right shape
for those features. It is not (see Risks).

## Goals

- Lowering reads recorded facts and never re-runs a checking algorithm.
- No phase after type checking distinguishes a "flexible" (unsuffixed) integer literal from a
  fixed one, or knows what peer type resolution is.
- Lowering is independent of the order the checker visited things in; it walks the AST in
  source order.
- Cross-phase disagreement is caught by an assertion rather than discovered by a miscompile.
- The boundary is structural: `ir_builder` does not import `typcheck` at all, and reaches into
  `patterns` only for the constructor dataclasses it must dispatch on.

## Non-goals

- Changing any accepted or rejected program, except the one bug fix noted below (#69).
- Changing generated LLVM IR. Tasks 1–4 and 6–7 are output-preserving; only the #69 fix
  changes emitted code, and only for a program that currently crashes the compiler.
- Introducing a typed IR between the AST and the CFG. That alternative is discussed and
  explicitly deferred.
- Reworking how comptime arguments are inferred (see "Deferred: check each argument once").
- Adding a `SwitchInstr` (#68). This design makes that easier but does not do it.

## Finding: the `expected_typ` hints are dead code

Issue #70 asks whether the hints are necessary. They are not — not merely redundant, but
never read.

`expected_typ` is a parameter of seven methods — `_build_expr`, `_build_block_expr`,
`_build_if_expr`, `_build_if_arm`, `_build_match_expr`, `_build_bin_op_expr` and
`_build_unary_op_expr` — and a non-`None` value is supplied at eight call sites (`build_fn`,
`_build_ret_stmt`, `_build_call_expr`, `_build_array_access_expr`, `_build_struct_lit_expr`,
`_build_array_lit_expr`, `_build_assignment_stmt` and `_build_let_initializer`). It is never
*consumed*. The only two leaves that could consume it already read recorded facts and ignore
it:

- `ast.IntLit` → `_build_int_lit` → `TypCheckResults.int_lit_typ(lit_ast)`
- `ast.BraceExpr` → `_build_brace_expr` → `TypCheckResults.brace_expr_typ(brace_expr)`

So the hint's entire remaining effect is that three methods branch on whether it is `None`,
to decide which operand or arm to lower first.

**Measured.** On a scratch copy of the tree at `2cb0d4b`, every trace of `expected_typ` was
deleted — the parameter, all pass-throughs, and all three peer-ordering branches, leaving
`if`/`else`, `match` arms and binary operands lowered in source order:

- `uv run pytest` — 1299 passed, nothing failed, nothing reported as skipped or xfailed.
- Generated `.ll` was byte-identical for programs exercising if-peer resolution, match-peer
  resolution, and both with nested control flow inside the arms.

That is a targeted corpus, not the whole language; the implementation plan turns it into a
standing before/after check over a much wider one.

### Why removing the ordering coupling is sound

The user question behind this design was whether lowering may ignore the order the checker
worked in. It may, for two independent reasons.

**Nothing side-effecting is ever reordered.** The reorder only ever defers an expression for
which `typcheck.is_flexible_int_lit` is true: a bare `ast.IntLit`, a negated one, or an empty
`ast.BlockExpr` wrapping one. Those emit no instructions and allocate no basic blocks. The
existing comments in `_build_bin_op_expr` and `_build_if_expr` say as much, and that is
exactly the invariant that makes today's code correct.

**The CFG that comes out is the same object graph.** Since the deferred expression creates
no basic block, the reorder changes no block, no block *name* (names come from
`CfgBuilder._generate_bb_name` at `_add_bb` time, so they would otherwise be
creation-ordered), no edge and no instruction. Only the wall-clock order in which those
identical blocks and edges are created changes.

That distinction matters because `Cfg` is a `networkx.DiGraph`, whose per-node adjacency
order *does* follow edge-insertion order — so "codegen sorts the blocks anyway" is not on its
own a sufficient argument. It holds here for a specific reason: each block's *outgoing* edges
are added by the `_branch`/`_cbranch` calls belonging to that block's own lowering, and the
reorder does not move those relative to each other. An `if`'s two successors are added by one
`_cbranch` in a fixed true-then-false order before either arm is lowered; a match's arm blocks
are made successors of the test blocks by `_build_match_tests`, which runs before any arm body
is lowered; and each arm block has exactly one outgoing edge, to the merge block. So every
node's successor order is identical under either arm order.

Given an identical graph with identical adjacency, `codegen._compile_fn_instance` emits
blocks in reverse postorder from `cfg.entry`
(`reversed(list(nx.dfs_postorder_nodes(inst.cfg, inst.cfg.entry)))`), which is then identical
too. Both predictions held: the emitted IR was byte-identical.

Removing the coupling is therefore not merely safe, it is a strict improvement. Today's swap
is correct only because of the side-effect-freedom invariant, which nothing states, tests, or
enforces; the first time the checker needs to defer an expression that *can* have side
effects, a lowerer that mirrors the checker's order would silently reorder evaluation.
Lowering in source order cannot acquire that bug.

## Design

### The rule

> If type checking computes it, type checking records it, and lowering reads it.
> Lowering computes only what is about emitting instructions: basic blocks, terminators,
> phi placement, and the instruction sequence itself.

### The facts vocabulary

`TypCheckResults` gains four kinds of fact. It keeps `coercion`, `generic_call`,
`generic_var_ref`, `brace_expr_typ`, `struct_field_index`, `let_declared_typ`,
`match_scrutinee_typ` and the nested `resolve.Resolutions`; `int_lit_typ` is subsumed by
fact 4 below, and `match_arm_pattern` and `match_arm_constructors` are subsumed by fact 3.

**1. Expression and place types.** Two partial maps:

```python
def expr_typ(self, node: ast.Expr) -> Optional[typs.Typ]: ...   # written by _check_expr
def place_typ(self, node: ast.Expr) -> Optional[typs.PtrTyp]: ...  # written by _check_place
```

Both are partial and a node may appear in either, both, or neither. `_check_place` returns
early for an `ast.VarExpr` without ever calling `_check_expr` on it, so a variable used only
as a place (`&a`, the receiver in `a.f()`, the base in `a[i]`) has a `place_typ` and no
`expr_typ`. That asymmetry is real and the two maps record it faithfully rather than papering
over it.

`CfgBuilder._build_expr` checks the value it produced against whichever map matches its
`_ExprContext`, after substituting this instance's comptime arguments:

```python
def _build_expr(self, expr_ast: ast.Expr, ctx: _ExprContext) -> ir_values.Value:
    value = self._build_expr_uncheck(expr_ast, ctx)
    self._assert_recorded_typ(value, expr_ast, ctx)
    return value
```

**The check is one-directional, and must be.** Lowering may legitimately produce a `never`
value where the checker recorded a concrete type, because divergence is discovered
structurally during lowering. For `g() + 1i32` with `g() never`,
`TypCheck._check_bin_op_expr` returns `i32` (`never` stands in for either operand's value
type), while `CfgBuilder._build_bin_op_expr` hits `_propagate_never` on the left operand and
yields a `NeverValue`. So the assertion is

```
value.typ == typs.NEVER  or  value.typ == recorded.substitute_typ_params(mapping)
```

with the `PLACE` form comparing `PtrTyp`s and exempting a `never` pointee. This is sound:
a `never`-typed value means control cannot reach the point where a value would be read.

`asserts.assert_eq` and friends compile to bare `assert` statements, so these checks cost
nothing under `python -O`.

**2. Local binding types.** `TypCheck._local_typs` — a `PtrTyp` carrying both the bound type
and the binding's mutability, already computed for `ast.Param`, `ast.Receiver`, `ast.LetStmt`
and `ast.BindingPattern` — is recorded:

```python
def local_typ(self, decl: resolve.LocalDecl) -> typs.PtrTyp: ...
```

`_build_let_stmt`, `_build_match_binding` and `build_fn` allocate from it instead of from the
lowered initializer's type and a re-read of `mut`. This is what fixes the `*mut i32` /
`*i32` disagreement above, and it is a prerequisite for the `place_typ` assertion rather than
an optional extra: with the divergence in place, the assertion fails.

**Doing this exposes a second defect, which has to be fixed with it.** `_coerce`'s
`PtrMutRelax` case returns the value *untouched* — that is the whole point of the coercion,
since `*mut T` and `*T` share a representation. So the coerced initializer's `typs` type is
still `*mut i32` while the recorded local type is `*i32`. Today that is invisible because the
alloca takes the value's type, so the two agree by construction. Allocate from the recorded
type instead and `StoreInstr.__init__`'s `asserts.assert_eq(value.typ, dest_typ.pointee_typ)`
fails: the regression test for this very divergence would crash rather than pass. The same
applies to `_build_assignment_stmt`, which stores into a place whose pointee now comes from
the recorded type.

`StoreInstr` is the only site affected. Auditing every type-equality assertion in
`ir_values.py`: `CallInstr` does not check arguments against parameter types at all;
`BinOpInstr`'s operand check and `PhiInstr`'s incoming-value check are not on a coercion path
that can produce this mismatch; `ComptimeArray`'s element check and `ComptimeStruct`'s field
check run at construction, where every value is a freshly-built `UndefValue` of the declared
type — the *coerced* values arrive later through `insert_value`, which asserts nothing.

The resolution is to give `_coerce` a representation-preserving retype value — a
`PtrMutRelaxInstr` with a comptime counterpart — so the IR's Leech type tracks the coercion.
The two pointer types share an LLVM representation, so codegen emits nothing for it, and
`StoreInstr` keeps its strict `asserts.assert_eq(value.typ, dest_typ.pointee_typ)`, which
continues to report both types on a genuine mismatch.

**3. The match plan.** A `match` expression's whole pattern analysis is done once, in
`patterns.py`, and recorded:

```python
@dataclasses.dataclass(frozen=True)
class ArmTest:
    """One arm's entry in a match's test chain."""

    arm_index: int
    constructors: tuple[ConstructorKind, ...]


@dataclasses.dataclass(frozen=True)
class MatchPlan:
    """How to lower one match, and what its arms cover."""

    tests: tuple[ArmTest, ...]
    reachable_arms: tuple[int, ...]
    missing: tuple[Witness, ...]


def build_match_plan(rows: Sequence[PatternKind], space: ConstructorSpace) -> MatchPlan: ...
```

`reachable_arms` is exactly today's `reachable_indices`, computed by the same `is_useful`
formula — so the checker's unreachable-arm warnings and the lowerer's block selection stop
being two computations that happen to agree. `missing` is today's
`missing_patterns(rows, space)`; `TypCheck._check_match_expr` raises
`NonExhaustiveMatchError` when it is non-empty and records the plan only for a match that
passed, so a *recorded* plan always has `missing` empty. The field stays on the returned
value because it is part of the one analysis, not a separate call.

The dataclasses alone do not pin the lowering down, so the invariants are part of the
contract, asserted in `build_match_plan` and tested directly:

- `tests` contains **exactly one** `ArmTest` for every arm in `reachable_arms`, in ascending
  `arm_index` order, and none for any other arm.
- `constructors == ()` means the arm is entered **unconditionally**, and such an entry is
  always the last in `tests`. An arm that needs no test and an arm with zero constructors are
  the same thing, so `build_match_plan` never emits a non-final empty entry.
- An or-pattern with a wildcard alternative yields `constructors == ()`, matching today's
  `_match_arm_is_wildcard`, which treats *any* wildcard alternative as covering everything.
- `tests == ()` means no arm is reachable at all, and lowering branches straight to the merge
  block.

Two consequences are worth stating, because getting them wrong is easy.

`tests` is never a *proper* prefix of `reachable_arms`. Once an arm is entered
unconditionally — a wildcard arm, or one for which `_match_arm_covers_remaining` holds — every
later arm is by construction not useful, so it is not in `reachable_arms` at all. The arms
after it are unreachable arms whose bodies are still lowered, not reachable arms lacking a
test. An implementation that truncates `tests` under some "already covered" condition is
solving a problem that cannot arise.

The unconditional final entry is guaranteed for any recorded plan. A match that reaches
lowering is exhaustive: over a closed space that means the last reachable arm covers the
remainder, and an open space (integers) can only be exhausted by a wildcard — either way the
final test is unconditional. The zero-reachable-arm case (a `never` scrutinee) goes through
`tests == ()` instead. Lowering asserts this guarantee instead of carrying it as a field: it
walks `plan.tests`, breaks at the unconditional entry, and asserts the block it ended in is
already terminated, so a non-exhaustive plan reaching lowering fails loudly rather than
silently falling off the end of the test chain.

**Unreachable arms' bodies must still be lowered.** Today `_build_match_expr` lowers every
arm's body while emitting tests only for the reachable ones; the unreachable blocks end up
with no predecessors and codegen's DFS from `entry` never emits them. Skipping their lowering
would look like a free optimisation and would change output: `_add_bb` draws names from a
per-basename counter (`naming.VarNamer`), so blocks an unreachable body would have created
shift the names of every block created after it. Lowering all bodies is therefore a
requirement of this change, not an accident of it.

The constructor-candidate extraction moves too. `ArmTest.constructors` is what
`TypCheckResults.match_arm_constructors` produces today via
`typcheck._pattern_constructor_candidates`; since `patterns.py` cannot import `typcheck`,
that logic becomes a private helper inside `patterns.py` and both public entry points
disappear.

`patterns.py` stays a pure leaf module: `ArmTest` and `MatchPlan` name arm *indices* and
`ConstructorKind`s, and import neither `leech.ast` nor `leech.typs`.

`CfgBuilder._build_match_expr` then walks `plan.tests`, emitting one comparison chain, and
`_build_match_tests`, `_match_arm_is_wildcard`, `_match_arm_covers_remaining` and
`TypCheckResults.match_arm_constructors` (with its `_pattern_constructor_candidates` helper)
all disappear. `match_scrutinee_typ` stays: `_match_case_value` still needs the *substituted*
scrutinee type to give a `ComptimeInt` case value its type.

`ir_builder` keeps importing `patterns`, for the constructor *vocabulary* alone —
`_match_case_value` dispatches on `VariantConstructor` / `BoolConstructor` /
`IntConstructor` to build the value it compares against. What it stops doing is calling any
function in the module. That is the same line rustc draws (below): the constructor concept is
shared, the analysis is not.

This also closes a latent generic-instance hazard. Today `ir_builder` computes the
constructor space from the scrutinee type *after* comptime-argument substitution, while
`typcheck` computes it *before*. For a scrutinee of type-parameter type the checker sees an
open space and the lowerer, in a concrete instance, could see a closed one. It cannot bite
today — a pattern can only name a constructor of a type the declaration can name, so a
type-parameter scrutinee admits only wildcards and bindings — but the two phases are
computing different things from different inputs and calling the answers the same. With one
recorded plan there is one answer, computed once, in the declaration's own terms.

**4. Folded integer literals.** `_check_neg_expr` already tests `isinstance(op_ast.operand,
ast.IntLit)`, computes `-operand.value`, and range-checks it; `_build_unary_op_expr` repeats
the `isinstance` test and re-derives the sign through `_build_int_lit`'s `negated` parameter.
Recording the constant that was actually checked, rather than only its type, replaces
`int_lit_typ`:

```python
def folded_int_lit(self, node: ast.IntLit | ast.UnaryOpExpr) -> Optional[tuple[typs.IntTyp, int]]: ...
```

keyed by the `ast.IntLit` for a positive literal and by the `ast.UnaryOpExpr` when a unary
minus folded into it. Lowering builds a `ir_values.ComptimeInt` from it directly;
`_build_int_lit` and its `negated` parameter disappear along with the `isinstance` special
case. A literal's written value and its negation stop being two independently-derived
numbers.

The lookup key and the attribution node are deliberately different for the negated form. The
fact is keyed by the `UnaryOpExpr`, because that is the node whose *value* was checked, but
the emitted `ComptimeInt` keeps today's attribution to the operand `ast.IntLit` — that is what
`_build_int_lit(op_ast.operand, True)` does now, and changing it would move a diagnostic span
for no reason.

One loose end goes with it. `_check_int_lit_pattern` records an `int_lit_typ` for the
literal inside an integer pattern, but nothing reads it — `_build_match_compare` works from
the plan's `IntConstructor` and the substituted scrutinee type, and a pattern literal is
never lowered as an expression. Reading the code says it is write-only; the implementation
confirms that and drops it rather than porting a dead record to the new map.

### Recording discipline

Recording more facts makes overwriting them more dangerous, and `_check_expr` is called more
than once on the same node today:

- `_check_place` calls `_check_expr(expr_ast, …)` and then `_place_mut(expr_ast, …)`, which
  re-checks `expr_ast.value` / `expr_ast.ptr` for the `FieldAccessExpr` and `DerefExpr` cases.
- `_check_assignment_stmt` checks the place and then calls `_place_mut` on it.
- `_infer_comptime_args` checks each non-flexible argument with **no** expected type, purely
  to infer the comptime arguments; `_check_call_expr` then re-checks the same argument nodes
  against the resolved parameter types.

The first two re-visits are idempotent. The third is not: it is a speculative probe, and it
genuinely records a type the authoritative pass then overwrites. The shape needs two
ingredients — an argument that is not itself a flexible literal (or the probe would skip it)
but *contains* flexible literals with no fixed peer among them, positioned after the argument
that binds the comptime parameter:

```leech
fn f[T](x: T, y: T) T { return x; }
pub fn main() i32 {
    let z = f(5i64, 1 + 2);
    return 0i32;
}
```

The probe binds `T := i64` from `5i64`, then checks `1 + 2` with **no** expected type: neither
operand is fixed, so both literals record `i32`. `_check_call_expr` then re-checks the same
nodes against the resolved parameter type `i64` and records `i64` over them. Compiling this
today emits `llvm.sadd.with.overflow.i64(i64 1, i64 2)`, confirming the second write is the
one that survives — today's map is correct only because the authoritative pass happens to run
second.

The enclosing function matters: at module level, `let z = …` runs through
`comptime.Interpreter` and folds to a single constant, so the two literals never appear in the
emitted IR and the observation would not be visible.

So `TypCheck` gets an explicit recording flag:

```python
@contextlib.contextmanager
def _speculative(self) -> Iterator[None]:
    """Check without recording context-dependent facts: probes, not decisions."""
```

`_infer_comptime_args` wraps its probe in it, and every setter then asserts that a re-record
agrees with what is already stored. That converts "correct by visit order" into "correct, and
checked". Two details are load-bearing.

**Only *context-dependent* facts are suppressed.** A generic argument can be an arbitrary
expression, including one that introduces bindings — `f({ let y = 1i32; y })` compiles today.
If the probe suppressed `local_typ`, the block's own tail expression would immediately try to
read the binding it just failed to record, and the checker would raise `KeyError` during
inference. A match binding used in its arm body fails identically. So the split is:

| Suppressed during a probe | Always recorded |
|---|---|
| `expr_typ`, `place_typ`, `coercion`, `folded_int_lit`, `brace_expr_typ`, `let_declared_typ`, `struct_field_index`, `match_plan`, `match_scrutinee_typ` | `local_typ`, everything in `resolve.Resolutions` |

The dividing line is whether the fact can differ between the probe and the authoritative pass.
Anything derived from the expected type can. `local_typ` cannot: a `let`'s bound type comes
from its own annotation or its own initializer — `_check_let_initializer` passes `declared_typ`
down, never the enclosing context's expected type — and a `BindingPattern`'s comes from the
scrutinee, which is always checked with no expected type. `resolve.Resolutions` is likewise
context-free and `_resolve_var` already memoizes it deliberately. These are checker-environment
facts, not lowering decisions, and the checker needs them mid-probe.

**`_speculative()` must save and restore the previous flag, not unconditionally re-enable it.**
A probe can contain a nested generic call, which opens a nested `_speculative()` scope; an
inner scope that sets the flag back to "recording" on exit would silently re-arm recording for
the remainder of the outer probe.

### Marker coercions need value equality

The re-record assertion has a prerequisite. `NeverDiverge` and `IntExt` are frozen dataclasses
and compare by value, but `PtrMutRelax` and `Invalid` are plain classes, so
`PtrMutRelax() == PtrMutRelax()` is `False`. `_record_coercion` builds a fresh marker on every
visit, so the ordinary idempotent re-visits above would fail an equality assertion for a
semantically identical write — reachable through any repeated place check whose subexpression
carries a mut-to-const coercion, such as `&make(&a).x` where `make` takes a `*i32` and `a` is
`mut`, which `_check_place` checks once via `_check_field_access_expr` and again via
`_place_mut`.

Making the two markers frozen dataclasses like their siblings fixes it and makes the hierarchy
uniform. Class patterns in `_coerce`'s `match` are unaffected.

### Two pre-existing defects this work uncovers but deliberately does not fix

Working out what `_speculative` should suppress surfaced two live bugs. Both predate this
design; both come from the same double visit; neither is fixed here.

**The probe emits duplicate diagnostics.** A `match` inside an inferred generic argument
reports each unreachable arm twice, because `errors.register_error` runs on both visits:

```leech
enum E { A, B }
fn f[T](x: T) T { return x; }
let z = f(match (E::A) { E::A => 1i32, _ => 2i32, _ => 3i32 });
```

compiles with the same `match arm is unreachable` warning printed twice.

**The probe rejects valid programs.** Worse, an error raised during the probe aborts
compilation even when the authoritative pass would not have raised it:

```leech
fn f[T](x: T, y: T) T { return x; }
let z = f(5i64, 3000000000 + 0);
```

is rejected with `Integer literal 3000000000 does not fit in type "i32"`. The parameter type
resolves to `i64`, in which the literal fits fine — but the probe checks the argument with no
expected type, infers `i32`, and raises. This is a valid program the compiler refuses.

`_speculative` suppresses **fact recording only**. Diagnostics behave exactly as they do
today, so this design leaves both bugs standing, and Task 4 asserts no diagnostic change. That
is deliberate: this document's compatibility contract is that no accepted or rejected program
changes except #69, and fixing either bug would break that.

The natural fix, for whoever takes it: treat a probe failure the way `_infer_comptime_args`
already treats a flexible literal — as an argument contributing no inference information —
and suppress diagnostics for the duration of the probe, letting the authoritative pass produce
the real one. It needs its own issue, its own tests, and a decision about what happens when
*every* argument fails to probe. It should not be smuggled into a refactor.

### Where the facts live

`TypCheckResults` and the `Coercion` hierarchy (`NeverDiverge`, `PtrMutRelax`, `IntExt`,
`Invalid`) move out of `typcheck.py` into a new `check_results.py`, beside the existing
`resolve.py`. After the move:

```
resolve.py         name-resolution facts (Resolutions, LocalDecl, VarTarget)
check_results.py   TypCheckResults, Coercion + subclasses; imports resolve, patterns, typs
typcheck.py        TypCheck — the checker; imports check_results
ir_builder.py      CfgBuilder — the lowerer; imports check_results
```

`ir_builder` stops importing `typcheck` entirely, and keeps `patterns` only for the
constructor and pattern dataclasses. That is the point: the boundary becomes something the
import graph states, not something a reviewer has to notice. `typcheck`'s remaining helpers —
`is_flexible_int_lit`, `resolve_peer_typ`, `match_arm_check_order`,
`match_constructor_space` — have no callers outside the module once the lowerer stops using
them, so they become private. "No phase after type checking considers fixed versus flexible
literals" then holds because there is nothing left to import.

The residual `patterns` dependency is data-only: `ir_builder` imports `patterns` solely for
the constructor and plan dataclasses it must dispatch on, and calls no function in the module.

`check_results.py` keeps the `TYPE_CHECKING`-only `ir_module` import that `TypCheckResults`
needs today for `FnSymbol`, so the module cycle situation is unchanged. This chips at #53
without claiming to resolve it.

### #69 falls out

`_build_if_expr` builds a phi over the two arms' values whenever both reach the merge block.
When both arms are void that is a phi over two `VoidValue`s, and codegen asserts
(`a void value should never need a runtime representation`). `_build_match_expr` already
avoids this by returning a fresh `VoidValue` when every incoming value is void.

Once `_build_if_expr` reads the recorded result type instead of deriving one from the arms,
the void case is a check on that type rather than a special case bolted on:

```python
if recorded_typ == typs.VOID:
    self._set_position(end_bb)
    return ir_values.VoidValue(if_ast)
```

Fixing #69 separately would mean writing the ad-hoc guard and then deleting it, so it lands
here — as its own commit, since it is the one behaviour change in this work.

## How other compilers draw this line

Leech's `TypCheckResults` is a **side table**: facts keyed by AST-node identity, written by
the checker and read by the lowerer, with the AST itself unchanged. That is one of two
mainstream designs.

**rustc — side table, then a typed tree.** `TypeckResults` is the side table, keyed by
`HirId`; types, adjustments and method resolutions are written into it during HIR type
checking. But rustc does not then lower from HIR plus the table. It builds
[THIR](https://rustc-dev-guide.rust-lang.org/thir.html), *"a lowered version of the HIR where
all the types have been filled in, which is possible after type checking has completed"*, in
which *"automatic references and dereferences are made explicit, and method calls and
overloaded operators are converted into plain function calls"*. MIR building consumes THIR.
The side table is an intermediate, not the interface lowering reads.

Rust's integer literals are the direct analogue of Leech's flexible literals: they get
inference variables during type checking, defaulting to `i32` if unconstrained, and are fully
resolved by the time typeck ends — *"partially inferred terms with inference variables don't
appear in the final `TypeckResults`"*. Nothing after type checking knows a literal was ever
unsuffixed. That is the property this design adopts.

Match is the interesting comparison, because rustc does **not** hand its lowerer a plan.
Exhaustiveness *"is run before MIR building in `check_match`"*, in the
[`rustc_pattern_analysis`](https://rustc-dev-guide.rust-lang.org/pat-exhaustive-checking.html)
crate; MIR match lowering then builds its own decision tree from THIR patterns using separate
`Candidate` / `TestKind` machinery, and the dev guide notes the constructor concept *"can be
found in other places of the compiler, like in the MIR-lowering of a match expression"*.
So rustc duplicates the *constructor* vocabulary while running *usefulness* exactly once.

That is a real counter-precedent for recording a full plan, and it is worth being precise
about what it does and does not license. rustc's split is checking-work-once,
lowering-work-once — and Leech's problem is that `_build_match_expr` re-runs
`is_useful` and rebuilds the constructor space, which is checking work. Copying rustc exactly
would fix that much and leave the test-chain derivation in `ir_builder`. Recording the whole
plan goes one step further, and the justification is Leech-specific rather than borrowed:
rustc's MIR lowering derives its tree from THIR patterns that are already concrete and
already checked, whereas Leech's lowerer derives its tree from the *substituted* scrutinee
type, which is a different input from the one the checker used. Add guards (#62), where a
guarded arm must count as covering nothing for coverage and still be tested, and the two
derivations stop being able to agree by construction. One recorded plan is the cheaper
invariant for a compiler this size.

**Swift — rewrite the tree.** Semantic analysis *"transform[s] the parsed AST into a
well-formed, fully-type-checked form of the AST"*; the constraint solver's solution is
applied to the expression by `CSApply`, and
[SILGen](https://www.swift.org/documentation/swift-compiler/) *"lowers the type-checked AST"*
without re-solving. Integer literals are protocol-based (`ExpressibleByIntegerLiteral`) with a
default type when the context does not constrain them — the same "context decides, then it's
concrete" shape.

**Clang — rewrite the tree, minimally.** Sema inserts
[`ImplicitCastExpr`](https://clang.llvm.org/doxygen/classclang_1_1ImplicitCastExpr.html)
nodes, which exist to *"allow explicit representation of implicit type conversions, which
have no direct representation in the original source code"* and *"represent only semantics in
the AST"*. CodeGen walks a tree in which every conversion is already a node and re-decides
nothing. C has no context-typed literals at all: a literal's type comes from its spelling and
value, so the question never arises.

**Zig — analysis produces the typed IR.** AstGen lowers the AST to untyped ZIR; Sema walks
ZIR, runs [peer type resolution](https://ziglang.org/documentation/master/) and comptime
evaluation, and emits AIR, which is typed. `comptime_int` — *"only allowed for comptime-known
values; the type of integer literals"* — is a type that must be coerced away before AIR
exists. Zig's result-location semantics is its version of an expected-type hint, and it lives
in AstGen and Sema; the backends see AIR, where every type is settled.

**The common shape.** All four make lowering read a representation in which the checker's
decisions are already materialised, and none re-derives a literal's type or a peer type
downstream of checking. They differ in *how* the decisions are materialised: a rewritten
typed tree (Clang, Swift, Zig, and rustc's THIR) or a side table (rustc's `TypeckResults`).
Leech uses a side table, and this design keeps it — see the alternative below.

Sources: [THIR](https://rustc-dev-guide.rust-lang.org/thir.html) ·
[exhaustiveness checking](https://rustc-dev-guide.rust-lang.org/pat-exhaustive-checking.html) ·
[`TypeckResults`](https://doc.rust-lang.org/nightly/nightly-rustc/rustc_middle/ty/typeck_results/struct.TypeckResults.html) ·
[`rustc_mir_build::build::matches`](https://doc.rust-lang.org/nightly/nightly-rustc/rustc_mir_build/build/matches/index.html) ·
[Swift compiler overview](https://www.swift.org/documentation/swift-compiler/) ·
[`clang::ImplicitCastExpr`](https://clang.llvm.org/doxygen/classclang_1_1ImplicitCastExpr.html) ·
[Zig language reference](https://ziglang.org/documentation/master/).
Quoted text is from those pages; the comparisons drawn from them are this document's own.

## Alternatives considered

### Lower from a typed IR instead of growing the side table

The uniform `expr_typ` / `place_typ` maps are the side-table approach taken to its limit:
every expression gets a recorded type. Past that point, a typed tree — Leech's own THIR — is
arguably cheaper. It would make types and coercions structural instead of looked-up, remove
the AST-node-identity keying entirely, and give #68's `SwitchInstr` work something better to
consume.

Rejected for now, for three reasons. It is a much larger change than anything here and would
block the pattern-matching queue behind it. Leech's side table is small and, once this work
lands, complete and asserted — the argument for a typed tree gets *stronger* with the maps in
place, not weaker, because the maps make the missing structure obvious. And a typed IR is a
decision about the compiler's shape that deserves its own issue and its own design, not a
paragraph inside a cleanup. Recorded here so it is not silently re-opened: if the side table
starts needing facts that are really *nodes* (an explicit coercion node, a desugared method
call), that is the signal to revisit.

### Keep the hints, but make them read from `TypCheckResults`

Issue #70 offers this as one option: have lowering read pre-recorded expected types rather
than replay the traversal. Rejected — it preserves a parameter no leaf reads. The measurement
above shows the hint is not merely re-derivable, it is unused; recording a value nothing
consumes is worse than deleting it.

### Record only where lowering demonstrably re-derives

A targeted version — record the `if`/`match` result types and the local binding types, leave
everything else to the values lowering already builds. Smaller, and it fixes both known
divergences. Rejected in favour of the uniform maps because the targeted version cannot
*find* the divergences it does not already know about: the whole value of the assertion is
that it runs on every expression in the test suite. The cost is two dicts sized by AST and an
assertion that is stripped under `-O`.

### Deferred: check each argument once

`_infer_comptime_args`' probe exists because a parameter's type is not known until the
comptime arguments have been inferred from the argument types. Removing the double visit
needs real inference machinery — type variables and unification, as rustc and Swift use — and
is a change to how inference works, not to where facts are stored. The `_speculative` flag
makes the current shape honest; the underlying redundancy stays. Worth its own issue if
argument checking ever becomes expensive or a diagnostic is emitted twice.

## Compatibility and behaviour

- Tasks 1–4 and 6–7 accept and reject exactly the same programs and emit identical LLVM IR.
  The plan requires that to be demonstrated, not assumed.
- Task 5 (#69) makes `if (c) { …void… } else { …void… }` compile instead of crashing the
  compiler with an internal assertion. No previously-valid program changes meaning.
- No language-visible change: no grammar, no diagnostics text, no new errors or warnings.
- `patterns.py` gains public API (`ArmTest`, `MatchPlan`, `build_match_plan`) and keeps its
  leaf-module purity. `ir_builder` keeps importing it for the constructor dataclasses and
  stops calling any function in it.
- `typcheck.py` loses four public functions to privacy; nothing outside the module used them
  once the lowerer stops.

## Risks

**The assertions surface unknown divergences.** This is the point, but it makes Task 4's size
uncertain: the two known cases are the `*mut`/`*` local and the legitimate `never`
asymmetry, and there may be more. The policy when one fires is *the checker is right* — it is
the phase that produced the diagnostics the user already saw — unless the checker is plainly
the one that is wrong, in which case that is a separate bug with its own test. If Task 4
uncovers something that is neither, it stops and gets discussed rather than being papered
over with an exemption.

**`MatchPlan` is more surface than `patterns.py` has today**, and it is deliberately shaped
for the language as it stands, not for the queued pattern features. Being explicit about the
extensibility boundary, because it would be easy to overclaim here:

- **Ranges (#63)** need no change to `MatchPlan`. `IntConstructor` becomes a range inside
  `patterns.py`, and the plan carries whatever `ConstructorKind` is.
- **Guards (#62)** need a new field. A guarded arm must count as covering nothing while still
  being tested, which no combination of the current fields expresses — `ArmTest` would gain a
  guard marker, and lowering's assumption that the final test is unconditional would have to
  account for a guard failing.
- **Struct patterns (#65)** need a different *shape*. Constructors gain arity, so tests nest,
  and a flat `tests` chain cannot express a tree.

So `MatchPlan` is not "the surface those issues want". The claim this design actually makes is
narrower and is about duplication, not extensibility: each of those features changes the
coverage analysis, and with the analysis in one place each is a change to one module instead
of two that must be kept in step. `MatchPlan` will itself be reshaped by #62 and #65, and that
is expected. Its compensation is that it is unit-testable without the rest of the compiler,
because `patterns.py` imports neither `ast` nor `typs` — Task 6 adds direct
`build_match_plan` tests alongside the existing `tests/test_patterns.py`.

**Re-record assertions may fire in the speculative path.** If some site other than
`_infer_comptime_args` turns out to be speculative, the assertion finds it on the first test
run. That is the desired failure mode, but it may add work to Task 4.

**Lowering order changes are unobservable *today*.** The argument in "Why removing the
ordering coupling is sound" rests on a flexible literal emitting no instruction and creating
no block, and on codegen's emission order. The plan's output-identity harness verifies the
byte-identical result over a wider corpus rather than pinning either property. The emission
order is an implementation detail, not a guaranteed contract, and a flexible literal's
side-effect-freedom cannot easily be pinned by a test — but after this change nothing depends
on it, which is the point: it is load-bearing today and inert afterwards.

## Open questions

- **Should `typcheck.py`'s peer-type helpers eventually be replaced?** `resolve_peer_typ`
  takes the first non-`never` fixed type and defaults to `i32`. That is a two-peer rule
  generalised by accident; Zig's peer type resolution and Rust's inference variables both do
  something meaningfully stronger. Out of scope here — this design only stops *later phases*
  from seeing it — but the shape of the helper is worth revisiting when a third peer form
  appears (`else if` chains, #31, will produce one).
- **Does `place_typ` want to cover the `never` pointee case, or should that be a bug?**
  `_propagate_never` in `PLACE` context routes a diverging expression through
  `_value_to_ptr`, allocating storage of type `never`. The assertion has to exempt it. It is
  more likely a latent bug in its own right than a fact worth recording; deliberately left
  alone here and flagged for a separate issue rather than fixed in a refactor.
- **Should `SLF001` (private-member access) be enabled to enforce the new privacy?** It would
  pin the boundary, but `TypCheck` writes through `self.results._set_*`, so it needs a
  per-file ignore or public setters. Deferred to #7, which owns lint selection.

## Implementation

See `docs/plans/2026-08-30-typcheck-lowering-separation.md`.
