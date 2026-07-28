"""Type checking of Leech code, kept separate from lowering to LLVM IR.

:class:`TypCheck` mirrors :class:`~leech.ir_builder.CfgBuilder`'s ``build_*``
method structure one-for-one: as checking logic migrates out of
``CfgBuilder``, each ``check_*`` method here will take over the checks its
same-named ``build_*`` counterpart currently performs inline, recording
the result into a :class:`TypCheckResults` instead. ``CfgBuilder`` will
then read from that table rather than re-deriving the same facts during
lowering.

Only int-literal typing (and the peer-type resolution it depends on) is
genuinely checked and recorded so far - everything else below computes a
best-effort type, defensively (never raising past what's already a
legitimate, pre-existing diagnostic - e.g. an unresolvable name), just
enough to thread ``expected_typ`` correctly and support peer resolution.
``CfgBuilder`` still performs every other check unchanged; those will
migrate in later steps.
"""

from typing import Final, Optional

from leech import ast
from leech.ir_env import Env
from leech.ir_values import Param, Value
from leech.opt_util import opt_map, opt_or_default
from leech.errors import IntLitOverflowError
from leech.typs import (
    BOOL,
    CONST,
    CSTR,
    I32,
    NEVER,
    SIGNED,
    USIZE,
    VOID,
    ArrayTyp,
    CallableTyp,
    FnTyp,
    IntTyp,
    Mutability,
    PtrTyp,
    StructTyp,
    Typ,
)


def is_flexible_int_lit(expr_ast: ast.Expr) -> bool:
    """Whether an expression's type isn't decided by the expression itself.

    True only for an integer literal written without a type suffix, or
    the negation of one: those take their type from context (see
    :meth:`TypCheck._infer_int_lit_typ`), so where several expressions
    have to agree on a type, they're the ones that can adapt.

    :param expr_ast: The parsed expression to classify.
    :return: Whether the expression's type is still open.
    """
    if isinstance(expr_ast, ast.IntLit):
        return expr_ast.explicit_typ is None
    if isinstance(expr_ast, ast.UnaryOpExpr) and expr_ast.op.name == "-":
        return is_flexible_int_lit(expr_ast.operand)
    if isinstance(expr_ast, ast.BlockExpr):
        return (
            not expr_ast.stmts
            and expr_ast.expr is not None
            and is_flexible_int_lit(expr_ast.expr)
        )
    return False


def resolve_peer_typ(fixed_typs: list[Typ]) -> Typ:
    """The type a group of peer expressions should agree on.

    "Peers" are expressions that have to end up with one shared type -
    an array literal's elements, an ``if``/``else``'s two arms, an
    operator's two operands. ``fixed_typs`` are the types of the peers
    whose type the expression itself decides (everything that isn't a
    bare integer literal - see :func:`is_flexible_int_lit`), in source
    order.

    :param fixed_typs: The already-decided peers' types, in source order.
    :return: The type to check/lower the flexible peers with.
    """
    for typ in fixed_typs:
        if typ != NEVER:
            return typ
    return I32


def _callable_typ(typ: Typ) -> Optional[CallableTyp]:
    """``typ``, unwrapped to the type it's callable as, if it's one.

    :param typ: The type to check.
    :return: ``typ.pointee_typ`` if ``typ`` is a pointer to a callable
        type, otherwise ``None``.
    """
    if isinstance(typ, PtrTyp) and isinstance(typ.pointee_typ, CallableTyp):
        return typ.pointee_typ
    return None


def _struct_field_typ(typ: Typ, name: str) -> Optional[Typ]:
    """The type of ``typ``'s field called ``name``, if it has one.

    :param typ: The type to look ``name`` up on.
    :param name: The field name to look up.
    :return: The field's type, or ``None`` if ``typ`` isn't a struct type
        or has no field called ``name``.
    """
    if not isinstance(typ, StructTyp):
        return None
    return opt_map(typ.fields.get(name), lambda f: f.typ)


