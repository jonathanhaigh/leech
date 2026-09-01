<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Tagged Unions (`union`) — Design

For: #24: Tagged unions (union)

## Problem

Leech cannot express "might not be present" or "might have failed" in a type. Absence is a
raw pointer plus `__is_null`; failure is `panic`. `Option[T]` and `Result[T, E]` are the
payoff, and both need a nominal type whose value is exactly one of several named
alternatives, each carrying its own payload.

`enum` is not that type and is not being made into it. A Leech `enum` is a fixed set of
integer discriminants with an explicit or inferred backing integer type, deliberately
C-shaped: `EnumTyp` is documented as "never generic and never instantiated - one `enum`
declaration is always exactly one `EnumTyp`", and its aliased-discriminant semantics
(`enum Alias(u8) { A = 1, B = 1 }`) are a C-ABI feature the `match` design already relies
on. Generic enums would also be nearly useless on their own, since a discriminant cannot
mention a type parameter. A separate declaration form keeps a complete, well-tested feature
undisturbed.

The three hard prerequisites #24 names are all resolved: `match` (#30), target-data type
sizes (#4), and the struct template/instance split (#3) have landed. Untagged unions (#25)
are still open and are *not* a prerequisite — this design lays out payload storage itself
(see [Layout](#layout-discriminant-then-payload)), in a shape #25 could later supply.

## Goals

- Declare a `union`, generic over comptime parameters, with positional variant payloads.
- Construct a variant, with comptime arguments inferred where possible.
- Destructure via `match`, with arbitrarily nested payload patterns and full exhaustiveness
  and reachability checking.
- Correct layout across mixed payload sizes and alignments, observable through `__size_of`.
- Monomorphize and emit union instances, including at compile time.
- Carry inherent and trait `impl` blocks.
- Leave `enum` behaviour entirely unchanged.

## Non-goals

Each is deferred with a follow-on issue to raise:

- **Niche filling.** Layout is always discriminant-then-payload, so `Option[*T]` costs a
  pointer plus a tag rather than being pointer-sized.
- **Struct-like variants**, `Some { value: T }`. Positional only.
- **Explicit discriminant values and an explicit tag type**, `union U(u8) { A = 1 }`. Tags
  are always inferred and always dense `0..n-1`.
- **Variant constructors as values**, `let f = Option::Some;`.
- **`Option[T]` and `Result[T, E]` in the prelude.** #24 declares its unions in test
  sources.
- **Peer-context inference across `if`/`match` branches.** This is the sharpest edge in the
  feature and the one most likely to be met on day one, so it is called out rather than
  buried:

  ```leech
  let o = if (c) { Option::Some(1i32) } else { Option::None };   // rejected in #24
  ```

  The first branch fixes `Option[i32]`, but the second is checked with no expected type and
  raises `CannotInferComptimeArgError`. The existing peer machinery in `_check_if_expr` and
  `_check_match_expr` hints only *flexible integer literals* from sibling branches; widening
  it to "any expression wanting a type hint" needs a broader predicate, conflict and ordering
  rules, and a visit order the checker and builder both agree on. The workaround is one word
  — annotate the binding, `let o: Option[i32] = …` — and accepting more programs later is
  backwards-compatible, so deferring forecloses nothing.

Not deferred so much as out of frame: `==` on unions belongs to #15; whether a payload
binding copies or references belongs to #34; a `switch`-based lowering belongs to #68.

## Sequencing: the path/comptime-argument refactor comes first

`Option[i32]::Some(x)` does not parse today, and the reason is a latent design error rather
than a parser limitation.

Leech attaches comptime arguments to the *end of a whole path*:

```lark
basic_typ: path [comptime_args]
var_expr:  path [comptime_args]
path: ident ("::" ident)*
```

That works only because every generic entity Leech has is the last segment of its path —
`mod::Pair[i32]`, `f[i32]`. A variant hanging off a generic type is the first case where
the trailing position is the wrong position: in `Option::Some[i32]` the arguments belong to
`Option`, not to `Some`.

Adding segment-level arguments *while keeping* the trailing form makes `Foo[i32]` genuinely
ambiguous — last segment's arguments, or the path's trailing arguments? — and lark reports
exactly that, as a shift/reduce conflict on `[` after `<path_seg : ident>` under
`strict=True`. Moving the arguments onto segments and deleting the trailing form builds
clean:

```lark
path: path_seg ("::" path_seg)*
path_seg: ident [comptime_args]
basic_typ: path
var_expr: path
```

This was spiked against the real `leech.lark`. It is behaviour-preserving: every Leech
source in the repository — the three `src/leech/std/*.leech` files plus all 1040 source
strings embedded in `tests/` — parses identically under both grammars (1043/1043), because
today's arguments always sit on what becomes the final segment.

Every language with both qualified paths and generic arguments attaches the arguments to
the segment being instantiated:

| Language | Spelling | Attaches to | Disambiguator |
|---|---|---|---|
| [Rust](https://doc.rust-lang.org/reference/paths.html) (type position) | `mod::Vec<T>::Item` | any segment — `TypePathSegment → PathIdentSegment (::? GenericArgs)?` | none |
| Rust (expression position) | `Option::<i32>::Some(x)` | any segment — `PathExprSegment → PathIdentSegment (:: GenericArgs)?` | **turbofish `::<`**, because `<` is also less-than |
| [Swift](https://the-swift-programming-language.readthedocs.io/en/latest/md/GenericParametersAndArguments/) | `Optional<Int>.some(42)` | each component of a dotted `type-identifier` | none |
| [Go](https://go.dev/ref/spec#Instantiations) | `pkg.Foo[int]`, `min[int](5, 3)` | the operand name instantiated | none |
| C++ | `A<T>::B<U>::c` | any nested-name-specifier component | `template`, in dependent contexts |
| [Carbon](https://docs.carbon-lang.dev/docs/design/sum_types.html) | `Optional(i32).Some(42)` | parenthesised application to the type | none |
| [Zig](https://zig.guide/language-basics/unions/) | `Option(i32).Some` | n/a — a generic type *is* a comptime function returning a type | none |

Leech is entitled to the spelling Rust cannot have. Rust needs the turbofish only because
`<` collides with less-than; Leech spells comptime arguments with `[`, and array indexing
with `.[` precisely so a `[` following an identifier is unambiguous. `Option[i32]::Some(x)`
costs nothing syntactically.

**This refactor is a separate prerequisite issue**, not part of #24: it is right on its own
merits regardless of unions, it touches `ast.Path`/`BasicTyp`/`VarExpr`,
`ir_env.resolve_path` and the checker's explicit-comptime-argument sites, and none of that
is about tagged unions. #24 depends on it and the #55 graph gains a `NEW --> 24` edge.

## Surface design

### Declaration

```leech
union Option[T] { None, Some(T) }
union Result[T, E] { Ok(T), Err(E) }
union Shape { Point, Line(Vec2, Vec2), Circle(Vec2, i64) }
pub union Empty {}
```

```lark
defn: … | union_defn

union_defn: access "union" ident [comptime_params] "{" union_variant_list "}"
union_variant_list: _union_variant_list?
_union_variant_list: union_variant ("," union_variant)* ","?
union_variant: ident ["(" _union_payload_list ")"]
_union_payload_list: typ ("," typ)* ","?
```

Verified conflict-free against the real grammar under LALR `strict=True`, alongside
unchanged `enum` declarations. `"union"` joins `reserved.KEYWORDS`; because
`test_keyword_set_matches_grammar` derives the expected set from the grammar's string
terminals, that test stays green as written.

`A()` is a parse error: a unit variant is spelled `A`, and there is exactly one spelling for
it. Variant names live in the union's own namespace, are checked against
`reserved.is_reserved`, and must be unique within the declaration. Variant visibility
follows the union's own `pub`, exactly as an enum's variants do — there is no per-variant
access specifier.

A zero-variant `union Empty {}` is legal and uninhabited. Its constructor space is empty and
closed, so `match (e) {}` over one is exhaustive, which is the same mechanism that already
makes an empty match on `never` exhaustive. Being uninhabited, it has no constructor and so
no runtime value: `pub fn absurd(e: Empty) i32 { return match (e) {}; }` is a thing to compile,
never a thing to call. Its acceptance test is a compile-and-inspect one; manufacturing a
value from uninitialised storage to run it would be testing undefined behaviour.

### Referring to a variant

```leech
let a = Option::Some(1i32);           // T inferred from the payload argument
let b: Option[i32] = Option::None;    // T inferred from the expected type
let c = Option[i32]::Some(1i32);      // explicit
let d = Result[i32, MyErr]::Err(e);   // explicit, because inference alone cannot bind T
```

The `Result` case is the one worth reading twice: `Err(E)`'s payload infers `E` and says
nothing about `T`, so `Result::Err(e)` on its own leaves `T` unbound, and it is `T` the
diagnostic must name. The explicit arguments on line four are what supply it; an expected
type would have done the same.

Comptime arguments belong to the union, so they attach to the union's segment.
`Option::Some[i32]` — arguments on the *variant's* segment — parses under the segment-level
grammar but names nothing generic, and is rejected at resolution with
`ComptimeArgsOnNonGenericItemError`. Rust reaches the opposite conclusion, accepting
`Option::Some::<i32>(x)` on the grounds that a variant constructor inherits its enum's
generics; Leech does not, because it already has the unambiguous spelling that says what is
meant.

A variant is always reached through its union: `Option::Some`, never a bare `Some`. This
matches how enum variants already work, and keeps the "a misspelled variant is a hard
error, never a silent catch-all" property the `match` design bought.

Comptime arguments are resolved in this order:

1. **Explicit** — `Option[i32]::Some`. The instance is fully determined.
2. **Expected type** — when the checker has an expected type that is a `UnionTyp` of the
   same template, its comptime arguments seed the bindings. This is what makes
   `let b: Option[i32] = Option::None;` work, and it is the only way a unit variant of a
   generic union can be named without explicit arguments.

   This is the one place the feature needs the checker to carry context it does not carry
   today: `_check_expr_inner` drops `expected_typ` on both its `VarExpr` and its `CallExpr`
   arms, so *neither* form of variant reference can see it yet. Threading it to both is a
   precondition for this rule, not an optimisation of it, and the expected type must reach
   every context that already computes one — a typed `let`, an assignment, a `return`, a
   block tail expression, a call argument — not merely the `let` the example uses.
3. **Payload arguments** — each declared payload type infers structurally against the
   corresponding argument type, through the existing `Typ.infer_typ_args`, in the same
   speculative pass `_infer_comptime_args` already uses for generic calls.

Anything still unbound is `CannotInferComptimeArgError`, naming the union and the parameter.

Every comparable language offers both an explicit and a context-inferred form: Swift's
implicit member expression (`let x: Optional<Int> = .some(42)`), Carbon's `.Some` in a
`match` case, Rust's ordinary inference on `Option::Some(x)`. Leech's inferred form is
`Option::Some(x)`; it deliberately does not add a leading-dot shorthand, which would need
its own grammar category and buys little while variants must be written qualified anyway.

A payload variant is only valid in call position, and a unit variant only in value position.
`Option::Some` alone is `VariantConstructorNotAValueError`; `Option::None(x)` is
`UnitVariantCalledError`. Rust makes a tuple variant a real function item, which is a
pleasant property but needs constructors to carry function types and linkage of their own;
that is the deferred "variant constructors as values" non-goal.

### Patterns

```lark
path_pattern: path ["(" _pattern_list ")"]
_pattern_list: pattern ("," pattern)* ","?
```

Verified conflict-free. Payload sub-patterns nest arbitrarily and reuse the whole existing
pattern vocabulary:

```leech
match (r) {
    Result::Ok(Option::Some(let x)) => x,
    Result::Ok(Option::None)        => 0i32,
    Result::Err(let e)              => handle(e),
}

match (o) {
    Option::Some(0i32) | Option::Some(1i32) => small(),
    Option::Some(let n)                     => big(n),
    Option::None                            => none(),
}
```

Rules:

- The number of payload sub-patterns must equal the variant's arity;
  `WrongNumberOfPayloadPatternsError` otherwise. A payload variant written bare
  (`Option::Some => …`) is the arity-0 case of the same error, which reads better than a
  separate diagnostic.
- A payload list on anything that is not a union variant — an enum variant, a non-variant
  path — is `PayloadPatternOnNonVariantError`.
- Comptime arguments may be written in a pattern (`Option[i32]::Some(let x)`). They are
  redundant, since the column type determines the instance, but they are checked to agree
  with it and produce `PatternTypMismatchError` when they do not.
- The existing prohibition on `let` bindings inside or-pattern alternatives is retained and
  now applies at any depth: a binding anywhere under an `OrPattern` is
  `BindingInOrPatternError`. This is what keeps or-patterns nearly free — it sidesteps the
  "every alternative binds the same names at the same types" rule entirely.
- Two bindings of the same name within one arm are already rejected: all of an arm's
  bindings share one arm scope, so `Env.add_var` raises `DuplicateItemDefnError` unchanged.
- A binding copies its payload. Leech is copy-only today, and binding modes are #34's
  business.

Payloads are reachable *only* through `match`. There is no field-access or index spelling
for a payload and no unchecked accessor, which is the guarantee that distinguishes a tagged
union from an untagged one — and the reason #24 named #30 an absolute prerequisite.

### `impl` blocks

Inherent and trait `impl` blocks both accept a union:

```leech
impl [T] Option[T] {
    pub fn is_some(*self) bool {
        return match (self.*) { Option::Some(_) => true, Option::None => false };
    }
}

impl [T] SomeTrait for Option[T] { … }
```

Trait impls already accept any self type, so the only genuine restriction to lift is the
inherent one (`ImplForNonStructTypError`). The coherence machinery needs union arms in three
structural helpers — `typs._contains_typ`, `typs.typs_overlap`'s `unify`, and
`ir_traits._head_shape`/`_is_local` — all of which mirror their `StructTyp` arms exactly.
Omitting any of them would silently mis-handle overlap and orphan checking for generic union
impls rather than fail loudly, which is why they are called out individually in the plan.

## Type representation

`union` follows the template/instance split #3 established for structs, because a generic
union must participate in monomorphization the same way and should not invent a second
pattern for it.

- `UnionTypTemplate(GenericTypTemplate)` mirrors `StructTypTemplate`: it owns the
  declaration AST and its declaration environment, validates variant names once, and
  produces instances through `Ctx.instantiate_union`. It has the same `_validation_instance`
  (an opaque instance over its own comptime parameters, used to validate the declaration
  whether or not anything instantiates it), `module_instance` and `validate_declaration`.
- `UnionTyp(Typ)` is a usable nominal instance: template plus comptime arguments, with
  `substitute_typ_params`, `infer_typ_args`, `is_concrete`, `name`, `qualified_name` and
  `span` written exactly as `StructTyp`'s are. Instances are owned by the compilation
  context, not the global type cache, so `cache_key` raises for the same reason
  `StructTyp.cache_key` does.
- `UnionVariant` carries its declaration index, its AST and the instance's environment, and
  resolves `payload_typs` lazily. Its tag value is its declaration index.

A variant can be *named* before its union's comptime arguments are known — that is the whole
point of `Option::Some(x)` — so a variant reference cannot always carry a `UnionVariant`,
which is instance-scoped by construction. The template therefore owns a separate,
instance-independent **variant descriptor**: name, declaration index, and declaration AST.
A reference holds a descriptor plus its owner; once inference settles the comptime
arguments, the concrete `UnionVariant` is looked up on the resulting instance by index.
Payload types needed *for* that inference resolve in the template's own
parameter-bound environment, so the reference never retains the validation instance's
environment — which exists only to check the declaration and must not leak into checking
uses of it.

`typs.TypKind` gains `UnionTyp`. `compilation.Ctx` gains `_union_instances`,
`instantiate_union` and `requested_union_instances`, in the same shape as the struct
versions including the `record_request` flag.

### Layout: discriminant then payload

The tag type is the smallest unsigned builtin integer fitting `n - 1` for `n` variants —
`u8` up to 256 variants, then `u16`, `u32`, `u64` — mirroring how `EnumTyp.backing_typ`
infers an unsigned type when none is written. A zero-variant union nominally uses `u8`; it
can never be constructed.

The LLVM type is an identified struct:

```llvm
%mod::Option[i32] = type { i8, [1 x i32] }
```

that is, `{ tag_ty, [K x iA] }` where `A` is the maximum ABI alignment in bytes over every
variant's payload struct (1 when no variant has a payload), `S` is the maximum ABI size, and
`K = ceil(S / A)`. The payload field's element type `iA*8` is what carries the alignment
requirement into the containing struct, so:

- the union's alignment is `max(align(tag_ty), A)`, as required;
- the payload field is placed at an `A`-aligned offset, and its `K * A` bytes are at least
  `S`, so every variant fits.

A variant's payload is projected by viewing the storage as that variant's own payload
struct, `{T1, T2, …}` — an ordinary LLVM literal struct, so LLVM computes the inter-field
padding and offsets. This is Clang's union lowering, and the reason it is chosen over an
`[S x i8]` byte array is that a byte array has alignment 1 and would under-align a union
nested inside a struct.

`__size_of[Option[i32]]` therefore answers from `ll_typs.ll_typ` plus `target_data()`, with
no union-specific size arithmetic, the same way it already does for structs.

Niche filling is what makes `Option[*T]` pointer-sized in Rust, and Swift does comparable
spare-bit packing. Zig does not do it outside its builtin optional. It is deferred because a
predictable, `__size_of`-checkable layout is worth more right now than the space, and because
niche selection interacts with `untagged_union` (#25), pointer validity and the ownership
model (#34) — all of which are still open.

`[K x iA]` is deliberately the shape an untagged union would supply. If #25 lands, it can
replace the storage field without disturbing anything above it.

### Recursive layouts

`union List[T] { Nil, Cons(T, *List[T]) }` is fine: the recursion is behind a pointer.
`union Bad { A, B(Bad) }` has infinite size and must be rejected, as must recursion through
a struct field, through an array element, and through structurally growing generic
arguments.

That machinery already exists for structs — `StructTyp._check_finite_size`,
`CycleDomain.STRUCT_LAYOUT`, `StructTyp._layout_repeats`, `errors.StructLayoutHop` and
`InfiniteSizeStructError` — and is generalised rather than duplicated: the layout walk
recurses into union payload types as well as struct fields, the cycle identity becomes
`StructTyp | UnionTyp`, and a layout hop names either a field or a variant payload position.
`CycleDomain.STRUCT_LAYOUT` becomes `TYPE_LAYOUT` and the error becomes
`InfiniteSizeTypError` to stop the names lying.

This also bounds the pattern checker: a union payload can only reach another union by value
through a finite chain, because an infinite one is rejected here.

## Pattern checking with arity

`patterns.py` currently states its own limitation:

> Every constructor has arity zero, so every matrix is one column wide and one constructor
> space describes the whole scrutinee. […] supporting constructors with arity (struct
> patterns, tuples, tagged-union payloads) would additionally require threading a
> constructor space per column through the recursion.

#24 does exactly that. This is the extension #30 built the module to absorb, and it does not
change the algorithm — only what it is parameterised over.

- `Constructor.arity` stops being a zero `ClassVar` and becomes a real field.
  `BoolConstructor` and `IntConstructor` keep arity 0.
- A constructor gains `field_spaces()`, returning the constructor space for each of its
  sub-columns. `VariantConstructor` computes it through a **lazily invoked provider**, held
  in a separate field compared `compare=False` so constructor identity stays on the
  discriminant. Laziness here is an optimisation and a safe default, not a termination
  requirement: by-value layout cycles are already rejected before any space is built, and a
  payload reached through a pointer or an array maps to an open space, so the by-value union
  graph of any constructible type is finite and eager construction would in fact terminate.
  What laziness buys is not building spaces for columns the algorithm never splits on.
- `is_useful` and `missing_patterns` take a tuple of spaces, one per column. Specialising on
  constructor `c` replaces the head space with `c.field_spaces()`; `default` drops it. The
  three comments in `_is_useful` and `_missing` that currently apologise for reusing the
  outer space at arity 0 all delete.
- `Witness.render` renders payloads: `Option::Some(_)`, `Result::Ok(Option::None)`.

A variant constructor's display name is `<template name>::<variant name>` — the union's
*bare* name, without comptime arguments. `UnionTyp.name` includes them, as `StructTyp.name`
does, so taking the display name from it would render `Option[i32]::None`, or
`Option[T]::None` inside a generic body. The scrutinee's full type is already established at
the error site, so the arguments add width without information; `Color::Blue` sets the
precedent for enums and unions should read the same.

`VariantConstructor` keeps comparing on its discriminant, not its display name. For a union
the discriminant is the variant's declaration index, so no two variants of one union
collide; the aliasing behaviour that makes `enum Alias { A = 1, B = 1 }` deduplicate is
untouched.

The checker builds a union's space from `UnionTyp.variants`, with each variant's substituted
payload types supplying its sub-column spaces, memoised per instance. Every non-union,
non-enum, non-bool column type is an open space, so a struct or tuple payload is matched only
by a wildcard or binding until #65 and #29 land.

## Lowering

### The match plan gets smaller, not bigger

Today's `MatchPlan` carries `tests: tuple[ArmTest, …]`, a flat chain of equality comparisons
against one scrutinee value. Nesting cannot be expressed in it — `Option::Some(Result::Ok(_))`
tests two values at two depths — so it goes, and with it `ArmTest`:

```python
@dataclasses.dataclass(frozen=True)
class MatchPlan:
    reachable_arms: tuple[int, ...]
    missing: tuple[Witness, ...]
```

Lowering tests each reachable arm's pattern in order, falling through to the next reachable
arm on failure, and enters the **last** reachable arm unconditionally. That last part is not
an optimisation heuristic but a theorem, and `build_match_plan` asserts it: arms after the
last reachable one are by definition covered by earlier arms, so arms `0..last` cover
everything an exhaustive match covers; therefore whatever arms `0..last-1` leave uncovered,
arm `last` covers. The checker rejects a non-exhaustive match, so lowering only ever sees
exhaustive plans and the fall-through chain always terminates in an arm rather than in "no
arm matched". This preserves the existing "no dead final comparison" behaviour with less
machinery than `ArmTest` needed.

An empty match — the `never` scrutinee case — has no reachable arms and lowers to a branch
straight to the merge block, unchanged.

### Emitting one arm's test

A sequential per-arm matcher, rather than a decision tree. Maranget's decision-tree
compilation would produce better code, but LLVM's `SimplifyCFG` recovers most of it from a
comparison chain anyway, and the naive form is linear in total pattern size, obviously
correct, and does not need a second algorithm maintained beside the usefulness one.

Tests **must** short-circuit rather than being combined into one boolean: projecting
`Option::Some`'s payload out of a value that is actually `None` reads storage that was never
initialised for that variant. At runtime that is a harmless load from valid stack storage,
but the compile-time interpreter models a union as a variant plus its payload values and has
nothing to return. So the emitter threads control flow:

```
_emit_pattern_test(pattern_ast, value, fail_bb):
    wildcard / binding      -> nothing
    int or bool literal     -> compare; cbranch(cond, cont, fail_bb); position at cont
    enum variant path       -> enum_to_int once; compare; cbranch
    union variant path      -> tag = union_tag(value); compare to the variant index;
                               cbranch; then recurse into each payload column with
                               union_payload(value, variant, i)
    or-pattern              -> each alternative but the last gets its own alt_fail block
                               and branches to a shared cont on success; the last uses
                               the real fail_bb
```

Bindings are stored at the top of the arm block, not during testing, by re-walking the
pattern along the single unambiguous path to each binding. That path is unambiguous
precisely because bindings cannot appear inside or-pattern alternatives — an or-pattern
contributes no bindings, so the walk never has to know which alternative matched.

`_build_if_arm`'s `(value, last_bb, reaches_end)` contract and the N-ary merge phi are
reused verbatim.

### Three new IR instructions

```
UnionMakeInstr(union_typ, variant_index, values)          -> UnionTyp
UnionTagInstr(operand)                                    -> the union's tag IntTyp
UnionPayloadInstr(operand, variant_index, field_index)    -> that payload field's type
```

The `match` design took "no new IR instruction" as a constraint, because a new instruction
needs an arm in `codegen._compile_instr` *and* in `comptime.Interpreter._eval_instr`, which
raises on anything it does not know. #24 pays that cost deliberately, three times, because
the alternative is worse: expressing construction and projection with the existing
`alloca`/`gep`/`ptr_cast` would make every union unusable at compile time.
`PtrCastInstr` raises `PtrCastNotComptimeEvaluableError` by design — the `Comptime*` value
model is value-oriented, not byte-oriented, so it has no sound way to reinterpret storage.

With dedicated instructions the interpreter models a union the way it already models a
struct. `ir_values.ComptimeUnion` holds its type, its variant index and its payload values;
`UnionMakeInstr` builds one, `UnionTagInstr` reads the index as an integer, and
`UnionPayloadInstr` returns a payload value — asserting the variant matches, which
short-circuited tests guarantee.

`Interpreter._check_not_temporary` must gain an explicit `ComptimeUnion` arm. Its existing
recursion is gated on `isinstance(value, ComptimeAggregate)`, so a union that is not one of
those is simply not descended into, however many payload accessors it offers. Left as-is
that is a real escape: a temporary compile-time pointer wrapped in a union would pass a
check written to catch exactly that.

Codegen expands the instructions locally: `UnionTagInstr` is `extract_value 0`;
`UnionMakeInstr` for a unit variant is `insert_value` of the tag into `undef`, and for a
payload variant is an `alloca`/store-tag/bitcast-payload/store-fields/load sequence;
`UnionPayloadInstr` is `alloca`/store/bitcast/gep/load. `mem2reg` and SROA remove the
traffic. Only codegen ever sees a bitcast, so the interpreter never meets one.

### Compile-time union constants need their own LLVM type

The interpreter runs in exactly one place: `ir_module.ModVar.initializer`. So "unions work at
compile time" means precisely "a module-level `let` can hold a union", and that runs into a
real LLVM constraint.

A `ComptimeUnion` cannot be emitted as a constant of the union's own LLVM type. That type's
payload field is `[K x iA]`, and a payload of, say, `{i8, i32}` — or worse, a pointer to
another global — has no expression as `iA` integer constants.

The fix is the one Clang uses: give the *global* a per-initializer LLVM type that mirrors the
union's layout, and register a bitcast to the union's pointer type as the item's value, so
every use is unaffected. For `union U { I(i32), D(i64) }` initialised to `I(5)`:

```llvm
@g = global <{ i8, [7 x i8], i32, [4 x i8] }> <{ i8 0, [7 x i8] undef, i32 5, [4 x i8] undef }>, align 8
```

Three details make that sound:

- The ad-hoc type is **packed**, with explicit `i8` padding computed from the real element
  offsets of the union's own LLVM type via `target_data()`. Unpacked, LLVM would re-pad it
  to its own liking and the constant could disagree with the type the rest of the program
  uses.
- Packing drops the type's alignment to 1, so the global carries an explicit `align`
  matching the union's ABI alignment.
- The construction is recursive, and recursion means more than "pack the container too".
  Every level must reproduce the *original* layout: each struct field at its original
  offset, each array element at its original stride, and the original tail padding. An array
  whose elements are different variants has ad-hoc element types that differ from each
  other, so it becomes a packed literal struct of per-element constants padded to the
  original stride, rather than an `ArrayType`. This is Clang's behaviour for C union
  initializers, and the contract to implement is "build `(constant, ad_hoc_type)` from the
  original target layout, inserting explicit `i8` padding to the original offsets and size
  at every aggregate level".

This **does** change the declaration ordering, and the earlier draft of this document was
wrong to say otherwise. `_declare_mod_var` reads `var.typ`, which forces `initializer` — but
only as a `ComptimeValue`, the interpreter's result. The ad-hoc LLVM type exists only once
that value has been lowered to an LLVM constant, and today that lowering happens later, in
`_compile_mod_var`. Since a global's LLVM type is fixed when the `GlobalVariable` is
constructed, `_declare_mod_var` must now lower the initializer — or at least compute its
ad-hoc type — during the declare pass. That has a knock-on: lowering a constant whose payload
points at another module variable forces that variable's global to be declared early too.

The bookkeeping either side of it changes as well. Today
`_declare_mod_var` registers the `GlobalVariable` as the item's value and `_compile_mod_var`
looks that same value back up to assign `.initializer`. A bitcast constant expression is not
a `GlobalVariable` and cannot receive an initializer, so registering the bitcast naively
would break every union-typed module variable at compile time. The raw global must be
retained separately for initializer, linkage and alignment, with the bitcast exposed only as
the value other code consumes.

Two tests, not one. An ABI-size assertion catches a wrong top-level type but not a wrong
offset or stride, so the emitted global's size must be checked against `__size_of` *and* a
running program must read fields and elements back out of module-level struct and array
constants containing mixed variants, including a pointer-bearing payload.

## Monomorphization

`mono.discover` walks `StructTyp` requests only. Unions add a second request log, and the
two are **mutually recursive**: forcing a struct's fields can request a union instance, and
forcing that union's payloads can request a further struct instance. Today's discovery runs
functions then structs, draining each log once, which is sufficient only because the
dependency runs one way. It is not sufficient here — whichever of the struct and union
passes runs second can append to the other's already-drained log, and `MonoResult` would
silently omit a reachable instance.

So discovery becomes a **joint fixed point**: persistent cursors over both logs, alternating
until neither advances. `MonoResult` gains `union_instances`. `codegen.compile` declares
union instances in the same types-first pass as struct instances; `ll_typs`' existing
identified-type recursion guard is shared between the two, keyed by qualified name as it
already is.

`typs.StructTyp`'s over-approximation caveat applies unchanged to unions: validating an
unused declaration can request an instance.

## Diagnostics

New:

- `DuplicateVariantInUnionDefnError`
- `WrongNumberOfPayloadPatternsError` — variant, given arity, expected arity
- `PayloadPatternOnNonVariantError`
- `VariantConstructorNotAValueError` — a payload variant named outside call position
- `UnitVariantCalledError`

Reused: `ItemNotFoundError` for an unknown variant name, `PatternTypMismatchError` for a
variant of the wrong union, `NotEnoughArgsError`/`TooManyArgsError` for a construction with
the wrong payload arity, `ComptimeArgsOnNonGenericItemError` for `Option::Some[i32]` — it
already exists and already says the right thing — plus `CannotInferComptimeArgError`,
`ReservedNameError`, `DuplicateItemDefnError`, `NonExhaustiveMatchError` and
`UnreachableMatchArmWarning`.

Renamed, because unions make the current names inaccurate: `InfiniteSizeStructError` →
`InfiniteSizeTypError`, `StructLayoutHop` → `TypLayoutHop`, `ImplForNonStructTypError` →
`ImplForNonNominalTypError`, `ImplForNonLocalStructTypError` → `ImplForNonLocalTypError`.

`NonExhaustiveMatchError` needs no change: it already takes a `Sequence[Witness]` and emits
one note per uncovered case, which matters more here than for enums because a union match
can miss many more shapes at once.

## Examples

Accepted:

```leech
union Option[T] { None, Some(T) }
union Result[T, E] { Ok(T), Err(E) }

pub fn unwrap_or[T](o: Option[T], dflt: T) T {
    return match (o) {
        Option::Some(let v) => v,
        Option::None        => dflt,
    };
}

pub fn main() i32 {
    let r: Result[Option[i32], i32] = Result::Ok(Option::Some(7i32));
    return match (r) {
        Result::Ok(Option::Some(let x)) => x,
        Result::Ok(Option::None)        => 1i32,
        Result::Err(let e)              => e,
    };
}
```

Rejected:

```leech
match (o) { Option::Some(let x) => x }
//    non-exhaustive: uncovered pattern "Option::None"

match (o) { Option::Some => 0i32, Option::None => 1i32 }
//    Option::Some takes 1 payload pattern, 0 given

match (o) { Option::Some(let x) | Option::None => 0i32 }
//    binding inside an or-pattern alternative

let x = Option::None;              // cannot infer comptime argument "T" for "Option"
let f = Option::Some;              // Option::Some is not a value
let y = Option::None(1i32);        // Option::None takes no payload
union Bad { A, B(Bad) }            // type "Bad" has infinite size
```

## Risks

- **The ad-hoc constant type is the sharpest edge.** Two independently built LLVM types must
  agree on size and offsets, and getting it wrong is silent. Mitigated by packing with
  explicitly computed padding and by a size-agreement test.
- **The `MatchPlan` change touches delivered, tested `match` behaviour.** Enum, bool and
  integer matches all re-lower through the new emitter. Mitigated by `tests/test_match.py`
  and `tests/test_patterns.py` being run unchanged, which is the point of doing the plan
  change as its own reviewable task before unions depend on it.
- **The coherence helpers fail silently, not loudly**, if a union arm is missed in
  `_contains_typ`, `unify`, `_head_shape` or `_is_local`. Mitigated by naming each site
  individually in the plan and by overlapping-impl tests over generic unions.
- **`_infer_comptime_args` is known-buggy** (#72, #73 — a speculative probe letting its
  diagnostics escape). Variant construction reuses it and so inherits both. Not fixed here;
  the follow-on fix closes both for calls and variants at once.

## Open questions

None blocking. Two worth recording:

- Whether a variant's tag should ever be observable without `match` — an
  `__union_tag`-style intrinsic mirroring `__enum_to_int`. Not needed for anything #24
  delivers, and adding it later is backwards-compatible.
- Whether the eventual `Option`/`Result` prelude types should be spelled with methods
  (`o.unwrap_or(0)`) or free functions. `impl` on unions makes both possible; the follow-on
  prelude issue decides.

## Implementation plan

See `docs/plans/2026-09-01-tagged-unions.md`.
