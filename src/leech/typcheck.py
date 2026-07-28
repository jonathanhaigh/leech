"""Type checking of Leech code, kept separate from lowering to LLVM IR.

:class:`TypCheck` mirrors :class:`~leech.ir_builder.CfgBuilder`'s ``build_*``
method structure one-for-one: as checking logic migrates out of
``CfgBuilder``, each ``check_*`` method here will take over the checks its
same-named ``build_*`` counterpart currently performs inline, recording
the result into a :class:`TypCheckResults` instead. ``CfgBuilder`` will
then read from that table rather than re-deriving the same facts during
lowering.
"""

from typing import Final

from leech import ast
from leech.ir_env import Env


class TypCheckResults:
    """The result of type-checking one function body or module-variable
    initializer.

    A side table of per-expression-node facts, keyed by AST node identity
    (plain object identity - :class:`~leech.ast.Ast` nodes don't override
    equality). Empty for now; populated incrementally as checking logic
    migrates out of :class:`~leech.ir_builder.CfgBuilder`.
    """


class TypCheck:
    """Type-checks a function body or module-variable initializer.

    Doesn't check anything yet - :attr:`results` is unconditionally empty.
    """

    results: Final[TypCheckResults]

    def __init__(self) -> None:
        self.results = TypCheckResults()

    def check_fn(self, fn_ast: ast.FnDefn, e: Env) -> TypCheckResults:
        """Type-check a function body.

        :param fn_ast: The parsed function definition.
        :param e: The scope to resolve names in.
        :return: :attr:`results`.
        """
        return self.results

    def check_var_initializer(self, defn_ast: ast.VarDefn, e: Env) -> TypCheckResults:
        """Type-check a module-level ``let`` initializer expression.

        :param defn_ast: The parsed variable declaration.
        :param e: The scope to resolve names in.
        :return: :attr:`results`.
        """
        return self.results