class _TypedPlace(Value[PtrTyp]):
    """Stands in for a local variable's address during type checking.

    :class:`TypCheck` doesn't lower anything, so it has no real alloca to
    bind a parameter or ``let`` name to - only the type such an alloca
    would have.

    :param typ: The type an alloca for this variable would have.
    """

    _typ: Final[PtrTyp]

    def __init__(self, typ: PtrTyp) -> None:
        super().__init__(None)
        self._typ = typ

    def calculate_typ(self) -> PtrTyp:
        return self._typ


class TypCheckResults:
    """The result of type-checking one function body or module-variable
    initializer.

    A side table of per-expression-node facts, keyed by AST node identity
    (plain object identity - :class:`~leech.ast.Ast` nodes don't override
    equality). Only int-literal types are recorded so far; more will be
    added as further checking logic migrates out of
    :class:`~leech.ir_builder.CfgBuilder`.
    """

    _int_lit_typs: Final[dict[ast.IntLit, IntTyp]]

    def __init__(self) -> None:
        self._int_lit_typs = {}

    def int_lit_typ(self, node: ast.IntLit) -> IntTyp:
        """The type chosen (and overflow-checked) for an integer literal.

        :param node: The literal's AST node, as checked by :class:`TypCheck`.
        :return: The literal's type.
        """
        return self._int_lit_typs[node]

    def _set_int_lit_typ(self, node: ast.IntLit, typ: IntTyp) -> None:
        self._int_lit_typs[node] = typ


