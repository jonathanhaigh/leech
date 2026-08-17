<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Reify impl blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give inherent `impl` blocks a real `ir_traits.Impl` object registered in `ImplRegistry`, so one impl-selection path serves both inherent and trait impls.

**Architecture:** `Impl` gains an optional `trait` — `None` means an inherent block. Both kinds register in `ImplRegistry`, keyed on `(Optional[Trait], head_shape)`. Member lookup moves off `typs.StructTyp` and onto the registry, which lets `StructTyp` stop importing `ir_module` and lets `Fn` carry a single `impl` parent pointer in place of the denormalized `trait_name` string and `impl_siblings` list.

**Tech Stack:** Python 3.14, uv, pytest, ruff, basedpyright, llvmlite.

This is Stage 1 of the [`Fn` hierarchy restructure staging plan](2026-08-17-fn-hierarchy-restructure-staging.md).

## Global Constraints

- Python 3.14. Four-space indent, Ruff 100-column limit.
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format .`, `uv run basedpyright` and `uv run reuse lint` must all pass before the stage is done.
- Baseline before starting: **1112 tests pass in ~42s**. Any drop in that count is a regression to investigate, not to accept.
- Imports stay module-qualified: `from leech import typs`, then `typs.Typ`.
- Sphinx-style docstrings per `AGENTS.md`. Document purpose, interface, surprising behaviour and non-obvious contracts; omit what the name and signature already say. Non-public items need docstrings only for surprising behaviour. `:pre:`/`:post:`/`:invariant:` fields go last. **Docstrings must not reference this plan, the staging document, or any design doc.**
- New files carry SPDX headers (`uv run reuse lint` enforces this).
- No language-visible behaviour changes except the one defect fix in Task 6, which gets its own test and commit.

## File Structure

| File | Responsibility after this stage |
| --- | --- |
| `src/leech/visibility.py` | **new** — `Access`, `PUBLIC`, `PRIVATE`. Extracted so `typs` can name visibility without importing `ir_module`. |
| `src/leech/ir_traits.py` | `Trait`, `TraitMethod`, `Impl` (both kinds), `ImplRegistry` (both kinds), `lookup_member`, `lookup_assoc_fn`. Gains the inherent-impl responsibilities. |
| `src/leech/typs.py` | Types only. Loses `get_assoc_fn`, `_find_generic_assoc_fn`, `_scan_generic_assoc_fn`, `_generic_assoc_fn_cache`, `add_assoc_fn`; keeps a name-collision check. Loses its `ir_module` import. |
| `src/leech/ir_module.py` | Loses `Access`; `_build_inherent_impl_defn` builds an `Impl`; `Fn` gains `impl` and loses `trait_name`/`impl_siblings`. |
| `src/leech/ir_env.py` | `_resolve_path_segment` takes the impl registry to resolve `Foo::assoc_fn`. |
| `src/leech/codegen.py` | Import update only. |
| `tests/test_impl.py` | Gains the duplicate-name, registry and ambiguity tests (Tasks 2, 5, 6). |
| `tests/test_traits.py` | Gains the registration and parent-pointer tests (Tasks 3, 4). |

---

### Task 1: Extract `Access` into its own module

The module is named `visibility` rather than `access` deliberately: `ModItem` has an `access` field and `Mod._add_item` an `access` parameter, so a module named `access` would be shadowed at exactly the sites that need it.

**Files:**
- Create: `src/leech/visibility.py`
- Modify: `src/leech/ir_module.py:9` (drop `enum` if unused), `:32-48` (delete), `:57`, `:58`, `:64`, and every `Access.from_ast`/`PUBLIC`/`PRIVATE` use
- Modify: `src/leech/typs.py:651-653`, `:666`
- Modify: `src/leech/ir_env.py:164`
- Modify: `src/leech/codegen.py:178`

**Interfaces:**
- Consumes: nothing.
- Produces: `visibility.Access` (enum with `PRIVATE = 0`, `PUBLIC = 1`, and `@staticmethod from_ast(pub_ast: Optional[ast.Access]) -> Access`), module constants `visibility.PRIVATE` and `visibility.PUBLIC`.

- [ ] **Step 1: Create the new module**

```python
# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Whether declared items are visible outside their own module."""

import enum
from typing import Optional

from leech import asserts, ast


class Access(enum.Enum):
    """Whether a module item is visible outside its declaring module."""

    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(pub_ast: Optional[ast.Access]) -> Access:
        """Return public for a parsed ``pub`` keyword and private otherwise."""
        if pub_ast is None:
            return PRIVATE
        asserts.assert_eq(pub_ast.value, "pub")
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC
```

- [ ] **Step 2: Delete the original and re-point every use**

In `ir_module.py`, delete lines 32-48 (the `Access` class and the two constants), add `visibility` to the `from leech import (...)` block, and replace each use: `Access.from_ast(...)` → `visibility.Access.from_ast(...)`, bare `PRIVATE`/`PUBLIC` → `visibility.PRIVATE`/`visibility.PUBLIC`, and the `ModItem.access` annotation → `visibility.Access`.

In `typs.py`, `ir_env.py` and `codegen.py`, replace `ir_module.Access` → `visibility.Access` and `ir_module.PUBLIC` → `visibility.PUBLIC`, adding `visibility` to each import block.

- [ ] **Step 3: Verify nothing still names the old location**

Run: `grep -rn "ir_module\.Access\|ir_module\.PUBLIC\|ir_module\.PRIVATE" src/ tests/`
Expected: no output.

- [ ] **Step 4: Run the full suite and the checkers**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright && uv run reuse lint`
Expected: 1112 passed; ruff, basedpyright and reuse all clean.

