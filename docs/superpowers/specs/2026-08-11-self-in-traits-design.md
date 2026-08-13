<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Bind `Self` in trait declarations and inherent impls

## Problem

`Self` is bound in exactly one place: `Impl.__init__`
(`self.env.add_container("Self", self_typ)`). Neither a trait declaration
nor an inherent impl binds it, so both of these fail with
`ItemNotFoundError: Type or module "Self" not found`:

```leech
trait Comparable { fn cmp(*self, other: *Self) i32; }
impl Foo { fn twin(*self) Self { ... } }
```

A trait therefore cannot state that a method takes or returns the
implementing type. The only way to express that today is to give the trait
a type parameter and have every use site bound it F-style
(`trait Comparable[T]` used as `fn max[T: Comparable[T]]`), which needs
generic traits — a much later gap — to say something `Self` says directly.

This is gap 2 in
[the generics/traits roadmap](2026-08-08-generics-traits-gap-assessment-design.md#self-in-trait-declarations).

## Goal

- `fn cmp(*self, other: *Self) i32;` in a trait declaration resolves, with
  `Self` meaning the implementing type.
- `fn twin(*self) Self` in an inherent impl resolves, including under a
  generic `impl[T] Foo[T]`.
- An impl may write either the concrete type or `Self`; both are accepted
  for the same method.
- `Self` cannot be declared as a type or type-parameter name.
- No behavior change for trait impls, which already bind `Self`.

## Design

### `Self` in trait method signatures

`TraitMethod.fn_typ_for_self` is the only place a trait method's parameter
and return types are ever resolved, and every call already supplies a
concrete self type — `Impl.add_method` passes the impl's self type, and
`typcheck._resolve_callee` passes the receiver's pointee when dispatching
through a trait bound. So `Self` needs no representation of its own: it can
be bound as a name, per call, in a child of the trait's env.

```python
def fn_typ_for_self(self, self_typ: typs.Typ) -> typs.FnTyp:
    assert self.ast.receiver is not None
    recv_typ = typs.PtrTyp.get_or_create(self_typ, typs.Mutability.from_ast(self.ast.receiver.mut))
    env = self._trait._env.new_child()
    env.add_container("Self", self_typ)
    param_typs = [recv_typ] + [
        typs.Typ.from_ast(param_ast.typ, env) for param_ast in self.ast.params
    ]
    ret_typ = typs.VOID if self.ast.ret_typ is None else typs.Typ.from_ast(self.ast.ret_typ, env)
    return typs.FnTyp.get_or_create(ret_typ, tuple(param_typs))
```

The receiver keeps its existing handling — `*self` already derives from
`self_typ` and the receiver's own mutability, so this only adds `Self` for
explicitly written parameter and return types.

The decisive property of binding rather than substituting: **an
unsubstituted `Self` cannot exist.** `Self` never becomes a `Typ`; it is a
name that only ever resolves to a real type. The class of bugs where an
abstract `Self` escapes into type checking or codegen is structurally
impossible rather than merely avoided. See [Rejected
alternative](#rejected-alternative-an-abstract-self-placeholder).

Because the trait side substitutes nothing, dispatch through a trait bound
gets the right answer for free: `_resolve_callee` calls
`fn_typ_for_self(pointee_typ)` where the pointee is the receiver's
`TypParamTyp`, so `*Self` resolves to `*T` inside a generic body.

### `Self` in inherent impls

One line in `_build_inherent_impl_defn`, mirroring what trait impls already
do:

```python
fn_env.add_container("Self", typ)
```

For `impl[T] Foo[T]`, `typ` is already the abstract `Foo[T]` instance that
the impl's methods are built against, so `Self` substitutes through the
existing `StructTyp` machinery when a method is instantiated. No new
mechanism.

### Impls may write `Self` or the concrete type

`Impl.add_method` compares `fn.fn_typ is not
trait_method.fn_typ_for_self(self._self_typ)`. An impl's own env binds
`Self` to its concrete self type, and `FnTyp` is interned, so an impl
written as `fn cmp(*self, other: *i32)` and one written as
`fn cmp(*self, other: *Self)` produce the identical `FnTyp` and both match.

Nothing is added to make this work — it falls out of interning plus both
sides funnelling through `fn_typ_for_self`. It is called out because it is
a real, testable guarantee, not an accident to be discovered later.

### Reserving `Self`

`Self` is a plain identifier in the grammar, so `struct Self`,
`trait Foo[Self]` and `impl[Self] Foo[Self]` are all currently writable.
Left alone, the two new binding sites would disagree about what that means:
the trait side's child env would silently *shadow* a same-named type
parameter, while the inherent-impl side would raise
`DuplicateItemDefnError` because both names land in one scope.

Rather than document that asymmetry, `Self` becomes a reserved name. Every
language with a `Self`-like type reserves it (Rust, Swift, Scala,
TypeScript); the languages that don't have one, like Java and C#, end up
threading a `TSelf` type parameter by convention instead. Reserving is also
cheapest now — once programs exist that name something `Self`, it becomes a
breaking change.

This needs no grammar change. `Self` must stay a valid identifier in *use*
position (it is resolved as an ordinary path), so the check belongs at
declaration sites, of which there are two:

- **`typs.typ_params_from_ast`** — the single funnel for every generic
  parameter declaration, covering functions, structs, traits and impls
  alike.
- **`Mod._add_item`**, when the item's namespace is `CONTAINERS` — covering
  struct, enum and trait declarations and imports, since `_add_item`
  derives the namespace from the value it is given.

Both raise a new `errors.ReservedTypNameError`. `Impl` and `TraitMethod`
bind `Self` through `Env.add_container` directly, not through either
checkpoint, so the legitimate bindings are unaffected. Builtins are
likewise unaffected — `ir_builtins` binds through `add_container` too.

## Non-goals

Each of these is added to the roadmap's future-work list rather than
handled here.

- **`Self` in a trait's own generic-parameter bounds**
  (`trait Convert[T: Convert[Self]]`). Bounds resolve through
  `check_typ_arg_bounds` in an env derived from the instantiation site, not
  from the trait, so `Self` does not resolve there and the program fails
  with `ItemNotFoundError`. Supporting it means threading the current self
  type down alongside `in_progress` — note that binding `Self` to the
  `typ_arg` being checked would be *wrong*, since `Self` refers to the
  enclosing obligation's subject, not the current one. It has no observable
  benefit until generic traits exist, because such a bound carries type
  arguments and so already stops at the unsupported-feature guard.
- **`Self` in struct definitions** (`struct Node { next: *Self }`). Purely
  sugar today, since `struct Node { next: *Node }` already works.
- **Abstract `Self`.** No `Self`-typed value ever exists under this design,
  so nothing can call a method on one. That is [default method
  bodies](2026-08-08-generics-traits-gap-assessment-design.md#default-method-bodies)'
  problem, and it needs same-trait method *resolution*, not just a binder.

## Rejected alternative: an abstract `Self` placeholder

The roadmap sketched binding `Self` to a `TypParamTyp` in `Trait.__init__`
and substituting it away in `fn_typ_for_self`. That is what default method
bodies will eventually need, since checking a body once requires a real
abstract binder.

It was rejected for this change. The placeholder would be created and
immediately substituted away on every existing path, making it unobservable
and therefore only indirectly testable, while introducing exactly the
leak risk that binding-by-name forecloses. It also would not be sufficient
for the gap that wants it: default method bodies additionally need method
lookup on an abstract `Self` to find the declaring trait's own methods,
which a bare placeholder does not provide. Introducing the abstract binder
alongside that resolution machinery, when its requirements are in hand, is
better than guessing at it now.

## Testing

**Trait declarations.** `Self` as a parameter type and as a return type,
compiled and run end to end. An impl written with the concrete type and an
equivalent impl written with `Self`, both accepted. A genuinely mismatched
impl still raising `TraitMethodSignatureMismatchError` — this is the
regression guard proving the previous case reflects real type identity
rather than a weakened comparison. A method called through a trait bound
(`fn f[T: Comparable](a: *T, b: *T) i32 { a.cmp(b) }`), covering the
`_resolve_callee` path where `Self` resolves to a `TypParamTyp`.

**Inherent impls.** `fn twin(*self) Self` on a plain struct, and on a
generic `impl[T] Foo[T]` where the method is instantiated at two different
type arguments, proving `Self` substitutes rather than sticking to the
first instantiation.

**Reservation.** `struct Self`, `trait Self`, `fn f[Self](x: Self)` and
`impl[Self] Foo[Self]` each rejected with `ReservedTypNameError`, covering
both checkpoints. A trait impl's own `Self` still working, as the guard
that the reservation didn't over-reach.

**Documented limitations.** `Self` in a trait's generic-parameter bounds,
and `Self` in a free function, both still raising `ItemNotFoundError` —
pinning the non-goals so a later change that fixes them has to do so
deliberately.
