<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Total and Flat Function Instantiation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every source and built-in function declaration one flat `instantiate(args)` entry point and make every emitted function body belong to an `FnInstance`.

**Architecture:** An impl's type arguments precede a function's own arguments in one cache key. Module construction eagerly type-checks every declaration but monomorphization lowers only `main`, the public surface, and instances reached from those roots. Declarations retain enough symbol and access policy for instances to preserve every current LLVM name and linkage.

**Tech Stack:** Python 3.14, uv, pytest, Ruff, basedpyright, llvmlite.

**Spec:** `docs/superpowers/specs/2026-08-20-total-flat-fn-instantiation-design.md`

## Global Constraints

- Work on `main`, as explicitly requested by the user.
- Ask before every commit; leave implementation changes unstaged for review.
- Do not use comprehensions containing more than one `for`; write explicit nested loops.
- Preserve all accepted-language behavior except the separately committed rejection of unconstrained impl parameters.
- Preserve every existing emitted symbol spelling and linkage.
- Keep imports module-qualified and docstrings Sphinx-style according to `AGENTS.md`.
- Before completing the stage, run `uv run pytest -q`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run basedpyright`, and `uv run reuse --no-multiprocessing lint`.
- The pre-stage baseline is 1,134 passing tests.

---

### Task 1: Reject unconstrained impl type parameters

**Files:**
- Modify: `src/leech/errors.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/typs.py`
- Test: `tests/test_impl.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `errors.UnconstrainedImplTypParamError(name, span)`.
- Produces: `typs.contains_typ_param(typ, typ_param) -> bool` for structural occurrence in a type.
- Produces: `Impl.typ_params: tuple[typs.TypParamTyp, ...]` in declaration order.
- Changes: `Impl.__init__` receives the exact parameter tuple constructed in `_build_impl_defn`.

- [ ] **Step 1: Write the diagnostic and behavior tests**

Add an error-rendering test asserting that `U` is underlined and named for:

```leech
struct Box[T] { val: T }
impl[T, U] Box[T] {
    fn get(*self) T { return self.*.val; }
}
```

Add focused impl tests proving that a parameter nested in `Pair[Box[T], i32]` is constrained, while appearances only in a bound or method signature are not.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_impl.py -k unconstrained -v
uv run pytest tests/test_errors.py -k unconstrained -v
```

Expected: failure because the program currently compiles and the error class does not exist.

- [ ] **Step 3: Add structural occurrence and the diagnostic**

Implement `contains_typ_param` alongside the type structural helpers. It recursively checks function return/parameters, pointer pointees, array elements, struct arguments, and enum backing types; concrete leaf types return `False`.

Add `UnconstrainedImplTypParamError`, pointing at the generic parameter identifier and explaining that it is not constrained by the impl self type.

- [ ] **Step 4: Construct impl parameters once and validate them**

In `_build_impl_defn`, construct:

```python
impl_typ_params = typs.typ_params_from_ast(impl_ast, impl_ast.generic_params, self.env)
```

Bind those same objects into `impl_env`, pass them into `Impl`, and expose them as `Impl.typ_params`. After resolving `Impl.self_typ`, reject each parameter for which `contains_typ_param(impl.self_typ, typ_param)` is false. Do this before registering the impl.

- [ ] **Step 5: Run focused and full verification**

Run the two focused commands, then:

```bash
uv run pytest -q
uv run ruff check src/leech/errors.py src/leech/ir_module.py src/leech/ir_traits.py src/leech/typs.py tests/test_impl.py tests/test_errors.py
uv run basedpyright
git diff --check
```

Expected: at least 1,136 tests pass and all checkers are clean.

- [ ] **Step 6: Stop for review and request permission to commit**

Suggested commit subject: `Reject unconstrained impl type parameters`.

---

### Task 2: Introduce flat instantiation

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/typcheck.py`
- Modify: `tests/test_builtins.py`
- Modify: `tests/test_generic_fns.py`
- Modify: `tests/test_impl.py`
- Modify: `tests/test_traits.py`

**Interfaces:**
- Produces: `FnTemplate.instantiate(args: tuple[typs.Typ, ...]) -> FnInstance`.
- Produces: `FnInstance.args`, `impl_args`, and `fn_args` tuple properties.
- Deletes: `Fn.instance`, `Fn.impl_instance`, and `Fn.for_recv_typ`.
- Changes: source `Fn` and `GenericBuiltinFn` caches are keyed by the complete flat tuple.

- [ ] **Step 1: Update cache identity tests to the target interface**

