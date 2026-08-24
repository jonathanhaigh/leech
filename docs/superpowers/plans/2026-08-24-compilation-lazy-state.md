<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Compilation-wide Lazy State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize lazy instance requests and semantic cycle tracking in one per-compilation context, replacing polling and depth-limit failures with direct cycle handling.

**Architecture:** A new `compilation.Ctx` is owned by `ModLoader`, inherited through `Env`, and shared with `ImplRegistry`. It provides typed function/struct instance caches, insertion-ordered request logs for monomorphization, and a domain-keyed active-computation stack whose cycles are translated into existing or new user diagnostics by each semantic layer.

**Tech Stack:** Python 3.14, pytest, Ruff, basedpyright, REUSE

**Spec:** `docs/superpowers/specs/2026-08-24-compilation-lazy-state-design.md`

## Global Constraints

- Preserve accepted language behavior, symbol spelling, and linkage.
- The three intentional diagnostic corrections—growing struct recursion, recursive trait
  bounds, and recursive impl selection—must each land with focused tests and in a separate
  commit from mechanical state migration.
- Keep imports module-qualified and avoid runtime import cycles with `TYPE_CHECKING` or local imports where needed.
- Do not use comprehensions containing more than one `for`; use ordinary nested loops.
- Do not build a general query engine, dependency graph, invalidation system, or parallel executor.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright --pythonpath /home/jonathan/work/leech/.venv/bin/python`, `uv run reuse --no-multiprocessing lint`, and `git diff --check` before completion.
- Ask the user before every commit.

---

### Task 1: Add and propagate the compilation context

**Files:**
- Create: `src/leech/compilation.py`
- Modify: `src/leech/ir_loader.py`
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_module.py`
- Test: `tests/test_loader.py`

**Interfaces:**
- Produces: `compilation.Ctx`
- Produces: `Env.ctx: compilation.Ctx`
- Produces: `ImplRegistry.ctx: compilation.Ctx`

- [x] **Step 1: Write context-identity tests**

Add focused tests showing that a loader, its registry, every loaded module environment,
and child environments share one context, while two loaders use distinct contexts:

```python
def test_loader_scopes_and_impl_registry_share_compilation_context(tmp_path):
    path = tmp_path / "main.leech"
    util.write_whole_file(path, "pub fn main() i32 { return 0; }")
    loader = ir_loader.ModLoader()
    mod = loader.load(path, "main")
    assert mod.env.ctx is loader.ctx
    assert mod.env.new_child().ctx is loader.ctx
    assert loader.impl_registry.ctx is loader.ctx


def test_loaders_have_distinct_compilation_contexts() -> None:
    assert ir_loader.ModLoader().ctx is not ir_loader.ModLoader().ctx


def test_env_and_registry_share_explicit_ctx() -> None:
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    assert env.impl_registry.ctx is env.ctx
```

- [x] **Step 2: Run the new tests and verify the missing interface fails**

Run: `uv run pytest tests/test_loader.py -q`

Expected: FAIL because `ModLoader`, `Env`, and `ImplRegistry` do not expose `ctx`.

- [x] **Step 3: Implement the empty compilation-state owner**

Create `compilation.py` with SPDX headers and the context shell:

```python
class Ctx:
    """Own compilation-wide lazy requests and active semantic computations."""
```

The request caches arrive in Task 2 and active-computation machinery in Task 4, so this
commit does not ship unused cycle APIs.

- [x] **Step 4: Thread one context through loader, environments, and registry**

Construct `ModLoader.ctx` before `ImplRegistry`, pass it to the registry and to each root
`Env`, and pass the same context, registry, and panic reference explicitly from
`Env.new_child()`. Make those three dependencies required constructor arguments; focused
tests must construct them explicitly rather than relying on a special standalone fallback.

- [x] **Step 5: Run focused and full verification**

Run:

```bash
uv run pytest tests/test_loader.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright --pythonpath /home/jonathan/work/leech/.venv/bin/python
```

Expected: all pass with no diagnostic or emitted-IR changes.

- [x] **Step 6: Ask for review and permission to commit**

Proposed commit subject: `Add compilation-wide lazy state context`

