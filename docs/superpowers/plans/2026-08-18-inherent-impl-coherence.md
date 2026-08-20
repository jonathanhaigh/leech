<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Inherent impl coherence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate field names from inherent function names while rejecting same-named functions in overlapping inherent impls at declaration time.

**Architecture:** `ImplRegistry` owns inherent coherence and member lookup. `StructTyp` stores fields only; overlapping inherent blocks are legal unless they declare a shared function name. Path resolution calls registry methods through its originating `Env`.

**Tech Stack:** Python 3.14, uv, pytest, Ruff, basedpyright.

## Global Constraints

- Keep all changes unstaged and uncommitted until the user reviews and explicitly authorizes a commit.
- Use module-qualified imports, four-space indentation, and Ruff's 100-column limit.
- Preserve existing diagnostics except for the approved field/function coexistence and declaration-time generic inherent conflict changes.
- Use identifier spans for duplicate associated-function diagnostics.
- `uv run pytest`, `uv run ruff check .`, and `uv run basedpyright` must pass.

---

### Task 1: Move inherent coherence into `ImplRegistry`

**Files:**
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/typs.py`
- Test: `tests/test_impl.py`

**Interfaces:**
- Produces declaration-time rejection when structurally overlapping inherent impls declare a shared function name.
- Produces separate field and associated-function name domains.
- Preserves same-block duplicate detection through `Impl.add_fn`.

- [ ] Replace field/function collision tests with executable coexistence tests for a receiver method and a receiverless associated function.
- [ ] Add a failing test where two generic inherent impls overlap and declare the same function name even when the function is never called; expect `DuplicateItemDefnError` during compilation.
- [ ] Add a passing test where structurally disjoint inherent impls reuse a function name.
- [ ] Add a duplicate-span regression test that compares the raised error span with the second function identifier span.
- [ ] Run the new tests and confirm coexistence/generic-declaration tests fail for the intended reasons before implementation.
- [ ] In `ImplRegistry.add_impl`, compare a new inherent impl with existing inherent impls under the same head shape. For structurally overlapping pairs only, reject intersecting AST function names with `DuplicateItemDefnError("associated function", name, new_name_span, existing_name_span)`.
- [ ] Preserve trait conflict diagnostics and bound-insensitive coherence, using
  `typs.typs_overlap` so trait impls also gain true partial-overlap detection.
- [ ] Remove `StructTyp._assoc_fn_spans` and `reserve_assoc_fn_name`; update its class and `_add_field` docstrings to describe fields only.
- [ ] Remove the reservation call from `Mod._build_impl_fns` without otherwise restructuring the function.
- [ ] Change `Impl.add_fn` duplicate diagnostics to use `fn.ast.name.span`, proving the same-block inherent branch remains live and precise.
- [ ] Run the focused tests and the full suite.

---

### Task 2: Make member lookup registry-owned

**Files:**
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/typcheck.py`
- Modify: `tests/test_traits.py`

**Interfaces:**
- Produces `ImplRegistry.lookup_assoc_fn(typ, name, span)`.
- Produces `ImplRegistry.lookup_member(typ, name, span)`.
- Removes free `ir_traits.lookup_assoc_fn` and `ir_traits.lookup_member`.

- [ ] Move both lookup functions onto `ImplRegistry`, replacing the explicit registry parameter with `self`.
- [ ] Keep `disambiguate` as a module helper.
- [ ] Restore a concise comment explaining why a structurally matched function must call `for_recv_typ(typ)`; make the explanation apply to both inherent and trait lookup.
- [ ] Update all lookup call sites and tests to use the registry methods.
- [ ] Convert `Env._resolve_path_segment` from a static method to an instance method; use `self.impl_registry` internally while retaining the separate `scope` being traversed.
- [ ] Run focused path, method, visibility, ambiguity, and generic lookup tests.

---

### Task 3: Remove incidental leftovers and verify

**Files:**
- Modify: `src/leech/typs.py`
- Modify: `tests/test_generic_structs.py`

**Interfaces:**
- Produces a read-only live field mapping without an unnecessary copy.

- [ ] Return `types.MappingProxyType(self._members)` from `StructTyp.fields` after `_check_finite_size`.
- [ ] Ensure comments and docstrings no longer describe fields and associated functions as sharing a namespace or refer to deleted `StructTyp` lookup APIs.
- [ ] Run searches confirming `_assoc_fn_spans`, `reserve_assoc_fn_name`, and the free lookup functions are gone.
- [ ] Run `uv run pytest -q`, `uv run ruff check .`, `uv run basedpyright`, and `git diff --check`.
- [ ] Request independent review and leave all changes unstaged for user review.

---

### Task 4: Extract impl conflict checks

**Files:**
- Modify: `src/leech/ir_traits.py`
- Test: `tests/test_impl.py`
- Test: `tests/test_traits.py`

**Interfaces:**
- Preserves `ImplRegistry.add_impl(impl)` behavior and diagnostic ordering.
- Produces private trait/inherent conflict-checking helpers.

- [ ] Extract trait candidate traversal into a private iterator that uses explicit nested
  `for` loops rather than a multi-level comprehension.
- [ ] Extract trait conflict checking into `_check_trait_impl_conflicts`.
- [ ] Extract inherent shared-name checking into `_check_inherent_impl_conflicts`.
- [ ] Keep `add_impl` responsible only for orphan checking, bucket selection, conflict-helper
  dispatch, index creation, and registration.
- [ ] Run focused inherent and trait coherence tests, Ruff, basedpyright, and `git diff --check`.
- [ ] Request independent review and leave all changes unstaged for user review.
