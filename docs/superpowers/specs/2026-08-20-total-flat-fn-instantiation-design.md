<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Total and flat function instantiation

## Goal

Give every function declaration one instantiation entry point whose arguments
fully identify the concrete function instance. Every source or built-in
function body emitted by code generation belongs to an `ir_module.FnInstance`;
template declarations are never body-emission targets. Bodyless external
`FnDecl`s remain LLVM declarations and are not instantiated.

This is Stage 2 of the
[`Fn` hierarchy restructure staging plan](../plans/2026-08-17-fn-hierarchy-restructure-staging.md).
It preserves the language accepted by the compiler.

## Instantiation interface

The existing `ir_module.FnTemplate` declaration protocol exposes one operation:

```python
def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance: ...
```

Source-defined and built-in function declarations both use this interface.
Their internal instance construction may differ, but callers do not distinguish
them. `FnInstance` is an instance rather than a declaration and does not expose
`instantiate`; Stage 3 will make that nominal distinction explicit.

This stage deletes the old `Fn.instance`, `Fn.impl_instance`, and
`Fn.for_recv_typ` entry points. It does not rename any classes; the class-name
cleanup remains isolated in Stage 4.

## Flat argument order

The argument tuple contains every substitution needed by the instance, in this
fixed order:

1. the parent impl's type arguments, in the impl declaration's parameter order;
2. the function's own type arguments, in the function declaration's parameter
   order.

For example, an instance of a method declared as follows:

```leech
impl[T] Box[T] {
    fn convert[U](...) ...
}
```

has the flat argument tuple `(T_argument, U_argument)`. A free function has an
empty impl prefix. A wholly non-generic function is `instantiate(())`.

`ir_traits.Impl` exposes its type parameters in declaration order so callers do
not recover that order from a concrete receiver. Adding that property is part
of this stage; Stage 1 did not add it. `_build_impl_defn` creates that tuple
once using the enclosing module environment, binds the same objects into the
impl environment, and passes the tuple into `Impl`. This preserves each
`TypParamTyp`'s declaration environment for later bound resolution.

`Fn.instantiate` validates the total arity, splits the tuple at the
impl-parameter count, and builds separate impl and function bindings. Supplying
the wrong arity is an internal compiler error, not a user diagnostic:
user-facing type-argument checks happen before instantiation.

### Constrained impl parameters

Every impl type parameter must be constrained by the impl head, following
Rust's rule. With generic traits not yet supported, that means each parameter
must occur in the impl's self type. Merely appearing in another parameter's
bound or in a function signature or body does not constrain it.

For example, this is rejected because `U` cannot be recovered when selecting
the impl for a concrete `Box`:

```leech
impl[T, U] Box[T] {
    fn get(*self) T { return self.*.val; }
}
```

The compiler reports a user-facing unconstrained-impl-type-parameter error at
`U`'s declaration. This is a deliberate defect fix: the program is accepted
today only because the unused parameter is silently discarded. It gets a
focused regression test and a separate commit before the flat instantiation
refactor. When generic traits are added, appearing in the implemented trait's
arguments may also constrain a parameter, matching Rust's broader rule.

## Instance identity and construction

Each declaration owns a cache keyed only by the complete flat argument tuple.
Repeated calls with the same tuple return the identical `FnInstance` object.
The cache no longer uses `(Optional[Typ], tuple[Typ, ...])`, because the impl
receiver is represented by its positional arguments rather than as a separate
input.

`FnInstance` stores the complete flat tuple but also retains the split between
its impl prefix and function suffix. It receives already-determined impl and
function bindings. Its constructor no longer infers impl parameters by
structurally matching the concrete receiver against `impl.self_typ`. The
concrete receiver is instead the result of substituting the impl prefix into
`impl.self_typ`.

Substituting type parameters into an existing instance becomes uniform:
substitute into every flat argument, then call `instantiate` with the resulting
tuple. This replaces `FnInstance.substitute_typ_params`'s current choice between
`instance` and `impl_instance`.

Free functions and impl functions still require different name prefixes, and
functions outside an impl still have no impl siblings. The simplification is
that these decisions use the declaration's single `impl` parent pointer rather
than the removed optional concrete-receiver representation. The branches do
not themselves disappear.

## Lookup and sibling calls

Impl lookup already structurally matches an abstract impl self type against the
requested receiver. That match supplies the impl-argument prefix in declaration
order. Associated-function and method lookup instantiate the selected function
with that prefix plus any function arguments.

When a function body calls a sibling in the same impl, the builder takes the
current instance's impl prefix and appends the sibling call's function
arguments. A non-sibling source `Fn` is converted with `instantiate(())`; no
lowering path returns a bare declaration as a callable value. The builder does
not re-infer the prefix and does not branch on whether its current function is
an `FnInstance`, because every lowered body now belongs to an instance.

Symbol rendering uses only the function-argument suffix for the function's
`[args]` portion. Impl arguments affect the substituted receiver prefix, but
must not also appear after the function name. Thus `Box[i32]::unwrap` does not
become `Box[i32]::unwrap[i32]`.

## Checking, module construction, and reachability

