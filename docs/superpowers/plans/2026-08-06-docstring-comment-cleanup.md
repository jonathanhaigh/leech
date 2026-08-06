<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Concise Docstrings and Comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make documentation throughout `src/leech/` concise and durable, and require
Codex and Claude reviews to enforce the same standard.

**Architecture:** Keep agent guidance in `AGENTS.md`, which `CLAUDE.md` already imports,
and apply a judgment-based documentation audit to every Python module in `src/leech/`.
The cleanup changes documentation only: it does not introduce tooling, alter interfaces,
or change compiler behavior.

**Tech Stack:** Markdown, Python 3.14, Sphinx-style docstrings, Ruff, basedpyright, pytest,
and REUSE.

## Global Constraints

- Work only in the clean `docstring-comment-cleanup` worktree and preserve the primary
  checkout's uncommitted changes.
- Review every Python file directly under `src/leech/`.
- Public items generally retain concise docstrings.
- Non-public items have docstrings only for surprising behavior or non-obvious contracts.
- Docstrings describe the documented item's purpose, interface, surprising behavior, and
  non-obvious contracts—not callers, usage sites, history, or neighboring components.
- Omit details already apparent from names, signatures, annotations, or adjacent code.
- Comments explain non-obvious reasons and constraints rather than narrating code.
- Keep Sphinx-style `:pre:`, `:post:`, and `:invariant:` fields last.
- Do not add automated documentation checks or change runtime behavior.

---

### Task 1: Agent Writing and Review Guidance

**Files:**
- Modify: `AGENTS.md`
- Verify unchanged import: `CLAUDE.md`

**Interfaces:**
- Consumes: The documentation principles in the approved design.
- Produces: The single source of writing and review rules used by Codex and Claude.

- [ ] **Step 1: Rewrite the documentation guidance**

  In `Coding Style & Naming Conventions`, replace the current compact documentation
  paragraph with rules that state every Global Constraint concerning docstrings and
  comments. Keep the wording concise and normative. Retain the existing Sphinx contract
  convention and the surrounding Python style guidance.

- [ ] **Step 2: Add an explicit agent-review requirement**

  Add a short `Review Guidelines` section requiring agents to inspect every added or
  changed docstring and comment against the writing guidance during code review. State
  that reviewers should flag unnecessary private docstrings, repetition, caller lists,
  excessive cross-references, narration, and details coupled to other code.

- [ ] **Step 3: Verify the shared guidance path**

  Run: `git diff -- AGENTS.md CLAUDE.md`

  Expected: `AGENTS.md` contains both writing and review rules; `CLAUDE.md` remains
  `!include AGENTS.md`, with no duplicated rules.

- [ ] **Step 4: Check Markdown and licensing**

  Run: `uv run reuse lint`

  Expected: PASS, including the existing SPDX header in `AGENTS.md`.

- [ ] **Step 5: Commit the guidance**

  ```bash
  git add AGENTS.md
  git commit -m "Clarify documentation review guidance"
  ```

### Task 2: Small Compiler Modules

**Files:**
- Modify as needed: `src/leech/__init__.py`
- Modify as needed: `src/leech/asserts.py`
- Modify as needed: `src/leech/driver.py`
- Modify as needed: `src/leech/naming.py`
- Modify as needed: `src/leech/opt_util.py`
- Modify as needed: `src/leech/parse.py`
- Modify as needed: `src/leech/signage.py`
- Modify as needed: `src/leech/src.py`
- Modify as needed: `src/leech/target.py`

**Interfaces:**
- Consumes: The rules added to `AGENTS.md` in Task 1.
- Produces: Concise documentation for package setup, assertions, driver, parsing, source,
  target, naming, optional-value, and signedness helpers.

- [ ] **Step 1: Audit every listed module from top to bottom**

  For each module, inspect module, class, function, method, attribute, and inline
  documentation. Shorten public docstrings to information needed to use the item. Remove
  private docstrings that merely restate names or signatures. Preserve private
  documentation only when it records surprising behavior or a contract.

- [ ] **Step 2: Audit comments separately**

  Remove narration and stale descriptions of callers or neighboring components. Preserve
  SPDX headers, actionable work notes, external constraints, and explanations of non-obvious
  choices. Tighten preserved comments without changing their meaning.

- [ ] **Step 3: Inspect the documentation-only diff**

  Run:

  ```bash
  git diff --word-diff=plain -- src/leech/__init__.py src/leech/asserts.py \
    src/leech/driver.py src/leech/naming.py src/leech/opt_util.py src/leech/parse.py \
    src/leech/signage.py src/leech/src.py src/leech/target.py
  ```

  Expected: Changes are confined to docstrings and comments; Python tokens and behavior
  are unchanged.

- [ ] **Step 4: Run focused validation**

  Run:

  ```bash
  uv run ruff check src/leech/__init__.py src/leech/asserts.py src/leech/driver.py \
    src/leech/naming.py src/leech/opt_util.py src/leech/parse.py src/leech/signage.py \
    src/leech/src.py src/leech/target.py
  uv run pytest tests/test_cli.py tests/test_parsing.py tests/test_errors.py
  ```

  Expected: Both commands PASS.