---

### Task 2: Centralize function and struct instance requests

**Files:**
- Modify: `src/leech/compilation.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/typs.py`
- Test: `tests/test_generic_fns.py`
- Test: `tests/test_generic_structs.py`
- Test: `tests/test_call.py`
- Test: `tests/test_typs.py`

**Interfaces:**
- Consumes: `Env.ctx`
- Produces: typed `compilation.Ctx.instantiate_fn`, `fn_instances`, and `requested_fn_instances`
- Produces: typed `compilation.Ctx.instantiate_struct`, `struct_instances`, and `requested_struct_instances`
- Preserves: `FnSymbol.instances`, `FnSymbol.instantiate`, `StructTyp.instances`, and `StructTyp.instance`

- [x] **Step 1: Add per-compilation identity and request-order tests**

Extend existing instantiation tests to assert:

```python
assert fn.instantiate(args) is fn.instantiate(args)
assert tuple(loader.ctx.requested_fn_instances()).count(fn.instantiate(args)) == 1
assert struct.instance(args) is struct.instance(args)
assert tuple(loader.ctx.requested_struct_instances()).count(struct.instance(args)) == 1
```

Reuse one parsed generic-struct AST with two standalone environments and assert
`StructTyp.get_or_create` returns distinct templates tied to the two contexts. This covers
the process-cached bundled-AST case that ordinary `tmp_path` compilation does not.

- [x] **Step 2: Run focused tests and verify request-log assertions fail**

Run:

```bash
uv run pytest tests/test_generic_fns.py tests/test_generic_structs.py tests/test_call.py -q
```

Expected: FAIL because the context has no request caches or logs.

- [x] **Step 3: Add typed request-cache methods**

Implement dictionaries grouped as `owner -> {args: instance}` so `instances(owner)` keeps
its current insertion order without scanning every compilation instance. Maintain separate
append-only flat request lists. `requested_fn_instances()` and
`requested_struct_instances()` return read-only `Sequence` annotations backed by the live
internal lists, not tuple snapshots, because Task 3 drains while lowering appends. Document
that aliasing contract. Use `TYPE_CHECKING` imports for `ir_module` and `typs`; use a private
generic helper only if it does not expose `object`-typed cache values to callers. Append
only after construction succeeds, so exceptions leave neither a cache entry nor a request
log entry.

- [x] **Step 4: Delegate every function instance cache to the context**

Change `SrcFnSymbol`, `IntrinsicFnSymbol`, and `ExternFnSymbol` so `instantiate` calls
`self.env.ctx.instantiate_fn(self, args)` after its existing arity assertions. The context
constructs `FnInstance` directly on a miss. Delete their `_instance_cache` and `_instance`
cached properties. Make each `instances` property delegate to `self.env.ctx.fn_instances(self)`.

`ExternFnSymbol.instances` consequently stops creating its bodyless instance on inspection;
this makes its contract match the other symbols. Extern declaration and codegen behavior do
not change until Task 3, where bodyless requests are explicitly excluded.

- [x] **Step 5: Delegate generic struct instance caching to the context**

Delete `StructTyp._instance_cache`. Make `instances` delegate through `_decl_env.ctx`
and make `instance` call `instantiate_struct`. Give `StructTyp` a narrowly scoped internal
method that owns the `StructTyp.get_or_create` arguments needed when the context misses. Add
`_decl_env.ctx.typ_cache_token` to `StructTyp.cache_key`, so a process-cached bundled AST
cannot reuse a template carrying another compilation's environment without making the weak
type-cache key retain the whole context. The template itself remains the cache owner key;
assert that concrete instances cannot become new template keys.

- [x] **Step 6: Run focused and full verification**

Run the focused command from Step 2, then the complete verification suite from Task 1.

Expected: all pass; emitted names and linkage remain byte-identical for existing symbol
tests.

- [ ] **Step 7: Ask for review and permission to commit**

Proposed commit subject: `Centralize lazy instance requests`

---

### Task 3: Make monomorphization drain request logs

