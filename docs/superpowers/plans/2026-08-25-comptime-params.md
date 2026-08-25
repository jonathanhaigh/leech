<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Comptime Generic Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a generic parameter bind a compile-time value (`fn f[T, N: usize](x: T) i32`) as well as a type, resolving GitHub issue #39.

**Architecture:** A comptime parameter is either a `TypParamTyp` (today's type parameter) or a new `ValueParamTyp`, both sharing a `ComptimeParamTyp` base; a concrete value argument is a new `ComptimeValueTyp` `Typ` node, interned like any other `Typ` so equal values are the same instance. Because a value argument is just another `Typ`, the existing substitution, monomorphization-key, and symbol-mangling machinery (all already `tuple[Typ, ...]`-shaped) needs no structural change - only renaming from "typ"/"generic" to "comptime" vocabulary, plus new dispatch where a parameter's *kind* (type vs. value) must be told apart. `ArrayTyp.length` becomes `Typ`-shaped so `[T; N]` can carry a value parameter, and a value parameter is bound into both of `ir_env.Env`'s namespaces so it can be used structurally (`[T; N]`) and as a runtime expression (`return N;`).

**Tech Stack:** Python 3.14, `lark` (LALR parser), `llvmlite`, `pytest`, `ruff`, `basedpyright`.

**Spec:** `docs/superpowers/specs/2026-08-25-comptime-params-design.md`

## Global Constraints

- Every renamed identifier is renamed exactly as the spec's Terminology Rename table states; `is_generic`, `substitute_typ_params`, `infer_typ_args`, `match_typ_args`, and other general `Typ`-tree method names are **not** renamed.
- Value parameters support exactly `IntTyp` and `BoolTyp` in this issue (per spec's `UnsupportedValueParamTypError`).
- Value arguments are literals or bare identifiers only - no arithmetic (`f[i32, N * 2]` is out of scope).
- After every task: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright` must all pass before committing.
- Follow `AGENTS.md`: four-space indent, Ruff's 100-column limit, Sphinx-style docstrings, `asserts` helpers over bare `assert` where one fits, commit messages end with `For: #39: Non-type generic parameters`.

---

## Task 1: Terminology rename (grammar through tests)

Pure rename, no behavior change. Verified by the full existing test suite passing unchanged (after the same renames are applied to tests that reference these identifiers).

**Files:**
- Modify: `src/leech/leech.lark`
- Modify: `src/leech/ast.py`
- Modify: `src/leech/typs.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/ir_builtins.py`
- Modify: `src/leech/typcheck.py`
- Modify: `src/leech/errors.py`
- Modify: `tests/test_ast.py`, `tests/test_builtins.py`, `tests/test_enums.py`, `tests/test_errors.py`, `tests/test_generic_fns.py`, `tests/test_generic_structs.py`, `tests/test_impl.py`, `tests/test_parsing.py`, `tests/test_traits.py`, `tests/test_typs.py`

**Interfaces:**
- Produces: every identifier in the rename table below, in place of its "before" spelling, ready for Task 2 onward to build on.

**Rename table** (apply exactly - do not rename anything not listed here):

| Kind | Before | After |
|---|---|---|
| Grammar rules (`leech.lark`) | `generic_params`, `generic_param`, `generic_args` | `comptime_params`, `comptime_param`, `comptime_args` |
| AST class (`ast.py`) | `GenericParam` | `ComptimeParam` |
| AST attributes (`ast.py`, every `FnDecl`/`StructDefn`/`TraitDefn`/`ImplDefn`/`BasicTyp`/`VarExpr`) | `.generic_params`, `.generic_args` | `.comptime_params`, `.comptime_args` |
| `typs.py` free function | `typ_params_from_ast` | `comptime_params_from_ast` (its `generic_params: Sequence[ast.GenericParam]` parameter becomes `comptime_params: Sequence[ast.ComptimeParam]`) |
| `typs.py` / `ir_traits.py` / `ir_module.py` public property | `.typ_params` (on `StructTypTemplate`, `SrcFnSymbol`, `ExternFnSymbol`, `IntrinsicFnSymbol`, `Trait`, `Impl`) | `.comptime_params` |
| `typs.py` public function | `check_typ_arg_bounds` | `check_comptime_arg_bounds` |
| `typs.py` local variable (`resolve_bound`) | `bound_typ_args` | `bound_comptime_args` |
| `ir_module.py` local variables | `impl_typ_params` | `impl_comptime_params` |
| `ir_traits.py` method | `check_typ_params_constrained` | `check_comptime_params_constrained` |
| `ir_builder.py` field/param | `_typ_arg_mapping`, `typ_arg_mapping` | `_comptime_arg_mapping`, `comptime_arg_mapping` |
| `ir_builder.py` local variable | `concrete_typ_args` | `concrete_comptime_args` |
| `ir_builtins.py` abstract/concrete method + its parameter | `fn_typ_for_typ_args(self, typ_params: ...)` | `fn_typ_for_comptime_args(self, comptime_params: ...)` |
| `ir_builtins.py` `_build_body` parameter | `typ_args: tuple[typs.Typ, ...]` | `comptime_args: tuple[typs.Typ, ...]` |
| `typcheck.py` method | `_resolve_explicit_typ_args` | `_resolve_explicit_comptime_args` |
| `typcheck.py` method | `_infer_typ_args` | `_infer_comptime_args` |
| `typcheck.py` method | `_typ_params_of` | `_comptime_params_of` |
| `typcheck.py` local variables (`_resolve_generic_call`, `_generic_var_ref_typ`) | `typ_params`, `typ_args`, `mapping` args | `comptime_params`, `comptime_args` |
| `errors.py` classes | `MissingTypArgsError`, `CannotInferTypArgError`, `WrongNumberOfTypArgsError`, `TypArgsOnNonGenericItemError`, `UnconstrainedImplTypParamError` | `MissingComptimeArgsError`, `CannotInferComptimeArgError`, `WrongNumberOfComptimeArgsError`, `ComptimeArgsOnNonGenericItemError`, `UnconstrainedImplComptimeParamError` |

**Deliberately not renamed** (leave exactly as-is, with a one-line comment where the name might otherwise look inconsistent with a renamed neighbor):
- `typs.TypParamTyp` itself - stays; Task 2 gives it a `ComptimeParamTyp` base and a `ValueParamTyp` sibling.
- `Typ.substitute_typ_params`, `Typ.infer_typ_args`, `typs.match_typ_args`, `typs._contains_typ`, `typs.contains_typ`, `typs.typs_overlap`, `typs.unsatisfied_bound` - general `Typ`-tree method/function names, not the parameter-list concept.
- `ir_module.FnSymbol.is_generic` - an adjective, not a noun naming the concept.
- `IntrinsicFnSymbol._typ_params` (cached backing field) and `IntrinsicFnSymbol._typ_param_names` - intrinsics never declare value parameters in this issue, so these stay genuinely type-only; add the comment `# Intrinsics take only type parameters in this issue - see #39.` above `_typ_param_names`'s declaration to explain the asymmetry with the now-renamed public `.comptime_params` property built from it.

- [ ] **Step 1: Rename in the grammar and AST layer**

In `src/leech/leech.lark`, rename the five grammar rules per the table (`generic_params`/`generic_param`/`generic_args` -> `comptime_params`/`comptime_param`/`comptime_args`) at every occurrence (lines defining and referencing them: `fn_defn`, `struct_defn`, `trait_defn`, `impl_defn`, the `generic_params`/`generic_args` rule definitions themselves, `basic_typ`, `var_expr`).

In `src/leech/ast.py`, rename `class GenericParam` to `class ComptimeParam` and every `.generic_params`/`.generic_args` attribute (on `FnDecl`, `StructDefn`, `TraitDefn`, `ImplDefn`, `BasicTyp`, `VarExpr`) to `.comptime_params`/`.comptime_args`, including their docstrings' prose mentions.

Run, from the repo root:

```bash
cd src/leech
sed -i \
  -e 's/\bgeneric_params\b/comptime_params/g' \
  -e 's/\bgeneric_param\b/comptime_param/g' \
  -e 's/\bgeneric_args\b/comptime_args/g' \
  leech.lark
sed -i \
  -e 's/\bGenericParam\b/ComptimeParam/g' \
  -e 's/\bgeneric_params\b/comptime_params/g' \
  -e 's/\bgeneric_args\b/comptime_args/g' \
  ast.py
cd ../..
```

- [ ] **Step 2: Verify parsing and AST tests fail only on identifier names, then fix them**

```bash
uv run pytest tests/test_parsing.py tests/test_ast.py -q
```

Expected: failures referencing the old grammar-rule-name strings (e.g. `T("generic_params", ...)`) and old attribute names (`.generic_params`), since Step 1 didn't touch tests yet.

Apply the same three-way sed to the test files:

```bash
sed -i \
  -e 's/\bGenericParam\b/ComptimeParam/g' \
  -e 's/\bgeneric_params\b/comptime_params/g' \
  -e 's/\bgeneric_param\b/comptime_param/g' \
  -e 's/\bgeneric_args\b/comptime_args/g' \
  tests/test_parsing.py tests/test_ast.py
```

Run again; expected: PASS.

- [ ] **Step 3: Rename in `typs.py`**

Edit `src/leech/typs.py`:
- Rename `typ_params_from_ast` (function name and its `generic_params` parameter, now `comptime_params: Sequence[ast.ComptimeParam]`) to `comptime_params_from_ast`; update its call sites in the same file.
- Rename `check_typ_arg_bounds` to `check_comptime_arg_bounds`.
- In `resolve_bound`, rename the local `typ_args` to `bound_comptime_args` and its uses of `bound.generic_args` to `bound.comptime_args`; rename its call to `check_typ_arg_bounds` accordingly.
- In `StructTypTemplate`, rename the `typ_params` cached property to `comptime_params` (still calling `comptime_params_from_ast`).
- In `_basic_typ_from_ast`, rename `typ_ast.generic_args` to `typ_ast.comptime_args` and `item.typ_params` to `item.comptime_params`.

```bash
cd src/leech
sed -i \
  -e 's/\btyp_params_from_ast\b/comptime_params_from_ast/g' \
  -e 's/\bcheck_typ_arg_bounds\b/check_comptime_arg_bounds/g' \
  -e 's/\btyp_args\b/bound_comptime_args/g;s/\bgeneric_args\b/comptime_args/g;s/\bgeneric_params\b/comptime_params/g' \
  typs.py
cd ../..
```

The `typ_args` -> `bound_comptime_args` substitution above is too broad (it will also hit `StructTypTemplate.instantiate`'s `typ_args` parameter and `StructTyp.typ_args`, which the table separately renames to plain `comptime_args`, not `bound_comptime_args`). After running the sed, manually review the diff and fix every `bound_comptime_args` that isn't inside `resolve_bound` back to `comptime_args` (this affects `StructTypTemplate.instantiate`, `StructTypTemplate._validation_instance`, `StructTypTemplate.module_instance`, and `StructTyp`'s `typ_args` field, `__init__`, `qualified_name`, `substitute_typ_params`, `infer_typ_args`, and `name`).

- [ ] **Step 4: Rename in `ir_module.py`, `ir_traits.py`, `ir_builder.py`, `ir_builtins.py`, `typcheck.py`**

```bash
cd src/leech
for f in ir_module.py ir_traits.py; do
  sed -i \
    -e 's/\btyp_params\b/comptime_params/g' \
    -e 's/\bgeneric_params\b/comptime_params/g' \
    -e 's/\bgeneric_args\b/comptime_args/g' \
    -e 's/\bimpl_typ_params\b/impl_comptime_params/g' \
    -e 's/\bcheck_typ_params_constrained\b/check_comptime_params_constrained/g' \
    "$f"
done
sed -i \
  -e 's/\btyp_params\b/comptime_params/g' \
  -e 's/\btyp_args\b/comptime_args/g' \
  -e 's/\b_typ_arg_mapping\b/_comptime_arg_mapping/g' \
  -e 's/\btyp_arg_mapping\b/comptime_arg_mapping/g' \
  -e 's/\bconcrete_typ_args\b/concrete_comptime_args/g' \
  ir_builder.py
sed -i \
  -e 's/\btyp_params\b/comptime_params/g' \
  -e 's/\btyp_args\b/comptime_args/g' \
  -e 's/\bfn_typ_for_typ_args\b/fn_typ_for_comptime_args/g' \
  ir_builtins.py
sed -i \
  -e 's/\b_resolve_explicit_typ_args\b/_resolve_explicit_comptime_args/g' \
  -e 's/\b_infer_typ_args\b/_infer_comptime_args/g' \
  -e 's/\b_typ_params_of\b/_comptime_params_of/g' \
  -e 's/\btyp_params\b/comptime_params/g' \
  -e 's/\btyp_args\b/comptime_args/g' \
  -e 's/\bgeneric_args\b/comptime_args/g' \
  typcheck.py
cd ../..
```

Then in `ir_module.py`, manually rename `IntrinsicFnSymbol`'s `fn_typ_for_typ_args` (already caught by the shared `typ_params`/`typ_args` sed above only for its *parameter* names, not the method name itself - rename the method name to `fn_typ_for_comptime_args` by hand at its declaration and any override) - actually this method is declared in `ir_module.py` (`IntrinsicFnSymbol.fn_typ_for_typ_args`, abstract) and implemented in `ir_builtins.py`; make sure both got the rename (the `ir_builtins.py` sed above covers the implementations; run `grep -rn fn_typ_for_typ_args src/leech/` and fix any remaining occurrence by hand, including the abstract declaration in `ir_module.py`).

Manually restore `IntrinsicFnSymbol._typ_params` and `IntrinsicFnSymbol._typ_param_names` in `ir_module.py` back to their original names if the blanket sed touched them (it will, since they match `\btyp_params\b`-adjacent patterns only partially - check with `grep -n '_typ_param' src/leech/ir_module.py` and revert any renamed occurrence of these two specific private members), adding the explanatory comment from the table above.

- [ ] **Step 5: Rename the five error classes**

In `src/leech/errors.py`, rename the five classes per the table (class name only; keep their docstrings' wording, updating "type argument(s)"/"type parameter" to "comptime argument(s)"/"comptime parameter" only where it reads naturally - e.g. `MissingComptimeArgsError`'s docstring becomes "Raised when a generic item is used as a value without required comptime arguments." and its message string `f'Generic item "{item_name}" used without required type arguments'` becomes `f'Generic item "{item_name}" used without required arguments'`, dropping "type" since the argument may now be a value too). Apply the same message-wording fix to `CannotInferComptimeArgError`, `WrongNumberOfComptimeArgsError`, `TypArgsOnNonGenericItemError` -> `ComptimeArgsOnNonGenericItemError`, and `UnconstrainedImplComptimeParamError`'s messages (replace "type argument"/"type parameter" with "argument"/"parameter" in each f-string).

```bash
sed -i \
  -e 's/\bMissingTypArgsError\b/MissingComptimeArgsError/g' \
  -e 's/\bCannotInferTypArgError\b/CannotInferComptimeArgError/g' \
  -e 's/\bWrongNumberOfTypArgsError\b/WrongNumberOfComptimeArgsError/g' \
  -e 's/\bTypArgsOnNonGenericItemError\b/ComptimeArgsOnNonGenericItemError/g' \
  -e 's/\bUnconstrainedImplTypParamError\b/UnconstrainedImplComptimeParamError/g' \
  src/leech/errors.py
```

Then hand-edit the five message strings as described above (`grep -n 'type argument\|type parameter' src/leech/errors.py` to find them).

- [ ] **Step 6: Rename in every test file, then run the full suite**

```bash
for f in tests/test_builtins.py tests/test_enums.py tests/test_errors.py \
         tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py \
         tests/test_traits.py tests/test_typs.py; do
  sed -i \
    -e 's/\bGenericParam\b/ComptimeParam/g' \
    -e 's/\bgeneric_params\b/comptime_params/g' \
    -e 's/\bgeneric_args\b/comptime_args/g' \
    -e 's/\btyp_params\b/comptime_params/g' \
    -e 's/\bMissingTypArgsError\b/MissingComptimeArgsError/g' \
    -e 's/\bCannotInferTypArgError\b/CannotInferComptimeArgError/g' \
    -e 's/\bWrongNumberOfTypArgsError\b/WrongNumberOfComptimeArgsError/g' \
    -e 's/\bTypArgsOnNonGenericItemError\b/ComptimeArgsOnNonGenericItemError/g' \
    -e 's/\bUnconstrainedImplTypParamError\b/UnconstrainedImplComptimeParamError/g' \
    "$f"
done
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Expected: all four commands pass with zero failures/errors. Fix any remaining reference by hand (search with `grep -rn 'generic_param\|generic_arg\|typ_params\|typ_args\|TypArgsError\|TypParamError' src/leech tests` and confirm every remaining hit is one of the deliberately-not-renamed names listed above).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Rename generic params/args to comptime params/args

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```

---

## Task 2: `ComptimeParamTyp`/`ValueParamTyp`/`ComptimeValueTyp` types

Splits `TypParamTyp` to inherit from a new shared `ComptimeParamTyp` base and adds two new leaf `Typ` classes, tested in isolation via direct construction (not yet reachable through parsing - that's Task 4).

**Files:**
- Modify: `src/leech/typs.py`
- Test: `tests/test_typs.py`

**Interfaces:**
- Consumes: `Typ` base class (`cache_key`, `get_or_create`), `ast.BasicTyp`, `ir_env.Env` (unchanged from Task 1).
- Produces: `typs.ComptimeParamTyp` (base, never instantiated directly), `typs.TypParamTyp(ComptimeParamTyp)` (unchanged behavior), `typs.ValueParamTyp(ComptimeParamTyp)` with `value_typ: Typ`, `typs.ComptimeValueTyp(Typ)` with `value_typ: Typ` and `value: int | bool`, both interned via `Typ.get_or_create`. `ArrayTyp.of_length(element_typ: Typ, length: int) -> ArrayTyp` convenience constructor for a concrete literal length (used pervasively in Task 5).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_typs.py`:

```python
def test_comptime_value_typ_interns_equal_values():
    a = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    b = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    assert a is b


def test_comptime_value_typ_distinguishes_by_typ_and_value():
    four_usize = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    five_usize = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 5)
    four_u32 = typs.ComptimeValueTyp.get_or_create(typs.U32, 4)
    assert four_usize is not five_usize
    assert four_usize is not four_u32


def test_comptime_value_typ_bool_and_int_never_alias():
    true_val = typs.ComptimeValueTyp.get_or_create(typs.BOOL, True)
    one_val = typs.ComptimeValueTyp.get_or_create(typs.U32, 1)
    assert true_val is not one_val


def test_comptime_value_typ_name_is_lowercase():
    assert typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4).name == "4"
    assert typs.ComptimeValueTyp.get_or_create(typs.BOOL, True).name == "true"
    assert typs.ComptimeValueTyp.get_or_create(typs.BOOL, False).name == "false"


def test_comptime_value_typ_is_concrete_and_self_substitutes():
    v = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    assert v.is_concrete()
    assert v.substitute_typ_params({}) is v


def test_value_param_typ_interns_by_owner_and_index(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f() {}")
    (fn,) = mod.defns
    p0 = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)
    p0_again = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)
    p1 = typs.ValueParamTyp.get_or_create(fn, 1, "M", typs.BOOL)
    assert p0 is p0_again
    assert p0 is not p1
    assert p0.name == "N"
    assert p0.value_typ is typs.USIZE
    assert not p0.is_concrete()


def test_typ_param_typ_and_value_param_typ_never_alias(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f() {}")
    (fn,) = mod.defns
    type_param = typs.TypParamTyp.get_or_create(fn, 0, "T")
    value_param = typs.ValueParamTyp.get_or_create(fn, 0, "T", typs.USIZE)
    assert type_param is not value_param


def test_value_param_typ_substitutes_to_comptime_value_typ(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f() {}")
    (fn,) = mod.defns
    p = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)
    four = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    assert p.substitute_typ_params({p: four}) is four


def test_array_typ_of_length_matches_get_or_create_with_comptime_value():
    length = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 3)
    assert typs.ArrayTyp.of_length(typs.I32, 3) is typs.ArrayTyp.get_or_create(typs.I32, length)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_typs.py -k "comptime_value_typ or value_param_typ or array_typ_of_length" -v
```

Expected: FAIL with `AttributeError: module 'leech.typs' has no attribute 'ComptimeValueTyp'` (and similarly for `ValueParamTyp`, `of_length`).

- [ ] **Step 3: Implement the types**

In `src/leech/typs.py`, replace the existing `TypParamTyp` class (the one Task 1 left untouched) with:

```python
class ComptimeParamTyp(Typ):
    """Shared identity, caching, and substitution for a comptime parameter.

    Interned by its owner and position - a parameter is declared in exactly
    one place, so the first interning fixes it. Never instantiated
    directly; use :class:`TypParamTyp` or :class:`ValueParamTyp`.
    """

    _owner: Final[Hashable]
    _index: Final[int]
    _name: Final[str]

    def __init__(self, owner: Hashable, index: int, name: str) -> None:
        self._owner = owner
        self._index = index
        self._name = name

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key a comptime parameter by its declaring item and position.

        :pre: len(args) > 1 [assert_gt]
        """
        asserts.assert_gt(len(args), 1)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return self._name

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return mapping.get(self, self)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        bindings.setdefault(self, actual)

    @override
    def is_concrete(self) -> bool:
        return False


class TypParamTyp(ComptimeParamTyp):
    """An opaque generic type parameter interned by its owner and position.

    It has no LLVM representation and stores only its display name, trait
    bounds and the scope those bounds are written in.

    :param decl_env: The scope enclosing this parameter's declaration, in
        which its bounds' trait names are spelled. Not part of the cache
        key, which the owner alone determines - a parameter is declared
        in exactly one place, so the first interning fixes it.
    """

    bounds: Final[tuple[ast.BasicTyp, ...]]
    decl_env: Final[Optional[ir_env.Env]]

    def __init__(
        self,
        owner: Hashable,
        index: int,
        name: str,
        bounds: tuple[ast.BasicTyp, ...] = (),
        decl_env: Optional[ir_env.Env] = None,
    ) -> None:
        super().__init__(owner, index, name)
        self.bounds = bounds
        self.decl_env = decl_env

    def declares_bound(self, trait: ir_traits.Trait) -> bool:
        """Whether this parameter's own declared bounds include ``trait``.

        This is what stands in for an impl inside a generic definition:
        nothing is registered against a type parameter, so what its
        declaration assumes about it is all that can be known.

        :param trait: The trait to look for.
        :return: Whether some bound resolves to ``trait``.
        """
        return any(
            resolve_bound(bound, opt_util.opt_unwrap(self.decl_env))[0] is trait
            for bound in self.bounds
        )

    @property
    def is_declared_by_trait(self) -> bool:
        """Whether this parameter belongs to a trait declaration.

        Only trait-owned bounds can recursively validate other declaration
        bounds. Impl bounds may recur legally while selection descends through
        a smaller type, and impl-obligation recursion is guarded separately.
        Function, struct, and intrinsic bounds are one-shot entry points whose
        recursive paths must pass through a trait-owned bound to close a cycle.
        """
        return isinstance(self._owner, ast.TraitDefn)


class ValueParamTyp(ComptimeParamTyp):
    """An opaque comptime value parameter interned by its owner and position.

    It has no LLVM representation of its own; a concrete argument
    substitutes to a :class:`ComptimeValueTyp` of the same ``value_typ``.

    :param value_typ: This parameter's declared type - an :class:`IntTyp`
        or :data:`BOOL`.
    """

    value_typ: Final[Typ]

    def __init__(self, owner: Hashable, index: int, name: str, value_typ: Typ) -> None:
        super().__init__(owner, index, name)
        self.value_typ = value_typ


class ComptimeValueTyp(Typ):
    """A concrete compile-time value used as a comptime argument.

    The type-level counterpart to :class:`~leech.ir_values.ComptimeValue`:
    a ``Typ`` node whose entire content is one concrete compile-time value,
    interned the same way every other ``Typ`` is - equal values are
    therefore always the same instance.

    :param value_typ: This value's type - an :class:`IntTyp` or :data:`BOOL`.

    :pre: (value_typ is BOOL) == isinstance(value, bool) [assert]
    """

    value_typ: Final[Typ]
    value: Final[int | bool]

    def __init__(self, value_typ: Typ, value: int | bool) -> None:
        assert (value_typ is BOOL) == isinstance(value, bool), (
            f"{value!r} does not match declared type {value_typ.name}"
        )
        self.value_typ = value_typ
        self.value = value

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key a comptime value by its type and its own value.

        :pre: len(args) == 2 [assert_eq]
        """
        asserts.assert_eq(len(args), 2)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return str(self.value).lower()

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return self

    @override
    def is_concrete(self) -> bool:
        return True
```

Note `BOOL` is defined later in the file (near the other builtin singletons) but is only referenced inside `__init__`'s body, evaluated at call time after the whole module has loaded - no forward-reference problem.

Then, in `ArrayTyp`, add (near its `get_or_create` usages, e.g. right after the class's `__init__`):

```python
    @staticmethod
    def of_length(element_typ: Typ, length: int) -> ArrayTyp:
        """Return the cached instance for a concrete literal ``length``."""
        return ArrayTyp.get_or_create(element_typ, ComptimeValueTyp.get_or_create(USIZE, length))
```

(`ArrayTyp.length` is still plain `int` at this point in the plan - Task 5 changes it to `Typ`. `of_length` is added now so its own unit test above passes, and so Task 5 has fewer call sites to touch by hand.) For this task only, temporarily make `of_length` return via `ArrayTyp.get_or_create(element_typ, length)` (passing the bare `int`, matching today's `ArrayTyp.__init__(self, element_typ: Typ, length: int)`) - the `ComptimeValueTyp`-taking version above is what Task 5 finishes wiring once `ArrayTyp.length` itself becomes `Typ`-shaped. Use this interim body for now:

```python
    @staticmethod
    def of_length(element_typ: Typ, length: int) -> ArrayTyp:
        """Return the cached instance for a concrete literal ``length``."""
        return ArrayTyp.get_or_create(element_typ, length)
```

Update `test_array_typ_of_length_matches_get_or_create_with_comptime_value` from Step 1 to match this interim shape instead:

```python
def test_array_typ_of_length_matches_get_or_create():
    assert typs.ArrayTyp.of_length(typs.I32, 3) is typs.ArrayTyp.get_or_create(typs.I32, 3)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_typs.py -v
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run basedpyright
```

Expected: all PASS (the pre-existing `TypParamTyp`-based tests in `test_typs.py` must still pass unchanged, since `TypParamTyp`'s own behavior didn't change - only its base class).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add ComptimeParamTyp, ValueParamTyp, and ComptimeValueTyp

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```

---

## Task 3: Grammar and AST for value arguments and symbolic array length

Adds `int_lit`/`bool_lit` as `comptime_arg` alternatives and a bare identifier as an `array_length` alternative. Parser-level only - `typcheck`/`typs` don't yet resolve the new forms (that's Task 4/5); this task's tests check parsing and AST shape only.

**Files:**
- Modify: `src/leech/leech.lark`
- Modify: `src/leech/ast.py`
- Test: `tests/test_parsing.py`, `tests/test_ast.py`

**Interfaces:**
- Consumes: `ast.IntLit`, `ast.BoolLit`, `ast.Typ`, `ast.Path` (all pre-existing).
- Produces: `ast.ComptimeArg` (`type ComptimeArg = Typ | IntLit | BoolLit`), `ast.Typ.comptime_arg_from_tree(file, tree) -> ComptimeArg` (dispatch used by `BasicTyp.comptime_args`/`VarExpr.comptime_args`), `ast.ArrayLength` reshaped to `value: Optional[int]` / `path: Optional[Path]`.

- [ ] **Step 1: Write the failing parser tests**

Add to `tests/test_parsing.py` (near the existing `comptime_args`/array tests found via `grep -n comptime_args tests/test_parsing.py`, matching that file's existing `T(...)`/`Tok(...)` tree-matching style):

```python
def test_comptime_arg_accepts_int_lit():
    tree = parse.build_parser("typ").parse("Buf[i32, 4]")
    assert tree == T("basic_typ").cs(
        T("path", "ident", Tok("Buf")),
        T("comptime_args").cs(
            T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
            T("int_lit", Tok("4")),
        ),
    )


def test_comptime_arg_accepts_bool_lit():
    tree = parse.build_parser("typ").parse("Flag[true]")
    assert tree == T("basic_typ").cs(
        T("path", "ident", Tok("Flag")),
        T("comptime_args").cs(T("bool_lit", Tok("true"))),
    )


def test_comptime_arg_accepts_bare_identifier():
    tree = parse.build_parser("typ").parse("Buf[i32, N]")
    assert tree == T("basic_typ").cs(
        T("path", "ident", Tok("Buf")),
        T("comptime_args").cs(
            T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
            T("basic_typ").cs(T("path", "ident", Tok("N")), None),
        ),
    )


def test_array_length_accepts_identifier():
    tree = parse.build_parser("typ").parse("[i32; N]")
    assert tree == T("array_typ").cs(
        T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
        T("array_length", T("path", "ident", Tok("N"))),
    )


def test_array_length_still_accepts_literal():
    tree = parse.build_parser("typ").parse("[i32; 4]")
    assert tree == T("array_typ").cs(
        T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
        T("array_length", Tok("4")),
    )
```

(Check the exact `T`/`Tok`/`.cs` helper signatures already used in `tests/test_parsing.py` near line 1176-1196 before finalizing these - match that file's established style exactly, since it may wrap tokens or trees slightly differently than shown here.)

Add to `tests/test_ast.py`:

```python
def test_basic_typ_comptime_args_accepts_int_lit(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f(x: Buf[i32, 4]) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.param_list.params
    assert isinstance(param.typ, ast.BasicTyp)
    _, int_arg = param.typ.comptime_args
    assert isinstance(int_arg, ast.IntLit)
    assert int_arg.value == 4


def test_array_length_path_is_populated_for_identifier(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[N](x: [i32; N]) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.param_list.params
    assert isinstance(param.typ, ast.ArrayTyp)
    assert param.typ.length.value is None
    assert param.typ.length.path is not None
    assert param.typ.length.path.str() == "N"


def test_array_length_value_is_populated_for_literal(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f(x: [i32; 4]) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.param_list.params
    assert isinstance(param.typ, ast.ArrayTyp)
    assert param.typ.length.value == 4
    assert param.typ.length.path is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_parsing.py tests/test_ast.py -k "comptime_arg or array_length" -v
```

Expected: FAIL - `lark.exceptions.UnexpectedToken` for the int/bool-literal and bare-identifier-array-length cases (grammar doesn't accept them yet), and `AttributeError`/`AssertionError` for the AST-shape ones.

- [ ] **Step 3: Update the grammar**

In `src/leech/leech.lark`, change:

```text
comptime_args: "[" _comptime_arg_list "]"
_comptime_arg_list: typ ("," typ)* ","?
```

to:

```text
comptime_args: "[" _comptime_arg_list "]"
_comptime_arg_list: comptime_arg ("," comptime_arg)* ","?
comptime_arg: typ
  | int_lit
  | bool_lit
```

and change:

```text
array_length: NONNEG_INT
```

to:

```text
array_length: NONNEG_INT | path
```

- [ ] **Step 4: Update `ast.py`**

Add, right after the `Typ` class's `from_tree` (so `IntLit`/`BoolLit`, defined earlier in the file, and `Typ`, just above, are all in scope):

```python
type ComptimeArg = Typ | IntLit | BoolLit
"""A parsed comptime argument: a type, or a literal value."""


def _comptime_arg_from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> ComptimeArg:
    """Build a comptime argument - a type or a value literal - from its parse tree."""
    if tree.data == "int_lit":
        return IntLit(file, tree)
    if tree.data == "bool_lit":
        return BoolLit(file, tree)
    return Typ.from_tree(file, tree)
```

In `BasicTyp.__init__`, change:

```python
    generic_args: Final[tuple[Typ, ...]]
```

(now `comptime_args` per Task 1) to:

```python
    comptime_args: Final[tuple[ComptimeArg, ...]]
```

and its construction:

```python
        if comptime_args is not None:
            self.comptime_args = tuple(
                _comptime_arg_from_tree(file, _as_tree(c)) for c in _as_tree(comptime_args).children
            )
```

(replacing the `Typ.from_tree(file, _as_tree(c))` call with `_comptime_arg_from_tree(file, _as_tree(c))`). Apply the identical change to `VarExpr`'s `comptime_args` field and construction.

Replace the `ArrayLength` class body with:

```python
class ArrayLength(Ast):
    """An array type's length: a literal, e.g. the ``4`` in ``[i32; 4]``,
    or a reference to an in-scope value parameter, e.g. the ``N`` in
    ``[i32; N]``.
    """

    value: Final[Optional[int]]
    path: Final[Optional[Path]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "array_length")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        if isinstance(child, lark.Token):
            self.value = int(child)
            self.path = None
        else:
            self.value = None
            self.path = Path(file, _as_tree(child))

    @override
    def diag_str(self) -> str:
        if self.value is not None:
            return f'array length "{self.value}"'
        assert self.path is not None
        return f'array length "{self.path.str()}"'
```

(This drops `ArrayLength`'s previous `Expr` base in favor of `Ast` directly, since it was never reachable through `Expr.from_tree`'s dispatch table and isn't really an expression - and drops the unused `token` field. Confirm nothing outside `ast.py` reads `ArrayLength.token` before removing it: `grep -rn 'length\.token' src/leech tests`.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_parsing.py tests/test_ast.py -v
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run basedpyright
```

Expected: all PASS. (The full suite passing here confirms nothing outside `ast.py`/tests read `ArrayLength.token`, and that no other code path constructs a `comptime_arg` that isn't `typ`/`int_lit`/`bool_lit`-shaped yet, since no source in the existing suite uses the new syntax.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Parse int/bool literals as comptime args and identifiers as array lengths

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```

---

## Task 4: Value parameter declarations, arguments, and expression use

The full vertical slice: declare a value parameter, disambiguating it from a trait-bounded type parameter; resolve explicit value arguments; check kind/type mismatches; and use a value parameter as a runtime expression in a function or impl-method body. Does not yet cover `[T; N]` with `N` symbolic (Task 5) - this task's struct/array field types stay literal.

**Files:**
- Modify: `src/leech/errors.py`
- Modify: `src/leech/typs.py`
- Modify: `src/leech/resolve.py`
- Modify: `src/leech/typcheck.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Test: `tests/test_generic_fns.py`, `tests/test_impl.py`, `tests/test_errors.py`

**Interfaces:**
- Consumes: `typs.ComptimeParamTyp`/`TypParamTyp`/`ValueParamTyp`/`ComptimeValueTyp` (Task 2), `ast.ComptimeParam`/`ComptimeArg` (Task 1/3).
- Produces: `typs.comptime_params_from_ast` disambiguates value parameters; `typs.resolve_comptime_arg(param, arg_ast, e) -> Typ`; `typs.check_comptime_arg_bounds` branches on parameter kind; `resolve.VarTarget` includes `typs.ValueParamTyp`; a bare value-parameter name resolves and lowers as a runtime expression.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_generic_fns.py`:

```python
def test_value_param_declaration_typechecks(tmp_path):
    src = """
    fn f[N: usize]() usize { return N; }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_with_explicit_value_arg(tmp_path):
    src = """
    fn f[N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_calling_generic_fn_with_explicit_bool_arg(tmp_path):
    src = """
    fn f[B: bool]() bool { return B; }
    pub fn main() i32 {
        if (f[true]()) { return 0; };
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_value_and_typ_params_coexist(tmp_path):
    src = """
    fn f[T, N: i32](x: T) i32 { return N; }
    pub fn main() i32 {
        return f[i32, 3](9) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_equal_value_args_share_one_instance(tmp_path):
    src = """
    fn f[N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - f[4]();
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert ir_text.count('define linkonce_odr i32 @"main::f[4]"') == 1


def test_distinct_value_args_get_distinct_instances(tmp_path):
    src = """
    fn f[N: i32]() i32 { return N; }
    pub fn main() i32 {
        return f[4]() - f[5]() + 1;
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert 'define linkonce_odr i32 @"main::f[4]"' in ir_text
    assert 'define linkonce_odr i32 @"main::f[5]"' in ir_text


def test_value_param_with_unsupported_typ_is_rejected(tmp_path):
    src = """
    struct Foo {}
    fn f[N: Foo]() {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.UnsupportedValueParamTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Foo"' in str(exc_info.value)


def test_typ_arg_given_for_value_param_is_rejected(tmp_path):
    src = """
    fn f[N: usize]() usize { return N; }
    pub fn main() i32 {
        let _ = f[i32]();
        return 0;
    }
    """
    with pytest.raises(errors.WrongKindOfComptimeArgError):
        util.compile_str(tmp_path, src)


def test_value_arg_given_for_typ_param_is_rejected(tmp_path):
    src = """
    fn f[T](x: T) T { return x; }
    pub fn main() i32 {
        let _ = f[4](9);
        return 0;
    }
    """
    with pytest.raises(errors.WrongKindOfComptimeArgError):
        util.compile_str(tmp_path, src)


def test_wrong_typ_value_arg_is_rejected(tmp_path):
    src = """
    fn f[N: usize]() usize { return N; }
    pub fn main() i32 {
        let _ = f[true]();
        return 0;
    }
    """
    with pytest.raises(errors.WrongComptimeValueTypError):
        util.compile_str(tmp_path, src)
```

Add to `tests/test_impl.py` (matching that file's existing `impl[T] Box[T] { ... }` style):

```python
def test_impl_value_param_used_in_method_body(tmp_path):
    src = """
    struct Box[N: i32] {}
    impl[N: i32] Box[N] {
        fn get(*self) i32 { return N; }
    }
    pub fn main() i32 {
        let b = Box[4] {};
        return b.get() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_generic_fns.py tests/test_impl.py -k "value" -v
```

Expected: FAIL - a value-parameter declaration like `N: usize` today resolves through `resolve_bound`, hits `BoundNotATraitError` (since `usize` isn't a trait), or an unhandled-AST-node `AssertionError` inside `typs.resolve_comptime_arg` calls that don't exist yet.

- [ ] **Step 3: Add the two new errors**

In `src/leech/errors.py`, add (near the other `Comptime*Args*`/`*Param*` errors):

```python
class UnsupportedValueParamTypError(UserError):
    """Raised when a value parameter's declared type isn't one this
    compiler can represent as a comptime value."""

    def __init__(self, typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"{typ_name}" cannot be used as a value parameter\'s type',
            span,
        )


class WrongKindOfComptimeArgError(UserError):
    """Raised when a type argument is given for a value parameter, or a
    value argument is given for a type parameter."""

    def __init__(self, param_name: str, arg_desc: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'{arg_desc} does not match the kind of parameter "{param_name}" expects',
            span,
        )


class WrongComptimeValueTypError(UserError):
    """Raised when a value argument's type doesn't match its parameter's
    declared type."""

    def __init__(self, arg_name: str, expected_typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Value "{arg_name}" does not have the expected type "{expected_typ_name}"',
            span,
        )
```

- [ ] **Step 4: Disambiguate value parameters in `comptime_params_from_ast`**

In `src/leech/typs.py`, replace `comptime_params_from_ast` with:

```python
def comptime_params_from_ast(
    owner: Hashable, comptime_params: Sequence[ast.ComptimeParam], e: ir_env.Env
) -> tuple[ComptimeParamTyp, ...]:
    """Intern parsed comptime parameters under ``owner``, preserving declaration order.

    A single-entry bound list is resolved against ``e``: a name that
    denotes a ``Trait`` declares a type parameter with that bound (as a
    multi-entry bound list always does); a name that denotes an
    :class:`IntTyp` or :data:`BOOL` declares a value parameter of that
    type instead.

    :param e: The scope enclosing the declaration, in which parameters'
        bounds are resolved. See :attr:`TypParamTyp.decl_env`.
    :raises ReservedNameError: If a parameter takes a reserved name.
    :raises UnsupportedValueParamTypError: If a single bound names a type
        that can't be a value parameter's type.
    :raises BoundNotATraitError: If a single bound names neither a trait
        nor a usable value type.
    """
    # Local to avoid the ir_traits import cycle.
    from leech import ir_traits  # noqa: PLC0415

    for param_ast in comptime_params:
        if reserved.is_reserved(param_ast.ident.name):
            raise errors.ReservedNameError(param_ast.ident.name, param_ast.ident.span)

    result: list[ComptimeParamTyp] = []
    for index, param_ast in enumerate(comptime_params):
        value_typ: Optional[Typ] = None
        if len(param_ast.bounds) == 1:
            (bound,) = param_ast.bounds
            item = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
            if isinstance(item, (IntTyp, BoolTyp)):
                value_typ = item
            elif isinstance(item, Typ) and not isinstance(item, ir_traits.Trait):
                raise errors.UnsupportedValueParamTypError(item.name, bound.span)
            elif not isinstance(item, ir_traits.Trait):
                raise errors.BoundNotATraitError(bound.path.str(), bound.path.span)
        if value_typ is not None:
            result.append(
                ValueParamTyp.get_or_create(owner, index, param_ast.ident.name, value_typ)
            )
        else:
            result.append(
                TypParamTyp.get_or_create(
                    owner, index, param_ast.ident.name, param_ast.bounds, e
                )
            )
    return tuple(result)
```

(`ir_traits.Trait` is never an instance of `Typ`, so the `isinstance(item, Typ) and not isinstance(item, ir_traits.Trait)` branch is really just "is a `Typ` that isn't `IntTyp`/`BoolTyp`" - written this way to read clearly next to the `Trait` branch below it.)

- [ ] **Step 5: Add `resolve_comptime_arg` and rewire bound/argument checking**

In `src/leech/typs.py`, add (near `check_comptime_arg_bounds`):

```python
def resolve_comptime_arg(param: ComptimeParamTyp, arg_ast: ast.ComptimeArg, e: ir_env.Env) -> Typ:
    """Resolve one parsed comptime argument against its declared parameter.

    An unsuffixed integer literal takes ``param``'s declared value type
    when ``param`` is a :class:`ValueParamTyp`; an explicitly suffixed one
    resolves to its own suffix, exactly like an ordinary integer literal
    elsewhere.

    :raises WrongKindOfComptimeArgError: If a bare integer literal is
        given for a type parameter.
    """
    if isinstance(arg_ast, ast.IntLit):
        if arg_ast.explicit_width is not None:
            assert arg_ast.explicit_signage is not None
            lit_typ: Typ = IntTyp.get_or_create(arg_ast.explicit_width, arg_ast.explicit_signage)
        elif isinstance(param, ValueParamTyp):
            lit_typ = param.value_typ
        else:
            raise errors.WrongKindOfComptimeArgError(param.name, arg_ast.diag_str(), arg_ast.span)
        return ComptimeValueTyp.get_or_create(lit_typ, arg_ast.value)
    if isinstance(arg_ast, ast.BoolLit):
        return ComptimeValueTyp.get_or_create(BOOL, arg_ast.value)
    return Typ.from_ast(arg_ast, e)
```

Replace `check_comptime_arg_bounds` with:

```python
def check_comptime_arg_bounds(
    comptime_params: Sequence[ComptimeParamTyp],
    comptime_args: tuple[Typ, ...],
    e: ir_env.Env,
    span: Optional[src.SrcSpan],
) -> None:
    """Raise if a comptime argument violates its corresponding parameter's bounds.

    :raises WrongKindOfComptimeArgError: If a type argument was given for a
        value parameter, or vice versa.
    :raises WrongComptimeValueTypError: If a value argument's type doesn't
        match its parameter's declared type.
    :raises UnsatisfiedBoundError: If a type argument doesn't satisfy a
        trait bound.
    """
    typ_params: dict[TypParamTyp, Typ] = {}
    for param, arg in zip(comptime_params, comptime_args, strict=True):
        arg_value_typ = (
            arg.value_typ if isinstance(arg, (ValueParamTyp, ComptimeValueTyp)) else None
        )
        if isinstance(param, ValueParamTyp):
            if arg_value_typ is None:
                raise errors.WrongKindOfComptimeArgError(param.name, arg.name, span)
            if arg_value_typ is not param.value_typ:
                raise errors.WrongComptimeValueTypError(arg.name, param.value_typ.name, span)
        else:
            if arg_value_typ is not None:
                raise errors.WrongKindOfComptimeArgError(param.name, arg.name, span)
            typ_params[asserts.checked_cast(param, TypParamTyp)] = arg

    violated = unsatisfied_bound(typ_params, e)
    if violated is not None:
        typ_param, typ_arg, trait = violated
        raise errors.UnsatisfiedBoundError(typ_arg.name, trait.name, typ_param.name, span)
```

Update `resolve_bound` to resolve its own `bound_comptime_args` through `resolve_comptime_arg` instead of `Typ.from_ast` directly:

```python
    bound_comptime_args = tuple(
        resolve_comptime_arg(param, arg_ast, e)
        for param, arg_ast in zip(trait.comptime_params, bound.comptime_args, strict=True)
    )
    check_comptime_arg_bounds(trait.comptime_params, bound_comptime_args, e, bound.span)
    return trait, bound_comptime_args
```

Update `_basic_typ_from_ast` similarly:

```python
    comptime_args = tuple(
        resolve_comptime_arg(param, arg_ast, e)
        for param, arg_ast in zip(item.comptime_params, typ_ast.comptime_args, strict=True)
    )
    check_comptime_arg_bounds(item.comptime_params, comptime_args, e, typ_ast.span)
    return item.instantiate(comptime_args)
```

- [ ] **Step 6: Rewire `typcheck.py`'s explicit-argument resolution**

In `src/leech/typcheck.py`, update `_resolve_explicit_comptime_args`'s signature and body to use `typs.resolve_comptime_arg`:

```python
    def _resolve_explicit_comptime_args(
        self,
        fn: ir_module.FnSymbol,
        comptime_params: Sequence[typs.ComptimeParamTyp],
        comptime_args: Sequence[ast.ComptimeArg],
        span: Optional[src.SrcSpan],
        e: ir_env.Env,
    ) -> dict[typs.ComptimeParamTyp, typs.Typ]:
        """Resolve explicit comptime arguments against parameters positionally."""
        if len(comptime_args) != len(comptime_params):
            raise errors.WrongNumberOfComptimeArgsError(
                fn.name, len(comptime_args), len(comptime_params), span
            )
        return {
            param: typs.resolve_comptime_arg(param, arg_ast, e)
            for param, arg_ast in zip(comptime_params, comptime_args, strict=True)
        }
```

Widen three more signatures the same way, since a `mapping`/`typ_params`-shaped value can now hold a `ValueParamTyp`, not only a `TypParamTyp`:
- `_comptime_params_of`'s return type: `list[typs.TypParamTyp]` -> `list[typs.ComptimeParamTyp]`.
- `_infer_comptime_args`'s return type: `dict[typs.TypParamTyp, typs.Typ]` -> `dict[typs.ComptimeParamTyp, typs.Typ]`.
- `_generic_var_ref_typ`'s `mapping` (built from `_resolve_explicit_comptime_args`, whose own return type Step 6 above already widened) - no signature to change there beyond what's already done, but confirm with `basedpyright` that its `typ_args = tuple(mapping[typ_param] for typ_param in typ_params)` line type-checks now that both sides are `ComptimeParamTyp`-keyed.

Check with `grep -n 'typ_param\|typ_arg' src/leech/typcheck.py` that no stray `TypParamTyp`-typed annotation remains in `_resolve_generic_call`, `_generic_var_ref_typ`, `_infer_comptime_args`, or `_comptime_params_of`.

- [ ] **Step 7: Bind value parameters into `VARS` too, and resolve/lower a bare reference**

In `src/leech/resolve.py`, add `typs` to the module-level import (`from leech import ast, ir_values, typs`) and widen `VarTarget`:

```python
type VarTarget = (
    LocalDecl
    | ir_module.ModVar
    | ir_module.FnSymbol
    | ir_traits.ImplFnSelection
    | ir_values.ComptimeEnum
    | typs.ValueParamTyp
)
```

In `src/leech/typcheck.py`'s `_check_var_expr`, add a branch right after the existing `ComptimeEnum` check:

```python
        if isinstance(var, typs.ValueParamTyp):
            # Not a place (same reasoning as the ComptimeEnum case above) -
            # a value parameter has no address to take.
            return var.value_typ
```

In `src/leech/ir_builder.py`'s `_build_var_expr`, add a branch right after the existing `ComptimeEnum` check:

```python
        if isinstance(target, typs.ValueParamTyp):
            concrete = asserts.checked_cast(
                target.substitute_typ_params(self._comptime_arg_mapping), typs.ComptimeValueTyp
            )
            value: ir_values.ComptimeValue = (
                ir_values.ComptimeBool(asserts.checked_cast(concrete.value, bool), var_ast)
                if concrete.value_typ is typs.BOOL
                else ir_values.ComptimeInt(
                    asserts.checked_cast(concrete.value_typ, typs.IntTyp),
                    asserts.checked_cast(concrete.value, int),
                    var_ast,
                )
            )
            return self._in_context(value, ctx)
```

- [ ] **Step 8: Bind value parameters into the declaration environment's `VARS` namespace**

In `src/leech/ir_module.py`, at the fn declaration site (`for comptime_param in typs.comptime_params_from_ast(fn_ast, fn_ast.comptime_params, self.env): self.env.add_container(...)`), add:

```python
        for comptime_param in typs.comptime_params_from_ast(fn_ast, fn_ast.comptime_params, self.env):
            self.env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, typs.ValueParamTyp):
                self.env.add_var(comptime_param.name, comptime_param)
```

and, at the impl declaration site (around `impl_comptime_params = typs.comptime_params_from_ast(...)`):

```python
        impl_comptime_params = typs.comptime_params_from_ast(
            impl_ast, impl_ast.comptime_params, self.env
        )
        for comptime_param in impl_comptime_params:
            impl_env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, typs.ValueParamTyp):
                impl_env.add_var(comptime_param.name, comptime_param)
```

In `src/leech/ir_traits.py`'s `Trait.__init__`:

```python
        for comptime_param in self.comptime_params:
            self._env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, typs.ValueParamTyp):
                self._env.add_var(comptime_param.name, comptime_param)
```

In `src/leech/typs.py`'s `StructTypTemplate.__init__` (the `param_env` loop):

```python
        for comptime_param in self.comptime_params:
            param_env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, ValueParamTyp):
                param_env.add_var(comptime_param.name, comptime_param)
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
uv run pytest tests/test_generic_fns.py tests/test_impl.py tests/test_errors.py -v
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run basedpyright
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Support value parameter declarations, arguments, and expression use

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```

---

## Task 5: Symbolic `ArrayTyp.length`

Changes `ArrayTyp.length` from `int` to `Typ`, so `[T; N]` can carry a value parameter that infers and substitutes exactly like `T` does, and updates every reader of `.length`.

**Files:**
- Modify: `src/leech/typs.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/ir_values.py`
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/typcheck.py`
- Test: `tests/test_generic_structs.py`, `tests/test_generic_fns.py`, `tests/test_arrays.py`, `tests/test_typs.py`

**Interfaces:**
- Consumes: `typs.ComptimeValueTyp`, `typs.ValueParamTyp` (Task 2/4), `ast.ArrayLength.value`/`.path` (Task 3).
- Produces: `typs.ArrayTyp.length: Typ`; `typs.ArrayTyp.of_length` now genuinely wraps `ComptimeValueTyp.get_or_create(USIZE, length)`; array-typed fields/parameters can reference a value parameter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_generic_structs.py`:

```python
def test_struct_value_param_used_in_array_field(tmp_path):
    src = """
    struct Buf[T, N: usize] {
        data: [T; N],
    }
    pub fn main() i32 {
        let buf = Buf[i32, 3] { data: [1, 2, 3] };
        return buf.data.[0] + buf.data.[1] + buf.data.[2] - 6;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_struct_value_param_mangled_name(tmp_path):
    src = """
    struct Buf[T, N: usize] { data: [T; N] }
    pub fn main() i32 {
        let buf = Buf[i32, 4] { data: [1, 2, 3, 4] };
        return buf.data.[0] - 1;
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert '%"main::Buf[i32, 4]"' in ir_text
```

Add to `tests/test_generic_fns.py`:

```python
def test_value_param_inferred_from_array_arg(tmp_path):
    src = """
    fn first[T, N: usize](x: [T; N]) T { return x.[0]; }
    pub fn main() i32 {
        return first([7, 8, 9]) - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_value_param_inference_distinguishes_array_lengths(tmp_path):
    # N must stay usize here (array lengths are always usize), so the
    # result is compared rather than turned into main's i32 return value -
    # there's no int-to-int cast operator in this language.
    src = """
    fn len_times_2[T, N: usize](x: [T; N]) usize { return N * 2; }
    pub fn main() i32 {
        if (len_times_2([1, 2, 3]) == 6) { return 0; };
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

Add to `tests/test_arrays.py`:

```python
def test_generic_fn_body_uses_array_typ_with_value_param(tmp_path):
    src = """
    fn sum3[N: usize](x: [i32; N]) i32 { return x.[0] + x.[1] + x.[2]; }
    pub fn main() i32 {
        return sum3[3]([4, 5, 6]) - 15;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

Add to `tests/test_typs.py`:

```python
def test_array_typ_length_substitutes_value_param(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[N: usize]() {}")
    (fn,) = mod.defns
    n = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)
    four = typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)
    symbolic = typs.ArrayTyp.get_or_create(typs.I32, n)

    assert not symbolic.is_concrete()
    assert symbolic.substitute_typ_params({n: four}) is typs.ArrayTyp.of_length(typs.I32, 4)


def test_array_typ_infers_symbolic_length_from_actual(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[N: usize]() {}")
    (fn,) = mod.defns
    n = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)
    declared = typs.ArrayTyp.get_or_create(typs.I32, n)
    actual = typs.ArrayTyp.of_length(typs.I32, 4)

    bindings: dict[typs.ComptimeParamTyp, typs.Typ] = {}
    declared.infer_typ_args(actual, bindings)
    assert bindings[n] is typs.ComptimeValueTyp.get_or_create(typs.USIZE, 4)


def test_typs_overlap_treats_array_length_as_unifiable(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[N: usize]() {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    n = typs.ValueParamTyp.get_or_create(fn, 0, "N", typs.USIZE)

    symbolic = typs.ArrayTyp.get_or_create(typs.I32, n)
    # The symbolic length binds to any concrete length, via the same
    # bind() path a ValueParamTyp element typ would take.
    _assert_typs_overlap_symmetric(symbolic, typs.ArrayTyp.of_length(typs.I32, 3), True)
    # Two concrete, unequal lengths correctly fail to unify.
    _assert_typs_overlap_symmetric(
        typs.ArrayTyp.of_length(typs.I32, 3), typs.ArrayTyp.of_length(typs.I32, 4), False
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_generic_structs.py tests/test_generic_fns.py tests/test_arrays.py tests/test_typs.py -k "value_param or array_typ" -v
```

Expected: FAIL - `[T; N]` with `N` a bare identifier currently hits `typs.py`'s `Typ.from_ast`'s `ArrayTyp` case, which reads `typ_ast.length.value` (now `None` for an identifier per Task 3), producing a `TypeError` from `ArrayTyp.get_or_create(elt, None)`'s implicit assumptions, or an assertion failure.

- [ ] **Step 3: Change `ArrayTyp.length` to `Typ`**

In `src/leech/typs.py`, replace the `ArrayTyp` class body's relevant members:

```python
class ArrayTyp(Typ):
    element_typ: Final[Typ]
    length: Final[Typ]

    def __init__(self, element_typ: Typ, length: Typ) -> None:
        self.element_typ = element_typ
        self.length = length

    @staticmethod
    def of_length(element_typ: Typ, length: int) -> ArrayTyp:
        """Return the cached instance for a concrete literal ``length``."""
        return ArrayTyp.get_or_create(element_typ, ComptimeValueTyp.get_or_create(USIZE, length))

    @property
    @override
    def name(self) -> str:
        return f"[{self.element_typ.name}; {self.length.name}]"

    @property
    @override
    def qualified_name(self) -> str:
        return f"[{self.element_typ.qualified_name}; {self.length.qualified_name}]"

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return ArrayTyp.get_or_create(
            self.element_typ.substitute_typ_params(mapping),
            self.length.substitute_typ_params(mapping),
        )

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if isinstance(actual, ArrayTyp):
            self.element_typ.infer_typ_args(actual.element_typ, bindings)
            self.length.infer_typ_args(actual.length, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.element_typ.is_concrete() and self.length.is_concrete()
```

(This removes the interim `of_length` body Task 2 left in place, replacing it with the intended one - the `ComptimeValueTyp`/`USIZE` names it needs are now both defined by Task 2, so the forward-reference note from Task 2 no longer applies.)

Add a helper next to `Typ.from_ast` (or as a private static method on `Typ`) and use it in the `ArrayTyp` case of `Typ.from_ast`:

```python
    @staticmethod
    def _array_length_from_ast(length_ast: ast.ArrayLength, e: ir_env.Env) -> Typ:
        """Resolve a parsed array length: a literal, or a value parameter reference."""
        if length_ast.value is not None:
            return ComptimeValueTyp.get_or_create(USIZE, length_ast.value)
        assert length_ast.path is not None
        item = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, length_ast.path)
        if isinstance(item, (ValueParamTyp, ComptimeValueTyp)) and item.value_typ is USIZE:
            return item
        raise errors.WrongComptimeValueTypError(
            item.name if isinstance(item, Typ) else length_ast.path.str(),
            USIZE.name,
            length_ast.path.span,
        )
```

and change the `Typ.from_ast` `ArrayTyp` case from:

```python
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
```

to:

```python
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e),
                    Typ._array_length_from_ast(typ_ast.length, e),
                )
```

`typs_overlap` (which `unify()` is nested inside) treats a bare `TypParamTyp` as a unification variable to `bind()` - that's how e.g. `Pair[T]` overlaps `Pair[i32]`. Once `ArrayTyp.length` can hold a `ValueParamTyp`, the same treatment must apply there too, or a symbolic length would silently fail to unify (falling through every `isinstance` branch to `return False`) instead of binding - breaking exactly the trait-obligation unification the spec promises for `[T; N]`. Widen every `TypParamTyp`-specific check in `typs_overlap` to `ComptimeParamTyp`, its true general case:

```python
def typs_overlap(a: Typ, b: Typ) -> bool:
    bindings: dict[ComptimeParamTyp, Typ] = {}

    def resolve(typ: Typ) -> Typ:
        while isinstance(typ, ComptimeParamTyp) and typ in bindings:
            typ = bindings[typ]
        return typ

    def occurs(typ_param: ComptimeParamTyp, typ: Typ) -> bool:
        return _contains_typ(typ, typ_param, resolve)

    def bind(typ_param: ComptimeParamTyp, typ: Typ) -> bool:
        typ = resolve(typ)
        if typ is typ_param:
            return True
        if occurs(typ_param, typ):
            return False
        bindings[typ_param] = typ
        return True

    def unify(left: Typ, right: Typ) -> bool:
        left = resolve(left)
        right = resolve(right)
        if left is right:
            return True
        if isinstance(left, ComptimeParamTyp):
            return bind(left, right)
        if isinstance(right, ComptimeParamTyp):
            return bind(right, left)
```

(Everything else in `typs_overlap`/`unify` - the `FnTyp`/`PtrTyp`/`StructTyp`/`EnumBackingTyp` branches - is unchanged; only the four `TypParamTyp` mentions shown above become `ComptimeParamTyp`.) Then change the `ArrayTyp` branch:

```python
        if isinstance(left, ArrayTyp) and isinstance(right, ArrayTyp):
            return left.length == right.length and unify(left.element_typ, right.element_typ)
```

to:

```python
        if isinstance(left, ArrayTyp) and isinstance(right, ArrayTyp):
            return unify(left.length, right.length) and unify(left.element_typ, right.element_typ)
```

Similarly widen `match_typ_args`'s signature, since `ArrayTyp.infer_typ_args` can now call `self.length.infer_typ_args(...)` where `self.length` is a `ValueParamTyp` (a `ComptimeParamTyp`, not necessarily a `TypParamTyp`), inserting a `ComptimeParamTyp` key into whatever `bindings` dict was passed - `match_typ_args`'s own `dict[TypParamTyp, Typ]` annotation is too narrow to accept that under `basedpyright`:

```python
def match_typ_args(declared: Typ, actual: Typ) -> Optional[dict[ComptimeParamTyp, Typ]]:
    """Match a possibly generic ``declared`` type against a specific ``actual`` one.

    The match succeeds when some substitution for ``declared``'s own type
    parameters turns it into exactly ``actual`` - so ``Pair[A, A]``
    matches ``Pair[i32, i32]`` but not ``Pair[i32, bool]``.

    :param declared: The type as written, possibly naming type parameters.
    :param actual: The type to match it against.
    :return: The substitution that makes them equal, or ``None`` if no
        substitution does. An empty mapping means they were already equal.
    """
    bindings: dict[ComptimeParamTyp, Typ] = {}
    declared.infer_typ_args(actual, bindings)
    return bindings if declared.substitute_typ_params(bindings) is actual else None
```

- [ ] **Step 4: Fix every other reader of `.length`**

In `src/leech/typcheck.py`'s `_check_array_expr`, change `typs.ArrayTyp.get_or_create(elt_typ, len(arr_expr.elements))` to `typs.ArrayTyp.of_length(elt_typ, len(arr_expr.elements))`.

In `src/leech/ir_builder.py`:
- Change `arr_typ = typs.ArrayTyp.get_or_create(elt_typ, len(elts))` to `arr_typ = typs.ArrayTyp.of_length(elt_typ, len(elts))`.
- Change `length = ir_values.ComptimeInt(typs.USIZE, array_typ.length, aa_expr)` to `length = ir_values.ComptimeInt(typs.USIZE, asserts.checked_cast(array_typ.length, typs.ComptimeValueTyp).value, aa_expr)`.

In `src/leech/ir_values.py`:
- Change `typs.ArrayTyp.get_or_create(typs.U8, len(self.value))` (in `ComptimeCStr.initializer_typ`) to `typs.ArrayTyp.of_length(typs.U8, len(self.value))`.
- Change `asserts.assert_eq(typ.length, len(elements))` (in `ComptimeArray.__init__`) to `asserts.assert_eq(asserts.checked_cast(typ.length, typs.ComptimeValueTyp).value, len(elements))`.

In `src/leech/codegen.py`, change `return ll.ArrayType(self._ll_mod_items.get(typ.element_typ), typ.length)` to `return ll.ArrayType(self._ll_mod_items.get(typ.element_typ), asserts.checked_cast(typ.length, typs.ComptimeValueTyp).value)`.

In `tests/test_typs.py`, replace every remaining `typs.ArrayTyp.get_or_create(<typ>, <int literal>)` call with `typs.ArrayTyp.of_length(<typ>, <int literal>)` (find them with `grep -n 'ArrayTyp.get_or_create' tests/test_typs.py` - as of Task 2 there are six such call sites left over from before this task).

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_generic_structs.py tests/test_generic_fns.py tests/test_arrays.py tests/test_typs.py -v
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run basedpyright
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Make ArrayTyp.length symbolic, so [T; N] can carry a value parameter

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```

---

## Task 6: Trait value parameters and acceptance verification

Confirms the acceptance criteria explicitly: a trait can declare a value parameter (incidental capability from sharing `comptime_params_from_ast`), and every criterion from the spec's Goal/Acceptance is covered by a passing test. Closes with the full verification suite.

**Files:**
- Test: `tests/test_traits.py`
- Test: `tests/test_generic_fns.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5. No production code changes expected in this task - if a test here fails for a reason other than "test not yet written," that's a defect introduced earlier that must be fixed here (see Step 3).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_traits.py` (matching that file's style, e.g. near `test_generic_trait_impl_method_calls_sibling`):

```python
def test_trait_declares_value_param(tmp_path):
    src = """
    trait Sized[N: i32] {
        fn size(*self) i32;
    }
    struct Buf[N: i32] {}
    impl[N: i32] Sized[N] for Buf[N] {
        fn size(*self) i32 { return N; }
    }
    pub fn main() i32 {
        let b = Buf[4] {};
        return b.size() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

Add to `tests/test_generic_fns.py`, matching the Goal section's own motivating example from the spec:

```python
def test_first_from_generic_struct_with_value_param(tmp_path):
    src = """
    struct Buf[T, N: usize] {
        data: [T; N],
    }

    fn first[T, N: usize](b: *Buf[T, N]) T {
        return b.*.data.[0];
    }

    pub fn main() i32 {
        let buf = Buf[i32, 4] { data: [11, 22, 33, 44] };
        return first(&buf) - 11;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
```

- [ ] **Step 2: Run tests to verify they fail (for the right reason)**

```bash
uv run pytest tests/test_traits.py::test_trait_declares_value_param tests/test_generic_fns.py::test_first_from_generic_struct_with_value_param -v
```

Expected: FAIL only because these two tests are new (`AssertionError`/output mismatch or simply not-yet-existing before this step is applied) - not because of an unhandled-AST-node crash or an unimplemented-feature `AssertionError`, since every capability these tests use (trait value params, struct+array value params, inference, mangled instance identity) was implemented in Tasks 1-5.

- [ ] **Step 3: Make them pass**

If either test fails for a *structural* reason (not just "doesn't exist yet"), that indicates a gap in Tasks 1-5's implementation, not new production code this task is scoped to add. Diagnose with `superpowers:systematic-debugging` and fix the root cause in the file it belongs to (`typs.py`, `typcheck.py`, `ir_builder.py`, `ir_traits.py`, or `ir_module.py`), then re-run. Do not special-case these two tests with new production code paths that duplicate what Task 4/5 already built.

```bash
uv run pytest tests/test_traits.py::test_trait_declares_value_param tests/test_generic_fns.py::test_first_from_generic_struct_with_value_param -v
```

Expected: PASS.

- [ ] **Step 4: Full acceptance sweep**

Confirm every spec acceptance point has a passing test already in the suite (no new code expected - this is a checklist, not an implementation step):

```bash
uv run pytest tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py tests/test_traits.py tests/test_arrays.py tests/test_typs.py tests/test_parsing.py tests/test_ast.py tests/test_errors.py -v
```

Verify by name that these are all present and passing: a function taking and using a value parameter (`test_value_param_declaration_typechecks`, `test_calling_generic_fn_with_explicit_value_arg`); a struct taking and using one (`test_struct_value_param_used_in_array_field`); equal values sharing one instance (`test_equal_value_args_share_one_instance`) and distinct values not (`test_distinct_value_args_get_distinct_instances`); values in mangled names (`test_struct_value_param_mangled_name`, and the `f[4]`/`f[5]` symbol names asserted above); inference (`test_value_param_inferred_from_array_arg`); trait/impl value parameters (`test_trait_declares_value_param`, `test_impl_value_param_used_in_method_body`); and the spec's own `Buf`/`first` example (`test_first_from_generic_struct_with_value_param`).

- [ ] **Step 5: Final verification**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run reuse lint
git diff --check
```

Expected: every command passes cleanly.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Add trait value parameter and acceptance-criteria tests for #39

For: #39: Non-type generic parameters" -m "$(cat <<'EOF'
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BT5ZiidLkMUuBCwAQ2sp3a
EOF
)"
```