This is a pure move, so the existing suite is its test. There is no new behaviour to write a failing test against.

- [ ] **Step 5: Commit**

```bash
git add src/leech/visibility.py src/leech/ir_module.py src/leech/typs.py src/leech/ir_env.py src/leech/codegen.py
git commit -m "Extract Access into its own module"
```

---

### Task 2: Let `Impl` describe an inherent block

**Files:**
- Modify: `src/leech/ir_traits.py:123-218` (the `Impl` class)

**Interfaces:**
- Consumes: `visibility` (not directly), `errors.DuplicateItemDefnError`.
- Produces:
  - `Impl.__init__(impl_ast: ast.ImplDefn, trait: Optional[Trait], self_typ: typs.Typ, e: ir_env.Env, mod_name: str)`
  - `Impl.trait: Optional[Trait]` and `Impl.self_typ: typs.Typ` — both public, renamed from `_trait`/`_self_typ` (used only inside `ir_traits.py`, so the rename is contained)
  - `Impl.fns -> Collection[ir_module.Fn]` — every function in the block, in declaration order, read live
  - `Impl.get_fn(name: str) -> Optional[ir_module.Fn]`
  - `Impl.add_fn(fn: ir_module.Fn) -> None` — registers, and for a trait impl also runs the prototype checks `add_method` runs today
  - `Impl.name` returns `self_typ.name` for an inherent block, `<SelfTyp as Trait>` otherwise
  - `check_orphan_rule()` and `check_complete()` become no-ops when `trait is None`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_impl.py`:

```python
def test_two_assoc_fns_of_one_name_in_one_inherent_impl(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn get(*self) i32 { return 1; }
        fn get(*self) i32 { return 2; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)
    assert "associated function" in str(exc_info.value)
```

This passes today via `StructTyp._add_member`. It is written first so that Task 4, which deletes that path, cannot silently drop the diagnostic.

- [ ] **Step 2: Run it to confirm it passes today**

Run: `uv run pytest tests/test_impl.py::test_two_assoc_fns_of_one_name_in_one_inherent_impl -v`
Expected: PASS. (This one characterizes existing behaviour rather than driving new code; the new behaviour arrives in Task 6.)

- [ ] **Step 3: Rewrite the `Impl` class**

```python
class Impl:
    """An ``impl`` block's functions, resolved under its ``Self`` binding.

    :param trait: The trait implemented, or ``None`` for an inherent
        block.
    """

    ast: Final[ast.ImplDefn]
    trait: Final[Optional[Trait]]
    self_typ: Final[typs.Typ]
    env: Final[ir_env.Env]
    _mod_name: Final[str]
    _methods: Final[dict[TraitMethod, ir_module.Fn]]
    _fns: Final[dict[str, ir_module.Fn]]

    def __init__(
        self,
        impl_ast: ast.ImplDefn,
        trait: Optional[Trait],
        self_typ: typs.Typ,
        e: ir_env.Env,
        mod_name: str,
    ) -> None:
        self.ast = impl_ast
        self.trait = trait
        self.self_typ = self_typ
        self._mod_name = mod_name
        self.env = e.new_child()
        self.env.add_container(reserved.SELF_TYP_NAME, self_typ)
        self._methods = {}
        self._fns = {}

    @property
    def name(self) -> str:
        """This impl's diagnostic name, e.g. ``<i32 as Show>`` or ``Foo``."""
        if self.trait is None:
            return self.self_typ.name
        return f"<{self.self_typ.name} as {self.trait.name}>"

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this impl block."""
        return self.ast.span

    @property
    def fns(self) -> Collection[ir_module.Fn]:
        """Every function declared in this block, in declaration order.

        Read live, so a function registered after a caller takes this
        still counts.
        """
        return self._fns.values()

    def get_fn(self, name: str) -> Optional[ir_module.Fn]:
        """Return the function called ``name``, if this block declares one."""
        return self._fns.get(name)

    def check_orphan_rule(self) -> None:
        """Raise unless this impl's trait or self type is defined in its own module.

        Leech's orphan rule (similar to Rust's): it keeps any two modules
        from being able to write conflicting impls of the same trait for
        the same type, since the self type isn't restricted to a local
        type the way an inherent impl's target is. An inherent block is
        exempt - its own target is required to be local.

        :raises OrphanImplError: If neither is local.
        """
        if self.trait is None:
            return
        if not _is_local(self.trait, self._mod_name) and not _is_local(
            self.self_typ, self._mod_name
        ):
            raise errors.OrphanImplError(self.trait.name, self.self_typ.name, self.span)

    def add_fn(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this block's functions.

        For a trait impl this also checks ``fn`` against the prototype
        the trait declares for it.

        :raises DuplicateItemDefnError: If this block already declares a
            function of that name.
        :raises ExtraMethodInImplError: If this impl's trait declares no
            method named ``fn.name``.
        :raises TraitMethodSignatureMismatchError: If ``fn``'s signature
            doesn't match the one this impl's trait declares for it.
        """
        existing = self._fns.get(fn.name)
        if existing is not None:
            kind = "associated function" if self.trait is None else "method"
            raise errors.DuplicateItemDefnError(kind, fn.name, fn.span, existing.span)
        self._fns[fn.name] = fn
        if self.trait is not None:
            self._add_trait_method(self.trait, fn)

    def _add_trait_method(self, trait: Trait, fn: ir_module.Fn) -> None:
        trait_method = trait.get_method(fn.name)
        if trait_method is None:
            raise errors.ExtraMethodInImplError(trait.name, fn.name, fn.span)
        expected_typ = trait_method.fn_typ_for_self(self.self_typ)
        if fn.fn_typ is not expected_typ:
            raise errors.TraitMethodSignatureMismatchError(
                trait.name, fn.name, fn.fn_typ.name, expected_typ.name, fn.span
            )
        self._methods[trait_method] = fn

    def get_method(self, trait_method: TraitMethod) -> Optional[ir_module.Fn]:
        """Find this impl's implementation of ``trait_method``."""
        return self._methods.get(trait_method)

    def check_complete(self) -> None:
        """Raise if this impl is missing any of its trait's methods.

        An inherent block has no obligations to check.

        :raises TraitMethodNotImplementedError: If this impl's trait
            declares a method this impl never defined.
        """
        if self.trait is None:
            return
        for trait_method in self.trait.methods:
            if trait_method not in self._methods:
                raise errors.TraitMethodNotImplementedError(
                    self.trait.name, trait_method.name, self.self_typ.name, self.span
                )