- [ ] **Step 5: Commit the small-module cleanup**

  ```bash
  git add src/leech/__init__.py src/leech/asserts.py src/leech/driver.py \
    src/leech/naming.py src/leech/opt_util.py src/leech/parse.py src/leech/signage.py \
    src/leech/src.py src/leech/target.py
  git commit -m "Tighten small-module documentation"
  ```

### Task 3: Syntax, Types, and Diagnostics

**Files:**
- Modify: `src/leech/ast.py`
- Modify: `src/leech/errors.py`
- Modify: `src/leech/typs.py`

**Interfaces:**
- Consumes: The rules added to `AGENTS.md` in Task 1.
- Produces: Concise documentation for syntax nodes, diagnostics, and semantic types.

- [ ] **Step 1: Audit `ast.py`**

  Retain concise descriptions of public syntax-node shapes and genuinely non-obvious
  construction contracts. Remove redundant examples, class cross-references, parse-tree
  narration, and private factory docstrings when names, signatures, and dispatch code are
  sufficient.

- [ ] **Step 2: Audit `errors.py`**

  Keep each public diagnostic's triggering condition understandable without enumerating
  the compiler passes or call sites that raise it. Remove constructor and rendering
  docstrings that repeat signatures or nearby code; retain non-obvious severity,
  aggregation, location, and formatting contracts.

- [ ] **Step 3: Audit `typs.py`**

  Describe public type concepts and non-obvious equivalence, substitution, layout, or
  inference contracts directly. Remove caller-oriented explanations and cross-references
  that are unnecessary to use the documented item. Apply the same standard to comments.

- [ ] **Step 4: Inspect the documentation-only diff**

  Run: `git diff --word-diff=plain -- src/leech/ast.py src/leech/errors.py src/leech/typs.py`

  Expected: Changes are confined to docstrings and comments; definitions, expressions,
  annotations, and diagnostics text are unchanged.

- [ ] **Step 5: Run focused validation**

  Run:

  ```bash
  uv run ruff check src/leech/ast.py src/leech/errors.py src/leech/typs.py
  uv run pytest tests/test_ast.py tests/test_errors.py tests/test_typs.py
  ```

  Expected: Both commands PASS.

- [ ] **Step 6: Commit the syntax and type cleanup**

  ```bash
  git add src/leech/ast.py src/leech/errors.py src/leech/typs.py
  git commit -m "Tighten syntax and type documentation"
  ```

### Task 4: Semantic Analysis and Compile-Time Evaluation

**Files:**
- Modify: `src/leech/comptime.py`
- Modify: `src/leech/typcheck.py`

**Interfaces:**
- Consumes: The rules added to `AGENTS.md` in Task 1 and the terminology retained in
  `ast.py`, `errors.py`, and `typs.py` by Task 3.
- Produces: Concise documentation for compile-time evaluation and type checking.

- [ ] **Step 1: Audit `comptime.py`**

  Keep non-obvious evaluation, memory, pointer, and failure contracts. Remove docstrings
  and comments that list callers, repeat control flow, or describe another component more
  than is needed to understand the documented evaluator item.

- [ ] **Step 2: Audit `typcheck.py`**

  Keep public semantic contracts and explanations of surprising inference, coercion,
  scope, and control-flow decisions. Remove private docstrings that narrate implementation
  steps, repeat types, or catalog use sites. Tighten comments to the reason a constraint
  exists.

- [ ] **Step 3: Inspect the documentation-only diff**

  Run: `git diff --word-diff=plain -- src/leech/comptime.py src/leech/typcheck.py`

  Expected: Changes are confined to docstrings and comments; executable Python and user
  diagnostics are unchanged.

- [ ] **Step 4: Run focused validation**

  Run:

  ```bash
  uv run ruff check src/leech/comptime.py src/leech/typcheck.py
  uv run pytest tests/test_coercions.py tests/test_peer_typs.py tests/test_traits.py
  ```

  Expected: Both commands PASS.

- [ ] **Step 5: Commit the semantic-analysis cleanup**

  ```bash
  git add src/leech/comptime.py src/leech/typcheck.py
  git commit -m "Tighten semantic analysis documentation"
  ```

### Task 5: IR and LLVM Generation

**Files:**
- Modify: `src/leech/codegen.py`
- Modify: `src/leech/ir_builder.py`
- Modify: `src/leech/ir_builtins.py`
- Modify: `src/leech/ir_env.py`
- Modify: `src/leech/ir_loader.py`
- Modify: `src/leech/ir_module.py`
- Modify: `src/leech/ir_traits.py`
- Modify: `src/leech/ir_values.py`

**Interfaces:**
- Consumes: The rules added to `AGENTS.md` in Task 1 and terminology retained by Tasks 3
  and 4.
- Produces: Concise documentation for lowering, IR construction, loading, traits, values,
  builtins, environments, modules, and LLVM output.

