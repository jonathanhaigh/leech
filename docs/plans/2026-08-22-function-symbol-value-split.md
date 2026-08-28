<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Function Symbol/Value Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate function declarations, concrete instances, and expression values so only `FnRef` is an IR `Value`.

**Architecture:** Function declarations own metadata and instance caches, `FnInstance` contains one declaration plus flat concrete arguments, and a cached `FnRef` contains the instance. Environments bind declarations directly; type checking records a declaration and selected arguments; lowering creates references. Extern declarations use bodyless `()` instances but retain reachability-independent declaration through the module-item walk.

**Tech Stack:** Python 3.14, uv, pytest, Ruff, basedpyright, llvmlite.

**Spec:** `docs/specs/2026-08-22-function-symbol-value-split-design.md`

## Global Constraints

- Work on `main`, as explicitly requested by the user.
- Ask before every commit and leave each task's changes unstaged for review until authorized.
- Do not use comprehensions containing more than one `for`; use explicit nested loops.
- Preserve accepted language behavior, diagnostics, emitted symbol spelling, and linkage.
- Keep imports module-qualified and use Sphinx-style docstrings according to `AGENTS.md`.
- Do not perform the Stage 4 naming sweep in this plan.
- Before completing the stage, run `uv run pytest -q`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run basedpyright`, `uv run reuse --no-multiprocessing lint`, and `git diff --check`.
- The pre-stage baseline is 1,149 passing tests.

---

### Task 1: Introduce Function Reference Values

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/ir_loader.py`
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/comptime.py`
- Modify: `src/leech/codegen.py`
- Test: `tests/test_generic_fns.py`
- Test: `tests/test_prelude.py`
- Test: `tests/test_vars.py`

**Interfaces:**
- Produces: `FnInstance.ref: FnRef`, cached per instance.
- Produces: `FnRef(ir_values.ComptimeValue[typs.PtrTyp])` with `instance: FnInstance`.
- Preserves temporarily: declarations and `FnInstance` still inherit their existing value base until Task 4.
- Changes: every newly lowered source/builtin function expression produces `FnRef`.

- [x] **Step 1: Add failing identity and lowering tests**

Add focused tests that inspect one instance and its lowered call operand:

```python
def test_fn_instance_caches_one_reference(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn f() i32 { 1 }\npub fn main() i32 { f() }")
    fn = next(fn for fn in mod.fns if fn.name == "f")
    inst = fn.instantiate(())
    assert inst.ref is inst.ref
    assert inst.ref.instance is inst
    assert isinstance(inst.ref, ir_module.FnRef)


def test_lowered_call_uses_fn_ref(tmp_path):
    mod = util.build_ir_mod(tmp_path, "fn f() i32 { 1 }\npub fn main() i32 { f() }")
    main = next(fn for fn in mod.fns if fn.is_main).instantiate(())
    calls = []
    for bb in main.cfg.nodes:
        for instr in bb.instrs:
            if isinstance(instr, ir_values.CallInstr):
                calls.append(instr)
    assert any(isinstance(call.callee, ir_module.FnRef) for call in calls)
```

Use the actual CFG iteration helpers already used by neighboring tests rather than introducing a second graph traversal utility.

Update the existing generic-call, inherent-method, trait-method, and built-in lowering assertions so they unwrap the reference explicitly:

```python
callee = asserts.checked_cast(call.callee, ir_module.FnRef)
assert callee.instance.name == expected_instance_name
```

Add a panic-identity regression whose body contains both a source-level `panic(...)` on one branch and an overflow-checked addition on another. Collect the two calls whose `callee.instance.name == "panic"` and assert their `callee` objects are identical.

- [x] **Step 2: Run the new tests and verify failure**

Run:

```bash
uv run pytest tests/test_generic_fns.py -k "caches_one_reference or lowered_call_uses_fn_ref" -q
```

Expected: FAIL because `FnRef` and `FnInstance.ref` do not exist.

- [x] **Step 3: Add `FnRef` and cache it on each instance**

Add the value wrapper after `FnInstance` and a forward cached property on the instance:

```python
class FnRef(ir_values.ComptimeValue[typs.PtrTyp, ast.FnSpec]):
    """An immutable function-address value for one concrete instance."""

    instance: Final[FnInstance]

    def __init__(self, instance: FnInstance) -> None:
        super().__init__(instance.ast)
        self.instance = instance

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return self.instance.typ


class FnInstance(FnSpec[ast.FnDefn]):
    @functools.cached_property
    def ref(self) -> FnRef:
        """The unique function-address value for this instance."""
        return FnRef(self)
```

At this intermediate stage `FnInstance.typ` is still inherited. Task 4 replaces that dependency with an instance-owned concrete pointer type.

- [x] **Step 4: Route lowering and panic through references**

Change function-valued lowering helpers to return `.ref`:

```python
def _resolve_fn_ref(
    self, fn: ir_module.FnSpec, typ_args: tuple[typs.Typ, ...]
) -> ir_module.FnRef:
    concrete_typ_args = tuple(
        typ_arg.substitute_typ_params(self._typ_arg_mapping) for typ_arg in typ_args
    )
    return fn.instantiate(concrete_typ_args).ref
```

Make `_instantiate_source_fn` return `FnRef`, including the sibling path. Normalize `ModLoader.prelude_panic_fn`, `Env.panic_fn`, `CfgBuilder._panic_fn`, and `Interpreter._panic_fn` to the cached `FnRef`. Compiler-injected and source-level panic calls must obtain the same cached reference.

In the interpreter, accept `FnRef`, compare panic by reference identity, then unwrap `callee.instance` to obtain the body and parameters.

- [x] **Step 5: Map references in code generation**

When declaring an instance, map its reference to the LLVM function:

```python
self._ll_mod_items.set(inst.ref, ll_fn)
```

Add an explicit compile-time-value case:

```python
case ir_module.FnRef():
    return asserts.checked_cast(self._ll_mod_items.get(value), ll.Value)
```

Keep the temporary mapping from `FnInstance` if any untouched caller still requires it; Task 4 removes it after the structural sweep proves it dead.

- [x] **Step 6: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_generic_fns.py tests/test_call.py tests/test_builtins.py tests/test_impl.py tests/test_traits.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
git diff --check
```

Expected: all pass; the full count increases by the new tests.

- [x] **Step 7: Stop for review and request commit authorization**

Present the unstaged Task 1 diff and verification results. After explicit authorization only:

```bash
git add docs/plans/2026-08-22-function-symbol-value-split.md src/leech/ir_module.py src/leech/ir_builder.py src/leech/ir_loader.py src/leech/ir_env.py src/leech/comptime.py src/leech/codegen.py tests/test_generic_fns.py tests/test_prelude.py tests/test_vars.py
git commit -m "Introduce function reference values"
```

---

### Task 2: Give Extern Declarations Bodyless Instances

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/comptime.py`
- Test: `tests/test_call.py`
- Test: `tests/test_import.py`
- Test: `tests/test_vars.py`

**Interfaces:**
- Consumes: `FnInstance.ref: FnRef` from Task 1.
- Produces: `FnDecl.instantiate(args: tuple[typs.Typ, ...]) -> FnInstance`, accepting only `()`.
- Produces: extern `FnInstance.qualified_name` as the bare declaration name.
- Preserves: extern declaration emission through `_program_items`, outside `mono.discover`.

- [x] **Step 1: Add failing extern-instance regressions**

Add tests for identity, symbol/linkage, unused imported declarations, and stored references:

```python
def test_extern_fn_has_cached_bodyless_instance(tmp_path):
    mod = util.build_ir_mod(tmp_path, "extern fn puts(s: *u8) i32;\npub fn main() i32 { 0 }")
    decl = asserts.checked_cast(
        mod.get_item(ir_env.Env.Namespace.VARS, "puts").value, ir_module.FnDecl
    )
    inst = decl.instantiate(())
    assert inst is decl.instantiate(())
    assert not inst.has_body
    assert inst.qualified_name == "puts"


def test_extern_fn_value_remains_global_initializer(tmp_path):
    ir_text = util.compile_str(
        tmp_path,
        "extern fn puts(s: *u8) i32;\nlet p = puts;\npub fn main() i32 { 0 }",
    ).read_text()
    assert '@"main::p" = private global i32 (i8*)* @"puts"' in ir_text
```

Add an import fixture whose unused public extern must still appear as `declare`, and assert the declaration contains neither `linkonce_odr` nor a module-qualified symbol.

- [x] **Step 2: Run the extern tests and verify failure**

Run:

```bash
uv run pytest tests/test_call.py tests/test_import.py tests/test_vars.py -k "extern and (instance or initializer or unused or linkage)" -q
```

Expected: FAIL because `FnDecl` has no instance cache and extern lowering still returns the declaration.

- [x] **Step 3: Widen instance AST and add the extern cache**

Widen the instance AST annotation to `Optional[ast.FnSpec]`. Add an empty-argument cache to `FnDecl`:

```python
class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    @functools.cached_property
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        return ()

    @functools.cached_property
    def _instance(self) -> FnInstance:
        return FnInstance(self, ())

    @property
    def instances(self) -> Collection[FnInstance]:
        return (self._instance,)

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        assert not args, f"{self.name}: extern declarations take no type arguments"
        return self._instance

    @property
    def _qualified_name_prefix(self) -> str:
        return ""
```

Represent body absence explicitly on the instance, without adding a `_build_body` stub to `FnDecl`.

- [x] **Step 4: Preserve the extern declaration walk**

Keep `FnDecl` out of `Mod.fns` and `mono._fn_templates`. Change `_declare_mod_fn` to instantiate the declaration and map its reference:

```python
inst = fn.instantiate(())
ll_fn = ll.Function(self.ll_mod, self._ll_mod_items.get(inst.fn_typ), inst.qualified_name)
self._ll_mod_items.set(inst.ref, ll_fn)
_set_linkage(ll_fn, item.access)
```

Do not route extern instances through `_declare_fn_instance`, `MonoResult.fn_instances`, or `MonoResult.external_fn_instances`.

- [x] **Step 5: Route extern expressions and compile-time calls through `FnRef`**

Lower an `FnDecl` reference as `decl.instantiate(()).ref`. In the interpreter, after unwrapping the reference, raise the existing error from the declaration span when the instance has no body:

```python
if not inst.has_body:
    raise errors.CallExternFnAtComptimeError(callee.span)
```

- [x] **Step 6: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_call.py tests/test_import.py tests/test_vars.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
git diff --check
```

Expected: all pass; emitted extern names and linkage match the pre-task output.

- [x] **Step 7: Stop for review and request commit authorization**

After explicit authorization only:

```bash
git add src/leech/ir_module.py src/leech/ir_builder.py src/leech/codegen.py src/leech/comptime.py tests/test_call.py tests/test_import.py tests/test_vars.py
git commit -m "Instantiate extern function declarations"
```

---

### Task 3: Bind Function Symbols Directly

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_builtins.py`
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/resolve.py`
- Modify: `src/leech/typcheck.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/codegen.py`
- Modify: `docs/specs/2026-08-22-function-symbol-value-split-design.md`
- Test: `tests/test_builtins.py`
- Test: `tests/test_errors.py`
- Test: `tests/test_generic_fns.py`
- Test: `tests/test_generic_structs.py`
- Test: `tests/test_impl.py`
- Test: `tests/test_traits.py`

**Interfaces:**
- Consumes: all declaration kinds implement `instantiate(args) -> FnInstance`.
- Produces: `ir_traits.FnSelection(fn: ir_module.Fn, impl_args: tuple[typs.Typ, ...])`.
- Produces: environments and module items bind `FnSpec` declarations directly.
- Deletes: `GenericFn`.
- Fixes: duplicate and reserved-name generic free-function diagnostics gain
  declaration spans, matching non-generic functions.

- [x] **Step 1: Add failing direct-binding and generic-impl tests**

Replace wrapper-shape assertions with declaration assertions and add a generic-impl sibling case:

```python
def test_generic_builtin_is_bound_as_declaration(tmp_path):
    mod = util.build_ir_mod(tmp_path, "pub fn main() i32 { 0 }")
    builtin = mod.env.get(ir_env.Env.Namespace.VARS, "__size_of")
    assert isinstance(builtin, ir_module.FnSpec)
    assert not hasattr(ir_module, "GenericFn")


def test_bare_sibling_reference_in_generic_impl_needs_no_fn_args(tmp_path):
    src = """
    struct Box[T] { value: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.value }
        fn check_ref(*self) T {
            let getter = get;
            return self.*.value;
        }
    }
    pub fn main() i32 { 0 }
    """
    util.compile_str(tmp_path, src)
```

Add a lookup test asserting a generic inherent or trait impl lookup returns a selection without increasing `fn.instances` until lowering requests it.

- [x] **Step 2: Run focused tests and verify failure**

Run:

```bash
uv run pytest tests/test_builtins.py tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py -k "bound_as_declaration or bare_sibling or lookup_does_not_instantiate" -q
```

Expected: FAIL because generic functions are wrapped and registry lookup instantiates generic impl methods.

- [x] **Step 3: Introduce an explicit registry selection**

Add the immutable selection in `ir_traits.py`:

```python
@dataclasses.dataclass(frozen=True)
class FnSelection:
    fn: ir_module.Fn
    impl_args: tuple[typs.Typ, ...]

    @property
    def fn_typ(self) -> typs.FnTyp:
        impl = opt_util.opt_unwrap(self.fn.impl)
        mapping = dict(zip(impl.typ_params, self.impl_args, strict=True))
        return asserts.checked_cast(self.fn.fn_typ.substitute_typ_params(mapping), typs.FnTyp)
```

Change `lookup_assoc_fn` and `lookup_member` to return `FnSelection(found, args)` for concrete-receiver selections, including an empty argument tuple. Do not call `instantiate` in either lookup.

- [x] **Step 4: Record selections through type checking and resolution**

Update `resolve.VarTarget` and `resolve.Callee` to admit `FnSelection`. Type checking obtains the selected concrete signature from `selection.fn_typ`, delegates visibility/span checks to `selection.fn`, and records the selection unchanged.

Use only a declaration's own parameters to decide whether syntax needs function arguments:

```python
if isinstance(var, ir_module.FnSpec) and var.typ_params:
    return self._generic_var_ref_typ(var_ast, var, e)
```

Do not use `Fn.is_generic` for this decision; it includes parent-impl parameters used only for instantiation and emission policy.

- [x] **Step 5: Instantiate selections only during lowering**

Add one lowering helper:

```python
def _selection_ref(self, selection: ir_traits.FnSelection) -> ir_module.FnRef:
    args = tuple(
        arg.substitute_typ_params(self._typ_arg_mapping) for arg in selection.impl_args
    )
    return selection.fn.instantiate(args).ref
```

Use it for path-based associated functions and dot-call members. Keep the existing sibling path for an abstract `Fn` inside its own generic impl.

- [x] **Step 6: Remove `GenericFn` and bind declarations directly**

Delete `GenericFn`. Bind every free function and built-in declaration directly:

```python
fn = Fn(defn_ast, self.env, self.name)
fns.append(fn)
self._add_item(defn_ast.name.name, access, fn, qualify_name)
```

Update `ir_env.Var`, `ModItem.value`, `_add_item`, private-item diagnostics,
`resolve.VarTarget`, and codegen module-item matches to use `FnSpec` declarations.
Make `ModItem._ns` classify `FnSpec` explicitly as `VARS` even though it is not
yet detached from `Value` until Task 4.

- [x] **Step 7: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_builtins.py tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py tests/test_traits.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
git diff --check
```

Expected: all pass; `rg -n "GenericFn" src tests` returns no matches.

- [x] **Step 8: Stop for review and request commit authorization**

After explicit authorization only:

```bash
git add docs/plans/2026-08-22-function-symbol-value-split.md docs/specs/2026-08-22-function-symbol-value-split-design.md src/leech/ir_module.py src/leech/ir_builtins.py src/leech/ir_env.py src/leech/ir_traits.py src/leech/resolve.py src/leech/typcheck.py src/leech/ir_builder.py src/leech/codegen.py tests/test_builtins.py tests/test_errors.py tests/test_generic_fns.py tests/test_generic_structs.py tests/test_impl.py tests/test_traits.py
git commit -m "Bind function declarations as symbols"
```

---

### Task 4: Remove Function Value Inheritance

**Files:**
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_values.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/comptime.py`
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/typcheck.py`
- Test: `tests/test_generic_fns.py`

**Interfaces:**
- Consumes: direct declaration bindings and `FnSelection` from Task 3.
- Produces: `FnSpec` as a non-value declaration base.
- Produces: `BodyFnSpec` as an abstract mixin for body-owning declarations.
- Produces: `FnInstance` as a non-value concrete instance.
- Produces: `Param.fn: FnSpec | FnInstance` and `CfgBuilder._fn: Optional[FnInstance]`.
- Deletes: function-specific `load`, `store`, `is_temporary`, and `body_cfg`.

- [x] **Step 1: Add failing hierarchy tests**

Add structural assertions:

```python
def test_only_fn_ref_is_a_function_value(tmp_path):
    mod = util.build_ir_mod(
        tmp_path, "extern fn exit(code: i32);\nfn f() i32 { 1 }\npub fn main() i32 { f() }"
    )
    fn = next(fn for fn in mod.fns if fn.name == "f")
    decl = asserts.checked_cast(
        mod.get_item(ir_env.Env.Namespace.VARS, "exit").value, ir_module.FnDecl
    )
    inst = fn.instantiate(())
    assert not isinstance(fn, ir_values.Value)
    assert not isinstance(decl, ir_values.Value)
    assert not isinstance(inst, ir_values.Value)
    assert isinstance(inst.ref, ir_values.Value)
```

Add assertions that declarations and instances expose none of `load`, `store`, `is_temporary`, or `body_cfg`.

- [x] **Step 2: Run the hierarchy tests and verify failure**

Run:

```bash
uv run pytest tests/test_generic_fns.py -k "only_fn_ref or obsolete_function_pointer_methods" -q
```

Expected: FAIL because `FnSpec` and `FnInstance` still inherit `ComptimePtr`.

- [x] **Step 3: Make `FnSpec` a declaration base**

Remove the `ComptimePtr` base and give `FnSpec` its own AST/span and signature interface:

```python
class FnSpec[FnAstT_co: ast.FnSpec](abc.ABC):
    ast: Final[Optional[FnAstT_co]]

    def __init__(self, fn_ast: Optional[FnAstT_co]) -> None:
        self.ast = fn_ast

    @property
    def span(self) -> Optional[src.SrcSpan]:
        return opt_util.opt_map(self.ast, lambda node: node.span)

    @property
    @abc.abstractmethod
    def fn_typ(self) -> typs.FnTyp:
        raise NotImplementedError
```

Move any cached pointer-type calculation needed by references onto declarations or instances without making either a `Value`.

- [x] **Step 4: Split the body-owning interface**

Put metadata, type parameters, instances, and `instantiate` on `FnSpec`. Add:

```python
class BodyFnSpec(abc.ABC):
    env: ir_env.Env

    @property
    @abc.abstractmethod
    def typ_check_results(self) -> typcheck.TypCheckResults:
        raise NotImplementedError

    @abc.abstractmethod
    def _build_body(
        self, builder: ir_builder.CfgBuilder, args: tuple[typs.Typ, ...]
    ) -> None:
        raise NotImplementedError
```

`Fn` and `GenericBuiltinFn` inherit `BodyFnSpec`; `FnDecl` does not.
`FnInstance.cfg` narrows to `BodyFnSpec` and asserts that bodyless instances
never request a CFG. Delete `FnSpec.body_cfg` and the `Fn` override. Tests that
need a CFG request it from an instance, matching production lowering.

- [x] **Step 5: Make `FnInstance` a plain instance object**

Remove its `FnSpec` base. Give it declaration-derived `ast`, `span`, `fn_typ`, concrete `params`, and cached `ref` properties directly. `FnRef.calculate_typ` returns the instance's concrete pointer type.

Retype shared positions exactly:

```python
# ir_values.py
fn: Final[ir_module.FnSpec | ir_module.FnInstance]

# ir_builder.py
_fn: Final[Optional[ir_module.FnInstance]]
_panic_fn: Final[Optional[ir_module.FnRef]]

# ir_env.py and comptime.py
panic_fn: Final[Optional[ir_module.FnRef]]
```

Update built-in body builders to read the concrete `builder._fn` instance. Update resolution unions so symbols, selections, instances, and references occupy only the phases specified by the design.

- [x] **Step 6: Remove obsolete pointer and body branches**

Delete `FnSpec.load`, `store`, `is_temporary`, and `body_cfg`, plus function cases that existed only because declarations or instances were `Value`s. Update the compile-time-value exhaustiveness comment from `FnSpec` to `FnRef`.

Keep compile-time external-call behavior:

```python
callee = asserts.checked_cast(self._get_comptime_value(instr.callee), ir_module.FnRef)
if callee is self._panic_fn:
    raise errors.PanicAtComptimeError(_panic_message(args), instr.span)
inst = callee.instance
if not inst.has_body:
    raise errors.CallExternFnAtComptimeError(callee.span)
```

- [x] **Step 7: Run the structural sweep**

Run:

```bash
rg -n "GenericFn|\.body_cfg\(|def (load|store|is_temporary)\(" src/leech
rg -n "isinstance\([^\n]*FnSpec|FnSpec.*Value|FnInstance.*Value|ComptimePtr.*Fn" src/leech tests
rg -n "FnSpec/|FnSpec,|FnSpec\)" src/leech
```

Expected: no `GenericFn`, `body_cfg`, function-specific pointer-method definitions, or declaration/instance-as-value checks. Every remaining `FnSpec` occurrence denotes a declaration. For an annotation or comment reported by the search, replace the stale type with its final phase type: `FnSpec` for declarations, `FnInstance` for specializations, or `FnRef` for values. Re-run all three searches until they meet these expectations.

The first sweep is limited to `src/leech` because the hierarchy regression test
intentionally names each deleted method in `hasattr` assertions.

- [x] **Step 8: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_generic_fns.py tests/test_call.py tests/test_builtins.py tests/test_import.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run reuse --no-multiprocessing lint
git diff --check
```

Expected: all pass, with no function declaration or instance satisfying `Value`.

- [x] **Step 9: Confirm final hierarchy invariants**

Run:

```bash
uv run python -c 'from leech import ir_module, ir_values; assert not issubclass(ir_module.FnSpec, ir_values.Value); assert not issubclass(ir_module.FnInstance, ir_values.Value); assert issubclass(ir_module.FnRef, ir_values.Value)'
rg -n "class GenericFn|body_cfg|Can't dereference a function pointer" src/leech
```

Expected: the Python assertion succeeds and the search returns no matches.

- [x] **Step 10: Stop for review and request commit authorization**

After explicit authorization only:

```bash
git add docs/plans/2026-08-22-function-symbol-value-split.md src/leech/ir_module.py src/leech/ir_values.py src/leech/ir_builder.py src/leech/ir_env.py src/leech/comptime.py src/leech/codegen.py src/leech/typcheck.py tests/test_generic_fns.py tests/test_generic_structs.py tests/test_prelude.py
git commit -m "Separate function symbols from values"
```