```

Delete the old `add_method`. Update the remaining `_trait`/`_self_typ` references inside `ir_traits.py` — lines 286, 287, 292, 294, 296, 308 and 424 — to `trait`/`self_typ`.

- [ ] **Step 4: Update the one construction site**

`ir_module._build_trait_impl_defn:952` becomes:

```python
        impl = ir_traits.Impl(impl_ast, trait, self_typ, impl_env, self.name)
```

and `_build_impl_fns`'s `register` argument at `:966` becomes `impl.add_fn`.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q && uv run basedpyright`
Expected: 1112 passed (+1 for the new test = 1113); basedpyright clean.

- [ ] **Step 6: Commit**

```bash
git add src/leech/ir_traits.py src/leech/ir_module.py tests/test_impl.py
git commit -m "Let Impl describe an inherent block"
```

---

### Task 3: Register inherent impls in `ImplRegistry`

Two inherent blocks for one type are legal and common (`impl Foo { fn a() }` alongside `impl Foo { fn b() }`), so the conflicting-impls check stays trait-only. Inherent blocks conflict per member name, not per block — Task 6 covers that.

**Files:**
- Modify: `src/leech/ir_traits.py:254-373` (`ImplRegistry`)
- Modify: `src/leech/ir_module.py:876-907` (`_build_inherent_impl_defn`), `:961-969` (`_build_trait_impl_defn`), `:972-1021` (`_build_impl_fns`)
- Test: `tests/test_traits.py`

**Interfaces:**
- Consumes: `Impl.trait`, `Impl.self_typ`, `Impl.add_fn`, `Impl.name` from Task 2.
- Produces:
  - `ImplRegistry._impls: dict[tuple[Optional[Trait], Hashable], list[Impl]]`
  - `ImplRegistry.find_inherent_impls(typ: typs.Typ) -> list[Impl]`
  - `_build_impl_fns(impl_ast: ast.ImplDefn, impl: ir_traits.Impl, generic_fns: list[Fn], trait_name: Optional[str] = None) -> None` — replaces the `fn_env`/`recv_typ`/`register`/`qualified_prefix` parameters, all of which are now derivable from `impl`
  - Every function in every block is registered on its `Impl` via `add_fn`, inherent blocks included

- [ ] **Step 1: Write the failing test**

Add to `tests/test_traits.py`:

```python
def test_inherent_impl_is_registered(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        struct Foo { a: i32 }
        impl Foo {
            fn get(*self) i32 { return self.*.a; }
        }
        """,
    )
    foo = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Foo")
    impls = mod.env.impl_registry.find_inherent_impls(foo)
    assert len(impls) == 1
    assert impls[0].trait is None
    assert impls[0].self_typ is foo
    assert impls[0].get_fn("get") is not None
```