**Files:**
- Modify: `src/leech/mono.py`
- Modify: `src/leech/ir_module.py`
- Test: `tests/test_import.py`
- Test: `tests/test_impl.py`
- Test: `tests/test_traits.py`
- Test: `tests/test_call.py`
- Test: `tests/test_generic_fns.py`
- Test: `tests/test_generic_structs.py`
- Test: `tests/test_hello_world.py`

**Interfaces:**
- Consumes: `compilation.Ctx.requested_fn_instances()` and `requested_struct_instances()`
- Deletes: `mono._fixpoint` and `mono._fn_symbols`
- Deletes: `FnSymbol.instances` and its concrete implementations
- Preserves: validated `FnSymbol.instantiate`
- Preserves: `MonoResult` and `mono.discover(mod) -> MonoResult`

- [ ] **Step 1: Add discovery regression tests**

Add or strengthen tests for a generic function whose lowering requests another generic
function, an intrinsic instance, a generic struct whose field requests another struct
instance, an imported non-generic external leaf, and recursive function calls. Add an
emitted-IR regression test proving a used extern appears exactly once as `declare`, never as
`define`. Assert exact instance identities or qualified-name sets, not LLVM definition
order: this rewrite deliberately changes discovery from declaration scan order within a
round to global first-request order.

- [ ] **Step 2: Run the focused discovery tests**

Run:

```bash
uv run pytest tests/test_import.py tests/test_impl.py tests/test_traits.py tests/test_call.py tests/test_generic_fns.py tests/test_generic_structs.py tests/test_hello_world.py -q
```

Expected: PASS before implementation; these are regression tests for a mechanical
discovery rewrite.

- [ ] **Step 3: Replace function-cache polling with a cursor loop**

After preserving the existing root and module-variable seeding, iterate the context's live
function request sequence by index:

```python
cursor = 0
requests = mod.loader.ctx.requested_fn_instances()
while cursor < len(requests):
    inst = requests[cursor]
    cursor += 1
    if not inst.is_concrete() or not inst.has_body:
        continue
    if not _is_external_fn_instance(inst, mod):
        _ = inst.cfg
    discovered.append(inst)
```

Partition discovered instances into local and external lists exactly as today. Do not use a
snapshot: lowering may append to the same live sequence. Bodyless extern instances remain
absent from `MonoResult`; `_declare_mod_fn`'s module-item walk stays their sole declaration
path.

- [ ] **Step 4: Replace struct-cache polling with a cursor loop**

Walk the live struct request sequence the same way, skip non-concrete instances, force
`inst.fields`, and continue until the cursor reaches the moving end. Delete `_fixpoint`,
`_fn_symbols`, and their now-unused imports. Rewrite `mono.py`'s module docstring to describe
request-log draining rather than polling declaration-owned caches.

- [ ] **Step 5: Remove declaration-level instance enumeration**

Remove the abstract `FnSymbol.instances` property and its implementations from
`ExternFnSymbol`, `SrcFnSymbol`, and `IntrinsicFnSymbol`. Where tests need to observe that
lookup did not instantiate a function, query `fn.env.ctx.fn_instances(fn)` explicitly;
production code must no longer enumerate instances through a declaration.

- [ ] **Step 6: Verify discovery and the whole compiler**

Run the focused command from Step 2 and every global verification command.

Expected: all pass; recursive functions terminate discovery because cached requests enter
the log only once.

- [ ] **Step 7: Ask for review and permission to commit**

Proposed commit subject: `Discover monomorphizations from request logs`

---

### Task 4: Move module-variable cycle tracking into the context

