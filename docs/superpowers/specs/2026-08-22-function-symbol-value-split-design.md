<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Function Symbol/Value Split

## Goal

Separate function declarations, concrete instances, and expression values into
three distinct IR concepts:

```text
function declaration -> concrete instance -> function reference value
```

No declaration or instance is an `ir_values.Value`. A function expression is
always an `FnRef` wrapping one concrete `FnInstance`.

This is Stage 3 of the
[`Fn` hierarchy restructure staging plan](../plans/2026-08-17-fn-hierarchy-restructure-staging.md).
It preserves accepted language behavior, diagnostics, emitted symbols, and
linkage.

## Current Problem

`FnSpec` currently inherits `ComptimePtr`, so declarations and instances are
both values. Its `load` and `store` implementations raise `AssertionError`
because a function address is not a compiler-modelled storage location. This
violates the pointer interface instead of representing the distinction in the
type hierarchy.

Generic declarations cannot honestly be values because their pointer type
contains unsubstituted type parameters. `GenericFn` works around that by
wrapping generic declarations at selected binding sites. The wrapper creates
two environment representations for the same kind of declaration and requires
name resolution, type checking, lowering, and code generation to distinguish
them repeatedly.

Stage 2 made every emitted body belong to an `FnInstance`. Stage 3 completes
the separation by making only a reference to an instance a value.

## Architecture

The model uses composition rather than inheritance between declarations and
instances:

```text
FnSpec                         declaration interface, not a Value
|- NonBuiltinFnSpec            shared source-declaration behavior
|  |- FnDecl                   bodyless extern declaration
|  `- Fn                       source definition
`- GenericBuiltinFn            compiler-provided body declaration

FnInstance                    contains declaration + flat concrete type arguments
FnRef                         cached immutable value containing one instance
```

The diagram shows ownership, not subclassing, for the bottom two lines:
`FnInstance` contains an `FnTemplate`, and `FnRef` contains an `FnInstance`.
`FnRef` alone derives from `ir_values.ComptimeValue[typs.PtrTyp]`.

The existing names `FnSpec`, `NonBuiltinFnSpec`, and `FnTemplate` remain in
this stage even though the staging sketch calls the declaration a `FnSymbol`.
Renaming and folding them is Stage 4 and should remain a mechanical change.

## Function Declarations

`FnSpec` stops inheriting `ComptimePtr`. It retains declaration-level data and
behavior shared by source definitions, extern declarations, and built-ins:

- source AST and span;
- name and function signature;
- formal declaration parameters;
- symbol-name metadata;
- the `instantiate(args)` contract where applicable.

The `load`, `store`, and `is_temporary` methods disappear from function
declarations. These operations describe compile-time pointer storage, not
callable declarations.

`FnTemplate` remains the structural interface for an instantiable declaration,
and its AST type widens from `ast.FnDefn` to the common `ast.FnSpec` base so it
can include `FnDecl`. The flat argument convention remains established by each
declaration's `instantiate` implementation and by `FnInstance`: impl arguments
first, followed by the function's own arguments.

Body checking and construction are not part of the total instantiation
protocol. `FnTemplate` retains only the declaration metadata, parameters,
type-parameter list, instance cache, and `instantiate` operation needed by all
three declaration kinds. A separate body-owning protocol adds `env`,
`typ_check_results`, and `_build_body` for `Fn` and `GenericBuiltinFn`.
`FnDecl` supplies the empty `typ_params` required for instantiation but needs no
meaningless type-checking result or body-building stub. Accessing a body or CFG
first narrows the instance's declaration to the body-owning protocol. The
obsolete `FnSpec.body_cfg` interface is removed.

## Instances and References

`FnInstance` becomes a plain IR object rather than a `Value`. Its declaration
may be a source definition, compiler-provided definition, or bodyless extern
declaration. It contains:

- its source or built-in declaration;
- its flat concrete type arguments;
- the resulting substitution mapping;
- its concrete signature and parameters;
- its symbol name, body, and linkage policy;
- one cached `FnRef`.

Repeated instantiation with equal argument tuples returns the same
`FnInstance`. Repeated requests for that instance's reference return the same
`FnRef`. These identity guarantees give monomorphization and code generation
one stable instance and value for each specialization.

`FnRef` is an immutable compile-time-known function address. It derives from
`ComptimeValue[PtrTyp]`, not `ComptimePtr`, because it is a value rather than a
place that can be loaded from or stored through. Its type is the concrete
instance's function-pointer type. Its AST and span come from the declaration so
diagnostics such as `CallExternFnAtComptimeError` continue to point at the
declaration.