Add `ir_env` to that file's `from leech import ...` line if it isn't already there.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_traits.py::test_inherent_impl_is_registered -v`
Expected: FAIL with `AttributeError: 'ImplRegistry' object has no attribute 'find_inherent_impls'`.

- [ ] **Step 3: Widen the registry's key and add inherent lookup**

In `ImplRegistry`, change the `_impls` annotation to
`Final[dict[tuple[Optional[Trait], Hashable], list[Impl]]]`, leave
`_traits_by_shape` holding traits only, and rewrite `add_impl`:

```python
    def add_impl(self, impl: Impl) -> None:
        """Register ``impl``, rejecting it if it's incoherent or conflicts with one
        already registered.

        Two inherent blocks for one type are legal, so only trait impls
        are checked for conflicts; inherent blocks collide per member
        name instead.

        :param impl: The impl to register.
        :raises OrphanImplError: If neither ``impl``'s trait nor its self
            type is defined in ``impl``'s own module.
        :raises ConflictingImplsError: If ``impl``'s self type could
            overlap with an already-registered impl of the same trait.
        """
        impl.check_orphan_rule()
        shape = _head_shape(impl.self_typ)
        key = (impl.trait, shape)
        impls = self._impls.get(key)
        if impls is None:
            impls = []
            self._impls[key] = impls
            if impl.trait is not None:
                self._traits_by_shape.setdefault(shape, []).append(impl.trait)
        if impl.trait is not None:
            for existing in impls:
                if _typs_overlap(impl.self_typ, existing.self_typ):
                    raise errors.ConflictingImplsError(
                        impl.trait.name, impl.self_typ.name, impl.span, existing.span
                    )
        impls.append(impl)

    def find_inherent_impls(self, typ: typs.Typ) -> list[Impl]:
        """Find every inherent ``impl`` block that applies to ``typ``.

        Unlike :meth:`find_impl`, more than one can apply: inherent
        blocks are distinguished by the members they declare, not by
        being mutually exclusive.
        """
        found = []
        for impl in self._impls.get((None, _head_shape(typ)), ()):
            bindings = typs.match_typ_args(impl.self_typ, typ)
            if bindings is not None and self._impl_bounds_hold(impl, bindings):
                found.append(impl)
        return found
```

`find_impl` needs no change: it looks up `(trait, shape)` with a non-`None` trait, so it can never see an inherent block.

- [ ] **Step 4: Collapse `_build_impl_fns` onto the `Impl`**

Every function in every block now registers on its `Impl`, which is what lets Task 4 read siblings from it. The inherent path *additionally* keeps calling `add_assoc_fn` for now; Task 5 replaces that line.

```python
    def _build_impl_fns(
        self,
        impl_ast: ast.ImplDefn,
        impl: ir_traits.Impl,
        generic_fns: list[Fn],
        trait_name: Optional[str] = None,
    ) -> None:
        """Build and register every function in an ``impl`` block.

        :param impl_ast: The parsed ``impl`` block.
        :param impl: The block's reified form, which every built function
            is registered on.
        :param generic_fns: Accumulates functions from generic impls.
        :param trait_name: The module-qualified name of the trait this
            block implements, or ``None`` for an inherent block.
        """
        # Aliased by every function built here, so each one ends up
        # seeing the whole block without this loop needing to know its
        # contents up front.
        siblings: list[Fn] = []
        for fn_ast in impl_ast.fns:
            # Generic associated functions/methods aren't supported yet -
            # nothing upstream rejects the syntax, so fail loudly here
            # rather than silently mistreat the function's one literal
            # FnTyp as real.
            if fn_ast.generic_params:
                raise NotImplementedError("generic associated functions aren't supported yet")
            fn = Fn(
                fn_ast,
                impl.env,
                self.name,
                recv_typ=impl.self_typ,
                trait_name=trait_name,
                impl_siblings=siblings,
            )
            siblings.append(fn)
            impl.add_fn(fn)
            if impl.trait is None:
                asserts.checked_cast(impl.self_typ, typs.StructTyp).add_assoc_fn(fn)
            if impl_ast.generic_params:
                generic_fns.append(fn)
            else:
                self._add_item(
                    f"{impl.name}::{fn_ast.name.name}",
                    visibility.Access.from_ast(fn_ast.access),
                    fn,
                )
```

`impl.name` reproduces both old prefixes exactly: `self_typ.name` for an inherent block (what `typ.name` gave) and `<SelfTyp as Trait>` for a trait impl.

- [ ] **Step 5: Build and register an `Impl` for inherent blocks**

Replace the tail of `_build_inherent_impl_defn` (currently lines 902-907) with:

```python
        fn_env = typ.env.new_child()
        for typ_param in typs.typ_params_from_ast(impl_ast, impl_ast.generic_params, self.env):
            fn_env.add_container(typ_param.name, typ_param)

        impl = ir_traits.Impl(impl_ast, None, typ, fn_env, self.name)
        self.loader.impl_registry.add_impl(impl)
        self._build_impl_fns(impl_ast, impl, generic_fns)
```

`Impl.__init__` binds `Self` into its own child scope, which is what the deleted
`fn_env.add_container(reserved.SELF_TYP_NAME, typ)` did.

Update `_build_trait_impl_defn`'s tail to the new signature:

```python
        self._build_impl_fns(
            impl_ast, impl, generic_fns, trait_name=f"{trait.mod_name}::{trait.name}"
        )
        impl.check_complete()
