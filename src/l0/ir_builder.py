"""AST-to-IR lowering, with type checking performed inline as it goes."""

import enum
from typing import Final, Optional

from l0 import ir_module
from l0 import l0ast as ast
from l0.asserts import assert_in, checked_cast
from l0.ir_env import Env
from l0.ir_values import (
    BasicBlock,
    Cfg,
    ComptimeArray,
    ComptimeBool,
    ComptimeCStr,
    ComptimeInt,
    ComptimeStruct,
    NeverValue,
    UndefValue,
    Value,
    VoidValue,
)
from l0.l0errors import (
    AssignToConstError,
    DerefInvalidTypError,
    DuplicateFieldInStructExprError,
    EmptyArrayTypUnknownError,
    FieldAccessIntoInvalidTypError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleAssignmentTypError,
    IncompatibleBinOpArgTypsError,
    IncompatibleStructFieldTypError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    IntLitOverflowError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
    InvalidRetTypError,
    InvalidStructFieldError,
    InvalidVoidRetError,
    MissingFieldInStructExprError,
    MissingRetError,
    NotAMethodError,
    NotCallableError,
    NotEnoughArgsError,
    PrivateItemAccessError,
    PrivateStructFieldAccessError,
    RetNotInFnError,
    TooManyArgsError,
    TypeOfStructExprNotStructError,
    VoidArrayElementError,
    VoidVarInitializerError,
    WhileCondNotBoolError,
    WhileTypNotVoidError,
)
from l0.naming import VarNamer
from l0.opt_util import opt_map, opt_or_default
from l0.typs import (
    BOOL,
    CONST,
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


class ExprContext(enum.Enum):
    """Whether an expression is being lowered for its value or its address.

    An expression built in :attr:`PLACE` context yields a pointer to
    where the value lives (e.g. the target of an assignment or the
    operand of ``&``), rather than the value itself.
    """

    PLACE = 0
    VALUE = 1


class CfgBuilder:
    """Lowers a single function body or module-variable initializer to a
    :class:`~l0.ir_values.Cfg`.

    Each ``build_*`` method lowers one AST node kind, performing the
    relevant type checks (raising the corresponding
    :mod:`~l0.l0errors` error on failure) and emitting instructions into
    :attr:`curr_bb` as it goes.

    :param fn: The function whose body is being lowered, or ``None`` when
        lowering a module-variable initializer (which isn't part of any
        function).
    """

    fn: Optional[ir_module.FnSpec]
    cfg: Final[Cfg]
    curr_bb: BasicBlock
    _generate_bb_name: Final[VarNamer]

    def __init__(self, fn: Optional[ir_module.FnSpec] = None) -> None:
        self.fn = fn
        self.cfg = Cfg()
        self._generate_bb_name = VarNamer()
        self.cfg._entry = self.add_bb("entry")
        self.cfg._exit = self.add_bb("exit")
        self.curr_bb = self.cfg.entry

    def build_var_initializer(self, defn_ast: ast.VarDefn, e: Env) -> None:
        """Lower a module-level ``let`` initializer expression into :attr:`cfg`.

        :param defn_ast: The parsed variable declaration.
        :param e: The scope to resolve names in.
        :raises VoidVarInitializerError: If the initializer expression has
            type ``void``.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the initializer expression; see
            :meth:`build_expr`.
        """
        assert self.fn is None
        e = e.new_child()
        initializer = self.build_expr(defn_ast.let_stmt.expr, e, ExprContext.VALUE)
        if initializer.typ == VOID:
            raise VoidVarInitializerError(initializer.span)
        self.ret(initializer, defn_ast.let_stmt.expr)

    def build_fn(self, fn_ast: ast.FnDefn, e: Env) -> None:
        """Lower a function body into :attr:`cfg`.

        :param fn_ast: The parsed function definition.
        :param e: The scope to resolve names in.
        :raises InvalidRetTypError: If the body's tail expression's type
            doesn't match the function's declared return type.
        :raises MissingRetError: If the function's declared return type
            isn't ``void`` but the body doesn't end in a return or a
            diverging expression.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the body; see :meth:`build_expr`.
        """
        assert self.fn is not None
        e = e.new_child()

        for param in self.fn.params:
            assert param.ast is not None
            alloca = self.curr_bb.alloca(param.typ, CONST, 1, param.ast)
            self.curr_bb.store(param, alloca, param.ast)
            e.add_var(param.ast.name.name, alloca)

        block = self.build_expr(fn_ast.block, e, ExprContext.VALUE)
        ret_typ = self.fn.fn_typ.ret_typ
        ret_typ_span = opt_map(fn_ast.ret_typ, lambda x: x.span)
        ret_ast = opt_or_default(fn_ast.block.expr, fn_ast.block)

        if block.typ != VOID:
            if block.typ == NEVER:
                if not self.curr_bb.terminated:
                    self.curr_bb.unreachable(ret_ast)
                return
            coerced = self._coerce(block, ret_typ, ret_ast)
            if coerced is None:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name,
                    ret_typ_span,
                    block.typ.name,
                    ret_ast.span,
                )
            self.ret(coerced, ret_ast)
            return

        if self.curr_bb.terminated:
            return

        if ret_typ != VOID:
            raise MissingRetError(self.fn.name, fn_ast.span, ret_typ.name, ret_typ_span)

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_expr(
        self,
        expr_ast: ast.Expr,
        e: Env,
        ctx: ExprContext,
        expected_typ: Optional[Typ] = None,
    ) -> Value:
        """Lower any expression, dispatching to the ``build_*_expr`` method
        matching its AST node kind.

        :param expr_ast: The parsed expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower for the expression's value or its
            address.
        :param expected_typ: The type ``expr_ast`` is expected to have, if
            known from the surrounding context (e.g. a call argument's
            declared parameter type). Only consulted by expressions that
            can't otherwise infer their own type, such as an empty array
            literal; ignored everywhere else.
        :return: The lowered expression's value.
        :raises l0.l0errors.UserError: As any of the many subclasses that
            the individual ``build_*_expr`` methods (and, transitively,
            any sub-expressions) may raise.
        """
        match expr_ast:
            case ast.BlockExpr():
                return self.build_block_expr(expr_ast, e, ctx)
            case ast.IfExpr():
                return self.build_if_expr(expr_ast, e, ctx)
            case ast.WhileExpr():
                return self.build_while_expr(expr_ast, e, ctx)
            case ast.CallExpr():
                return self.build_call_expr(expr_ast, e, ctx)
            case ast.BinOpExpr():
                return self.build_bin_op_expr(expr_ast, e, ctx)
            case ast.UnaryOpExpr():
                return self.build_unary_op_expr(expr_ast, e, ctx)
            case ast.StrLit():
                return self._in_context(ComptimeCStr(expr_ast.value, expr_ast), ctx)
            case ast.IntLit():
                if not expr_ast.typ.fits(expr_ast.value):
                    raise IntLitOverflowError(
                        expr_ast.value, expr_ast.typ.name, expr_ast.span
                    )
                return self._in_context(
                    ComptimeInt(expr_ast.typ, expr_ast.value, expr_ast), ctx
                )
            case ast.BoolLit():
                return self._in_context(ComptimeBool(expr_ast.value, expr_ast), ctx)
            case ast.VarExpr():
                return self.build_var_expr(expr_ast, e, ctx)
            case ast.ArrayExpr():
                return self.build_array_expr(expr_ast, e, ctx, expected_typ)
            case ast.ArrayAccessExpr():
                return self.build_array_access_expr(expr_ast, e, ctx)
            case ast.StructExpr():
                return self.build_struct_expr(expr_ast, e, ctx)
            case ast.StructAccessExpr():
                return self.build_struct_access_expr(expr_ast, e, ctx)
            case ast.DerefExpr():
                return self.build_deref_expr(expr_ast, e, ctx)
            case _:
                assert False, f"unhandled expression kind {expr_ast}"

    def build_block_expr(
        self, block_ast: ast.BlockExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a ``{ ... }`` block expression.

        :param block_ast: The parsed block expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the tail expression for its value or
            its address.
        :return: The lowered tail expression's value if the block has one;
            otherwise a void value if its statements complete normally, or
            a never value if they already diverged (e.g. via ``return``).
        :raises l0.l0errors.UserError: As any of the many subclasses that
            lowering a statement (see :meth:`build_stmt`) or the tail
            expression (see :meth:`build_expr`) may raise.
        """
        e = e.new_child()
        for stmt_ast in block_ast.stmts:
            self.build_stmt(stmt_ast, e)

        if block_ast.expr is None:
            if self.curr_bb.terminated:
                return NeverValue(block_ast)
            return VoidValue(block_ast)
        return self.build_expr(block_ast.expr, e, ctx)

    def build_if_expr(self, if_ast: ast.IfExpr, e: Env, ctx: ExprContext) -> Value:
        """Lower an ``if``/``else`` expression.

        :param if_ast: The parsed ``if`` expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the taken branch for its value or its
            address.
        :return: The value of whichever branch is taken, merged via a phi
            instruction if both branches complete normally.
        :raises IfCondNotBoolError: If the condition's type isn't
            ``bool``.
        :raises IfElsTypMismatchError: If the ``if`` and ``else`` branches'
            types don't match.
        :raises IfTypNotVoidError: If there is no ``else`` branch and the
            ``if`` branch's type isn't ``void``.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the condition or either branch;
            see :meth:`build_expr`.
        """
        cond = self.build_expr(if_ast.condition, e, ExprContext.VALUE)
        if cond.typ != BOOL:
            raise IfCondNotBoolError(
                if_ast.condition.diag_str(), cond.typ.name, if_ast.condition.span
            )
        then_bb = self.add_bb("if")
        end_bb = self.add_bb("endif")
        els_bb = None
        if if_ast.els is not None:
            els_bb = self.add_bb("else")
            self.cbranch(cond, then_bb, els_bb, if_ast)
        else:
            self.cbranch(cond, then_bb, end_bb, if_ast)

        self.set_position(then_bb)
        then = self.build_expr(if_ast.then, e, ctx)
        then_last_bb = self.curr_bb
        if then.typ == NEVER and not then_last_bb.terminated:
            then_last_bb.unreachable(if_ast.then)
        have_then_value = not then_last_bb.terminated
        if have_then_value:
            self.branch(end_bb, if_ast.then)

        if els_bb is not None:
            self.set_position(els_bb)
            assert if_ast.els is not None
            els = self.build_expr(if_ast.els, e, ctx)
            els_last_bb = self.curr_bb
            if els.typ == NEVER and not els_last_bb.terminated:
                self.curr_bb.unreachable(if_ast.els)
            have_els_value = not els_last_bb.terminated
            if have_els_value:
                self.branch(end_bb, if_ast.els)

            if have_then_value and have_els_value:
                if then.typ != els.typ:
                    raise IfElsTypMismatchError(
                        then.typ.name,
                        if_ast.then.span,
                        els.typ.name,
                        if_ast.els.span,
                    )
                phi_incoming = {els_last_bb: els, then_last_bb: then}
                res = self.curr_bb.phi(phi_incoming, if_ast)
                return res
            if have_els_value:
                return els
            self.set_position(end_bb)
            if have_then_value:
                return then
            # Neither branch reaches end_bb: it's genuinely unreachable,
            # not just void, and needs a terminator of its own since
            # nothing branches into it.
            return self.curr_bb.unreachable(if_ast)

        if have_then_value:
            if then.typ != VOID:
                raise IfTypNotVoidError(then.typ.name, if_ast.then.span)

        self.set_position(end_bb)
        return VoidValue(if_ast)

    def build_while_expr(
        self, while_ast: ast.WhileExpr, e: Env, _ctx: ExprContext
    ) -> Value:
        """Lower a ``while`` loop expression.

        :param while_ast: The parsed ``while`` expression.
        :param e: The scope to resolve names in.
        :return: A void value.
        :raises WhileCondNotBoolError: If the condition's type isn't
            ``bool``.
        :raises WhileTypNotVoidError: If the loop body's type isn't
            ``void`` (or ``never``).
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the condition or the loop body;
            see :meth:`build_expr`.
        """
        cond_bb = self.add_bb("while_cond")
        loop_bb = self.add_bb("while_loop")
        end_bb = self.add_bb("while_end")

        self.branch(cond_bb, while_ast)
        cond = self.build_expr(while_ast.condition, e, ExprContext.VALUE)
        if cond.typ != BOOL:
            raise WhileCondNotBoolError(
                while_ast.condition.diag_str(),
                cond.typ.name,
                while_ast.condition.span,
            )

        self.cbranch(cond, loop_bb, end_bb, while_ast)
        self.set_position(loop_bb)
        block = self.build_expr(while_ast.block, e, ExprContext.VALUE)
        if block.typ not in (NEVER, VOID):
            raise WhileTypNotVoidError(block.typ.name, while_ast.block.span)

        if not self.curr_bb.terminated:
            self.branch(cond_bb, while_ast.block)

        self.set_position(end_bb)
        return VoidValue(while_ast)

    def build_call_expr(
        self, call_ast: ast.CallExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a function call expression.

        If the callee is written as ``x.name``, ``name`` is looked up as a
        method (an associated function with a ``self`` receiver) of
        ``x``'s struct type first; if found, ``x`` (its place - a method
        receiver is always by-pointer) becomes an implicit leading
        argument. Otherwise this falls back to ordinary field access,
        exactly as if the call weren't there (e.g. calling through a
        struct field that happens to hold a function pointer). Since a
        struct's fields and associated functions share one namespace, a
        name can't be both, so the fallback applies exactly when ``name``
        is a field, isn't a member of the struct at all, or ``x`` isn't
        struct-typed.

        :param call_ast: The parsed call expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The call's result.
        :raises NotAMethodError: If ``x.name(...)`` names a real
            associated function of ``x``'s struct type that has no
            ``self`` receiver.
        :raises PrivateItemAccessError: If ``x.name(...)`` names a private
            method, called from outside the module its struct is defined
            in.
        :raises NotCallableError: If the callee's type isn't a function
            pointer.
        :raises NotEnoughArgsError: If too few arguments are given.
        :raises TooManyArgsError: If too many arguments are given.
        :raises InvalidArgTypError: If an argument's type doesn't match
            the corresponding parameter's type.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the callee or an argument; see
            :meth:`build_expr`.
        """
        callee_ast = call_ast.callee
        recv_ast: Optional[ast.Expr] = None
        recv_arg: Optional[Value] = None

        if isinstance(callee_ast, ast.StructAccessExpr):
            recv_place = self.build_expr(callee_ast.struct, e, ExprContext.PLACE)
            method: Optional[ir_module.Fn] = None
            if isinstance(recv_place.typ, PtrTyp) and isinstance(
                recv_place.typ.pointee_typ, StructTyp
            ):
                struct_typ = recv_place.typ.pointee_typ
                candidate = struct_typ.get_assoc_fn(callee_ast.field.name)
                if candidate is not None:
                    if not candidate.is_accessible_from(callee_ast.span.file):
                        raise PrivateItemAccessError(
                            "function",
                            callee_ast.field.name,
                            callee_ast.field.span,
                            candidate.span,
                        )
                    assert candidate.ast is not None
                    if candidate.ast.receiver is None:
                        raise NotAMethodError(
                            callee_ast.field.name,
                            struct_typ.name,
                            callee_ast.field.span,
                            candidate.span,
                        )
                    method = candidate

            if method is not None:
                recv_arg = recv_place
                recv_ast = callee_ast.struct
                callee: Value = method
            else:
                callee = self._build_struct_field_access(
                    recv_place, callee_ast, ExprContext.VALUE
                )
        else:
            callee = self.build_expr(callee_ast, e, ExprContext.VALUE)

        callee_diag_str = callee_ast.diag_str()
        if not (
            isinstance(callee.typ, PtrTyp)
            and isinstance(callee.typ.pointee_typ, CallableTyp)
        ):
            raise NotCallableError(callee_diag_str, callee.typ.name, callee_ast.span)

        fn_typ = callee.typ.pointee_typ
        param_typs = fn_typ.param_typs
        offset = 1 if recv_ast is not None else 0
        arg_asts: list[ast.Expr] = (
            [recv_ast] if recv_ast is not None else []
        ) + list(call_ast.args)
        lowered_args = tuple(
            self.build_expr(
                arg_ast,
                e,
                ExprContext.VALUE,
                param_typs[i] if i < len(param_typs) else None,
            )
            for i, arg_ast in enumerate(call_ast.args, start=offset)
        )
        args = ((recv_arg,) if recv_arg is not None else ()) + lowered_args

        num_args = len(args)
        num_params = len(param_typs)
        if num_args < num_params:
            raise NotEnoughArgsError(
                callee_diag_str, call_ast.span, num_args, num_params
            )
        if num_args > num_params:
            raise TooManyArgsError(
                callee_diag_str,
                call_ast.span,
                call_ast.args[-1].span,
                num_args,
                num_params,
            )
        coerced_args = []
        for i, arg in enumerate(args):
            coerced = self._coerce(arg, param_typs[i], arg_asts[i])
            if coerced is None:
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.span,
                    i + 1,
                    arg.typ.name,
                    param_typs[i].name,
                    arg_asts[i].span,
                )
            coerced_args.append(coerced)

        return self._in_context(
            self.curr_bb.call(callee, tuple(coerced_args), call_ast), ctx
        )

    def build_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a binary operator expression (arithmetic or comparison).

        :param op_ast: The parsed binary operation.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The operation's result.
        :raises InvalidBinOpArgTypError: If either operand's type isn't an
            integer type.
        :raises IncompatibleBinOpArgTypsError: If the operands' types
            don't match.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering either operand; see
            :meth:`build_expr`.
        """
        lhs = self.build_expr(op_ast.lhs, e, ExprContext.VALUE)
        if not isinstance(lhs.typ, IntTyp):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs.typ.name,
                "an integer type",
                op_ast.lhs.span,
            )

        rhs = self.build_expr(op_ast.rhs, e, ExprContext.VALUE)
        if lhs.typ != rhs.typ:
            raise IncompatibleBinOpArgTypsError(
                op_ast.op.name,
                op_ast.op.span,
                lhs.typ.name,
                op_ast.lhs.span,
                rhs.typ.name,
                op_ast.rhs.span,
            )

        op = op_ast.op.name
        match op:
            case "<" | "<=" | "==" | "!=" | ">=" | ">":
                if lhs.typ.signage == SIGNED:
                    res = self.curr_bb.icmp_signed(op, lhs, rhs, op_ast)
                else:
                    res = self.curr_bb.icmp_unsigned(op, lhs, rhs, op_ast)
            case "+":
                res = self.curr_bb.add(lhs, rhs, op_ast)
            case "-":
                res = self.curr_bb.sub(lhs, rhs, op_ast)
            case "*":
                res = self.curr_bb.mul(lhs, rhs, op_ast)
            case "/":
                if lhs.typ.signage == SIGNED:
                    res = self.curr_bb.sdiv(lhs, rhs, op_ast)
                else:
                    res = self.curr_bb.udiv(lhs, rhs, op_ast)
            case _:
                assert False, f"unhandled binary operator {op!r}"

        return self._in_context(res, ctx)

    def build_unary_op_expr(
        self, op_ast: ast.UnaryOpExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a unary operator expression (currently, only ``&``).

        :param op_ast: The parsed unary operation.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The operation's result.
        """
        match op_ast.op.name:
            case "&":
                return self._in_context(
                    self.build_expr(op_ast.operand, e, ExprContext.PLACE),
                    ctx,
                )
            case _:
                assert False, f"unhandled unary operator {op_ast.op.name!r}"

    def build_var_expr(self, var_ast: ast.VarExpr, e: Env, ctx: ExprContext) -> Value:
        """Lower a (possibly qualified) variable or function reference.

        :param var_ast: The parsed variable expression.
        :param e: The scope to resolve the path in.
        :param ctx: Whether to lower the result for its value or its
            address; ignored for function references, which are always
            returned as-is.
        :return: The referenced variable or function.
        :raises ItemNotFoundError: If the path cannot be resolved.
        """
        var = e.resolve_path(Env.Namespace.VARS, var_ast.path)
        if isinstance(var, ir_module.FnSpec):
            return var
        if ctx == ExprContext.PLACE:
            return var

        return self.curr_bb.load(var, var_ast)

    def build_array_expr(
        self,
        arr_expr: ast.ArrayExpr,
        e: Env,
        ctx: ExprContext,
        expected_typ: Optional[Typ] = None,
    ) -> Value:
        """Lower an array literal expression.

        An empty array literal has no elements to infer an element type
        from, so it's only accepted when ``expected_typ`` gives one (e.g.
        when the array literal is a call argument, a ``return`` value, a
        struct field's value, or an assignment's right-hand side, all of
        which have a statically-known target type); otherwise it's
        rejected. When ``expected_typ`` is known, it's also propagated to
        each element, so a nested empty array (e.g. one element of a
        non-empty outer array) can be resolved the same way.

        :param arr_expr: The parsed array expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :param expected_typ: This array literal's expected type, if known
            from the surrounding context.
        :return: The constructed array value.
        :raises EmptyArrayTypUnknownError: If the array literal is empty
            and ``expected_typ`` isn't a known array type.
        :raises VoidArrayElementError: If the element type is ``void``.
        :raises IncompatibleTypInArrayExpr: If an element's type doesn't
            match, and doesn't coerce to, the element type.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering an element; see :meth:`build_expr`.
        """
        expected_elt_typ = (
            expected_typ.element_typ if isinstance(expected_typ, ArrayTyp) else None
        )
        elts = [
            self.build_expr(elt, e, ExprContext.VALUE, expected_elt_typ)
            for elt in arr_expr.elements
        ]
        # Elements coerce only towards an element type the surrounding
        # context supplied. With nothing to go on the type is taken from
        # the first element, and coercing the rest towards that would let
        # the elements' order decide whether the literal is accepted.
        if expected_elt_typ is not None:
            elt_typ = expected_elt_typ
        elif elts:
            elt_typ = elts[0].typ
        else:
            raise EmptyArrayTypUnknownError(arr_expr.span)

        if elt_typ == VOID:
            raise VoidArrayElementError(elts[0].span if elts else arr_expr.span)
        arr_typ = ArrayTyp.get_or_create(elt_typ, len(elts))
        arr = ComptimeArray(
            arr_typ,
            [UndefValue(arr_typ.element_typ, arr_expr)] * len(elts),
            arr_expr,
        )

        for i, elt in enumerate(elts):
            elt_ast = arr_expr.elements[i]
            coerced = self._coerce(elt, elt_typ, elt_ast)
            if coerced is None:
                raise IncompatibleTypInArrayExpr(
                    elt.typ.name, i, elt_ast.span, arr_typ.name
                )
            elt_index = ComptimeInt(USIZE, i, elt_ast)
            arr = self.curr_bb.insert_value(arr, coerced, (elt_index,), elt_ast)

        return self._in_context(arr, ctx)

    def build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower an array indexing expression (``arr[index]``).

        :param aa_expr: The parsed array access expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The indexed element, or a pointer to it if ``ctx`` is
            :attr:`~ExprContext.PLACE`.
        :raises IndexIntoInvalidTypError: If the indexed expression's type
            isn't an array type.
        :raises InvalidIndexTypError: If the index expression's type isn't
            ``usize``.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the array or index expression; see
            :meth:`build_expr`.
        """
        arr_ptr = self.build_expr(aa_expr.array, e, ExprContext.PLACE)
        arr_ptr_typ = checked_cast(arr_ptr.typ, PtrTyp)
        if not isinstance(arr_ptr_typ.pointee_typ, ArrayTyp):
            raise IndexIntoInvalidTypError(
                arr_ptr_typ.pointee_typ.name, aa_expr.array.span
            )

        index = self.build_expr(aa_expr.index, e, ExprContext.VALUE)
        if index.typ != USIZE:
            raise InvalidIndexTypError(index.typ.name, aa_expr.index.span)

        # TODO: bounds check

        elt_ptr = self.curr_bb.gep(arr_ptr, index, aa_expr)
        return self._deref_in_context(elt_ptr, ctx, aa_expr)

    def build_struct_expr(
        self, struct_expr: ast.StructExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a struct literal expression.

        :param struct_expr: The parsed struct expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The constructed struct value.
        :raises ItemNotFoundError: If the named type cannot be resolved.
        :raises TypeOfStructExprNotStructError: If the named type isn't a
            struct type.
        :raises InvalidStructFieldError: If a given field name isn't a
            field of the struct type.
        :raises PrivateStructFieldAccessError: If a given field is private
            and ``struct_expr`` isn't in the struct's defining module.
        :raises DuplicateFieldInStructExprError: If a field is given a
            value more than once.
        :raises MissingFieldInStructExprError: If a required field is
            omitted.
        :raises IncompatibleStructFieldTypError: If a field's given value
            has the wrong type.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering a field's value expression; see
            :meth:`build_expr`.
        """
        struct_typ = Typ.from_ast(struct_expr.typ, e)
        if not isinstance(struct_typ, StructTyp):
            raise TypeOfStructExprNotStructError(struct_typ.name, struct_expr.typ.span)

        struct = ComptimeStruct(
            struct_typ,
            {
                f.name: UndefValue(f.typ, struct_expr)
                for f in struct_typ.fields.values()
            },
            struct_expr,
        )

        field_values = {}
        for field_expr in struct_expr.fields:
            if field_expr.ident.name not in struct_typ.fields:
                raise InvalidStructFieldError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    struct_typ.name,
                    struct_typ.span,
                )
            field = struct_typ.fields[field_expr.ident.name]
            if not field.is_accessible_from(struct_expr.span.file):
                raise PrivateStructFieldAccessError(
                    field.name, field_expr.ident.span, struct_typ.name, field.ast.span
                )
            if field_expr.ident.name in field_values:
                raise DuplicateFieldInStructExprError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    field_values[field_expr.ident.name].span,
                )
            field_values[field_expr.ident.name] = self.build_expr(
                field_expr.value, e, ExprContext.VALUE, field.typ
            )

        for i, field in enumerate(struct_typ.fields.values()):
            if field.name not in field_values:
                raise MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name, struct_expr.span
                )
            field_value = field_values[field.name]
            coerced = self._coerce(field_value, field.typ, field_value.ast)
            if coerced is None:
                raise IncompatibleStructFieldTypError(
                    field.name,
                    struct_typ.name,
                    field_value.typ.name,
                    field_value.span,
                    field.typ.name,
                    field.ast.span,
                )
            field_index = ComptimeInt(I32, i, field_value.ast)
            struct = self.curr_bb.insert_value(
                struct, coerced, (field_index,), field_value.ast
            )

        return self._in_context(struct, ctx)

    def build_struct_access_expr(
        self, sa_expr: ast.StructAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a struct field access expression (``s.field``).

        :param sa_expr: The parsed struct access expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~ExprContext.PLACE`.
        :raises FieldAccessIntoInvalidTypError: If the accessed
            expression's type isn't a struct type.
        :raises InvalidStructFieldError: If the named field isn't a field
            of the struct type.
        :raises PrivateStructFieldAccessError: If the named field is
            private and ``sa_expr`` isn't in the struct's defining module.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the struct expression; see
            :meth:`build_expr`.
        """
        struct_ptr = self.build_expr(sa_expr.struct, e, ExprContext.PLACE)
        return self._build_struct_field_access(struct_ptr, sa_expr, ctx)

    def _build_struct_field_access(
        self, struct_ptr: Value, sa_expr: ast.StructAccessExpr, ctx: ExprContext
    ) -> Value:
        """Lower ``s.field`` given ``s``'s already-lowered place pointer.

        Split out of :meth:`build_struct_access_expr` so
        :meth:`build_call_expr` can fall back to plain field access
        (e.g. a struct field that happens to hold a callable value)
        without re-lowering ``sa_expr.struct`` a second time.

        :param struct_ptr: ``sa_expr.struct`` already lowered in
            :attr:`~ExprContext.PLACE` context.
        :param sa_expr: The parsed struct access expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~ExprContext.PLACE`.
        :raises FieldAccessIntoInvalidTypError: If ``struct_ptr``'s
            pointee type isn't a struct type.
        :raises InvalidStructFieldError: If the named field isn't a field
            of the struct type.
        :raises PrivateStructFieldAccessError: If the named field is
            private and ``sa_expr`` isn't in the struct's defining module.
        """
        struct_ptr_typ = checked_cast(struct_ptr.typ, PtrTyp)
        struct_typ = struct_ptr_typ.pointee_typ
        if not isinstance(struct_typ, StructTyp):
            raise FieldAccessIntoInvalidTypError(struct_typ.name, sa_expr.struct.span)

        field_name = sa_expr.field.name
        if field_name not in struct_typ.fields:
            raise InvalidStructFieldError(
                field_name, sa_expr.field.span, struct_typ.name, struct_typ.span
            )
        field = struct_typ.fields[field_name]
        if not field.is_accessible_from(sa_expr.span.file):
            raise PrivateStructFieldAccessError(
                field_name, sa_expr.field.span, struct_typ.name, field.ast.span
            )

        index = ComptimeInt(I32, field.index, sa_expr)
        field_ptr = self.curr_bb.gep(struct_ptr, index, sa_expr)
        return self._deref_in_context(field_ptr, ctx, sa_expr)

    def build_deref_expr(
        self, d_expr: ast.DerefExpr, e: Env, ctx: ExprContext
    ) -> Value:
        """Lower a pointer dereference expression (``ptr.*``).

        :param d_expr: The parsed dereference expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The pointee's value, or the pointer itself unchanged if
            ``ctx`` is :attr:`~ExprContext.PLACE`.
        :raises DerefInvalidTypError: If the dereferenced expression's
            type isn't a data pointer type.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the pointer expression; see
            :meth:`build_expr`.
        """
        ptr = self.build_expr(d_expr.ptr, e, ExprContext.VALUE)
        if not isinstance(ptr.typ, PtrTyp) or isinstance(ptr.typ.pointee_typ, FnTyp):
            raise DerefInvalidTypError(ptr.typ.name, d_expr.ptr.span)
        return self._deref_in_context(ptr, ctx, d_expr)

    def build_stmt(self, stmt_ast: ast.Stmt, e: Env) -> None:
        """Lower any statement, dispatching to the ``build_*_stmt`` method
        matching its AST node kind.

        :param stmt_ast: The parsed statement.
        :param e: The scope to resolve names in.
        :raises l0.l0errors.UserError: As any of the many subclasses that
            the individual ``build_*_stmt`` methods (and, transitively,
            any sub-expressions) may raise.
        """
        match stmt_ast:
            case ast.ExprStmt():
                self.build_expr(stmt_ast.expr, e, ExprContext.VALUE)
            case ast.RetStmt():
                self.build_ret_stmt(stmt_ast, e)
            case ast.LetStmt():
                self.build_let_stmt(stmt_ast, e)
            case ast.AssignmentStmt():
                self.build_assignment_stmt(stmt_ast, e)

    def build_ret_stmt(self, ret_ast: ast.RetStmt, e: Env) -> None:
        """Lower a ``return`` statement.

        :param ret_ast: The parsed return statement.
        :param e: The scope to resolve names in.
        :raises RetNotInFnError: If not currently lowering a function body.
        :raises InvalidRetTypError: If the returned expression's type
            doesn't match the function's declared return type.
        :raises InvalidVoidRetError: If no expression is given but the
            function's declared return type isn't ``void``.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the returned expression; see
            :meth:`build_expr`.
        """
        if self.fn is None:
            raise RetNotInFnError(ret_ast.span)

        ret_typ = self.fn.fn_typ.ret_typ
        ret_typ_span = None
        if self.fn.ast is not None and self.fn.ast.span is not None:
            ret_typ_span = self.fn.ast.span

        if ret_ast.expr is not None:
            expr = self.build_expr(ret_ast.expr, e, ExprContext.VALUE, ret_typ)
            if expr.typ == NEVER:
                if not self.curr_bb.terminated:
                    self.curr_bb.unreachable(ret_ast)
                return
            coerced = self._coerce(expr, ret_typ, ret_ast.expr)
            if coerced is None:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name,
                    ret_typ_span,
                    expr.typ.name,
                    ret_ast.expr.span,
                )
            self.ret(coerced, ret_ast)
            return

        if ret_typ != VOID:
            raise InvalidVoidRetError(
                self.fn.name, ret_typ.name, ret_typ_span, ret_ast.span
            )

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> None:
        """Lower a local ``let`` statement.

        :param let_ast: The parsed ``let`` statement.
        :param e: The scope to resolve names in, and to bind the new
            variable in.
        :raises VoidVarInitializerError: If the initializer expression has
            type ``void``.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the initializer expression; see
            :meth:`build_expr`.
        """
        expr = self.build_expr(let_ast.expr, e, ExprContext.VALUE)
        if expr.typ == VOID:
            raise VoidVarInitializerError(expr.span)
        name = let_ast.ident.name
        mut = Mutability.from_ast(let_ast.mut)
        alloca = self.curr_bb.alloca(expr.typ, mut, 1, let_ast)
        e.add_var(name, alloca)
        self.curr_bb.store(expr, alloca, let_ast)

    def build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> None:
        """Lower an assignment statement (``place = expr``).

        l0 deliberately evaluates the place expression before the value
        expression (matching JavaScript, Java, C#, and Go; the opposite of
        Rust, Python, and C++17's built-in ``=``). Where the place and
        value expressions both have observable side effects (e.g. a call
        in an array index alongside a call on the right-hand side), the
        place's side effects happen first. This is also what makes the
        place's type available to use as the value expression's
        :meth:`build_expr`-level ``expected_typ`` hint (needed e.g. to
        resolve an empty array literal assigned into a place of known
        array type).

        :param ass_ast: The parsed assignment statement.
        :param e: The scope to resolve names in.
        :raises AssignToConstError: If the assignment target is const.
        :raises IncompatibleAssignmentTypError: If the assigned value's
            type doesn't match, and doesn't coerce to, the place's type.
        :raises l0.l0errors.UserError: Also raised, as any of many possible
            subclasses, while lowering the target or the assigned
            expression; see :meth:`build_expr`.
        """
        place = self.build_expr(ass_ast.place, e, ExprContext.PLACE)
        place_typ = checked_cast(place.typ, PtrTyp)
        assert place_typ.pointee_typ != VOID, "assignment place cannot be void"
        expr = self.build_expr(
            ass_ast.expr, e, ExprContext.VALUE, place_typ.pointee_typ
        )

        if place_typ.mut == CONST:
            raise AssignToConstError(ass_ast.place.span)

        coerced = self._coerce(expr, place_typ.pointee_typ, ass_ast.expr)
        if coerced is None:
            raise IncompatibleAssignmentTypError(
                expr.typ.name,
                place_typ.pointee_typ.name,
                ass_ast.expr.span,
                ass_ast.place.span,
            )
        expr = coerced

        self.curr_bb.store(expr, place, ass_ast)

    def _coerce(
        self, value: Value, target: Typ, ast: Optional[ast.Ast]
    ) -> Optional[Value]:
        """Convert ``value`` to ``target``, if the language allows it implicitly.

        The single place implicit coercion happens. Callers are the
        handful of sites with an unambiguous target type - call
        arguments (including a method's receiver), assignment, ``return``
        and a function's tail expression, struct literal fields, and
        array literal elements whose type comes from context. Operator
        operands deliberately don't coerce.

        Returning ``None`` rather than raising lets each caller report the
        failure with the error class that fits its context.

        :param value: The value to convert.
        :param target: The type to convert it to.
        :param ast: The AST node to attribute any emitted instruction to.
        :return: A value of type ``target``, or ``None`` if ``value``'s
            type doesn't coerce to it.
        """
        if value.typ == target:
            return value
        if not value.typ.coerces_to(target):
            return None
        return value

    def _value_to_ptr(self, value: Value) -> Value[PtrTyp]:
        alloca = self.curr_bb.alloca(value.typ, CONST, 1, value.ast)
        self.curr_bb.store(value, alloca, value.ast)
        return alloca

    def _in_context(self, value: Value, ctx: ExprContext) -> Value:
        if ctx == ExprContext.PLACE:
            return self._value_to_ptr(value)
        else:
            return value

    def _deref_in_context(
        self, ptr: Value, ctx: ExprContext, ast: Optional[ast.Ast]
    ) -> Value:
        if ctx == ExprContext.PLACE:
            return ptr
        else:
            return self.curr_bb.load(ptr, ast)

    def add_bb(self, basename: str) -> BasicBlock:
        """Create a new, empty basic block and add it to :attr:`cfg`.

        :param basename: A human-readable name to derive the block's
            (uniquified) name from.
        :return: The new basic block. It is not made the current block;
            use :meth:`set_position` for that.
        """
        bb = BasicBlock(self._generate_bb_name(basename))
        self.cfg.add_node(bb)
        return bb

    def set_position(self, bb: BasicBlock) -> None:
        """Make ``bb`` the block that subsequent instructions are added to.

        :param bb: The basic block to switch to; must already be in
            :attr:`cfg`.
        """
        assert_in(bb, self.cfg)
        self.curr_bb = bb

    def branch(self, target: BasicBlock, ast: ast.Ast) -> None:
        """Terminate :attr:`curr_bb` with an unconditional branch, and
        switch to ``target``.

        :param target: The basic block to branch to; must already be in
            :attr:`cfg`.
        :param ast: The AST node the branch instruction is attributed to.
        """
        assert_in(target, self.cfg)
        self.curr_bb.branch(target, ast)
        self.cfg.add_edge(self.curr_bb, target)
        self.curr_bb = target

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: ast.Ast,
    ) -> None:
        """Terminate :attr:`curr_bb` with a conditional branch.

        Unlike :meth:`branch`, this does not change :attr:`curr_bb`; the
        caller is expected to call :meth:`set_position` next.

        :param condition: The boolean value to branch on.
        :param true_target: The block to branch to if ``condition`` is
            true; must already be in :attr:`cfg`.
        :param false_target: The block to branch to if ``condition`` is
            false; must already be in :attr:`cfg`.
        :param ast: The AST node the branch instruction is attributed to.
        """
        assert_in(true_target, self.cfg)
        assert_in(false_target, self.cfg)
        self.curr_bb.cbranch(condition, true_target, false_target, ast)
        self.cfg.add_edge(self.curr_bb, true_target)
        self.cfg.add_edge(self.curr_bb, false_target)

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> None:
        """Terminate :attr:`curr_bb` with a return of ``value``.

        :param value: The value to return.
        :param ast: The AST node the return instruction is attributed to.
        """
        self.curr_bb.ret(value, ast)
        self.cfg.add_edge(self.curr_bb, self.cfg.exit)