Function references are general values, not only call targets. An `FnRef` may
be stored in a local, returned from compile-time evaluation, or used as a module
variable initializer. Omitting `ComptimePtr.is_temporary` is behavior-neutral:
the temporary-pointer check simply does not apply to this immutable value.

## External Declarations

Bodyless `FnDecl` declarations join the uniform instantiation interface. An
extern declaration accepts only `instantiate(())` and caches a bodyless
`FnInstance`. Its instance always uses the declaration's bare C symbol name and
external LLVM linkage; it never uses generic `linkonce_odr` linkage.

This deliberately supersedes Stage 2's temporary rule that external
declarations were emitted without instantiation. It does not change the
language or generated symbols. Code generation keeps declaring every local or
public imported extern through the existing reachability-independent module-item
walk, including unused public externs from the prelude. That declaration path
creates `decl.instantiate(()).ref`, declares its bare symbol with external
linkage, and maps the reference to the LLVM function. Extern instances do not
participate in monomorphization discovery and have no body to compile.

The benefit is a total value invariant—every callable expression is an `FnRef`
to an `FnInstance`, including an extern call—without narrowing the set of
declarations emitted today. “Extern declaration” refers to source syntax here;
monomorphization's “external function instances” remain imported,
another-module-owned definitions.

## Environments and Resolution

The variable namespace binds function declarations directly. `GenericFn` is
deleted. Generic and non-generic declarations therefore have the same
environment representation.

The environment's variable union and module-item value union admit function
declarations as symbols even though declarations are not `Value`s. `ModItem`
classifies declarations explicitly into the variable namespace instead of
relying on `isinstance(value, Value)`. Visibility and duplicate-name diagnostics
continue to use declaration metadata.

Name-resolution facts record declarations, not function values. Associated
function and method lookup stops instantiating generic-impl functions during
lookup. `ImplRegistry.lookup_assoc_fn` and `lookup_member` return a small
selection containing the `Fn` declaration and the impl arguments obtained from
the concrete receiver. Resolution tables preserve that selection until
lowering. Thus lookup can select an impl and calculate its arguments, but no
instance or `FnRef` is created merely by name lookup.

## Type Checking

Type checking is the sole phase that decides whether a function symbol has
enough type arguments to become a concrete reference:

- a non-generic source function or extern declaration selects `()`;
- a generic function resolves explicit arguments or infers them as today;
- only a declaration's own `typ_params` determine whether source syntax must
  supply function arguments; `Fn.is_generic`, which also counts its parent
  impl's parameters for emission policy, must not be used for this decision;
- an impl-scoped reference selects no function arguments during checking; the
  registry selection or the enclosing sibling instance supplies its impl
  argument prefix during lowering;
- a generic function named without sufficient arguments still raises
  `MissingTypArgsError`;
- wrong arity, failed inference, and unsatisfied-bound diagnostics retain their
  current spans and ordering.

Resolution tables record the selected declaration plus the function and impl
arguments known at that site. They do not store an `FnRef`, because reference
creation belongs to lowering and should occur only for expressions that are
lowered.

## CFG Lowering

When lowering a function expression or call, `CfgBuilder` asks the recorded
declaration for its cached `FnInstance` and then returns the instance's cached
`FnRef`. Sibling calls in generic impls retain the enclosing impl-argument
prefix exactly as in Stage 2.

`CallInstr.callee` remains a `Value`, but every function callee produced by
lowering is specifically an `FnRef`. Other callable pointer values remain
possible according to the language's existing type rules and are outside this
structural change.

Formal declaration parameters remain available for checking an unsubstituted
body. Each `FnInstance` retains concrete parameters for CFG lowering and
compile-time interpretation.

The old `FnSpec` supertype currently also names several mixed declaration and
instance positions. Their replacements are explicit:

- `Param.fn` is `FnSpec | FnInstance`, because both unsubstituted declaration
  parameters and concrete instance parameters exist;
- `CfgBuilder._fn` is `Optional[FnInstance]`, because Stage 2 guarantees that
  every lowered function body belongs to an instance; `None` remains the module
  variable initializer case;
- `Env.panic_fn`, `CfgBuilder._panic_fn`, and `Interpreter._panic_fn` hold the
  cached prelude `FnRef`.

## Monomorphization and Code Generation

Monomorphization continues to discover body-owning concrete instances from
source and built-in declaration caches. Roots, imported-instance
classification, and the pull-based reachability fixpoint introduced in Stage 2
do not change. Although each extern declaration caches its `()` instance, extern
declarations are not enumerated as monomorphization templates; the module-item
declaration walk handles them as described above.