```

- [ ] **Step 6: Run the new test and the suite**

Run: `uv run pytest tests/test_traits.py::test_inherent_impl_is_registered -v && uv run pytest -q && uv run basedpyright`
Expected: PASS; 1114 passed overall; basedpyright clean.

- [ ] **Step 7: Commit**

```bash
git add src/leech/ir_traits.py src/leech/ir_module.py tests/test_traits.py
git commit -m "Register inherent impls in the impl registry"
```

---

### Task 4: Replace `Fn.trait_name` and `Fn.impl_siblings` with `Fn.impl`

Both fields are denormalized stand-ins for a parent pointer that could not exist while inherent blocks were unreified. Task 3 made every function's block reachable, so one field replaces both.

**Files:**
- Modify: `src/leech/ir_module.py:252-278` (`Fn`), `:444-470` (`qualified_name`), `:490-501` (`_siblings`), `:961-969` (`_build_trait_impl_defn`), `_build_impl_fns`

**Interfaces:**
- Consumes: `Impl.trait`, `Impl.fns` from Task 2; `_build_impl_fns(impl_ast, impl, generic_fns, ...)` from Task 3.
- Produces: `Fn.impl: Final[Optional[ir_traits.Impl]]` — the block this function was declared in, `None` for a free function.
- Removed: `Fn.trait_name`, `Fn.impl_siblings`, and `_build_impl_fns`' `trait_name` parameter.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_traits.py`:

```python
def test_fn_points_at_its_impl_block(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        struct Foo { a: i32 }
        trait Show { fn show(*self) i32; }
        impl Show for Foo {
            pub fn show(*self) i32 { return self.*.a; }
        }
        """,
    )
    foo = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Foo")
    method = ir_traits.lookup_member(foo, "show", mod.env.impl_registry, None)
    assert method is not None
    assert method.impl is not None
    assert method.impl.trait is not None
    assert method.impl.trait.name == "Show"
```

Add `ir_traits` to that file's imports if absent.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_traits.py::test_fn_points_at_its_impl_block -v`
Expected: FAIL with `AttributeError: 'Fn' object has no attribute 'impl'`.

- [ ] **Step 3: Swap the two fields for the parent pointer**

In `ir_module.py`, replace `Fn`'s two fields and constructor parameters:

```python
class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    """A source-defined function that may serve as a generic template.

    :param impl: The ``impl`` block this function is declared in;
        ``None`` for a free function.
    """

    impl: Final[Optional[ir_traits.Impl]]

    @override
    def __init__(
        self,
        fn_ast: ast.FnDefn,
        e: ir_env.Env,
        mod_name: str,
        recv_typ: Optional[typs.Typ] = None,
        impl: Optional[ir_traits.Impl] = None,
    ) -> None:
        super().__init__(fn_ast, e, mod_name, recv_typ)
        self.impl = impl
```

`FnInstance.qualified_name`'s impl-instance branch becomes:

```python
        if self._self_typ is not None:
            prefix = self._self_typ.qualified_name
            impl = asserts.checked_cast(self._fn, Fn).impl
            trait = None if impl is None else impl.trait
            if trait is not None:
                prefix = f"<{prefix} as {trait.mod_name}::{trait.name}>"
            return f"{prefix}::{name}"
```

and `FnInstance._siblings`:

```python
    @functools.cached_property
    def _siblings(self) -> frozenset[Fn]:
        """Every function declared alongside this instance's own.

        Siblings come from the ``impl`` block a function was declared in
        rather than from the self type, which only an inherent impl's
        methods are registered on. Empty unless this is a method
        instance.
        """
        if self._self_typ is None:
            return frozenset()
        impl = asserts.checked_cast(self._fn, Fn).impl
        return frozenset() if impl is None else frozenset(impl.fns)
```

`Impl.fns` reads `_fns.values()` live, so it preserves the aliasing property the
`siblings` list provided: a function registered later still counts.

The `<SelfTyp as Trait>` string is unchanged, so no emitted symbol name moves.

- [ ] **Step 4: Drop `trait_name` and the local siblings list from `_build_impl_fns`**

```python
    def _build_impl_fns(
        self,
        impl_ast: ast.ImplDefn,
        impl: ir_traits.Impl,
        generic_fns: list[Fn],
    ) -> None:
        """Build and register every function in an ``impl`` block.

        :param impl_ast: The parsed ``impl`` block.
        :param impl: The block's reified form, which every built function
            is registered on and points back at.
        :param generic_fns: Accumulates functions from generic impls.
        """
        for fn_ast in impl_ast.fns:
            # Generic associated functions/methods aren't supported yet -
            # nothing upstream rejects the syntax, so fail loudly here
            # rather than silently mistreat the function's one literal
            # FnTyp as real.
            if fn_ast.generic_params:
                raise NotImplementedError("generic associated functions aren't supported yet")
            fn = Fn(fn_ast, impl.env, self.name, recv_typ=impl.self_typ, impl=impl)
            impl.add_fn(fn)
            if impl.trait is None:
                asserts.checked_cast(impl.self_typ, typs.StructTyp).add_assoc_fn(fn)
            if impl_ast.generic_params:
                generic_fns.append(fn)
            else:
                self._add_item(
                    f"{impl.name}::{fn_ast.name.name}",
                    visibility.Access.from_ast(fn_ast.access),
                    fn,
                )
```

In `_build_trait_impl_defn`, drop the now-removed keyword argument:

```python
        self._build_impl_fns(impl_ast, impl, generic_fns)
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest -q && uv run basedpyright && grep -rn "trait_name\|impl_siblings" src/`
Expected: 1115 passed; basedpyright clean; grep produces no output.

Sibling resolution is the thing most at risk here. If
`tests/test_traits.py::test_generic_trait_impl_method_calls_sibling_for_non_struct_self_typ`
fails, `Impl.fns` is being snapshotted somewhere rather than read live.

- [ ] **Step 6: Commit**

```bash
git add src/leech/ir_module.py tests/test_traits.py
git commit -m "Give Fn a parent impl pointer"
```

---

### Task 5: Look members up through the registry

**Files:**
- Modify: `src/leech/ir_traits.py:395-441` (`lookup_member`), plus a new `lookup_assoc_fn`
- Modify: `src/leech/typs.py:687` and `:806-879` (narrow and delete), plus a new `reserve_assoc_fn_name`
- Modify: `src/leech/ir_env.py:153-207` and `:218-226` (thread the registry)
- Modify: `src/leech/ir_module.py` (`_build_impl_fns`)

**Interfaces:**
- Consumes: `ImplRegistry.find_inherent_impls`, `Impl.get_fn` from Task 3.
- Produces:
  - `ir_traits.lookup_assoc_fn(typ, name, registry, span) -> Optional[ir_module.Fn | ir_module.FnInstance]` — inherent members only
  - `typs.StructTyp.reserve_assoc_fn_name(name: str, span: Optional[src.SrcSpan]) -> None`
  - `Env._resolve_path_segment(ns, scope, ident, registry)` — gains a fourth parameter
- Removed: `StructTyp.add_assoc_fn`, `get_assoc_fn`, `_find_generic_assoc_fn`, `_scan_generic_assoc_fn`, `_generic_assoc_fn_cache`, `_add_member`, `_member_ident_span`; `StructTyp._members` narrows to `dict[str, StructField]`.

`Foo::assoc_fn` keeps consulting **inherent** members only. Whether a path form should also see trait-provided members is an open language design question, deliberately unchanged here.

- [ ] **Step 1: Write the characterization tests**

Add to `tests/test_impl.py`:

```python
def test_generic_inherent_impl_method_found_through_registry(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn get(*self) T { return self.*.val; }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 7 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_assoc_fn_name_collides_with_field(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn a(*self) i32 { return 1; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)
    assert "associated function" in str(exc_info.value)
```

The first passes today through `_scan_generic_assoc_fn` and must keep passing through the registry. The second pins the field/associated-function collision check that moves out of `_add_member`.

- [ ] **Step 2: Run them to confirm they pass today**

Run: `uv run pytest tests/test_impl.py -k "registry or collides_with_field" -v`
Expected: both PASS. They are characterization tests guarding the refactor, so they must be green before and after.

- [ ] **Step 3: Add the inherent lookup and rewrite `lookup_member`**

In `ir_traits.py`:

```python
def lookup_assoc_fn(
    typ: typs.Typ,
    name: str,
    registry: ImplRegistry,
    span: Optional[src.SrcSpan],
) -> Optional[ir_module.Fn | ir_module.FnInstance]:
    """Find ``typ``'s inherent associated function called ``name``.

    :raises AmbiguousMethodError: If more than one applicable inherent
        block declares one of that name.
    """
    matches = []
    for impl in registry.find_inherent_impls(typ):
        fn = impl.get_fn(name)
        if fn is not None:
            matches.append(fn)
    found = disambiguate(matches, name, typ.name, span)
    if found is None:
        return None
    # A block matched structurally (e.g. `Box[T]` against `Box[i32]`)
    # still holds the function built against its own abstract self type;
    # instantiating it against `typ` substitutes the block's parameters.
    return found.for_recv_typ(typ)


def lookup_member(
    typ: typs.Typ,
    name: str,
    registry: ImplRegistry,
    span: Optional[src.SrcSpan],
) -> Optional[ir_module.Fn | ir_module.FnInstance]:
    """Find ``typ``'s member (inherent or via a trait impl) called ``name``.

    Only ever meaningful for a *concrete* type - a call through an
    unsubstituted type parameter resolves differently, against the
    parameter's own declared bounds rather than the registry.

    :raises AmbiguousMethodError: If more than one applicable impl
        provides a member of that name.
    """
    inherent = lookup_assoc_fn(typ, name, registry, span)
    if inherent is not None:
        return inherent

    matches = []
    for impl in registry._find_impls_for_typ(typ):
        trait_method = opt_util.opt_unwrap(impl.trait).get_method(name)
        if trait_method is None:
            continue
        method = impl.get_method(trait_method)
        if method is not None:
            matches.append(method)
    method = disambiguate(matches, name, typ.name, span)
    if method is None:
        return None
    return method.for_recv_typ(typ)
```

Add `opt_util` to `ir_traits.py`'s import block.

- [ ] **Step 4: Strip the associated-function machinery out of `typs.StructTyp`**