**Files:**
- Modify: `src/leech/compilation.py`
- Modify: `src/leech/ir_module.py`
- Create: `tests/test_compilation.py`
- Test: `tests/test_vars.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: generic `Cycle[T]` and stack-safe `compilation.Ctx.enter(...)`
- Consumes: `compilation.Ctx.enter(CycleDomain.MOD_VAR_INITIALIZER, ...)`
- Deletes: `ModVar._resolving`
- Preserves: `CircularVarInitializerError` contents and spans

- [ ] **Step 1: Strengthen cycle diagnostic tests**

Add a unit test that raises an unrelated exception inside `compilation.Ctx.enter`, then
enters the same identity again and receives no cycle; this pins `finally` cleanup and stack
discipline. For direct, two-way, three-way, and cross-module cycles, assert the primary
variable name and the ordered note names already exposed through `UserError.extra`. Keep a
non-cyclic diamond dependency test to prove repeated completed work is not an active cycle. Request a
cycle twice after catching the first error and assert the same diagnostic is produced,
proving failed `cached_property` evaluation did not cache a result or leak an active frame.

- [ ] **Step 2: Run the variable-cycle tests before implementation**

Run: `uv run pytest tests/test_vars.py tests/test_errors.py -q`

Expected: PASS; this task preserves behavior.

- [ ] **Step 3: Implement the typed active-computation tracker**

Add `CycleDomain`, a private frame, `Cycle[T]` with `details: tuple[T, ...]`, and
`compilation.Ctx.enter[T]`. `enter` accepts an optional identity-comparison callback for
Task 5's structural-growth rule; equality is the default. It searches only frames in the
same domain, yields the matching suffix plus the new detail on recurrence, and otherwise
pushes and pops in `finally` with a top-of-stack assertion. Its docstring must state that a
caller receiving a non-`None` cycle must raise rather than continue the guarded computation.

- [ ] **Step 4: Replace the class-global module-variable stack**

In `ModVar.initializer`, enter a frame using `self.env.ctx`, domain
`MOD_VAR_INITIALIZER`, identity `self`, and detail `self`. If the yielded cycle is not
`None`, construct `CircularVarInitializerError` from its `ModVar` details. Otherwise
evaluate the CFG normally. Delete the `ClassVar` import and `_resolving` declaration when
unused. This preserves diagnostics while preventing active state from leaking between
compilations in one process.

- [ ] **Step 5: Verify focused and full suites**

Run the command from Step 2 and every global verification command.

Expected: all pass with unchanged error rendering.

- [ ] **Step 6: Ask for review and permission to commit**

Proposed commit subject: `Centralize module variable cycle tracking`

---

### Task 5: Diagnose struct declaration cycles directly

**Files:**
- Modify: `src/leech/typs.py`
- Modify: `src/leech/errors.py`
- Modify: `src/leech/codegen.py`
- Test: `tests/test_structs.py`
- Test: `tests/test_generic_structs.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: `compilation.Ctx.enter(CycleDomain.STRUCT_LAYOUT, ...)`
- Produces: `typs.contains_typ(container, contained) -> bool`
- Deletes: `_MAX_STRUCT_INSTANTIATION_DEPTH`
- Deletes: `TypInstantiationDepthExceededError`
- Preserves and broadens: `InfiniteSizeStructError`

- [ ] **Step 1: Change the growing-generic-cycle test to the intended diagnostic**

Replace the `TypInstantiationDepthExceededError` expectation with
`InfiniteSizeStructError`, and assert that its notes identify the recursive `x` field.
Add a positive direct-field test for finite `Box[Box[i32]]`. Keep existing direct, mutual,
array, pointer-broken, and diamond layout tests. The existing
`struct L[T] { x: L[T] }` case may produce fewer duplicate field notes once detected at its
first growing declaration recurrence; pin meaningful edge names rather than the old count.

- [ ] **Step 2: Run the focused test and verify the diagnostic mismatch**

Run:

```bash
uv run pytest tests/test_generic_structs.py::test_growing_generic_struct_declaration_cycle tests/test_structs.py tests/test_errors.py -q
```

Expected: FAIL because the compiler still raises `TypInstantiationDepthExceededError`.

- [ ] **Step 3: Replace explicit recursion parameters with context frames**

Generalize the existing structural occurrence traversal into
`contains_typ(container, contained)`. Refactor `_check_finite_size` so each recursive
by-value edge enters a `STRUCT_LAYOUT` frame identified by its instantiated `StructTyp`,
with detail containing that type and the incoming field hop. Supply a domain comparison
that considers two frames repeated when their types are identical, or when they share one
`ast.StructDefn` and every earlier type argument structurally occurs in its corresponding
later argument. This rejects `L[i32] -> L[[i32; 1]]` but permits
`Box[Box[i32]] -> Box[i32]`. Translate the yielded frame suffix into
`InfiniteSizeStructError`. Continue to unwrap arrays and stop at pointers exactly as today.

