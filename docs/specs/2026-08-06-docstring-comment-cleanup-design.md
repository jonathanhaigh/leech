<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Concise Docstrings and Comments Design

## Goal

Make docstrings and comments in `src/leech/` concise and durable, and ensure that Codex
and Claude apply the same standard when writing or reviewing future changes.

## Guidance

Refine `AGENTS.md` around a few judgment-based rules:

- Document an item's purpose, interface, surprising behavior, and non-obvious contracts.
- Do not describe callers, usage sites, implementation history, or neighboring components
  unless that information is essential to using the documented item.
- Prefer direct wording over cross-references when the referenced item is not needed to
  understand the interface.
- Omit information already apparent from names, signatures, annotations, or adjacent code.
- Give public items concise docstrings. Give non-public items docstrings only when they
  explain surprising behavior or a non-obvious contract.
- Use comments for non-obvious reasons and constraints, not to narrate visible code.
- Keep Sphinx-style `:pre:`, `:post:`, and `:invariant:` fields at the end of docstrings.

Add an explicit review rule requiring agents to inspect every added or changed docstring
and comment against this guidance. `CLAUDE.md` already imports `AGENTS.md`, so the rules
will have a single source of truth for both Claude and Codex. Enforcement will remain
agent-based; no linter or automated repository check will be added.

## Cleanup Scope

Review every Python file under `src/leech/`. Public items will generally retain concise,
useful docstrings. Non-public docstrings will be removed unless they record surprising
behavior or a non-obvious contract. Comments will be removed or shortened when they
repeat the code, identify callers, discuss unrelated items, or preserve incidental
implementation details.

The cleanup will not change runtime behavior, rename items, restructure code, or pursue
uniform wording for its own sake. Existing comments that record actionable work, explain
non-obvious reasoning, or document external constraints remain when useful.

## Isolation and Change Safety

All work will occur in the clean worktree at `/tmp/leech-docstrings` on branch
`docstring-comment-cleanup`, based on commit `5fe14ae`. The modified primary checkout will
not be changed, and none of its uncommitted work will be included.

Because documentation-only edits can still accidentally affect Python syntax, the final
diff will be inspected for semantic changes and the full validation suite will be run.

## Verification

Verification will include:

- Review the complete diff and confirm that only documentation and agent guidance changed.
- Search remaining docstrings and comments for caller lists, unnecessary cross-references,
  narration, and details coupled to other code.
- Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run basedpyright`,
  `uv run pytest`, and `uv run reuse lint`.

The sandbox's Snap installation of `uv` cannot run because Snap confinement is unavailable.
The clean baseline was therefore tested with the primary checkout's existing virtual
environment while setting `PYTHONPATH` to the clean worktree; all 954 tests passed. The
same isolated-path technique may be used for final verification if `uv` remains unavailable.