Delete `add_assoc_fn`, `get_assoc_fn`, `_generic_assoc_fn_cache`, `_find_generic_assoc_fn` and `_scan_generic_assoc_fn` (lines 828-879). Narrow the `_members` annotation at line 687 to `Final[dict[str, StructField]]`, add a parallel `_assoc_fn_spans: Final[dict[str, Optional[src.SrcSpan]]]` initialized to `{}` in `__init__`, and replace `_member_ident_span`/`_add_member` with:

```python
    def _add_field(self, field: StructField) -> None:
        """Register ``field`` in the shared field and associated-function namespace.

        :raises ReservedNameError: If ``field`` takes a reserved name.
        """
        if reserved.is_reserved(field.name):
            raise errors.ReservedNameError(field.name, field.ast.ident.span)
        existing = self._members.get(field.name)
        if existing is not None:
            raise errors.DuplicateFieldInStructDefnError(
                field.name, field.ast.ident.span, existing.ast.ident.span
            )
        self._members[field.name] = field

    def reserve_assoc_fn_name(self, name: str, span: Optional[src.SrcSpan]) -> None:
        """Claim ``name`` in the shared field and associated-function namespace.

        Fields and associated functions share one namespace across every
        ``impl`` block for this type, so this is what rejects a second
        claim on a name whichever block makes it.

        :raises ReservedNameError: If ``name`` is reserved.
        :raises DuplicateItemDefnError: If a field or another associated
            function already has that name.
        """
        if reserved.is_reserved(name):
            raise errors.ReservedNameError(name, span)
        field = self._members.get(name)
        if field is not None:
            raise errors.DuplicateItemDefnError(
                "associated function", name, span, field.ast.ident.span
            )
        if name in self._assoc_fn_spans:
            raise errors.DuplicateItemDefnError(
                "associated function", name, span, self._assoc_fn_spans[name]
            )
        self._assoc_fn_spans[name] = span
```

Update `__init__`'s field loop (line 711) to call `self._add_field(...)`, and simplify `fields` (lines 881-891) — every `_members` value is now a `StructField`, so the `isinstance` filter goes. **Keep the `_check_finite_size` call**: it is what rejects by-value cycles and runaway generic nesting, and dropping it would silently disable that check.

```python
    @functools.cached_property
    def fields(self) -> types.MappingProxyType[str, StructField]:
        """Return fields by declaration order after rejecting infinite-size layouts."""
        self._check_finite_size([], [])
        return types.MappingProxyType(dict(self._members))
```

Leave `typs.py`'s import block alone for now — Task 7 owns removing `ir_module` from it.

- [ ] **Step 5: Thread the registry through path resolution**

In `ir_env.py`, add a `registry: ir_traits.ImplRegistry` parameter to `_resolve_path_segment` and replace its `StructTyp` case body with:

```python
            case typs.StructTyp():
                # A struct's members share one namespace and are all values,
                # never types, so a type lookup can't name one. Of those
                # members only associated functions are reachable by path:
                # `SomeStruct::x` is not a way to name a field, so a field
                # falls through to "not found" below.
                res = (
                    ir_traits.lookup_assoc_fn(scope, ident.name, registry, ident.span)
                    if ns == Env.Namespace.VARS
                    else None
                )
                # Private associated functions are invisible outside the
                # struct's own module, same as private Mod items above.
                if res is not None and not res.is_accessible_from(ident.span.file):
                    raise errors.PrivateItemAccessError(
                        "function", ident.name, ident.span, res.span
                    )
```

and pass `self.impl_registry` from both call sites in `resolve_path`:

```python
        scope = self
        for ident in path.idents[:-1]:
            scope = Env._resolve_path_segment(
                Env.Namespace.CONTAINERS, scope, ident, self.impl_registry
            )

        return Env._resolve_path_segment(ns, scope, path.idents[-1], self.impl_registry)
```

- [ ] **Step 6: Move the name reservation and bare-name binding into `_build_impl_fns`**

`add_assoc_fn` did two things: the namespace check, and the bare-name binding that lets one associated function call another without qualification. Both move to the caller, which keeps `typs` free of `ir_module`. In `_build_impl_fns`, replace the `add_assoc_fn` line with:

```python
            if impl.trait is None:
                struct_typ = asserts.checked_cast(impl.self_typ, typs.StructTyp)
                struct_typ.reserve_assoc_fn_name(fn_ast.name.name, fn_ast.name.span)
                struct_typ.env.add_var(fn.name, fn)
```

The reservation must run *before* `impl.add_fn(fn)` so that a name colliding with
a field is reported as a namespace clash rather than reaching the block's own
duplicate check, so hoist it above the `Fn(...)` construction:

```python
            if impl.trait is None:
                struct_typ = asserts.checked_cast(impl.self_typ, typs.StructTyp)
                struct_typ.reserve_assoc_fn_name(fn_ast.name.name, fn_ast.name.span)
            fn = Fn(fn_ast, impl.env, self.name, recv_typ=impl.self_typ, impl=impl)
            impl.add_fn(fn)
            if impl.trait is None:
                asserts.checked_cast(impl.self_typ, typs.StructTyp).env.add_var(fn.name, fn)
```

- [ ] **Step 7: Run the suite**

Run: `uv run pytest -q && uv run basedpyright`
Expected: 1117 passed; basedpyright clean.

- [ ] **Step 8: Commit**