class TypCheck:
    """Type-checks a function body or module-variable initializer.

    Walks the same shape of tree :class:`~leech.ir_builder.CfgBuilder`
    lowers, but only genuinely checks (and records into :attr:`results`)
    integer-literal typing so far; every other construct below computes a
    defensive, best-effort type just to keep that walk going correctly.
    """

    results: Final[TypCheckResults]
    _ret_typ: Optional[Typ]

    def __init__(self) -> None:
        self.results = TypCheckResults()
        self._ret_typ = None

    def check_fn(
        self, fn_ast: ast.FnDefn, e: Env, ret_typ: Typ, params: tuple[Param, ...]
    ) -> TypCheckResults:
        """Type-check a function body.

        :param fn_ast: The parsed function definition.
        :param e: The scope to resolve names in.
        :param ret_typ: The function's declared return type.
        :param params: The function's formal parameters.
        :return: :attr:`results`.
        """
        self._ret_typ = ret_typ
        e = e.new_child()
        for param in params:
            assert param.ast is not None
            e.add_var(param.ast.name.name, _TypedPlace(PtrTyp.get_or_create(param.typ, CONST)))
        self.check_expr(fn_ast.block, e, ret_typ)
        return self.results

    def check_var_initializer(self, defn_ast: ast.VarDefn, e: Env) -> TypCheckResults:
        """Type-check a module-level ``let`` initializer expression.

        :param defn_ast: The parsed variable declaration.
        :param e: The scope to resolve names in.
        :return: :attr:`results`.
        """
        self._ret_typ = None
        e = e.new_child()
        let_ast = defn_ast.let_stmt
        declared_typ = opt_map(let_ast.typ, lambda t: Typ.from_ast(t, e))
        self.check_expr(let_ast.expr, e, declared_typ)
        return self.results

    def check_expr(
        self, expr_ast: ast.Expr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        """Check any expression, dispatching to the ``check_*_expr`` method
        matching its AST node kind.

        :param expr_ast: The parsed expression.
        :param e: The scope to resolve names in.
        :param expected_typ: The type ``expr_ast`` is expected to have, if
            known; see :meth:`~leech.ir_builder.CfgBuilder.build_expr`.
        :return: ``expr_ast``'s type.
        """
        match expr_ast:
            case ast.BlockExpr():
                return self.check_block_expr(expr_ast, e, expected_typ)
            case ast.IfExpr():
                return self.check_if_expr(expr_ast, e, expected_typ)
            case ast.WhileExpr():
                return self.check_while_expr(expr_ast, e)
            case ast.CallExpr():
                return self.check_call_expr(expr_ast, e)
            case ast.BinOpExpr():
                return self.check_bin_op_expr(expr_ast, e, expected_typ)
            case ast.UnaryOpExpr():
                return self.check_unary_op_expr(expr_ast, e, expected_typ)
            case ast.StrLit():
                return CSTR
            case ast.IntLit():
                typ = self._infer_int_lit_typ(expr_ast, expected_typ)
                if not typ.fits(expr_ast.value):
                    raise IntLitOverflowError(expr_ast.value, typ.name, expr_ast.span)
                self.results._set_int_lit_typ(expr_ast, typ)
                return typ
            case ast.BoolLit():
                return BOOL
            case ast.VarExpr():
                return self.check_var_expr(expr_ast, e)
            case ast.ArrayExpr():
                return self.check_array_expr(expr_ast, e, expected_typ)
            case ast.ArrayAccessExpr():
                return self.check_array_access_expr(expr_ast, e)
            case ast.StructExpr():
                return self.check_struct_expr(expr_ast, e)
            case ast.StructAccessExpr():
                return self.check_struct_access_expr(expr_ast, e)
            case ast.DerefExpr():
                return self.check_deref_expr(expr_ast, e)
            case _:
                assert False, f"unhandled expression kind {expr_ast}"

    def check_block_expr(
        self, block_ast: ast.BlockExpr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        e = e.new_child()
        diverged = False
        for stmt_ast in block_ast.stmts:
            if self.check_stmt(stmt_ast, e):
                diverged = True
        if block_ast.expr is None:
            return NEVER if diverged else VOID
        return self.check_expr(block_ast.expr, e, expected_typ)

    def check_if_expr(
        self, if_ast: ast.IfExpr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        self.check_expr(if_ast.condition, e, None)

        if if_ast.els is None:
            self.check_expr(if_ast.then, e, expected_typ)
            return VOID

        els_ast = if_ast.els
        if (
            expected_typ is None
            and is_flexible_int_lit(if_ast.then)
            and not is_flexible_int_lit(els_ast)
        ):
            els_typ = self.check_expr(els_ast, e, expected_typ)
            then_hint = expected_typ
            if els_typ != NEVER:
                then_hint = resolve_peer_typ([els_typ])
            then_typ = self.check_expr(if_ast.then, e, then_hint)
        else:
            then_typ = self.check_expr(if_ast.then, e, expected_typ)
            els_hint = expected_typ
            if (
                expected_typ is None
                and then_typ != NEVER
                and is_flexible_int_lit(els_ast)
            ):
                els_hint = resolve_peer_typ([then_typ])
            els_typ = self.check_expr(els_ast, e, els_hint)

        if then_typ != NEVER:
            return then_typ
        if els_typ != NEVER:
            return els_typ
        return NEVER

    def check_while_expr(self, while_ast: ast.WhileExpr, e: Env) -> Typ:
        self.check_expr(while_ast.condition, e, None)
        self.check_expr(while_ast.block, e, None)
        return VOID

    def check_call_expr(self, call_ast: ast.CallExpr, e: Env) -> Typ:
        fn_typ, recv_typ = self._resolve_callee(call_ast.callee, e)

        param_typs = fn_typ.param_typs if fn_typ is not None else ()
        offset = 1 if recv_typ is not None else 0
        for i, arg_ast in enumerate(call_ast.args, start=offset):
            arg_hint = param_typs[i] if i < len(param_typs) else None
            self.check_expr(arg_ast, e, arg_hint)

        return fn_typ.ret_typ if fn_typ is not None else VOID

    def _resolve_callee(
        self, callee_ast: ast.Expr, e: Env
    ) -> tuple[Optional[CallableTyp], Optional[Typ]]:
        """The callee's type, and its receiver's type if it's a method call.

        If ``callee_ast`` is written as ``x.name``, ``name`` is looked up
        as a method of ``x``'s struct type first; otherwise (and if
        that lookup fails) it falls back to ordinary field access, exactly
        as :meth:`~leech.ir_builder.CfgBuilder.build_call_expr` does.

        :param callee_ast: The parsed callee expression.
        :param e: The scope to resolve names in.
        :return: ``(fn_typ, recv_typ)``. ``fn_typ`` is ``None`` if the
            callee doesn't resolve to a callable type. ``recv_typ`` is
            ``None`` unless ``callee_ast`` names a method, in which case
            it's that method's implicit receiver's type.
        """
        if not isinstance(callee_ast, ast.StructAccessExpr):
            return _callable_typ(self.check_expr(callee_ast, e, None)), None

        struct_typ = self.check_expr(callee_ast.struct, e, None)
        method = (
            struct_typ.get_assoc_fn(callee_ast.field.name)
            if isinstance(struct_typ, StructTyp)
            else None
        )
        if (
            method is not None
            and method.ast is not None
            and method.ast.receiver is not None
        ):
            return method.fn_typ, struct_typ

        field_typ = _struct_field_typ(struct_typ, callee_ast.field.name)
        return opt_map(field_typ, _callable_typ), None

    def check_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        if op_ast.op.name in ("and", "or"):
            return self.check_logic_bin_op_expr(op_ast, e)

        if is_flexible_int_lit(op_ast.lhs) and not is_flexible_int_lit(op_ast.rhs):
            rhs_typ = self.check_expr(op_ast.rhs, e, None)
            lhs_typ = self.check_expr(op_ast.lhs, e, resolve_peer_typ([rhs_typ]))
        else:
            lhs_hint = None
            if is_flexible_int_lit(op_ast.lhs):
                lhs_hint = expected_typ
            lhs_typ = self.check_expr(op_ast.lhs, e, lhs_hint)

            rhs_hint = None
            if is_flexible_int_lit(op_ast.rhs):
                rhs_hint = resolve_peer_typ([lhs_typ])
            rhs_typ = self.check_expr(op_ast.rhs, e, rhs_hint)

        if op_ast.op.name in ("<", "<=", "==", "!=", ">=", ">"):
            return BOOL
        return lhs_typ

    def check_logic_bin_op_expr(self, op_ast: ast.BinOpExpr, e: Env) -> Typ:
        self.check_expr(op_ast.lhs, e, None)
        self.check_expr(op_ast.rhs, e, None)
        return BOOL

    def check_unary_op_expr(
        self, op_ast: ast.UnaryOpExpr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        match op_ast.op.name:
            case "&":
                return PtrTyp.get_or_create(
                    self.check_expr(op_ast.operand, e, None), CONST
                )
            case "not":
                self.check_expr(op_ast.operand, e, None)
                return BOOL
            case "-":
                if isinstance(op_ast.operand, ast.IntLit):
                    typ = self._infer_int_lit_typ(op_ast.operand, expected_typ)
                    # A wrong-signedness operand is InvalidUnaryOpArgTypError's
                    # concern, not this checker's yet - matching the order
                    # CfgBuilder still checks in, that error preempts an
                    # overflow check on the negated value ever running.
                    if typ.signage == SIGNED:
                        value = -op_ast.operand.value
                        if not typ.fits(value):
                            raise IntLitOverflowError(value, typ.name, op_ast.span)
                    self.results._set_int_lit_typ(op_ast.operand, typ)
                    return typ
                return self.check_expr(op_ast.operand, e, expected_typ)
            case _:
                assert False, f"unhandled unary operator {op_ast.op.name!r}"

    def check_var_expr(self, var_ast: ast.VarExpr, e: Env) -> Typ:
        from leech import ir_module

        var = e.resolve_path(Env.Namespace.VARS, var_ast.path)
        if isinstance(var, ir_module.FnSpec):
            return var.typ
        return var.typ.pointee_typ

    def check_array_expr(
        self, arr_expr: ast.ArrayExpr, e: Env, expected_typ: Optional[Typ]
    ) -> Typ:
        expected_elt_typ = (
            expected_typ.element_typ if isinstance(expected_typ, ArrayTyp) else None
        )

        built: list[Optional[Typ]] = [None] * len(arr_expr.elements)
        for i, elt_ast in enumerate(arr_expr.elements):
            if not is_flexible_int_lit(elt_ast):
                built[i] = self.check_expr(elt_ast, e, expected_elt_typ)

        if expected_elt_typ is not None:
            elt_typ = expected_elt_typ
        else:
            elt_typ = resolve_peer_typ([t for t in built if t is not None])

        for i, t in enumerate(built):
            if t is None:
                self.check_expr(arr_expr.elements[i], e, elt_typ)

        if not arr_expr.elements:
            return ArrayTyp.get_or_create(opt_or_default(expected_elt_typ, VOID), 0)
        return ArrayTyp.get_or_create(elt_typ, len(arr_expr.elements))

    def check_array_access_expr(self, aa_expr: ast.ArrayAccessExpr, e: Env) -> Typ:
        arr_typ = self.check_expr(aa_expr.array, e, None)
        self.check_expr(aa_expr.index, e, USIZE)
        if isinstance(arr_typ, ArrayTyp):
            return arr_typ.element_typ
        return VOID

    def check_struct_expr(self, struct_expr: ast.StructExpr, e: Env) -> Typ:
        struct_typ = Typ.from_ast(struct_expr.typ, e)
        for field_expr in struct_expr.fields:
            field_typ = _struct_field_typ(struct_typ, field_expr.ident.name)
            self.check_expr(field_expr.value, e, field_typ)
        return struct_typ

    def check_struct_access_expr(self, sa_expr: ast.StructAccessExpr, e: Env) -> Typ:
        struct_typ = self.check_expr(sa_expr.struct, e, None)
        return opt_or_default(_struct_field_typ(struct_typ, sa_expr.field.name), VOID)

    def check_deref_expr(self, d_expr: ast.DerefExpr, e: Env) -> Typ:
        ptr_typ = self.check_expr(d_expr.ptr, e, None)
        if isinstance(ptr_typ, PtrTyp) and not isinstance(ptr_typ.pointee_typ, FnTyp):
            return ptr_typ.pointee_typ
        return VOID

    def check_stmt(self, stmt_ast: ast.Stmt, e: Env) -> bool:
        """Check any statement, dispatching to the ``check_*_stmt`` method
        matching its AST node kind.

        :param stmt_ast: The parsed statement.
        :param e: The scope to resolve names in.
        :return: Whether control can never fall through past ``stmt_ast``.
        """
        match stmt_ast:
            case ast.ExprStmt():
                return self.check_expr(stmt_ast.expr, e, None) == NEVER
            case ast.RetStmt():
                self.check_ret_stmt(stmt_ast, e)
                return True
            case ast.LetStmt():
                return self.check_let_stmt(stmt_ast, e)
            case ast.AssignmentStmt():
                return self.check_assignment_stmt(stmt_ast, e)
            case ast.BreakStmt():
                return True
            case ast.ContinueStmt():
                return True
            case _:
                assert False, f"unhandled statement kind {stmt_ast}"

    def check_ret_stmt(self, ret_ast: ast.RetStmt, e: Env) -> None:
        if ret_ast.expr is not None:
            self.check_expr(ret_ast.expr, e, self._ret_typ)

    def check_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> bool:
        declared_typ = opt_map(let_ast.typ, lambda t: Typ.from_ast(t, e))
        expr_typ = self.check_expr(let_ast.expr, e, declared_typ)
        bound_typ = opt_or_default(declared_typ, expr_typ)
        mut = Mutability.from_ast(let_ast.mut)
        e.add_var(let_ast.ident.name, _TypedPlace(PtrTyp.get_or_create(bound_typ, mut)))
        return expr_typ == NEVER

    def check_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> bool:
        place_typ = self.check_expr(ass_ast.place, e, None)
        expr_typ = self.check_expr(ass_ast.expr, e, place_typ)
        return place_typ == NEVER or expr_typ == NEVER

    def _infer_int_lit_typ(
        self, lit_ast: ast.IntLit, expected_typ: Optional[Typ]
    ) -> IntTyp:
        """Choose an integer literal's type.

        A literal written with a type suffix always has that type. One
        written without takes the type the surrounding context expects,
        if that's an integer type, and falls back to ``i32`` otherwise.

        :param lit_ast: The parsed integer literal.
        :param expected_typ: The type the context expects, if known.
        :return: The type to give the literal.
        """
        if lit_ast.explicit_typ is not None:
            return lit_ast.explicit_typ
        if isinstance(expected_typ, IntTyp):
            return expected_typ
        return I32