- [ ] **Step 4: Remove the depth-limit diagnostic**

Delete `_MAX_STRUCT_INSTANTIATION_DEPTH`, `TypInstantiationDepthExceededError`, and all
references. Update the codegen compilation-order comment and tests that claim growing
instances require a depth cap. Keep the walk explicit: it must not force a separate
`StructTyp.fields` property while layout frames are active.

- [ ] **Step 5: Verify focused and full suites**

Run the focused command from Step 2 and every global verification command.

Expected: all pass; accepted programs are unchanged, but growing recursion now has a cycle
diagnostic.

- [ ] **Step 6: Ask for review and permission to commit**

Proposed commit subject: `Report growing struct layouts as cycles`

---

### Task 6: Diagnose recursive trait declarations

**Files:**
- Modify: `src/leech/typs.py`
- Modify: `src/leech/errors.py`
- Test: `tests/test_traits.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: `compilation.Ctx.enter(CycleDomain.TRAIT_BOUND, ...)`
- Produces: `errors.RecursiveTraitBoundError`
- Deletes: `resolve_bound(..., in_progress=...)` and corresponding threaded parameters

- [ ] **Step 1: Rewrite recursive-bound tests for the user diagnostic**

Change direct, mutual, pointer-growing, array-growing, and mutually-growing recursive bound
tests to expect `errors.RecursiveTraitBoundError`. Assert the primary trait name and ordered
cycle notes. Retain tests proving that a finite distinct-trait chain and sequential uses of
one trait at different arguments are not cycles.

- [ ] **Step 2: Run the focused tests and verify they fail on error type**

Run:

```bash
uv run pytest tests/test_traits.py -k 'recursive_trait_bound or recursive_bounds or growing' -q
```

Expected: FAIL because recursive bounds still raise `NotImplementedError`.

- [ ] **Step 3: Add the user-facing error**

Add an error accepting a primary trait name/span and ordered `(name, span)` cycle entries:

```python
class RecursiveTraitBoundError(UserError):
    """Raised because recursive trait bounds are not supported."""

    def __init__(
        self,
        trait_name: str,
        trait_span: Optional[src.SrcSpan],
        cycle: Sequence[tuple[str, Optional[src.SrcSpan]]],
    ) -> None:
        super().__init__(ERROR, f'Trait "{trait_name}" has a recursive bound', trait_span)
        for name, span in cycle:
            self._add_extra(NOTE, f'Trait "{name}" participates in this bound cycle', span)