Change source and built-in cache tests to call `instantiate`. Add a generic-impl test with two impl parameters and one function parameter at the IR level, asserting flat order and identity. Add an arity assertion test.

- [ ] **Step 2: Run the focused tests and verify the new API fails**

Run:

```bash
uv run pytest tests/test_builtins.py::test_size_of_builtin_instance_caches_by_typ_args tests/test_generic_fns.py::test_fn_instance_caches_by_typ_args tests/test_impl.py -k flat_instantiation -v
```

Expected: failure because `instantiate` and the flat argument properties do not exist.

- [ ] **Step 3: Replace the declaration protocol and caches**

Rename `FnTemplate.instance` to `instantiate`. Make `Fn._instance_cache` a `dict[tuple[typs.Typ, ...], FnInstance]`. Validate:

```python
impl_arity = len(self.impl.typ_params) if self.impl is not None else 0
fn_arity = len(self.typ_params)
assert len(args) == impl_arity + fn_arity, (
    f"{self.name}: expected {impl_arity} impl and {fn_arity} function type arguments; "
    f"got {len(args)} total"
)
```

Use the same `instantiate` name for `GenericBuiltinFn` without changing its existing flat cache behavior.

- [ ] **Step 4: Make `FnInstance` consume and preserve the flat split**

Replace `_self_typ` and `_typ_args` with the flat tuple plus an impl-prefix length. Build the substitution mapping directly by zipping `impl.typ_params + fn.typ_params` with the flat tuple. Derive the concrete receiver by substituting the impl mapping into `impl.self_typ`. Render the function name from `fn_args` only. Implement `substitute_typ_params` by substituting every flat argument and calling `self._fn.instantiate(...)`.

- [ ] **Step 5: Thread impl bindings out of registry selection**

Have applicable-impl lookup retain the `match_typ_args` bindings. Add an `Impl.instantiation_args(bindings)` helper that returns values in `Impl.typ_params` order and asserts every parameter is present. Make associated-function and member lookup instantiate with that prefix rather than calling `for_recv_typ`.

- [ ] **Step 6: Flatten sibling and generic-call lowering**

`FnInstance.sibling_instance` always returns an `FnInstance`: siblings receive the current `impl_args` plus their function arguments, while a non-sibling non-generic declaration uses `instantiate(())`. Remove `_resolve_sibling`'s `isinstance(FnInstance)` branch. `_resolve_fn_instance` substitutes the explicit function arguments and prepends the current instance's impl prefix only when resolving an impl sibling.

- [ ] **Step 7: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_builtins.py tests/test_generic_fns.py tests/test_impl.py tests/test_traits.py -q
uv run pytest -q
uv run ruff check src/leech/ir_module.py src/leech/ir_traits.py src/leech/ir_builder.py src/leech/typcheck.py tests/test_builtins.py tests/test_generic_fns.py tests/test_impl.py tests/test_traits.py
uv run basedpyright
git diff --check
```

- [ ] **Step 8: Stop for review and request permission to commit**

Suggested commit subject: `Flatten function instantiation arguments`.

---

### Task 3: Separate eager checking from reachable lowering

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/mono.py`
- Modify: `tests/test_impl.py`
- Modify: `tests/test_generic_fns.py`
- Modify: `tests/test_vars.py`

**Interfaces:**
- Produces: `Mod.fns: Collection[Fn]`, containing every source function declaration.
- Deletes: `Mod.generic_fns` and the generic/non-generic `_build_impl_fns` split.
- Changes: `Mod.build()` eagerly asks every function and module variable for `typ_check_results`.
- Changes: monomorphization roots are `main`, public concrete functions and public variables.

- [ ] **Step 1: Add checking and reachability regressions**

Add compile-error tests for an uncalled private free function and private impl method with invalid return types. Add monomorphization tests proving an unused private function has no emitted body, a public non-generic function does, and a public generic declaration has no instance until concretely referenced.

- [ ] **Step 2: Run the focused tests**

Run:

```bash
uv run pytest tests/test_generic_fns.py tests/test_impl.py -k "uncalled and invalid" -v
uv run pytest tests/test_generic_fns.py tests/test_impl.py tests/test_vars.py -k "public or private" -v
```

Expected: the type-check tests already characterize current behavior; the reachability assertions fail under the current all-module-item lowering walk.

- [ ] **Step 3: Collect and eagerly check every declaration**

Replace `_generic_fns` with `_fns`. Append every free and impl `Fn` during construction. After definitions and impls are complete, force `typ_check_results` for every function and `ModVar`, without forcing their CFGs. Preserve `GenericFn` wrappers for generic free-function bindings.