- [ ] **Step 1: Audit `codegen.py`, `ir_builder.py`, and `ir_builtins.py`**

  Keep public lowering contracts and surprising LLVM constraints. Remove descriptions of
  call sites, duplicated LLVM operation summaries, and comments that merely narrate the
  emitted instruction sequence. Retain explanations for unintuitive ordering, ABI, and
  safety decisions.

- [ ] **Step 2: Audit `ir_env.py`, `ir_loader.py`, and `ir_module.py`**

  Keep public lookup, loading, ownership, initialization, and module-boundary contracts.
  Remove private documentation that lists consumers or repeats collection operations and
  type annotations. Keep comments that explain cycle handling or other non-obvious state.

- [ ] **Step 3: Audit `ir_traits.py` and `ir_values.py`**

  Keep public trait-resolution and IR-value semantics plus non-obvious invariants. Remove
  cross-references used only as navigation, caller lists, and descriptions apparent from
  class layout or dispatch code.

- [ ] **Step 4: Inspect the documentation-only diff**

  Run:

  ```bash
  git diff --word-diff=plain -- src/leech/codegen.py src/leech/ir_builder.py \
    src/leech/ir_builtins.py src/leech/ir_env.py src/leech/ir_loader.py \
    src/leech/ir_module.py src/leech/ir_traits.py src/leech/ir_values.py
  ```

  Expected: Changes are confined to docstrings and comments; executable Python, LLVM
  fragments, and diagnostics are unchanged.

- [ ] **Step 5: Run focused validation**

  Run:

  ```bash
  uv run ruff check src/leech/codegen.py src/leech/ir_builder.py \
    src/leech/ir_builtins.py src/leech/ir_env.py src/leech/ir_loader.py \
    src/leech/ir_module.py src/leech/ir_traits.py src/leech/ir_values.py
  uv run pytest tests/test_builtins.py tests/test_loader.py tests/test_methods.py \
    tests/test_generic_fns.py tests/test_generic_structs.py
  ```

  Expected: Both commands PASS.

- [ ] **Step 6: Commit the IR cleanup**

  ```bash
  git add src/leech/codegen.py src/leech/ir_builder.py src/leech/ir_builtins.py \
    src/leech/ir_env.py src/leech/ir_loader.py src/leech/ir_module.py \
    src/leech/ir_traits.py src/leech/ir_values.py
  git commit -m "Tighten IR documentation"
  ```

### Task 6: Repository-Wide Documentation Audit and Verification

**Files:**
- Review: `AGENTS.md`
- Review: `CLAUDE.md`
- Review: `src/leech/*.py`

**Interfaces:**
- Consumes: All documentation cleanup tasks.
- Produces: A verified, documentation-only branch covering every Python module in scope.

- [ ] **Step 1: Confirm complete module coverage**

  Run:

  ```bash
  git ls-files 'src/leech/*.py'
  git diff 5fe14ae --name-only
  ```

  Compare the tracked-module list with Tasks 2–5. Every Python module must have been
  explicitly reviewed even if it required no edit. The changed-file list may omit modules
  whose existing documentation already met the guidance.

- [ ] **Step 2: Search for likely fragile documentation**

  Run:

  ```bash
  rg -n ':class:|:func:|:meth:|called by|used by|caller|call site|invoked by' src/leech \
    --glob '*.py'
  rg -n '^\s*#' src/leech --glob '*.py'
  ```

  Review every match manually. Remove or rewrite it only when it violates `AGENTS.md`;
  cross-references and caller information that are essential to using an item may remain.

- [ ] **Step 3: Review all changed documentation in context**

  Run:

  ```bash
  git diff 5fe14ae --check
  git diff 5fe14ae --word-diff=plain -- AGENTS.md src/leech
  ```

  Expected: No whitespace errors. Every deletion preserves necessary interface and
  contract information, every retained private docstring earns its place, and no
  executable token or user-facing diagnostic changed.

- [ ] **Step 4: Run formatting and static checks**

  Run:

  ```bash
  uv run ruff format --check .
  uv run ruff check .
  uv run basedpyright
  uv run reuse lint
  ```

  Expected: All four commands PASS.

- [ ] **Step 5: Run the full test suite**

  Run: `uv run pytest`

  Expected: PASS with 954 tests or the current branch's complete collected test count.

  If Snap confinement prevents `uv` from running, use the existing virtual environment
  without installing into or editing the primary checkout:

  ```bash
  env PYTHONPATH=/tmp/leech-docstrings/src \
    PATH=/home/jonathan/work/l0/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    /home/jonathan/work/l0/.venv/bin/pytest
  ```

- [ ] **Step 6: Commit any final audit corrections**

  If Step 2 or Step 3 produced edits, stage only `AGENTS.md` and the reviewed
  `src/leech/*.py` files, then commit:

  ```bash
  git add AGENTS.md src/leech/*.py
  git commit -m "Finish documentation concision audit"
  ```

  If the worktree is already clean, do not create an empty commit.
