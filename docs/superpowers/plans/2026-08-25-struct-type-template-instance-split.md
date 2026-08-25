<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Struct Type Template/Instance Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Represent struct declarations as `StructTypTemplate` objects and every usable struct type, including non-generic structs, as a `StructTyp` instance.

**Architecture:** A compilation-local `StructTypTemplate` owns declaration metadata, type parameters, field-name validation, and instantiation. `compilation.Ctx` is the sole instance cache keyed by template and arguments; instances retain their template directly and own resolved fields, substitution, layout checking, monomorphization, and LLVM representation. Module and environment paths distinguish templates from instances by Python type, eliminating `is_generic_template` and `_typ_args` state tests.

**Tech Stack:** Python 3.14, uv, pytest, Ruff, basedpyright, llvmlite.

**Spec:** `docs/superpowers/specs/2026-08-25-struct-type-template-instance-split-design.md`

## Global Constraints

- Work directly on `main`; do not create feature branches or worktrees.
- Leave every task's changes unstaged and uncommitted until the repository owner reviews and authorizes a commit.
- Every eventual commit must reference GitHub issue #3.
- Preserve accepted language behavior, diagnostic types/spans/order/text, type identity, emitted symbol names, linkage, and LLVM output.
- The existing internal crash for a bare generic associated-function scope becomes `MissingTypArgsError`.
- Keep imports module-qualified and follow the repository's Sphinx-style docstring and comment guidance.
- Do not extend the split to `IntTyp`, `PtrTyp`, or other type-shape work tracked by issue #14.
- Before completion run `uv run pytest -q`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run basedpyright`, `uv run reuse --no-multiprocessing lint`, and `git diff --check`.

---

### Task 1: Introduce Struct Templates and Instance-Only Struct Types

**Files:**
- Modify: `src/leech/typs.py`
- Modify: `src/leech/compilation.py`
- Test: `tests/test_typs.py`
- Test: `tests/test_generic_structs.py`

**Interfaces:**
- Produces: `StructTypTemplate.create(ast, env, mod_name) -> StructTypTemplate`, unique per compilation and declaration.
- Produces: `StructTypTemplate.instantiate(args) -> StructTyp`, recording a public generic request.
- Produces: private `StructTypTemplate._validation_instance -> StructTyp`, cached without a monomorphization request.
- Produces: `StructTypTemplate.module_instance -> StructTyp` for a non-generic module item.
- Produces: `StructTyp(template, typ_args)` with public `template` and `typ_args` and delegating `ast`, `mod_name`, and `span` properties.
- Changes: `Ctx.instantiate_struct(template, args, *, record_request) -> StructTyp` owns the only struct-instance cache.

- [ ] **Step 1: Add failing template/instance identity tests**

Replace `test_struct_templates_are_isolated_by_compilation_ctx` in `tests/test_typs.py` and add adjacent tests with these assertions:

```python
def test_struct_templates_and_instances_are_isolated_by_compilation_ctx(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    first_ctx = compilation.Ctx()
    second_ctx = compilation.Ctx()
    first_env = ir_env.Env(first_ctx, ir_traits.ImplRegistry(first_ctx), None)
    second_env = ir_env.Env(second_ctx, ir_traits.ImplRegistry(second_ctx), None)

    first = typs.StructTypTemplate.create(struct_ast, first_env, "main")
    second = typs.StructTypTemplate.create(struct_ast, second_env, "main")
    first_instance = first.instantiate((typs.I32,))
    second_instance = second.instantiate((typs.I32,))

    assert first is not second
    assert first_instance is first.instantiate((typs.I32,))
    assert first_instance is not second_instance
    assert first_instance.template is first
    assert first_instance.typ_args == (typs.I32,)
    assert tuple(first_ctx.requested_struct_instances()) == (first_instance,)
    assert tuple(second_ctx.requested_struct_instances()) == (second_instance,)


def test_struct_template_is_unique_within_compilation(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)

    first = typs.StructTypTemplate.create(struct_ast, env, "main")

    with pytest.raises(AssertionError):
        typs.StructTypTemplate.create(struct_ast, env, "main")

    assert first.name == "Box"


def test_struct_validation_instance_is_cached_without_request(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    template = typs.StructTypTemplate.create(struct_ast, env, "main")

    validation = template._validation_instance

    assert validation is template._validation_instance
    assert validation.template is template
    assert validation.typ_args == template.typ_params
    assert tuple(ctx.requested_struct_instances()) == ()
```

- [ ] **Step 2: Run the new tests and verify the expected failures**

Run:

```bash
uv run pytest tests/test_typs.py -k "struct_template" -q
```

Expected: FAIL because `StructTypTemplate`, `StructTyp.template`, and `StructTyp.typ_args` do not exist.

- [ ] **Step 3: Add the unique declaration template**

In `src/leech/typs.py`, add `StructTypTemplate` before `StructTyp`. Keep the template outside the `Typ` hierarchy and use a weak compilation-local cache:

```python
class StructTypTemplate:
    """A struct declaration that owns its usable type instances."""

    _cache: ClassVar[
        weakref.WeakValueDictionary[tuple[object, ast.StructDefn], StructTypTemplate]
    ] = weakref.WeakValueDictionary()

    ast: Final[ast.StructDefn]
    _decl_env: Final[ir_env.Env]
    mod_name: Final[str]
    _field_asts: Final[types.MappingProxyType[str, tuple[int, ast.StructFieldDefn]]]

    @classmethod
    def create(
        cls, struct_ast: ast.StructDefn, e: ir_env.Env, mod_name: str
    ) -> StructTypTemplate:
        """Create the unique live template for this declaration and compilation."""
        key = (e.ctx.typ_cache_token, struct_ast)
        asserts.assert_not_in(key, cls._cache)
        template = cls(struct_ast, e, mod_name)
        cls._cache[key] = template
        return template

    def __init__(self, struct_ast: ast.StructDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = struct_ast
        self._decl_env = e
        self.mod_name = mod_name
        # Preserve current diagnostic priority: parameter names before fields.
        _ = self.typ_params
        members: dict[str, tuple[int, ast.StructFieldDefn]] = {}
        for index, field_ast in enumerate(struct_ast.fields):
            name = field_ast.ident.name
            if reserved.is_reserved(name):
                raise errors.ReservedNameError(name, field_ast.ident.span)
            existing = members.get(name)
            if existing is not None:
                _, existing_ast = existing
                raise errors.DuplicateFieldInStructDefnError(
                    name, field_ast.ident.span, existing_ast.ident.span
                )
            members[name] = (index, field_ast)
        self._field_asts = types.MappingProxyType(members)

    @property
    def name(self) -> str:
        """The declaration's bare source name."""
        return self.ast.ident.name

    @property
    def span(self) -> src.SrcSpan:
        """The source location of the struct declaration."""
        return self.ast.span

    @functools.cached_property
    def typ_params(self) -> tuple[TypParamTyp, ...]:
        """The declaration's interned formal type parameters."""
        return typ_params_from_ast(self.ast, self.ast.generic_params, self._decl_env)

    def instantiate(self, typ_args: tuple[Typ, ...]) -> StructTyp:
        """Return the cached instance for ``typ_args`` and record its request.

        :post: instantiate(a) is instantiate(a) for equal a [cache]
        """
        asserts.assert_eq(len(typ_args), len(self.typ_params))
        return self._decl_env.ctx.instantiate_struct(self, typ_args, record_request=True)

    @functools.cached_property
    def _validation_instance(self) -> StructTyp:
        return self._decl_env.ctx.instantiate_struct(
            self, self.typ_params, record_request=False
        )

    def validate_declaration(self) -> None:
        """Validate this declaration's layout with its bare root name."""
        self._validation_instance._check_finite_size(None, self.name)

    @functools.cached_property
    def module_instance(self) -> StructTyp:
        """Return the zero-argument instance bound for a non-generic declaration."""
        asserts.assert_eq(len(self.typ_params), 0)
        return self._decl_env.ctx.instantiate_struct(self, (), record_request=False)
```

Forward-reference annotations are valid under Python 3.14. Keep `StructTypTemplate`'s public surface limited to declaration behavior; do not add `qualified_name`, field type resolution, substitution, or LLVM behavior.
The weak uniqueness cache enforces one template while the first remains live;
module items retain production templates strongly, and the test deliberately
retains `first` for the same reason.

Update `Typ`'s class docstring to say that most types are weakly interned while
struct instances are owned by `compilation.Ctx`; its existing global
`get_or_create` invariant no longer applies to `StructTyp`.

- [ ] **Step 4: Make `StructTyp` instance-only**

Replace `StructTyp.__init__`, template operations, and private argument access with the instance model:

```python
class StructTyp(Typ):
    """A usable nominal struct instance owned by one declaration template."""

    template: Final[StructTypTemplate]
    typ_args: Final[tuple[Typ, ...]]
    _env: Final[ir_env.Env]
    _members: Final[dict[str, StructField]]

    def __init__(self, template: StructTypTemplate, typ_args: tuple[Typ, ...]) -> None:
        asserts.assert_eq(len(typ_args), len(template.typ_params))
        self.template = template
        self.typ_args = typ_args
        self._env = template._decl_env.new_child()
        for typ_param, typ_arg in zip(template.typ_params, typ_args, strict=True):
            self._env.add_container(typ_param.name, typ_arg)
        self._members = {
            name: StructField(index, field_ast, self._env)
            for name, (index, field_ast) in template._field_asts.items()
        }

    @property
    def ast(self) -> ast.StructDefn:
        return self.template.ast

    @property
    def mod_name(self) -> str:
        return self.template.mod_name

    @property
    def span(self) -> src.SrcSpan:
        return self.template.span
```

Delete `typ_params`, `instance`, `_create_instance`, `is_generic_template`, `cache_key`, and `_add_field` from `StructTyp`. Update `name`, `qualified_name`, `is_concrete`, `_contains_typ`, `typs_overlap`, substitution, and inference to use `typ_args` and template identity:

```python
def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
    substituted = tuple(typ_arg.substitute_typ_params(mapping) for typ_arg in self.typ_args)
    if substituted == self.typ_args:
        return self
    return self.template.instantiate(substituted)

def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
    if isinstance(actual, StructTyp) and actual.template is self.template:
        for declared_arg, actual_arg in zip(self.typ_args, actual.typ_args, strict=True):
            declared_arg.infer_typ_args(actual_arg, bindings)
```

Change struct unification and impl-registry shape consumers later in Task 2; within `typs.py`, every `_typ_args` access becomes `typ_args`, and declaration equality becomes `template is template`.

- [ ] **Step 5: Make the compilation context the sole instance cache**

Change `Ctx`'s cache and instantiation API in `src/leech/compilation.py`:

```python
_struct_instances: Final[
    dict[typs.StructTypTemplate, dict[tuple[typs.Typ, ...], typs.StructTyp]]
]

def instantiate_struct(
    self,
    template: typs.StructTypTemplate,
    args: tuple[typs.Typ, ...],
    *,
    record_request: bool,
) -> typs.StructTyp:
    """Return the cached struct instance for ``template`` and ``args``."""
    cached = self._cached_instance(self._struct_instances, template, args)
    if cached is not None:
        return cached

    instance = typs.StructTyp(template, args)
    instances = self._struct_instances.setdefault(template, {})
    asserts.assert_not_in(args, instances)
    instances[args] = instance
    if record_request:
        self._requested_struct_instances.append(instance)
    return instance
```

Do not put `StructTyp` instances in `Typ._cache`. The template cache holds the declaration weakly, while the compilation context owns live instances for the compilation.

- [ ] **Step 6: Preserve layout-cycle diagnostics through declaration validation**

Give the public `fields` property ordinary instance behavior; the template-only
entry point is `StructTypTemplate.validate_declaration` from Step 3:

```python
@functools.cached_property
def fields(self) -> types.MappingProxyType[str, StructField]:
    self._check_finite_size(None, None)
    return types.MappingProxyType(self._members)

def _check_finite_size(
    self,
    incoming_hop: Optional[errors.StructLayoutHop],
    root_name: Optional[str],
) -> None:
```

Within `_check_finite_size`, use `root_name` for the initial frame's owner and
for the final repeated-struct message. Propagate it through recursion so a
cycle detected below the first frame retains the declaration root name; use
`incoming_hop` to keep nested owner names instantiated:

```python
display_name = root_name if incoming_hop is None and root_name is not None else self.name
...
repeated, repeated_hop = cycle.details[0]
repeated_name = (
    root_name if repeated_hop is None and root_name is not None else repeated.name
)
raise errors.InfiniteSizeStructError(repeated_name, repeated.span, hops)
...
hop = errors.StructLayoutHop(display_name, field_ast.ident.name, field_ast.span, typ.name)
typ._check_finite_size(hop, root_name)
```

Change `_layout_repeats` to compare `template` identity and `typ_args` only. Run the existing four declaration-cycle tests after this step to prove exact messages remain unchanged.

Add a regression for a cycle rooted below the declaration being validated:

```python
def test_generic_struct_nested_cycle_keeps_nested_root_name(tmp_path):
    src = """
    struct A[T] { x: B[T] }
    struct B[T] { y: B[T] }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert exc_info.value.message.message == 'Struct "B[T]" has infinite size'
    assert [note.message for note in exc_info.value.extra] == [
        'Field "y" of struct "B[T]" contains "B[T]" by value'
    ]
```

- [ ] **Step 7: Run focused model and diagnostic tests**

Run:

```bash
uv run pytest tests/test_typs.py -k "struct_template" -q
uv run pytest tests/test_generic_structs.py -k "infinite_size_via_own_typ_param or growing_generic_struct_declaration_cycle or mutual_growing_generic_struct_declaration_cycle" -q
```

Expected: template/instance tests PASS; layout-cycle tests may still fail during module integration because module construction still creates the old `StructTyp` shape. Do not weaken their expected messages.

---

### Task 2: Integrate Templates with Resolution, Modules, Traits, and Code Generation

**Files:**
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/typs.py`
- Test: `tests/test_generic_structs.py`
- Test: `tests/test_impl.py`
- Test: `tests/test_traits.py`
- Test: `tests/test_loader.py`

**Interfaces:**
- Consumes: `StructTypTemplate` and instance-only `StructTyp` from Task 1.
- Changes: `ir_env.Container` and `ModItem.value` admit `StructTypTemplate`.
- Changes: `_basic_typ_from_ast` resolves a template to an instance and returns an existing `StructTyp` unchanged when no arguments are supplied.
- Changes: generic module items contain templates; non-generic module items contain cached zero-argument instances.
- Preserves: `MonoResult.struct_instances` and every LLVM type consumer contain only `StructTyp` instances.

- [ ] **Step 1: Add failing module-item and resolution tests**

Change the generic declaration helper in `tests/test_generic_structs.py`:

```python
def _get_struct_template(mod, name: str) -> typs.StructTypTemplate:
    """Get the generic struct template ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.StructTypTemplate)
```

Update generic-template callers to use `_get_struct_template(...).instantiate(...)`. Keep or add a separate helper for non-generic module items:

```python
def _get_struct_typ(mod, name: str) -> typs.StructTyp:
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.StructTyp)
```

Add:

```python
def test_generic_and_non_generic_struct_module_items_have_distinct_types(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "struct Box[T] { val: T }\nstruct Plain { val: i32 }",
    )

    template = _get_struct_template(mod, "Box")
    plain = _get_struct_typ(mod, "Plain")

    assert not isinstance(template, typs.Typ)
    assert isinstance(plain, typs.Typ)
    assert plain.typ_args == ()
    assert plain.template.name == "Plain"
```

- [ ] **Step 2: Run the module-item test and verify failure**

Run:

```bash
uv run pytest tests/test_generic_structs.py -k "module_items_have_distinct_types" -q
```

Expected: FAIL because `Mod._build_defn` still stores the old `StructTyp` object for both declarations.

- [ ] **Step 3: Widen container and module-item types**

In `src/leech/ir_env.py`, define:

```python
type Container = typs.Typ | typs.StructTypTemplate | ir_module.Mod | ir_traits.Trait
```

Add `StructTypTemplate` to `_private_item_diag_info`, the `_resolve_path_segment` scope annotation, and relevant pattern matches. Widen `resolve_typ`:

```python
def resolve_typ(self, path: ast.Path) -> typs.Typ | typs.StructTypTemplate:
    item = self.resolve_path(Env.Namespace.CONTAINERS, path)
    if isinstance(item, ir_module.Mod):
        raise errors.ModUsedAsTypError(item.name, path.idents[-1].span)
    if isinstance(item, ir_traits.Trait):
        raise errors.TraitUsedAsTypError(item.name, path.idents[-1].span)
    assert isinstance(item, (typs.Typ, typs.StructTypTemplate))
    return item
```

Widen `ir_module.ModItem.value`, `_add_item`, and other module-item unions to admit `StructTypTemplate` explicitly.

- [ ] **Step 4: Resolve templates by Python type**

Rewrite `Typ._basic_typ_from_ast` in `src/leech/typs.py`:

```python
item = e.resolve_typ(typ_ast.path)
if not isinstance(item, StructTypTemplate):
    if typ_ast.generic_args:
        raise errors.TypArgsOnNonGenericItemError(typ_ast.path.str(), typ_ast.span)
    return item

if not typ_ast.generic_args:
    raise errors.MissingTypArgsError(item.name, typ_ast.span)
if len(typ_ast.generic_args) != len(item.typ_params):
    raise errors.WrongNumberOfTypArgsError(
        item.name, len(typ_ast.generic_args), len(item.typ_params), typ_ast.span
    )
typ_args = tuple(Typ.from_ast(arg, e) for arg in typ_ast.generic_args)
check_typ_arg_bounds(item.typ_params, typ_args, e, typ_ast.span)
return item.instantiate(typ_args)
```

No code outside the new template class may inspect a struct declaration's `generic_params` to determine whether a resolved item is generic.

- [ ] **Step 5: Build generic templates and non-generic instances**

Change `Mod._build_defn`'s struct arm in `src/leech/ir_module.py`:

```python
case ast.StructDefn():
    template = typs.StructTypTemplate.create(defn_ast, self.env, self.name)
    value: typs.StructTypTemplate | typs.StructTyp
    if template.typ_params:
        value = template
    else:
        value = template.module_instance
    self._add_item(
        defn_ast.ident.name,
        visibility.Access.from_ast(defn_ast.access),
        value,
    )
```

Do not reach into `Ctx` or call public `instantiate(())` here;
`module_instance` owns the unrecorded zero-argument contract.

- [ ] **Step 6: Update traits, locality, and structural consumers**

In `src/leech/ir_traits.py`, keep `_is_local` accepting `Trait | typs.Typ` only; implementation targets already resolve to instances. Change `_head_shape` to return `typ.template` for `StructTyp`, and keep all impl registry inputs typed as `Typ`.

Update `isinstance` unions that classify nominal type declarations, such as private-item diagnostics and trait/container checks, only where a template can actually arrive. Do not broaden impl targets, `Impl.self_typ`, `_selection_shapes`, or LLVM-facing APIs to templates.

Change remaining struct declaration comparisons in `typs.py` to template identity:

```python
if isinstance(left, StructTyp) and isinstance(right, StructTyp):
    return (
        left.template is right.template
        and all(
            unify(left_arg, right_arg)
            for left_arg, right_arg in zip(left.typ_args, right.typ_args, strict=True)
        )
    )
```

Delete `test_typs_overlap_struct_typ_arity_mismatch_is_false` from
`tests/test_typs.py`. One template fixes one arity, constructor assertions
enforce it, and the old template/instance conflation was the only way to build
same-declaration instances with mismatched arity. Keep `strict=True` on the
remaining zips as a defensive assertion of that invariant.

- [ ] **Step 7: Route every code-generation pass by declaration type**

Rewrite the struct-related loops in `Compiler.compile`:

```python
for item in self._program_items():
    if isinstance(item.value, typs.StructTyp):
        self._declare_mod_item(item)

for item in self._program_items():
    if not isinstance(item.value, (typs.StructTyp, typs.StructTypTemplate)):
        self._declare_mod_item(item)

for item in self._program_items():
    if isinstance(item.value, typs.StructTyp):
        self._compile_mod_struct(item, item.value)

for item in self._program_items():
    if isinstance(item.value, typs.StructTypTemplate):
        item.value.validate_declaration()
    elif isinstance(item.value, typs.EnumTyp):
        _ = item.value.backing_typ
```

Add explicit no-op `StructTypTemplate` cases to `_declare_mod_item` and `_compile_mod_item` as defensive totality, even though the general declaration loop excludes templates. Leave `_ll_typ`, `_declare_struct_instance`, `_compile_struct_instance`, `_compile_mod_struct`, and `MonoResult.struct_instances` instance-only.

- [ ] **Step 8: Update tests that intentionally inspect the internal split**

Replace generic declaration casts and `.instance(...)` calls in
`tests/test_generic_structs.py`, `tests/test_impl.py`, `tests/test_traits.py`,
and `tests/test_typs.py` with `StructTypTemplate` and `.instantiate(...)`.
Keep casts to `StructTyp` wherever the tested object is a resolved type, an
impl self type, a field type, or a non-generic module item.

In `tests/test_loader.py`, preserve identity assertions for resolved non-generic structs. Add no template widening to loader assertions unless the fixture declares a generic struct.

---

### Task 3: Diagnose Bare Generic Associated-Function Scopes

**Files:**
- Modify: `src/leech/ir_env.py`
- Test: `tests/test_generic_structs.py`

**Interfaces:**
- Consumes: `StructTypTemplate` container bindings from Task 2.
- Produces: `MissingTypArgsError(template.name, ident.span)` when a generic template is used as an intermediate path scope.
- Preserves: associated lookup for concrete `StructTyp`, module, environment, and enum scopes.

- [ ] **Step 1: Add the regression test for the current internal crash**

Add to `tests/test_generic_structs.py`:

```python
def test_bare_generic_struct_assoc_fn_scope_requires_typ_args(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    pub fn main() i32 {
        let box = Box::make(7);
        return box.val;
    }
    """

    with pytest.raises(errors.MissingTypArgsError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"Box"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Box::make")
```

- [ ] **Step 2: Run the regression and verify failure**

Run:

```bash
uv run pytest tests/test_generic_structs.py -k "assoc_fn_scope_requires_typ_args" -q
```

Expected: FAIL before Task 2 integration, then PASS after Task 2 adds the
template scope arm and `scope_span` threading below.

- [ ] **Step 3: Add the explicit template scope arm**

Extend `_resolve_path_segment` with a `scope_span: Optional[src.SrcSpan]`
parameter and place this arm before `case typs.StructTyp()`:

```python
case typs.StructTypTemplate():
    assert scope_span is not None
    raise errors.MissingTypArgsError(scope.name, scope_span)
```

Track the span belonging to the current scope in `resolve_path`:

```python
scope: Env | ir_module.Mod | typs.StructTypTemplate | typs.StructTyp | typs.EnumTyp = self
scope_span: Optional[src.SrcSpan] = None
for ident in path.idents[:-1]:
    scope = self._resolve_path_segment(
        Env.Namespace.CONTAINERS, scope, ident, scope_span
    )
    scope_span = ident.span

return self._resolve_path_segment(ns, scope, path.idents[-1], scope_span)
```

The regression locks the diagnostic to the template-bearing `Box` segment,
not the following `make` identifier. `_resolve_path_segment` has no callers
outside `resolve_path`.

- [ ] **Step 4: Sweep away the old state model**

Run:

```bash
rg -n "is_generic_template|\._typ_args\b" src/leech tests
rg -n "\.instance\(" tests/test_typs.py tests/test_generic_structs.py tests/test_impl.py tests/test_traits.py
```

Expected: neither command prints a match. These test files contain every use
of the removed struct `.instance(...)` API on the pre-task tree.

Also run:

```bash
rg -n "StructTypTemplate" src/leech
```

Inspect every result. Templates may appear only in declaration/container/type-resolution/module-item code, template construction and caching, generic declaration validation, and the explicit missing-arguments path arm. They must not appear in `MonoResult.struct_instances`, LLVM type maps, struct field types, impl self types, or general `Typ` operations.

- [ ] **Step 5: Run final validation**

Run:

```bash
uv run pytest tests/test_generic_structs.py -q
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run reuse --no-multiprocessing lint
git diff --check
git status --short
```

Expected: all checks pass; the only working-tree changes are the two planning documents and the issue #3 implementation/test files named by this plan, all unstaged.

- [ ] **Step 6: Stop for manual review**

Do not start issue #14 or another task. Present the unstaged diff, validation results, and any intentional internal-test adaptations. Ask the repository owner whether to authorize the issue #3 commit; do not commit until explicitly approved.
