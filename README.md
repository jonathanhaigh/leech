<!--
SPDX-FileCopyrightText: 2026 Jonathan Haigh

SPDX-License-Identifier: MPL-2.0
-->

# Leech

Leech is a systems programming language. This repository contains its compiler, `leechc`,
which is written in Python and compiles one Leech module at a time to LLVM IR for x86-64
Linux. A build tool, `leech`, that builds and runs whole programs is planned.

## Install

`leechc` is installed from a source checkout as a [uv](https://docs.astral.sh/uv/) tool, from
the Python package named `leech`. uv downloads Python 3.14 if it is not already available.
From the repository root:

```bash
uv tool install .                # install leechc into an isolated tool environment
uv tool install --editable .     # or: track the checkout, so source edits apply immediately
uv tool install --reinstall .    # pick up changes in a non-editable install
uv tool uninstall leech          # remove it again (by package name)
```

If uv reports that its tool directory is not on `PATH`, run `uv tool update-shell`.

`uv build` writes a wheel and a source distribution to `dist/`. A wheel installs the same
way, with `uv tool install dist/leech-<version>-py3-none-any.whl`. The package declares the
`Private :: Do Not Upload` classifier, so package indexes such as PyPI reject it. The
package is deliberately not published to any registry yet.

Inside the checkout, `uv run leechc` works without installing anything.