- [ ] **Step 4: Collapse impl-function construction**

Register every impl function in its `Impl`, bind it in `impl.env`, and append it to `Mod.fns`. Do not create synthetic `ModItem`s for impl functions. Instantiate concrete functions only through `instantiate`.

- [ ] **Step 5: Replace monomorphization seeding**

Seed lowering from `main`, all public non-generic free and impl functions, and public variable initializers. Poll `Mod.fns` and built-in declaration caches for new concrete instances in the existing pull-based fixpoint. Remove `_generic_impl_method_fns`, `Mod.generic_fns`, and the loop that lowers every `Fn`/`ModVar` module item.

- [ ] **Step 6: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_generic_fns.py tests/test_impl.py tests/test_vars.py -q
uv run pytest -q
uv run ruff check src/leech/ir_module.py src/leech/mono.py tests/test_generic_fns.py tests/test_impl.py tests/test_vars.py
uv run basedpyright
git diff --check
```

- [ ] **Step 7: Stop for review and request permission to commit**

Suggested commit subject: `Separate function checking from emission`.

---

### Task 4: Emit every function body from an instance

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/mono.py`
- Modify: `tests/test_hello_world.py`
- Modify: `tests/test_generic_fns.py`
- Modify: `tests/test_impl.py`
- Modify: `tests/test_traits.py`

**Interfaces:**
- Produces: declaration-owned symbol spelling and access/linkage policy used by `FnInstance`.
- Deletes: code-generation paths that declare or compile a source `Fn` body directly.
- Preserves: bodyless `FnDecl` LLVM declarations.

- [ ] **Step 1: Pin current symbols and linkage**

Compile fixtures and assert the LLVM definitions include exactly:

```text
main
main::<Foo as Show>::show
main::Foo::get
main::id[i32]
main::Box[i32]::unwrap
```

Assert `main` is not `main::main`. Add linkage assertions for ordinary private/public non-generic functions and generic instances.

- [ ] **Step 2: Run the symbol tests against the current compiler**

Expected: all spelling tests pass as characterization tests.

- [ ] **Step 3: Move symbol policy onto source declarations**

Record the exact legacy non-generic qualified name, access, and whether generic-instance linkage applies when each `Fn` is built. `FnInstance.qualified_name` uses the legacy spelling when the declaration has zero impl and function parameters; otherwise it derives the substituted receiver prefix and renders only `fn_args` after the function name.

- [ ] **Step 4: Unify body declaration and compilation on `FnInstance`**

Remove `_compile_mod_fn` and stop `_compile_mod_item` from accepting a body-bearing `Fn`. Keep `_declare_mod_fn` only for bodyless `FnDecl`. Declare and compile all source/built-in bodies through `FnInstance`, choosing preserved ordinary linkage or existing `linkonce_odr` from the declaration policy.

- [ ] **Step 5: Run symbol, focused, and full verification**

Run:

```bash
uv run pytest tests/test_hello_world.py tests/test_generic_fns.py tests/test_impl.py tests/test_traits.py -q
uv run pytest -q
uv run ruff check src/leech/ir_module.py src/leech/codegen.py src/leech/mono.py tests/test_hello_world.py tests/test_generic_fns.py tests/test_impl.py tests/test_traits.py
uv run basedpyright
git diff --check
```

- [ ] **Step 6: Stop for review and request permission to commit**

Suggested commit subject: `Emit function bodies from instances`.

---

### Task 5: Verify the completed stage

**Files:**
- Modify only files needed for verified cleanup.

**Interfaces:**
- Confirms: exactly one declaration instantiation entry point and no declaration body emission.

- [ ] **Step 1: Verify structural deletions**

Run:

```bash
rg -n "def (instance|impl_instance|for_recv_typ)|\.impl_instance\(|\.for_recv_typ\(" src tests
rg -n "generic_fns|_self_typ|Optional\[typs\.Typ\].*tuple\[typs\.Typ" src
rg -n "_compile_mod_fn|case ir_module\.Fn\(\)" src/leech/codegen.py
```

Expected: no old function-instantiation entry points, receiver/cache split, generic-only module collection, or direct source-`Fn` body compilation.

- [ ] **Step 2: Run full stage verification**

Run:

```bash
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run reuse --no-multiprocessing lint
git diff --check
```

Expected: at least 1,136 tests pass and every checker is clean.

- [ ] **Step 3: Stop for final review**

Do not commit cleanup or declare the stage complete until the user reviews it and explicitly authorizes the commit.