```

- [ ] **Step 4: Keep one bound frame active through resolution and proof**

In `unsatisfied_bound`, enter `TRAIT_BOUND` before resolving each declaration-site bound.
Use the bound AST object as identity and detail, and keep the frame active across both
`resolve_bound(...)` and `impl_registry.implements(...)`. Translate a yielded cycle into
`RecursiveTraitBoundError` using the paths and spans in the bound AST details. This catches
direct, mutual, and argument-growing declared bounds, including through non-generic traits,
without conflating separate declarations mentioning one trait. Remove `in_progress` from
`resolve_bound`, `unsatisfied_bound`, and `check_typ_arg_bounds` and from every call site.

- [ ] **Step 5: Preserve impl selection's independent guard**

Leave `MAX_BOUND_DEPTH` and `ImplRegistry._selection_depth` in place for Task 7. They guard
a distinct recursion in conditional impl applicability, and removing them in this task
would permit an unhandled Python `RecursionError`.

- [ ] **Step 6: Verify focused tests, structural removal, and full suite**

Run:

```bash
uv run pytest tests/test_traits.py tests/test_errors.py -q
rg 'in_progress' src/leech tests
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright --pythonpath /home/jonathan/work/leech/.venv/bin/python
uv run reuse --no-multiprocessing lint
git diff --check
```

Expected: tests and checks pass; `rg` has no hits for the removed threaded state.

- [ ] **Step 7: Ask for review and permission to commit**

Proposed commit subject: `Report recursive trait bounds as cycles`

---

### Task 7: Diagnose recursive impl selection

**Files:**
- Modify: `src/leech/compilation.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/typs.py`
- Modify: `src/leech/errors.py`
- Test: `tests/test_traits.py`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: `compilation.Ctx.enter(CycleDomain.IMPL_SELECTION, ...)`
- Produces: `errors.RecursiveImplSelectionError`
- Deletes: `ImplRegistry._selection_depth`
- Deletes: `typs.MAX_BOUND_DEPTH`

- [ ] **Step 1: Add direct and mutual blanket-impl recursion tests**

Add one program with `impl[T: Show] Show for T` and one with mutually conditional blanket
impls `impl[T: B] A for T` / `impl[T: A] B for T`. Both currently reach the selection-depth
`NotImplementedError`; change their intended result to `RecursiveImplSelectionError` and
assert impl names/spans in the notes. Keep a terminating nested selection test where the
matched self type becomes a strict subterm.

- [ ] **Step 2: Run the tests and verify the intended error type fails**

Run: `uv run pytest tests/test_traits.py -k 'recursive_impl_selection' -q`

Expected: FAIL because selection still ends at the depth-limit `NotImplementedError`.

- [ ] **Step 3: Add the selection-cycle domain and diagnostic**

Add `CycleDomain.IMPL_SELECTION`. Add `RecursiveImplSelectionError`, whose primary message
states that selecting an implementation for the concrete type is recursive and whose notes
identify the participating impl declarations. Follow the `CircularVarInitializerError`
shape and describe the unsupported user construct in the class docstring.

- [ ] **Step 4: Guard concrete impl obligations**

In `_impl_bounds_hold`, reconstruct the matched self type with
`impl.self_typ.substitute_typ_params(bindings)`. Enter an `IMPL_SELECTION` frame keyed by
`(impl, matched_self_typ)` and carrying `(impl, matched_self_typ)` as detail before
calling `typs.unsatisfied_bound`. A repeated exact obligation raises
`RecursiveImplSelectionError`; selection on a different strict subterm continues.

- [ ] **Step 5: Remove the depth counter and cap**

Delete `ImplRegistry._selection_depth`, its initialization and increment/decrement logic,
and `typs.MAX_BOUND_DEPTH`. Update stale docstrings and comments. The precise obligation
cycle now handles direct and mutual recursion without an arbitrary numeric limit.

- [ ] **Step 6: Verify the focused tests, removal sweep, and full suite**

Run:

```bash
uv run pytest tests/test_traits.py tests/test_errors.py -q
rg 'MAX_BOUND_DEPTH|_selection_depth' src/leech tests
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run basedpyright --pythonpath /home/jonathan/work/leech/.venv/bin/python
uv run reuse --no-multiprocessing lint
git diff --check
```

Expected: all checks pass and `rg` finds no remaining depth guard.

- [ ] **Step 7: Ask for review and permission to commit**

Proposed commit subject: `Report recursive impl selection as a cycle`

---

### Task 8: Refresh Stage 5 documentation and run final verification

**Files:**
- Modify: `docs/superpowers/plans/2026-08-17-fn-hierarchy-restructure-staging.md`
- Modify: `docs/superpowers/specs/2026-08-24-compilation-lazy-state-design.md`
- Modify: `docs/superpowers/plans/2026-08-24-compilation-lazy-state.md`

**Interfaces:**
- Consumes: all preceding tasks
- Produces: completed Stage 5 checklist and accurate staging summary

- [ ] **Step 1: Reconcile the design and staging summary with implementation**

Confirm the documents still describe the implemented boundary: per-compilation instance
request caches, monomorphization request logs, and the shared active-computation tracker.
Record any review-approved deviations explicitly. Mark Stage 5 complete only after its exit
criteria hold.

- [ ] **Step 2: Mark completed plan checkboxes**

Mark only steps actually completed. Update commands or file lists if implementation review
proved the written plan inaccurate; do not silently broaden scope.

- [ ] **Step 3: Run final repository verification**

Run every global verification command and record the current pytest count in the handoff.

- [ ] **Step 4: Ask for final review and permission to commit**

Proposed commit subject: `Complete lazy state centralization plan`
