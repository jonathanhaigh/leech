# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Name-resolution facts shared between type checking and CFG lowering.

TypCheck is the sole walker of ``ir_env.Env`` for body-level
code; it records what each reference resolves to here, once, so CfgBuilder
never needs to re-resolve it.
"""

import enum
from typing import TYPE_CHECKING, Final, Optional

from leech import ast, ir_values, typs

if TYPE_CHECKING:
    # Runtime import is local because ir_module imports typcheck, which
    # imports this module.
    from leech import ir_module, ir_traits

type LocalDecl = ast.Param | ast.Receiver | ast.LetStmt | ast.BindingPattern
"""A local binding's declaration-site AST node - its stable cross-phase identity."""

type VarTarget = (
    LocalDecl
    | ir_module.ModVar
    | ir_module.FnCandidate
    | ir_values.ComptimeEnum
    | typs.ValueParamTyp
)
"""A local, module value, function candidate, enum variant, or value parameter."""

type Callee = ir_traits.ImplFnSelection
"""A callable a dot-call's member lookup found."""

type CalleeTarget = Callee | ir_traits.TraitMethod
"""A dot-call's resolved member target."""


class CalleeResolution(enum.Enum):
    """``Resolutions.callee``'s non-callable outcome."""

    # TODO: Python 3.15 adds a `sentinel()` builtin that gives each
    # sentinel its own precisely-typeable singleton type - once leech
    # updates to 3.15, reconsider replacing this enum with such a sentinel
    # instead. Not available on the 3.14 this project targets now.

    FIELD_ACCESS = enum.auto()
    """Confirmed: no callable member of that name; fall back to plain field access."""


class Resolutions:
    """Name-resolution facts for one checked body, keyed by AST-node identity."""

    _vars: Final[dict[ast.VarExpr, VarTarget]]
    _callees: Final[dict[ast.CallExpr, CalleeTarget | CalleeResolution]]
    _loop_targets: Final[dict[ast.BreakStmt | ast.ContinueStmt, ast.WhileExpr]]

    def __init__(self) -> None:
        self._vars = {}
        self._callees = {}
        self._loop_targets = {}

    def var(self, node: ast.VarExpr) -> VarTarget:
        """Return what ``node`` resolves to."""
        return self._vars[node]

    def set_var(self, node: ast.VarExpr, target: VarTarget) -> None:
        """Record what ``node`` resolves to."""
        self._vars[node] = target

    def callee(self, node: ast.CallExpr) -> CalleeTarget | CalleeResolution:
        """Return what a dot-call's member lookup resolved to for ``node``."""
        return self._callees[node]

    def set_callee(self, node: ast.CallExpr, target: Optional[Callee]) -> None:
        """Record ``node``'s member-lookup result; ``None`` means confirmed field access."""
        self._callees[node] = CalleeResolution.FIELD_ACCESS if target is None else target

    def set_trait_bound_callee(self, node: ast.CallExpr, target: ir_traits.TraitMethod) -> None:
        """Record ``node``'s trait-bound member-lookup result."""
        self._callees[node] = target

    def loop_target(self, node: ast.BreakStmt | ast.ContinueStmt) -> ast.WhileExpr:
        """Return the ``while`` loop ``node`` targets."""
        return self._loop_targets[node]

    def set_loop_target(
        self, node: ast.BreakStmt | ast.ContinueStmt, target: ast.WhileExpr
    ) -> None:
        """Record the ``while`` loop ``node`` targets."""
        self._loop_targets[node] = target
