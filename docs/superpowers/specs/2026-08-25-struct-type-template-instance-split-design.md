<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Struct Type Template/Instance Split

## Goal

Resolve GitHub issue #3 by representing generic struct declarations and usable
struct types with distinct Python types. Remove `StructTyp`'s template/instance
mode flags while preserving accepted programs, diagnostics, type identity,
emitted symbol names, and LLVM output.

The resulting model is:

```text
StructTypTemplate --instantiate(args)--> StructTyp
                                           ^
non-generic declaration -------------------|
                         instantiate(())
```

Every `StructTyp` is an instance. A non-generic declaration has one cached
zero-argument instance; a generic declaration has one cached instance per
argument tuple.

## Current Problem

`StructTyp` currently represents three states: a generic template, a generic
instance, and a non-generic struct. Empty `_typ_args` identifies both the first
and third states, so callers inspect the declaration AST or ask
`is_generic_template` to distinguish them. Template-only operations such as
`instance()` and instance-only operations such as substitution and code
generation coexist on every object.

This ambiguity leaks into type resolution, compilation caching,
monomorphization, code generation, layout-cycle detection, and tests. In
particular, code generation repeatedly branches on the runtime state of a
`StructTyp`, and substitution recovers an instance's template through a reverse
cache lookup.

## Architecture

`StructTypTemplate` represents a struct declaration that produces instances. It is a container
namespace symbol but not a `Typ`, because a bare generic declaration is not a
usable language type. It owns declaration metadata, declared type parameters,
the declaration environment, the field declaration namespace, and
`instantiate(args)`. Its bare `name`, `mod_name`, and `span` support type
resolution and diagnostics; it has no LLVM-oriented `qualified_name`.

`StructTyp` represents every usable struct type. It remains a `Typ` and owns a
direct reference to its declaration template plus its argument tuple. Its
fields resolve in an instance environment where declaration parameters are
bound to arguments. Naming, substitution, type-argument inference, layout
validation, implementation selection, monomorphization, and LLVM lowering
operate only on instances.

Both generic and non-generic declarations have a template internally. A
generic template is stored directly as the module item in the container
namespace. A non-generic declaration is instantiated with `()` during module
construction and that `StructTyp` instance is stored as the module item. This
keeps ordinary named structs usable anywhere a `Typ` is expected and prevents
downstream consumers from handling a declaration object unnecessarily.

The template is nevertheless retained directly by its instance, so code never
needs to reconstruct template ownership from AST and environment identity.

## Identity and Caching

`compilation.Ctx` owns struct instances by
`(StructTypTemplate, tuple[Typ, ...])`, just as it currently owns instances by
the template-shaped `StructTyp`. Module construction creates one template for
each struct declaration and stores either that template or its non-generic
module instance as the module item. Templates need no separate interning cache.

`StructTypTemplate.instantiate(args)` checks the argument count explicitly,
before consulting the instance cache, then delegates to
`Ctx.instantiate_struct`. Internal callers use an assertion for impossible
arity mismatches; source arity diagnostics remain in type resolution where
the source span is available.

`Ctx` becomes the only cache for `StructTyp` instances; the redundant weak
`Typ` cache entry is removed. Instance identity is compilation-local and nominal through
the owning template plus argument tuple. Repeated requests for equal arguments
return the same object, separate compilation contexts remain distinct, and
distinct declarations remain distinct.

The zero-argument instance of a non-generic template is created through the
same cache but is not recorded as a monomorphization request because
module-item code generation already declares and defines it. A generic
template's private validation instance is also cached without being recorded.
All other newly created instances requested through public `instantiate`
enter the append-only `requested_struct_instances` log; a later request equal
to the cached validation instance returns it without adding a late log entry.
That order-independent suppression is safe because validation arguments are
opaque `TypParamTyp` values and monomorphization would filter the instance as
non-concrete anyway.

## Type Resolution

The container namespace expands to admit `StructTypTemplate` alongside
ordinary `Typ` values, modules, and traits. `Typ._basic_typ_from_ast` handles
the cases by Python type:

- a `StructTypTemplate` requires exactly its declared generic arguments,
  checks their bounds, and returns its cached `StructTyp` instance;
- a `StructTyp` rejects explicit generic arguments and returns itself;
- every other resolved type retains the existing non-generic behavior.

This removes inspection of `ast.generic_params` and `_typ_args` from type
resolution. A bare generic template still raises `MissingTypArgsError`, and
arguments on a non-generic struct still raise
`TypArgsOnNonGenericItemError`, with existing spans and ordering.

`Env.resolve_typ` widens its return type to `Typ | StructTypTemplate` and
removes its unconditional `Typ` cast so `_basic_typ_from_ast` can perform this
dispatch. Its existing module-used-as-type and trait-used-as-type diagnostics
remain unchanged.

## Struct Instances

`StructTyp` stores `template` and public read-only `typ_args`.
Declaration-derived `ast`, `mod_name`, and `span` remain delegating properties
on the instance for compatibility with nominal-type consumers. New and
rewritten identity comparisons use `template` identity instead of AST identity
where possible, including overlap, inference, layout recurrence, and impl
registry head-shape keys. The instance environment always binds each declared
parameter to the corresponding argument; there is no parameter-mode branch.

Instance behavior includes:

- `name` and `qualified_name`, preserving their current spellings;
- `is_concrete`, based on the arguments;
- field construction and access;
- substitution and type-argument inference;
- finite-layout checking;
- nominal identity and structural overlap support.

Substitution calls `self.template.instantiate(substituted_args)` directly.
There is no template reverse lookup. Structural helpers use the public
`typ_args` interface rather than `_typ_args`.

