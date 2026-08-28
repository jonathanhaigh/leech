<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Comptime Generic Parameters

## Goal

Resolve GitHub issue #39 by letting a generic parameter bind a compile-time
value instead of only a type:

```text
struct Buf[T, N: usize] {
    data: [T; N],
}

fn first[T, N: usize](b: *Buf[T, N]) T {
    return b.data.[0];
}

let buf: Buf[i32, 4] = ...;
let x = first(&buf);          // N inferred as 4
let y = first[i32, 4](&buf);  // N given explicitly
```

`N: usize` uses the same bound-list syntax as a trait-bounded type parameter
(`T: Show`); which one it is gets resolved by what the name after `:` names,
not by a keyword. `array[T, N]` from #9, a safe `from_array[T, N]` slice
constructor from #26, and target-selected C integer widths from #33 all
become expressible once a value can flow through the same parameter and
argument machinery a type does today.

Throughout the codebase, "generic parameter/argument" is renamed to
"comptime parameter/argument" (`comptime_param`/`comptime_arg`) to name the
concept precisely: a parameter is generic either way, but what it binds is a
type or a compile-time value, and code that used to say "type parameter"
when it meant either now says what it means.

## Current Problem

`ast.GenericParam` (`generic_param: ident (":" bound_list)?`) has no value
form, and `generic_args` accepts only `typ`. `typs.TypParamTyp` is the only
parameter representation, `ArrayTyp.length` is a bare Python `int` rather
than a type-parameter-shaped node, and every monomorphization key
(`FnInstance`'s arguments, `StructTyp.typ_args`) is typed `tuple[Typ, ...]`
with no way to carry a value alongside a type.

## Syntax

`generic_param`'s grammar is unchanged: `ident (":" bound_list)?`. A
single-entry bound list is resolved by what the name denotes: a `Trait`
makes this a type parameter with that bound (today's behavior); an `IntTyp`
or `BoolTyp` makes this a value parameter of that type. A `Typ` of any
other kind (e.g. `N: SomeStruct`) is the existing `BoundNotATraitError`,
broadened to `InvalidComptimeBoundError` - one error for "this bound is
usable as neither a trait nor a value parameter's type," not two, since
which the author intended isn't decidable from the syntax and there's no
principled reason to give one non-trait `Typ` kind (a struct) a different
diagnostic from another (an enum). A multi-entry bound list (`T: A + B`) is
always a set of trait bounds, since a value parameter has exactly one type,
never several.

`generic_args` gains two new alternatives so a use site can supply a literal
value directly:

```text
generic_args: "[" _generic_arg_list "]"
_generic_arg_list: generic_arg ("," generic_arg)* ","?
generic_arg: typ
  | int_lit
  | bool_lit
```

A bare identifier argument (`f[i32, N]`) still parses through `typ`'s
existing `basic_typ: path` production and is resolved the same way as the
parameter side: if the name denotes a type, it's a type argument; if it
denotes an in-scope value parameter (or, in a concrete instantiation like
`f[i32, 4]`, is itself already a literal), it's a value argument. `int_lit`
and `bool_lit` are lexically unambiguous with every `typ` form, so they need
no such resolution.

This LALR grammar has no conflict introduced by this change, verified by
building the modified grammar with `lark`'s `lalr` parser and parsing
representative inputs (`Box[i32]`, `Box[i32, 4]`, `Box[i32, N]`, `*mut i32`,
`[i32; 4]`) before writing this spec.

Value arguments are restricted to literals and bare identifiers in this
issue - no arithmetic (`f[i32, N * 2]`). This is sufficient for #9, #26, #14,
and #33, and keeps this issue from also having to hook the comptime
tree-CFG-interpreter into comptime-argument resolution. A later issue can add
parenthesized compound expressions (`f[i32, (N * 2)]`) without breaking this
grammar or representation, because a bare identifier's ambiguity with a type
name is resolved post-parse either way, and parens are what make a compound
expression unambiguous against `typ`.

`array_typ`'s length gains the same bare-identifier form:

```text
array_length: NONNEG_INT | path
```

A `path` here can only mean a reference to an in-scope value parameter;
anything else it might resolve to is a diagnostic, not a new meaning.

## Type System Representation

`typs.TypParamTyp` splits into a shared base and two concrete kinds:

```python
class ComptimeParamTyp(Typ):
    """Shared owner/index/name/cache-key machinery for a comptime parameter."""
    _owner: Final[Hashable]
    _index: Final[int]
    _name: Final[str]
    # cache_key = (cls, owner, index)
    # substitute_typ_params = mapping.get(self, self)
    # is_concrete -> False

class TypParamTyp(ComptimeParamTyp):
    bounds: Final[tuple[ast.BasicTyp, ...]]
    decl_env: Final[Optional[ir_env.Env]]
    # unchanged from today

class ValueParamTyp(ComptimeParamTyp):
    value_typ: Final[Typ]   # e.g. USIZE, BOOL
```

`ComptimeParamTyp` is never instantiated directly - it exists so code that
doesn't care which kind of parameter it's holding (substitution, cache-key
tuples, mapping types) can say `ComptimeParamTyp` instead of a union. Code
that does care (bound checking, diagnostics) narrows with `isinstance`.

A concrete value argument is represented by a new leaf `Typ`:

```python
class ComptimeValueTyp(Typ):
    value_typ: Final[Typ]     # e.g. USIZE, BOOL
    value: Final[int | bool]
    # cache_key = (cls, value_typ, value)  # interned via Typ.get_or_create
    # name -> str(value).lower()            # "4", "true" (not Python's "True")
    # qualified_name -> name                 # nothing to qualify
    # substitute_typ_params -> self          # concrete, contains no parameters
    # is_concrete -> True
```

The name mirrors `ir_values.ComptimeValue`: this is that same notion's
type-level counterpart, a `Typ` node whose entire content is one concrete
compile-time value. It's deliberately not `ValueTyp`, which would collide
with the unrelated, well-established "value type vs. reference type"
distinction. It's interned through the same weak `Typ._cache` every other
`Typ` uses (the way `IntTyp.get_or_create(32, SIGNED)` already is), so
`ComptimeValueTyp.get_or_create(USIZE, 4) is ComptimeValueTyp.get_or_create(USIZE, 4)`
- two instantiations with equal values are the same object, satisfying the
"share one instance" acceptance criterion without any new deduplication
logic. `(value_typ, value)` as the key means a `bool` and an `int` value
never alias even where `hash(True) == hash(1)`, because `value_typ` (`BOOL`
vs. an `IntTyp`) differs between them.

`ArrayTyp.length` changes from `int` to `Typ`, holding a `ComptimeValueTyp`
once concrete or a `ValueParamTyp` before substitution - the same shape
`element_typ` already has. Consequences, all mechanical:

- `.name`/`.qualified_name` render `self.length.name`/`.qualified_name`
  instead of the bare int; `ComptimeValueTyp.name` being `str(value)` keeps
  `[i32; 4]` byte-identical to today's output.
- `substitute_typ_params` recurses into `length` too, not just
  `element_typ`.
- `infer_typ_args` binds `length` against an actual array argument's length,
  so `N` in `fn f[T, N: usize](x: [T; N])` infers from a call the same way
  `T` does, with no special-casing.
- `typs.unify`'s `left.length == right.length` becomes
  `unify(left.length, right.length)`, so a symbolic length participates in
  the same trait-obligation unification a type parameter already does.
- The few call sites reading `.length` as a raw `int` for codegen
  (`codegen.py`, `ir_builder.py`, `ir_values.py`) instead read
  `asserts.checked_cast(typ.length, ComptimeValueTyp).value` - safe, because
  by the time code reaches these sites every type involved is already
  concrete.

## Resolution and Inference

Resolving an explicit `comptime_arg` against its declared
`ComptimeParamTyp` branches on which concrete subclass the parameter is:

- `TypParamTyp` - resolve the argument as a type (today's behavior).
- `ValueParamTyp` - the argument must be an `int_lit`/`bool_lit` (evaluated
  directly to a `ComptimeValueTyp`) or a bare identifier resolving to an
  in-scope value (another value parameter, substituted through the caller's
  own mapping). Anything else raises `WrongKindOfComptimeArgError` (see
  Diagnostics).

Bound checking (`check_typ_arg_bounds`, renamed
`check_comptime_arg_bounds`) likewise branches: a `TypParamTyp` checks trait
bounds as today; a `ValueParamTyp` checks that the argument's type equals
`value_typ` exactly (rejecting `f[i32, true]` against `N: usize`), raising
`WrongComptimeValueTypError` (see Diagnostics).

Inference (`_infer_typ_args`, renamed `_infer_comptime_args`) needs no new
code path: it already calls `declared_typ.infer_typ_args(actual_typ,
bindings)` structurally over the declared parameter type, and once
`ArrayTyp.infer_typ_args` also unifies `length`, `N` infers from a concrete
`[T; N]`-typed call argument through the exact same walk `T` already takes.

`array_length`'s bare-identifier form resolves the same way generic
arguments do: the name must denote an in-scope value (a `ValueParamTyp` or
already-substituted `ComptimeValueTyp`), or it's a diagnostic.

## Value Parameters as Expressions

A value parameter is usable structurally (`[T; N]`, `Buf[T, N]`) the same
way a type parameter is, but that alone doesn't cover its most concrete
motivating case: a `from_array[T, N]` slice constructor (#26) must *store*
`N` into a runtime `len: usize` field, which means referencing a bare `N` as
an ordinary expression inside a function body, e.g.
`Slice { ptr: p, len: N }` or `return N;`.

`ValueParamTyp` is bound into both of `ir_env.Env`'s existing namespaces
wherever a comptime parameter is bound today - `CONTAINERS` (as now, for
`[T; N]` and `Buf[T, N]` structural use) and, new, `VARS` (for expression
use). This happens at every site that currently does
`env.add_container(typ_param.name, typ_param)` for a declaration's own
parameters: `fn_defn`, `impl_defn`, `trait_defn`, and `struct_defn`.
`TypParamTyp` is never added to `VARS` - a type has no runtime value to
reference as an expression.

Resolving a bare `var_expr` name that reaches a `ValueParamTyp` follows the
exact precedent `ir_values.ComptimeEnum` already sets for "a name that
resolves directly to a known compile-time value, not a place": in
`typcheck._check_var_expr`, it types the expression as the parameter's
`value_typ` and is not addressable (no `&N`), the same way an enum variant
name isn't. `resolve.VarTarget` widens to include `typs.ValueParamTyp`
alongside its existing `ir_values.ComptimeEnum` case.

Lowering follows the same precedent in `ir_builder._build_var_expr`: where
`ComptimeEnum` targets are built in place, a `ValueParamTyp` target is
substituted through the builder's existing `_typ_arg_mapping` (renamed with
the rest of `Mapping[TypParamTyp, Typ]`) to its concrete `ComptimeValueTyp`
for this instantiation, then materialized as an `ir_values.ComptimeInt` or
`ir_values.ComptimeBool` depending on `value_typ`.

## Monomorphization, Caching, and Mangling

`FnInstance`'s arguments and `StructTyp.typ_args` (renamed
`comptime_args`) need no structural change: both are already
`tuple[Typ, ...]`, and `ComptimeValueTyp` is just another `Typ` living in
that tuple. Substitution, cache-key tuples, and the `zip(all_params, args)`
pairing in `FnInstance.__init__` already thread arbitrary `Typ`s through
unmodified.

Mangled names need no rendering-code change either:
`FnInstance._render_name` and `StructTyp.qualified_name` already call
`.name`/`.qualified_name` on each argument, and a `ComptimeValueTyp` renders
as `"4"`/`"true"`, giving `Buf[i32, 4]`, `first[i32, 4]`, matching the
scheme `ArrayTyp` already uses for its own length.

## Diagnostics

The existing `BoundNotATraitError` broadens to `InvalidComptimeBoundError`:
a comptime parameter's single bound resolving to neither a `Trait` nor a
supported value type (`IntTyp`/`BoolTyp`) is one failure mode, covering
both a genuine trait-bound typo and an unsupported value-parameter type
(a struct, an enum, anything else) with one diagnostic - see Syntax above.

Two new errors, named to match the existing `*TypArgsError`/`*TypParamError`
family (renamed to the `Comptime` vocabulary per the rename below):

- `WrongKindOfComptimeArgError` - an explicit argument is a type where a
  value was declared, or vice versa.
- `WrongComptimeValueTypError` - a value argument's type doesn't match its
  parameter's declared `value_typ`, e.g. `f[i32, true]` against `N: usize`.
  Sits alongside the existing `UnsatisfiedBoundError`, which continues to
  cover unsatisfied trait bounds on type parameters.

An `array_length` identifier not resolving to an in-scope value reuses the
existing unresolved-name diagnostic family rather than introducing a new
error type.

## Terminology Rename

Applied consistently from the grammar down, so the vocabulary doesn't split
between layers:

| Layer | Before | After |
|---|---|---|
| Grammar (`leech.lark`) | `generic_params`, `generic_param`, `generic_args` | `comptime_params`, `comptime_param`, `comptime_args` |
| AST (`ast.py`) | `GenericParam`, `.generic_params`, `.generic_args` | `ComptimeParam`, `.comptime_params`, `.comptime_args` |
| Types (`typs.py`) | `TypParamTyp`, `typ_params_from_ast`, `.typ_params`, `typ_args` | `ComptimeParamTyp`/`TypParamTyp`/`ValueParamTyp`, `comptime_params_from_ast`, `.comptime_params`, `comptime_args` |
| IR (`ir_module.py`, `ir_traits.py`, `ir_builder.py`, `ir_builtins.py`) | `.typ_params`, `typ_args`, `_infer_typ_args`, `_resolve_explicit_typ_args` | `.comptime_params`, `comptime_args`, `_infer_comptime_args`, `_resolve_explicit_comptime_args` |
| Diagnostics (`errors.py`) | `MissingTypArgsError`, `CannotInferTypArgError`, `WrongNumberOfTypArgsError`, `TypArgsOnNonGenericItemError`, `UnconstrainedImplTypParamError` | `MissingComptimeArgsError`, `CannotInferComptimeArgError`, `WrongNumberOfComptimeArgsError`, `ComptimeArgsOnNonGenericItemError`, `UnconstrainedImplComptimeParamError` |

`is_generic` (an adjective, not a noun naming the parameter/argument
concept) keeps its name. User-facing diagnostic *prose* keeps reading
naturally in either vocabulary and isn't forced to say "comptime" where
"generic" reads better; this rename is about internal identifiers and error
*class names*, not about inventing new surface syntax words - source code
still spells these `[T, N: usize]`.

This rename touches `typs.py`, `ir_module.py`, `ir_traits.py`,
`typcheck.py`, `ir_builder.py`, `ir_builtins.py`, `ast.py`, and `errors.py`,
plus every test that references a renamed identifier. It is mechanical but
not blind: a `typ_param`/`typ_arg`-named local variable that only ever holds
an actual `Typ` in a context where a value could never appear is left alone,
since the rename tracks the comptime-parameter *concept*, not every
similarly spelled identifier.

`impl` and `trait` declarations share the identical `generic_params`
grammar production and `comptime_params_from_ast` machinery, so they gain
value-parameter capability incidentally, e.g. `impl[N: usize] ...`. This
isn't blocked, and per this design it's in scope for testing (see below),
not just an unblocked side effect.

## Testing

- `tests/test_generic_fns.py` - a value parameter declaration; explicit
  arguments (`f[i32, 4]`); inference from a concrete `[T; N]`-typed
  argument; referencing a value parameter as an expression in the body
  (`return N;`) and running the compiled program to check the returned
  value per instantiation; `WrongKindOfComptimeArgError` and
  value-type-mismatch diagnostics; identical values sharing one instance,
  checked against emitted mangled symbol names.
- `tests/test_generic_structs.py` - a struct with a value parameter used in
  an array field (`struct Buf[T, N: usize] { data: [T; N] }`);
  construction; field access; mangled name.
- `tests/test_impl.py` - an `impl[N: usize] ...` block using its value
  parameter in a method.
- `tests/test_traits.py` - a trait declaring a value parameter in its own
  `generic_params`.
- `tests/test_arrays.py` - `[T; N]` with `N` a value-parameter reference
  rather than a literal, substituted through to a concrete size; the
  `unify()` path for trait-bound checking over array types with symbolic
  lengths.
- Parser-level tests for the grammar changes: `generic_arg` accepting
  `int_lit`/`bool_lit`, `array_length` accepting an identifier.

## Out of Scope

Arbitrary comptime expressions as value arguments (`f[i32, N * 2]`) -
deferred to a later issue; the grammar and representation chosen here don't
foreclose it. Value parameter types beyond integers and `bool` (enums,
arbitrary comptime-evaluable values) - not needed by any currently known
consumer. Actually wiring `array` up as a generic type usable via
`array[T, N]` application syntax - that's issue #9's own work, built on top
of the machinery this issue provides.
