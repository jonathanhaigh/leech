<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Let trait bounds carry generic arguments

## Problem

A generic parameter's bound is currently a bare trait name:
`generic_param: ident (":" bound_list)?` and `bound_list: path ("+" path)*`.
`T: Container[i32]` doesn't parse — there's no way to require that a type
parameter satisfy one specific instantiation of a trait rather than the
trait in the abstract.

This is a named gap in
[the generics/traits gap assessment](2026-08-08-generics-traits-gap-assessment-design.md#bound-generic-args),
and a hard prerequisite for [generic traits](2026-08-08-generics-traits-gap-assessment-design.md#generic-traits)
(gap 4 in that doc): once a trait can itself take type arguments, bounds
need a way to name a specific instantiation of it. Doing the bound
representation now — before generic traits exist — avoids designing generic
traits' bound-checking against a representation that can't express what it
needs to check.

## Goal

- `T: Container[i32]` and `T: Container[i32] + Show` parse.
- Bounds are validated as far as is possible without generic traits
  existing: the named item is a trait, its arity matches the trait's own
  declared type parameters, and — recursively — the supplied type arguments
  themselves satisfy whatever bounds the trait declares on its own type
  parameters.
- Bounds may reference sibling type parameters from the same declaration
  (e.g. `struct Pair[A, B: Container[A]]`), resolved correctly against
  whatever the sibling is bound to at each point of use.
- Recursive bounds terminate deterministically rather than exhausting the
  stack — both those that repeat an obligation (`trait Foo[T: Foo[T]]`, or
  a cycle through several traits) and those that grow their argument each
  step and so never repeat one (`trait Foo[T: Foo[*T]]`).
- Every path that needs generic traits to actually exist raises
  `NotImplementedError`, uncaught — a deterministic, unmissable signal
  rather than silent under-checking.
- No behavior change for existing bare-name bounds (`T: Show`).

## Design

### Grammar & AST

`bound_list`'s entries become `basic_typ` (already exactly `path
[generic_args]`) instead of bare `path`:

```
bound_list: basic_typ ("+" basic_typ)*
```

No new grammar rule and no new AST class: `ast.BasicTyp` already has `.path`
and `.generic_args: tuple[ast.Typ, ...]`. `ast.GenericParam.bounds` retypes
from `tuple[Path, ...]` to `tuple[BasicTyp, ...]`; `typs.TypParamTyp.bounds`
retypes the same way, since `typ_params_from_ast` already forwards
`param_ast.bounds` through unchanged. A plain `T: Show` bound is just a
`BasicTyp` with empty `generic_args` — existing behavior for it is
unaffected.

### `NotImplementedError` for unimplemented paths

Several paths in this design can't do their real job until generic traits
exist. They raise `NotImplementedError`, uncaught, so it surfaces as a
Python traceback.

This is deliberate for this stage of development. It is *not* a
user-facing diagnostic (`errors.UserError` and its subclasses are), and
that's the point: these aren't user mistakes to be diagnosed, they're
unfinished compiler paths. A `UserError` subclass would be the wrong shape
— it would have to be removed again once generic traits land, and it would
imply the input is invalid when it's actually just unsupported.

Why not `assert`, which is what the codebase does today for this? Because
`python -O` strips assertions. For a plain invariant that's an acceptable
trade, but for these guards it isn't: with assertions stripped,
`_resolve_bound_method` would hand back a method signature resolved against
the trait's *unsubstituted* abstract type parameters, and compilation would
proceed to emit wrong IR instead of stopping. A guard whose whole job is to
prevent silently-wrong output must not be compiled away.

### Converting the existing "not supported yet" asserts

The same reasoning applies to the four guards already in the tree, so this
change converts them too, rather than leaving two conventions side by side:

| Location | Current |
| --- | --- |
| `ir_module.py:866` | `assert not trait_typ_ast.generic_args, "generic traits aren't supported yet"` |
| `ir_module.py:907` | `assert not fn_ast.generic_params, "generic associated functions aren't supported yet"` |
| `ir_traits.py:383` | `assert method.recv_typ is typ, "calling a method through a generic trait impl isn't supported yet"` |
| `ir_builder.py:379` | `assert method.recv_typ is recv_typ, "calling a method through a generic trait impl isn't supported yet"` |

Each becomes an explicit conditional raise, keeping its existing
explanatory comment. For example, `ir_traits.lookup_member`:

```python
if method.recv_typ is not typ:
    raise NotImplementedError("calling a method through a generic trait impl isn't supported yet")
```

The two `recv_typ` guards (`ir_traits.py`, `ir_builder.py`) are the ones
that most need this: like `_resolve_bound_method`'s, they exist to stop a
method resolved against an abstract receiver being used as if it were
concrete, which under `-O` would silently miscompile rather than fail.

Three existing tests assert on these guards and must be updated to expect
`NotImplementedError`:

- `tests/test_traits.py:191` (`pytest.raises(AssertionError)`)
- `tests/test_traits.py:210` (`pytest.raises(AssertionError, match=...)`)
- `tests/test_generic_fns.py:557` (`pytest.raises(AssertionError)`)

### `Trait.typ_params`

Validating a bound's arity needs to know how many type parameters the named
trait itself declares. `Trait` binds its own type parameters into its
env today but doesn't expose them. Add a cached property mirroring
`StructTyp.typ_params` exactly:

```python
@functools.cached_property
def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
    """Return this trait's own interned generic parameters."""
    return typs.typ_params_from_ast(self.ast, self.ast.generic_params)
```

`Trait.__init__`'s existing loop that binds each parameter into `self._env`
switches to iterate `self.typ_params` instead of calling
`typs.typ_params_from_ast` inline, so the two never compute independent,
possibly-inconsistent parameter lists. `ir_traits.py` doesn't import
`functools` yet; this adds that import.

### Resolving a bound: `typs.resolve_bound`

A new function, next to `check_typ_arg_bounds`, which it's mutually
recursive with:

```python
def resolve_bound(
    bound: ast.BasicTyp,
    e: ir_env.Env,
    in_progress: frozenset[tuple[ir_traits.Trait, tuple[Typ, ...]]] = frozenset(),
) -> tuple[ir_traits.Trait, tuple[Typ, ...]]:
    """Resolve a bound to its trait and validated, resolved type arguments.

    Mirrors Typ._basic_typ_from_ast's arity handling. Recursively validates
    that the resolved arguments satisfy the trait's own declared bounds on
    its type parameters, but does not check whether any particular type
    satisfies the bound as a whole — callers do that separately, once they
    know what "satisfies" should mean for their call site.

    :param in_progress: The (trait, type arguments) pairs whose bounds are
        already being validated further up the recursion, used to stop a
        self-referential bound recursing forever.
    """
    # This must be local because ir_traits imports this module.
    from leech import ir_traits  # noqa: PLC0415

    trait = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
    if not isinstance(trait, ir_traits.Trait):
        raise errors.BoundNotATraitError(bound.path.str(), bound.path.span)
    if not trait.typ_params:
        if bound.generic_args:
            raise errors.TypArgsOnNonGenericItemError(bound.path.str(), bound.span)
        return trait, ()
    if not bound.generic_args:
        raise errors.MissingTypArgsError(trait.name, bound.span)
    if len(bound.generic_args) != len(trait.typ_params):
        raise errors.WrongNumberOfTypArgsError(
            trait.name, len(bound.generic_args), len(trait.typ_params), bound.span
        )
    typ_args = tuple(Typ.from_ast(arg, e) for arg in bound.generic_args)

    # A trait whose own parameter is bounded by itself (directly, or
    # through a cycle of traits) would otherwise recurse until the stack
    # runs out. Such bounds have legitimate uses - symmetric two-type
    # relations, and mutually recursive trait families - for which the
    # eventual treatment is to discharge a repeated obligation as already
    # satisfied. Deciding that belongs with generic traits; until then,
    # stop deterministically rather than overflow the stack.
    key = (trait, typ_args)
    if key in in_progress:
        raise NotImplementedError(f"recursive trait bound on {trait.name} isn't supported yet")

    # Repetition alone doesn't bound the recursion: an argument that grows
    # each step never repeats a key. `Foo[T: Foo[*T]]` applied to i32 walks
    # Foo[i32], Foo[*i32], Foo[**i32], ... - all distinct. len(in_progress)
    # is the current nesting depth, since each level adds exactly one key.
    if len(in_progress) >= _MAX_BOUND_DEPTH:
        raise NotImplementedError(
            f"exceeded {_MAX_BOUND_DEPTH} levels of bound resolution at "
            f"{trait.name}; recursive trait bounds aren't supported yet"
        )

    check_typ_arg_bounds(trait.typ_params, typ_args, e, bound.span, in_progress=in_progress | {key})
    return trait, typ_args
```

This reuses the four error types `Typ._basic_typ_from_ast` already raises
for the identical arity questions about a generic struct reference, applied
here to a generic trait reference instead. No new error types are needed.

`(trait, typ_args)` is a sound recursion key: `Trait` instances are unique
per declaration and hash by identity, and `Typ` is weakly interned and
compares by identity, so the pair identifies exactly "these bounds, for
these arguments." Keying on the trait alone would wrongly reject a
legitimately nested non-cyclic use of the same trait at different arguments.

#### Why both guards are needed

The seen-set alone does not terminate. It fires only when an *identical*
obligation recurs, and a bound may transform its argument on each step
instead of repeating it:

```leech
trait Foo[T: Foo[*T]] { ... }   // applied to i32
// Foo[i32] -> Foo[*i32] -> Foo[**i32] -> ... every key distinct
```

`_generic_arg_list` admits any `typ`, so both `ptr_typ` (`*T`) and
`array_typ` (`[T; 2]`) can grow an argument this way. Nothing bounds the
resulting chain, and eager validation would run to `RecursionError`.

The two guards catch different things and neither subsumes the other. The
seen-set catches *repetition* — the genuinely cyclic obligations, which is
also where the future coinductive discharge belongs, so it must stay a
distinct case rather than being folded into a depth counter. The depth
limit catches *growth*, and is a backstop for anything that terminates
neither by repeating nor by finishing.

```python
#: Maximum nesting depth when validating a bound's own type-argument bounds.
#: Real bounds nest a few levels; anything deeper is a runaway recursion.
_MAX_BOUND_DEPTH = 32
```

The value is arbitrary but generous — deliberately far above any plausible
legitimate nesting, so exceeding it means runaway recursion rather than an
unusually elaborate bound. It's a module constant, not configurable: making
it tunable is speculative until something legitimate is observed near it.

It is deliberately well below CPython's own recursion limit rather than as
high as possible. Each level of bound resolution costs several Python
frames, and bound checking runs partway down an already-deep compiler
stack, so a limit set close to CPython's would risk `RecursionError`
winning the race — which is the failure this guard exists to prevent. 32 is
comfortably reachable in practice and leaves that headroom.

One honesty note about the error type. A depth overflow is not really an
unimplemented feature — it stays an error after generic traits land, and
mature solvers report it as a normal user-facing diagnostic (Rust's
"overflow evaluating the requirement"). It raises `NotImplementedError`
here only because in this increment it is unreachable except through
argument-bearing bounds, which are unsupported anyway, so the message is
true of every program that can reach it. When generic traits land, this
guard should become a real diagnostic rather than being carried over as-is.

#### Why the cycle exists at all

Worth being clear that this is a consequence of *this design's* choice to
validate a bound's arguments eagerly, not something inherent to the bound
syntax. ("Eager" here is about *depth*, not *timing* — how far a single
check expands once it runs. It's a separate axis from [what stays
lazy](#what-stays-lazy), which is about when checking is triggered at all;
this design is lazy on that axis and eager on this one.) A solver that answers "does `X` satisfy `Foo[Y]`?" on demand, as
Rust's does, never walks a trait's own parameter bounds to a fixpoint and
so has nothing to loop on — it reaches the same territory only when an
obligation is actually solved, and handles it with a recursion limit
(Rust's "overflow evaluating the requirement").

The eager check is kept anyway: it catches a wrong argument at the point
it's written rather than wherever it's eventually used, and the guards are
a frozenset membership test and an integer comparison. But the termination
burden comes with the eagerness, and if bound checking is ever restructured
to be demand-driven, these guards are what would be reconsidered.

Note that going demand-driven would not remove the need for the depth
limit — Rust is demand-driven and still needs its recursion limit, because
an argument that grows per step diverges regardless of *when* the
obligations are solved. Laziness changes how many obligations get raised,
not whether a divergent chain terminates.

Because `typs.py` has no `TYPE_CHECKING` block today, one is added for the
`ir_traits.Trait` annotation, matching `typcheck.py`'s existing pattern:

```python
if TYPE_CHECKING:
    # The runtime import is local because ir_traits imports this module.
    from leech import ir_traits
```

### Sibling-parameter substitution

A bound's type arguments can reference another parameter from the same
`generic_param` list, as `A` does in `B: Container[A]` above. Resolving `A`
correctly requires it to mean "whatever `A` is bound to right now" — the
concrete instantiation argument when checking a real instantiation, or the
abstract `TypParamTyp` itself when there is no concrete instantiation yet.

This isn't a new problem specific to bounds: `StructTyp.__init__` already
solves exactly this for field types, by building a child env with each
parameter name bound to its argument before resolving anything that might
reference a sibling:

```python
if typ_args:
    for param_ast, typ_arg in zip(struct_ast.generic_params, typ_args, strict=True):
        self.env.add_container(param_ast.ident.name, typ_arg)
else:
    for typ_param in self.typ_params:
        self.env.add_container(typ_param.name, typ_param)
```

`check_typ_arg_bounds` needs the same treatment, for the same reason —
`typs.py`:

```python
def check_typ_arg_bounds(
    typ_params: Sequence[TypParamTyp],
    typ_args: tuple[Typ, ...],
    e: ir_env.Env,
    span: Optional[src.SrcSpan],
    in_progress: frozenset[tuple[ir_traits.Trait, tuple[Typ, ...]]] = frozenset(),
) -> None:
    """Raise if a type argument violates its corresponding parameter's bounds.

    :param in_progress: Threaded through to resolve_bound; see its docs.
    """
    # Bind each parameter's own name to its argument first (all of them,
    # before checking any bound), so a bound's generic args can reference a
    # sibling parameter regardless of declaration order - matching how
    # StructTyp.__init__ already does this for field types.
    sub_env = e.new_child()
    for typ_param, typ_arg in zip(typ_params, typ_args, strict=True):
        sub_env.add_container(typ_param.name, typ_arg)

    for typ_param, typ_arg in zip(typ_params, typ_args, strict=True):
        for bound in typ_param.bounds:
            trait, bound_typ_args = resolve_bound(bound, sub_env, in_progress)
            if bound_typ_args:
                # Checking whether `typ_arg` itself satisfies a *specific*
                # instantiation of `trait` needs generic traits (matching
                # the bound's args against an impl's own trait args) -
                # nothing upstream rejects this bound shape syntactically,
                # so fail loudly here rather than silently under-check.
                raise NotImplementedError(
                    "checking bounds with generic arguments isn't supported yet"
                )
            if e.impl_registry.find_impl(trait, typ_arg) is None:
                raise errors.UnsatisfiedBoundError(typ_arg.name, trait.name, typ_param.name, span)
```

The local `ir_traits` import this function has today moves into
`resolve_bound`, which is now the only place that needs it — the
`isinstance(trait, ir_traits.Trait)` check moved there with it.

Two passes are required (build `sub_env` fully, then check) because a bound
can reference a sibling declared later in the list, e.g. `A: Trait[B]` where
`B` follows `A`.

`resolve_bound`'s own recursive `check_typ_arg_bounds` call (validating that
a bound's resolved arguments satisfy the named trait's own parameter
bounds) passes already-concrete `Typ` values, so it needs no further
substitution env of its own — it reuses whatever `e` it was given.

### `typcheck._resolve_bound_method`

This resolves a bound only to find a candidate method by name, inside a
still-generic body — no instantiation is happening, so there's nothing to
substitute and no `sub_env` to build. It doesn't need `resolve_bound`'s
arity/recursive-bound validation at all: a trait's method names and count
don't depend on what its own type arguments will eventually be. It changes
only to read `bound.path` instead of the bound itself, and to block if a
found method's trait bound carries type arguments — mirroring
`ir_traits.lookup_member`'s existing receiver-type guard, for the same
reason: `TraitMethod.fn_typ_for_self` resolves a method's signature against
the trait's own (unsubstituted) type parameters (see `ir_traits.py`), so
returning a match through an argument-bearing bound today would silently
hand back a signature that still mentions the trait's abstract parameters
instead of the bound's concrete ones.

```python
def _resolve_bound_method(
    self, typ_param: typs.TypParamTyp, name: str, span: Optional[src.SrcSpan], e: ir_env.Env
) -> Optional[ir_traits.TraitMethod]:
    """Resolve a type parameter's method against only its declared trait bounds."""
    # Local to avoid the ir_traits import cycle.
    from leech import ir_traits  # noqa: PLC0415

    matches: list[ir_traits.TraitMethod] = []
    for bound in typ_param.bounds:
        item = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
        if not isinstance(item, ir_traits.Trait):
            raise errors.BoundNotATraitError(bound.path.str(), bound.path.span)
        method = item.get_method(name)
        if method is not None:
            if bound.generic_args:
                raise NotImplementedError(
                    "calling a method through a bound with generic arguments isn't supported yet"
                )
            matches.append(method)
    return ir_traits.disambiguate(matches, name, typ_param.name, span)
```

Because this only blocks once a method name actually matches, a bound like
`T: Container[i32]` on a type parameter whose body never calls a `Container`
method still typechecks fine — matching the "no runtime effect until
something depends on the arguments" framing from the gap-assessment doc.

### What stays lazy

Bound resolution stays exactly as lazy as it is today: nothing validates a
bound at the point a generic item is *declared*. `BoundNotATraitError`,
arity errors, and the new `NotImplementedError`s are only ever reached from
`check_typ_arg_bounds` (a real instantiation: a generic struct or function
actually being applied to concrete arguments) or `_resolve_bound_method` (a
method call resolved against a bound, inside a still-generic body). A
generic item that's declared but never instantiated, and never has a
bound-gated method called on it, is never checked — this matches existing
behavior for plain bounds and isn't a new gap introduced here.

## Response to adversarial review

Two rounds of adversarial review have run against this design. Findings and
their resolutions are recorded here, including where a resolution departs
from the reviewer's specific recommendation.

### Round 1

Three findings, all accepted in substance; two resolved differently from
the recommendation.

1. **Guards stripped under `python -O` (accepted, as recommended).** The
   draft used `assert` for its unimplemented-path guards. Correct: `-O`
   would strip them and let compilation continue to wrong IR. Resolved by
   `NotImplementedError` throughout, and by converting the four existing
   assert-based guards in the tree for the same reason.

2. **Accepted syntax reaches a crash rather than a diagnostic (accepted;
   different resolution).** The reviewer recommended either implementing
   satisfaction checking first, or rejecting argument-bearing bounds with a
   user-facing compiler error. Neither is taken. Implementing satisfaction
   checking *is* generic traits — the feature this work is the prerequisite
   for — so it can't come first. A user-facing `UserError` subclass is
   rejected because it would misdescribe the situation (the input isn't
   invalid, the compiler is unfinished) and would have to be deleted again
   once generic traits land. An uncaught `NotImplementedError` is the
   intended end state for this stage of the project: the language is
   pre-release and has no stability promise, and an unmissable traceback on
   an unfinished path is preferable to a diagnostic implying permanence.
   The trade-off is deliberate, not overlooked.

3. **Recursive bounds exhaust the stack (accepted; different resolution).**
   The draft documented this as a known limitation and left it. Correct
   that it shouldn't ship that way — the cycle is newly reachable through
   this change. Resolved with the `in_progress` seen-set above.

   The reviewer recommended emitting a permanent *cycle diagnostic*. This
   raises `NotImplementedError` instead, for two reasons. First, every
   reachable cycle necessarily runs through an argument-bearing bound (a
   bound with no arguments either names a non-generic trait, which
   terminates immediately, or raises `MissingTypArgsError`), and
   argument-bearing bounds are exactly what this increment doesn't
   implement — so a cycle is unreachable except via already-unsupported
   territory. Second, and more importantly, **a permanent error would ban
   patterns that are legitimate**, so it would be wrong on the merits, not
   merely premature — see [which recursive bounds are
   legitimate](#which-recursive-bounds-are-legitimate).

### Round 2

One finding, accepted in full.

4. **Exact-key detection misses growing arguments (accepted, as
   recommended).** The round-1 seen-set fires only on an *identical*
   repeated obligation. A bound that transforms its argument each step
   never repeats a key: `trait Foo[T: Foo[*T]]` applied to `i32` walks
   `Foo[i32]`, `Foo[*i32]`, `Foo[**i32]`, … indefinitely, so the design
   still failed its own stated termination goal. Verified against the
   grammar before accepting — `_generic_arg_list: typ` admits `ptr_typ` and
   `array_typ`, so the example is expressible, and `array_typ` gives a
   second instance of the same class.

   This one lands squarely: the round-1 text already cited Rust's recursion
   limit as how demand-driven solvers handle this, and then didn't apply
   the lesson. Resolved with the `_MAX_BOUND_DEPTH` backstop alongside the
   seen-set, per the recommendation, plus the growth-specific regression
   tests it asked for. The seen-set is kept as a separate guard rather than
   replaced, because repetition is where the future coinductive discharge
   belongs and folding it into a depth counter would lose that distinction.

### Which recursive bounds are legitimate

A bound that mentions its own parameter is *F-bounded* quantification: the
bound is a function of the parameter (`T: F(T)`) rather than a fixed trait.
Historically it exists to compensate for a missing `Self` type — without
one, the only way to name "the implementing type" is to pass it back in as
a parameter and constrain that it really is the implementor.

Leech has no [`Self` in trait
declarations](2026-08-08-generics-traits-gap-assessment-design.md#self-in-trait-declarations)
today, and plans to add it. That removes the *unary* uses of the pattern —
comparing to, or returning, one's own type becomes a `Self` signature, with
no type parameter and no cycle:

```leech
trait Ord { fn cmp(*self, other: *Self) i32; }   // not trait Ord[T: Ord[T]]
```

Two families survive, and both are cyclic:

- **Symmetric two-type relations.** `Self` can't express these, because
  they constrain a *pair* of types and require the relation to round-trip:

  ```leech
  trait Convert[T: Convert[Self]] { fn convert(*self) T; }
  ```

  Proving `A: Convert[B]` requires `B: Convert[A]`, which requires
  `A: Convert[B]` — it holds only if the assumption being proved may
  discharge itself. Duals, adjoint pairs, and bidirectional codecs have the
  same shape.

- **Mutually recursive trait families**, which cycle through `Self` rather
  than around it:

  ```leech
  trait Graph[N: Node[Self]] { fn nodes(*self) Vec[N]; }
  trait Node[G: Graph[Self]]  { fn owner(*self) *G; }
  ```

So the anticipated resolution, once generic traits exist, is to **discharge
an in-progress key as already satisfied** rather than to reject it — the
coinductive reading, which is what makes both families above work. This is
a one-line change from the guard above and is unobservable today, since the
argument-bearing-bound guard fires immediately afterward either way. It is
recorded here as the expected direction so the eventual decision starts
from it rather than re-deriving it.

Note also that the single-trait `trait Foo[T: Foo[T]]` shape specifically
is expected to become vestigial once `Self` lands: it is not among the
surviving families, both of which involve either two traits or two types.
Nothing here is designed around keeping it usable.

## Non-goals

- Generic traits themselves (a `Trait` that can be instantiated with
  concrete type arguments, `ImplRegistry` matching an impl's own trait
  arguments against a bound's) — this is the follow-on gap this work
  prepares for, not part of it.
- Implementing coinductive discharge for recursive trait bounds. Detection
  and a deterministic stop are in scope; acting on a detected cycle as
  "satisfied" is not, and belongs with generic traits.
- Anything that would let `check_typ_arg_bounds`'s blocked case actually
  succeed. Every test for a bound with generic arguments necessarily stops
  at a `NotImplementedError` once arity and recursive-bound validation
  pass.

## Testing

- **Parsing:** `T: Container[i32]` and `T: Container[i32] + Show` parse;
  existing bare-name bounds (`T: Show`) are unaffected.
- **Arity errors:** a bound naming a non-generic trait with type args
  (`TypArgsOnNonGenericItemError`); a bound naming a generic trait with no
  args (`MissingTypArgsError`); a bound supplying the wrong number of args
  (`WrongNumberOfTypArgsError`); a bound whose path isn't a trait at all
  (`BoundNotATraitError` — regression coverage for the pre-existing check
  now reached through `resolve_bound`).
- **Recursive bound validation:** a trait declaring its own type parameter
  bound, e.g. `trait Container[T: Show]`, used as `U: Container[SomeType]`
  in a real instantiation. This is reachable and observable even though the
  feature as a whole is inert for full satisfaction-checking:
  `resolve_bound`'s recursive `check_typ_arg_bounds` call runs to
  completion (and can raise `UnsatisfiedBoundError`) *before* the outer
  call's `NotImplementedError` is reached. Cover both a passing case
  (`SomeType` implements `Show`) and a failing one (it doesn't).
- **Sibling-parameter substitution:** `struct Pair[A, B: Container[A]]`
  (with `Container[T: Show]` as above), instantiated as both `Pair[Showable,
  X]` and `Pair[NotShowable, X]`. The second must raise
  `UnsatisfiedBoundError` from the nested check — which only happens if `A`
  in `B`'s bound was actually substituted with the real instantiation
  argument rather than left as a dangling reference to `A`'s name. Also
  cover a bound referencing a sibling declared *later* in the parameter
  list, to exercise the two-pass `sub_env` construction.
- **Cycle detection (repetition):** a directly self-referential bound
  (`trait Foo[T: Foo[T]]`) and a two-trait cycle (`trait A[T: B[T]]`,
  `trait B[T: A[T]]`), each instantiated for real. Both must raise
  `NotImplementedError` rather than `RecursionError` — asserting on the
  exception type is what distinguishes "detected" from "crashed", since
  both stop compilation. Match on the `"recursive trait bound"` message
  too: every one of these programs would raise `NotImplementedError` from
  the argument-bearing-bound guard anyway, so only the message confirms
  detection fired first.
- **Depth limit (growth):** a bound whose argument grows each step and so
  never repeats a key — `trait Foo[T: Foo[*T]]` via `ptr_typ`, and
  `trait Bar[T: Bar[[T; 2]]]` via `array_typ`, plus a two-trait growing
  cycle. Each must terminate with the depth-limit `NotImplementedError`;
  match on the `"levels of bound resolution"` message to prove the depth
  guard fired rather than the seen-set. These are the cases the seen-set
  alone does not catch, so they are the regression coverage for that
  guard specifically — a `RecursionError` here is a failure.
- **Non-cycles that must not be flagged:** the same trait reached twice at
  *different* arguments in one resolution, e.g. `trait Outer[A:
  Container[i32], B: Container[u8]]`, which a trait-only key would falsely
  reject; and a legitimately nested chain several levels deep but well
  under `_MAX_BOUND_DEPTH`. Both must get past *both* guards and stop at
  the argument-bearing-bound guard instead.
- **`_resolve_bound_method` blocking:** a generic function with a
  parameter bound by an argument-carrying trait, where the body never calls
  a method of that trait, typechecks successfully; where it does call one,
  it raises `NotImplementedError`.
- **Updated existing tests:** the three `pytest.raises(AssertionError)`
  cases listed above switch to `NotImplementedError`, keeping their
  existing `match=` text where present.

All of the above are typecheck-level tests (`tests/test_traits.py`,
`tests/test_generic_fns.py`, `tests/test_generic_structs.py` as
appropriate) — nothing here reaches codegen, since every path that would
need to produce IR for an argument-bearing bound is blocked before getting
there.