## Validation of Generic Declarations

Generic struct declarations must still be validated even when no concrete
instance is requested. A template eagerly builds its field declaration
namespace in source order, rejecting reserved and duplicate field names during
module construction exactly as today. Instances create field views that pair
those declarations and indices with types resolved in the instance
environment, so name validation is not repeated per instantiation and its
diagnostic ordering does not move behind function-body checking.

The template eagerly materializes its declared type parameters before checking
field names. Reserved parameter names therefore retain their current priority
over field-name and function-body diagnostics.

To resolve field types and reject declaration-level layout cycles, a template
exposes one cached validation instance whose arguments are its declared opaque
`TypParamTyp` objects. This object is an ordinary `StructTyp`, but it is not
emitted and is cached without being added to the monomorphization request log.

Code generation asks every generic template to validate its declaration during
the existing declaration-validation pass. The template privately forces its
validation instance. Layout-cycle
comparison therefore receives only `StructTyp` values. Its growth comparison
uses each instance's `typ_args`; the validation instance naturally contributes
the declaration parameters without a template branch.

Declaration validation preserves existing diagnostic spelling. The initial
validation frame renders the template's bare declaration name (`L`), while
recursive field types retain their ordinary instantiated names (`L[T]`,
`B[T]`, and so on). The layout-check entry point records that only its root is
being validated as a declaration; it does not globally change how the cached
validation instance's `name` property renders. Existing infinite-layout error
types, spans, hop ordering, and message text therefore remain unchanged.
If recurrence starts below the initial frame, that nested repeated instance
retains its instantiated name; only recurrence at the hop-less root frame uses
the template's bare name.

## Modules, Environments, and Paths

`ir_env.Container` and `ir_module.ModItem.value` admit
`StructTypTemplate`. Diagnostic metadata treats both templates and instances
as struct declarations with the same source span and `"type"` item kind.

`Env._resolve_path_segment` widens its scope type and adds an explicit
`StructTypTemplate` arm. Associated-function paths currently resolve their
leading container directly rather than through `Typ.from_ast`, and the grammar
cannot put generic arguments on that scope segment. A path such as
`Box::make` therefore reaches the template arm and raises
`MissingTypArgsError` at the template identifier instead of falling through
the match or entering impl selection with an unusable template. This replaces
the current internal crash with the diagnostic already used for a bare generic
type; instance-scoped associated-function lookup remains unchanged.

Implementation targets remain `StructTyp` instances: generic impl targets such
as `Box[T]` resolve to an instance whose arguments are the impl's opaque type
parameters.

## Monomorphization and Code Generation

`mono._discover_struct_instances` consumes only requested `StructTyp`
instances. It keeps filtering non-concrete instances created while checking
generic definitions, forces fields to discover nested requests, and returns
concrete instances in request order.

Code generation branches on declaration versus instance types rather than on
object state in every module-item pass:

- module-item `StructTyp` values are non-generic zero-argument instances and
  are declared and defined through the ordinary module-item path;
- module-item `StructTypTemplate` values have no LLVM representation and only
  have their validation instance forced;
- discovered generic `StructTyp` instances are declared and defined through
  the monomorphization path.

The general declaration pass explicitly excludes both `StructTyp` and
`StructTypTemplate`, rather than relying on `not isinstance(value,
StructTyp)`. The final `_compile_mod_item` walk treats templates as a no-op,
just as its current `StructTyp` arm absorbs templates. These guards prevent a
template module item from reaching assertion-only fallthrough cases.

LLVM type lowering accepts only `StructTyp`, so there is no template guard in
`_ll_typ`. Existing qualified names continue to identify LLVM structs, and the
order of declarations and definitions remains unchanged.

## Diagnostics and Invariants

This change introduces no language diagnostic. Existing missing-argument,
wrong-arity, bound, privacy, implementation-locality, and infinite-layout
errors retain their types, spans, ordering, and message spelling. The one
newly handled case is an associated-function path scoped by a bare generic
template, which currently crashes internally; it now raises the existing
`MissingTypArgsError` diagnostic.

The structural invariants are:

- `StructTypTemplate` is never a `Typ` and never has an LLVM representation;
- every `StructTyp` is an instance and has one owning template;
- non-generic structs are zero-argument instances;
- only templates instantiate structs;
- callers receiving a `StructTyp` never branch on whether it is a template;
- equal template and argument requests return the same instance;
- generic validation instances are checked but never emitted or discovered as
  concrete monomorphizations.

## Testing

Focused structural tests will establish that templates and instances have
distinct Python types, non-generic module items are zero-argument instances,
generic templates cache instances, instances retain their template directly,
validation instances do not enter monomorphization discovery, and bare generic
associated-function scopes produce `MissingTypArgsError` rather than an
internal exception.

The existing compilation-isolation test in `tests/test_typs.py` is rewritten
to construct templates directly, instantiate each
template, assert template and instance isolation across contexts, and verify
the clarified request-log policy. Existing helpers and structural assertions
that intentionally retrieve a generic declaration are updated to expect
`StructTypTemplate`; this is test adaptation to the new internal API, not a
change to language-level expectations.

Existing generic-struct, type-resolution, implementation, trait, import,
layout-cycle, code-generation, and symbol-name tests must pass without changes
to their language-level expectations. The full test suite, Ruff formatting and
lint, basedpyright, REUSE, and `git diff --check` provide final validation.

## Out of Scope

This design does not split shape from concreteness for integer, pointer, array,
function, enum, or other types. That broader work remains in issue #14. It also
does not change non-type generic parameters, struct syntax, implementation
selection semantics, reachability policy, symbol mangling, or LLVM layout.