```bash
git add src/leech/ir_traits.py src/leech/typs.py src/leech/ir_env.py src/leech/ir_module.py tests/test_impl.py
git commit -m "Look impl members up through the impl registry"
```
---

### Task 6: Report conflicting generic inherent impls instead of asserting

`_scan_generic_assoc_fn` carried `assert found is None, "more than one applicable generic impl provides ..."` because nothing rejected two overlapping generic inherent blocks declaring the same member. Task 5 replaced that scan with `disambiguate`, which raises `AmbiguousMethodError`. This task pins the new behaviour.

**Files:**
- Test: `tests/test_impl.py`

**Interfaces:**
- Consumes: `lookup_assoc_fn` from Task 5.
- Produces: nothing — behaviour is already in place; this adds the test.

- [ ] **Step 1: Write the test**

```python
def test_conflicting_generic_inherent_impls(tmp_path):
    # Two generic inherent blocks both apply to Box[i32] and both
    # declare `get`. Nothing rejects the pair at declaration time, so
    # the ambiguity surfaces at the call site.
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] { fn get(*self) i32 { return 1; } }
    impl[U] Box[U] { fn get(*self) i32 { return 2; } }
    pub fn main() i32 {
        let b = Box[i32] { val: 0 };
        return b.get();
    }
    """
    with pytest.raises(errors.AmbiguousMethodError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"get"' in str(exc_info.value)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_impl.py::test_conflicting_generic_inherent_impls -v`
Expected: PASS.

If it fails with `AssertionError` instead, Task 5's `lookup_assoc_fn` is not
being reached for this shape — check that both blocks registered as separate
`Impl`s via `find_inherent_impls`.

- [ ] **Step 3: Verify the assert is gone**

Run: `grep -rn "more than one applicable generic impl" src/`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add tests/test_impl.py
git commit -m "Report conflicting generic inherent impls as ambiguous"
```

---

### Task 7: Drop `typs`' `ir_module` import and verify the stage

**Files:**
- Modify: `src/leech/typs.py:16-27` (import block), `:651-666` (`StructField.access`)
- Modify: `src/leech/typcheck.py` — remove any `# noqa: PLC0415` local import made unnecessary

**Interfaces:**
- Consumes: everything above.
- Produces: `typs.py` with no `ir_module` import.

- [ ] **Step 1: Check what still needs it**

Run: `grep -n "ir_module" src/leech/typs.py`
Expected: only `StructField.access`/`is_accessible_from` if Task 1 was not fully applied, otherwise nothing. Any remaining hit is a leftover from Task 5 — remove it before continuing.

- [ ] **Step 2: Remove the import**

Delete `ir_module` from the `from leech import (...)` block at `typs.py:16-27`.

- [ ] **Step 3: Confirm the cycle is broken**

Run: `uv run python -c "import leech.typs"` then `uv run basedpyright`
Expected: no `ImportError`; basedpyright clean.

The `typs` ⇄ `ir_traits` edge remains by design — bound resolution genuinely needs `Trait` — so the `if TYPE_CHECKING: from leech import ir_traits` block and the local import in `resolve_bound` stay.

- [ ] **Step 4: Remove now-unnecessary local imports**

Check each `# noqa: PLC0415` local import in `typcheck.py` (lines 429, 464, 502, 731, 905, 934) and `ir_module.py:733`: any that exists solely because `typs` imported `ir_module` can become a module-level import. Move only those that basedpyright and the suite both accept; leave the rest.

- [ ] **Step 5: Full stage verification**

Run:

```bash
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run reuse lint
```

Expected: 1118 passed; every checker clean.

Then confirm the stage's deletions actually happened:

```bash
grep -rn "add_assoc_fn\|get_assoc_fn\|_scan_generic_assoc_fn\|_generic_assoc_fn_cache" src/
grep -rn "trait_name\|impl_siblings" src/
grep -n "ir_module" src/leech/typs.py
```

Expected: all three produce no output.

- [ ] **Step 6: Commit**

```bash
git add src/leech/typs.py src/leech/typcheck.py src/leech/ir_module.py
git commit -m "Drop typs' dependency on ir_module"
```

---

## Out of scope

- **Declaration-time detection of conflicting inherent impls.** Task 6 reports the ambiguity at the call site, which is where the old assert fired. Rejecting the pair at declaration time needs per-member-name overlap checking across blocks — a real feature, not a refactor.
- **One producer for an impl method's symbol name.** `Fn.impl` makes it possible to unify `ir_traits.Impl.name` with `ir_module.FnInstance.qualified_name`, but they already differ deliberately (`m::<Foo as Show>::show` versus `<m::Box[m::Foo] as m::Show>::show`) and both are collision-free. Unifying moves emitted symbols; it is duplication, not a defect.
- **Breaking the `typs` ⇄ `ir_traits` cycle.** Bound resolution needs `Trait`. Moving it out of `typs` is a separate change.
- **`Foo::assoc_fn` seeing trait-provided members.** Path resolution stays inherent-only, exactly as today.
- **Anything from Stages 2-5.** In particular `Fn.instance`/`impl_instance`/`for_recv_typ` are untouched here; `lookup_assoc_fn` still calls `for_recv_typ`.