Type checking and emission reachability are separate. `Mod.build()` eagerly
forces `typ_check_results` for every source function and module variable after
all declarations and impls have been built. This preserves today's rejection
of errors in unreachable private functions, impl methods, generic functions,
generic impl methods, and variable initializers. Lowering a CFG is not required
merely to check a declaration.

`Mod` keeps one collection of every source `Fn` declaration, rather than the
current special `generic_fns` collection. `_build_defn` and `_build_impl_fns`
append every function to that collection. Impl functions are registered in
their `Impl` and bound in `impl.env`; they are not added as synthetic module
items. Free functions remain module items so name resolution and visibility are
unchanged. Generic free functions remain wrapped as `GenericFn` until Stage 3.

Concrete non-generic declarations create their `()` instance when their
callable value is bound or selected. A function in a generic impl is generic at
the impl level even if it has no function-level parameters, so lookup supplies
its non-empty impl prefix. Generic declarations otherwise produce instances
only when referenced with concrete arguments.

Monomorphization roots are:

- `main`;
- non-generic public free functions and public impl functions;
- public variables, including function instances reached from their
  initializers.

Public generic declarations are not roots because they have no concrete type
arguments to instantiate. They are emitted only when a concrete reference
requests an instance.

Discovery retains the existing pull-based fixpoint; CFGs are not traversed to
enumerate callees. It lowers the root instances and public-variable
initializers, then polls every declaration's instance cache (and the built-in
caches) for newly requested concrete instances. Lowering those instances can
request more, so polling continues to a fixpoint. The special module-item walk
that lowers every private concrete function and variable is removed. Only
`FnInstance` objects enter monomorphization and code generation.

## Symbol names and linkage

Changing which IR object is emitted must not change existing external names or
linkage. A source declaration therefore carries the symbol policy currently
held partly by `ModItem` and partly by `FnInstance`.

The following current spellings are preserved exactly:

```text
main
main::unused_private
main::unused_public
main::<Foo as Show>::show
main::Foo::get
main::id[i32]
main::Box[i32]::unwrap
```

In particular, `main` remains unqualified rather than becoming `main::main`.
A non-generic trait-impl method retains the unqualified self and trait names in
its existing `ModItem` spelling; a generic impl instance retains the qualified
concrete spelling already produced by `FnInstance`.

The declaration also carries the access needed to preserve the current linkage
of formerly module-emitted non-generic functions. Generic instances retain
their current `linkonce_odr` linkage. Code generation may share declaration
machinery, but it must not blindly apply generic-instance linkage to `main` or
ordinary non-generic functions.

## Built-in functions

Built-ins implement the same `instantiate(args)` contract as source-defined
functions. Existing built-in specialization rules and diagnostics remain
unchanged; only their caller-facing entry point is unified. All current
built-ins are generic, and `GenericBuiltinFn` already caches on one flat
`tuple[Typ, ...]`, so its part of this stage is principally an interface rename.
The contract also supports a future non-generic built-in through
`instantiate(())` without adding another entry point.

## Diagnostics and invariants

Apart from the separately committed unconstrained-impl-parameter defect fix,
this stage introduces no language diagnostic. Existing checks for missing,
extra, or unsatisfied function type arguments remain at
resolution/type-checking sites.

The following are internal invariants:

- the flat tuple has exactly the impl arity plus the function arity;
- every impl argument precedes every function argument;
- every emission target is an `FnInstance`;
- a cache contains at most one instance for a flat tuple;
- lookup and sibling resolution never reconstruct impl arguments from a
  concrete receiver after selection has already determined them;
- every impl type parameter is recoverable positionally from the selected impl
  head.

Invariant violations use assertions with messages that identify the expected
impl and function arities and the received total.

## Testing

Focused tests cover:

- `instantiate(())` for non-generic source and built-in functions;
- object identity for repeated instantiation with the same flat tuple;
- impl arguments preceding function arguments;
- sibling calls retaining the enclosing impl arguments;
- substitution of an existing instance's complete flat argument tuple;
- concrete public functions and public-variable initializers seeding
  monomorphization;
- public generic declarations not being emitted until concretely referenced;
- an uncalled private function and an uncalled private method with ill-typed
  bodies remaining compile errors;
- the entry point retaining the bare symbol `main`, not `main::main`;
- exact current symbols for a non-generic inherent method, a non-generic trait
  method, a generic free function, and a generic impl method.

A separate test rejects an impl type parameter that does not occur in its self
type. Structural checklist commands verify the absence of `instance`,
`impl_instance`, `for_recv_typ`, the split cache key, `Mod.generic_fns`, and the
special module-item seeding walk; those checks are not counted as behavioral
test coverage.

The full test suite, Ruff formatting and linting, basedpyright, and REUSE lint
must pass. The pre-stage baseline is 1,134 passing tests; the defect-fix test
increases that count. Since the remaining change is structural, any other
language-visible difference is a regression.

## Out of scope

- Renaming `FnSpec`, `NonBuiltinFnSpec`, or other function classes. That is
  Stage 4.
- Splitting declarations, instances, and values into separate nominal
  hierarchies. That is Stage 3.
- Centralizing caches outside their declarations. That is Stage 5.
- Generic associated functions and generic trait methods. This stage provides
  the argument model they require but does not add them to the language.
- Changing any emitted symbol spelling or linkage.
