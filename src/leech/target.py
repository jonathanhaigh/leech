# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Properties of the compilation target."""

import functools
from typing import Final

from llvmlite import binding as llb

ADDR_SIZE = 64
"""Bit width of a pointer, and of ``usize``/``isize``, on the target platform."""

TRIPLE: Final[str] = "x86_64-linux-gnu"
"""LLVM target triple this compiler emits code for."""

DATALAYOUT: Final[str] = (
    "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"
)
"""LLVM datalayout string for ``TRIPLE``.

Hardcoded rather than queried from llvmlite at startup: derived by hand from
``llvmlite.binding.Target.from_triple(TRIPLE).create_target_machine().target_data`` under
llvmlite 0.47.0 (the version pinned in ``pyproject.toml``). Re-derive and re-verify this
against ``test_size_of_runtime_padded_typs`` in ``tests/test_builtins.py`` by hand if
``TRIPLE`` or the installed llvmlite/LLVM version ever changes.
"""


@functools.cache
def target_data() -> llb.TargetData:
    """Return (building once, on first use) the ABI layout rules for ``TRIPLE``."""
    return llb.create_target_data(DATALAYOUT)