Code generation declares an LLVM function for each discovered body-owning
instance and maps the instance's singleton `FnRef` to that LLVM value. It also
maps every module-item extern's singleton reference while declaring externs,
before module initializers are compiled. Every `FnRef` reaching code generation
is therefore already mapped, including a function used as a module-variable
initializer. `_compile_comptime_value` handles `FnRef` explicitly by returning
that mapped LLVM function. Calls likewise look up their `FnRef` operand
directly. Only body-owning instances are compiled.

Symbol rendering and linkage remain properties derived from the instance and
its declaration. This stage does not change either policy.

## Compile-Time Interpretation

The interpreter recognizes `FnRef` as the compile-time-known callee, unwraps
its instance, and interprets that instance's body. Calling a bodyless extern
instance at compile time continues to raise `CallExternFnAtComptimeError` at
the declaration span.

The environment holds the prelude panic instance's singleton `FnRef`.
Compiler-injected panic calls and source-level `panic(...)` references both use
that cached object, and the interpreter compares `FnRef` identity before
unwrapping it. Panic diagnostics therefore remain unchanged.

## Built-In Functions

Built-in and source declarations use the same declaration → instance →
reference path. Generic built-ins retain their existing specialization caches
and body-building hooks, but their instances are no longer values themselves.
The environment binds the built-in declaration directly rather than wrapping
it in `GenericFn`.

No special non-generic built-in design is needed. A future non-generic built-in
would use `instantiate(())` and produce an `FnRef` in the same way as an
external or source declaration.

## Diagnostics and Invariants

This stage introduces no language diagnostic. Existing generic-argument,
visibility, callability, and compile-time external-call errors retain their
current behavior.

The following are internal invariants:

- no function declaration or instance is an `ir_values.Value`;
- every lowered function reference is an `FnRef`;
- every `FnRef` contains exactly one concrete `FnInstance`;
- equal instantiation arguments return the same instance and reference;
- an extern declaration has exactly one bodyless `()` instance;
- extern instances are declared by the module-item walk with bare names and
  external linkage, not discovered by monomorphization;
- every `FnRef` used by code generation is mapped to its LLVM function before
  use;
- only body-owning instances are compiled;
- `GenericFn` does not exist.

## Testing

Focused tests establish:

- `Fn`, `FnDecl`, `FnInstance`, and generic built-in declarations are not
  `Value`s;
- ordinary, generic, built-in, inherent-method, trait-method, imported, and
  external references lower to `FnRef`;
- repeated references share their `FnInstance` and `FnRef` identities;
- an extern declaration's `()` instance has no body and emits only an LLVM
  declaration;
- an unused public extern in an imported module is still declared;
- an extern stored as a module-variable value retains its function-pointer
  initializer;
- a bare sibling reference in a generic impl does not require explicit type
  arguments;
- compiler-injected and source-level panic calls share the cached panic
  reference;
- an extern instance has external rather than `linkonce_odr` linkage;
- generic declarations bound directly in environments preserve missing,
  explicit, inferred, wrong-arity, and bound-failure behavior;
- compile-time source calls still execute and compile-time external calls still
  fail with the existing diagnostic;
- existing emitted symbols and linkage are unchanged.

Structural checks verify that `GenericFn` is absent, no function declaration or
instance inherits `ComptimePtr`, and no obsolete function-specific `load`,
`store`, `is_temporary`, or `body_cfg` method remains. Comments that enumerate
compile-time function values are updated to name `FnRef`.

The full pytest suite, Ruff formatting and linting, basedpyright, REUSE lint,
and `git diff --check` must pass before the stage is complete.

## Implementation Boundaries

The implementation may use several reviewable commits:

1. Introduce `FnRef` for existing source and built-in instances while
   preserving the existing environment representation.
2. Give extern declarations bodyless `()` instances and preserve their module
   walk, bare symbols, external linkage, and value-initializer behavior.
3. Bind function declarations directly; update `ModItem`, registry selections,
   type checking, resolution facts, and lowering.
4. Remove `GenericFn` and function-value inheritance; retype `Param.fn`,
   `CfgBuilder._fn`, the panic path, and resolution unions; remove obsolete
   `load`, `store`, `is_temporary`, and `body_cfg` branches; then complete the
   structural checks and full verification.

No intermediate commit should intentionally change accepted programs, emitted
symbols, or linkage. The implementation must not include the Stage 4 naming
sweep or unrelated changes to the general value hierarchy.

## Out of Scope

- Renaming `FnSpec`, `NonBuiltinFnSpec`, or `FnTemplate`.
- Centralizing lazy recursion guards and caches; that remains Stage 5.
- Adding generic associated functions or generic trait methods.
- Changing function-pointer language features.
- Changing emitted symbol spelling or linkage.
