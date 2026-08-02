# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""AST-to-IR lowering, assuming an already type-checked program.

Type checking happens beforehand, as its own pass (see
:mod:`~leech.typcheck`), forced by :attr:`~leech.ir_module.Fn.cfg` and
:attr:`~leech.ir_module.ModVar.cfg` before either ever constructs a
:class:`CfgBuilder`. Everything below trusts the result: it doesn't
validate that a program is well-typed, only emits IR under the
assumption that it is - reading back decisions
(:class:`~leech.typcheck.TypCheckResults`) that ``TypCheck`` already
made (an integer literal's type, whether and how a value coerces,
which of two call/field-access candidates applies, and so on) rather
than re-deriving them.
"""

import dataclasses
import enum
from collections.abc import Mapping
from typing import Final, Optional

from leech import (
    asserts,
    ast,
    ir_env,
    ir_module,
    ir_traits,
    ir_values,
    naming,
    opt_util,
    signage,
    typcheck,
    typs,
)


class _ExprContext(enum.Enum):
    """Whether an expression is being lowered for its value or its address.

    An expression built in :attr:`PLACE` context yields a pointer to
    where the value lives (e.g. the target of an assignment or the
    operand of ``&``), rather than the value itself.
    """

    PLACE = 0
    VALUE = 1


class CfgBuilder:
    """Lowers a single function body or module-variable initializer to a
    :class:`~leech.ir_values.Cfg`.

    Each ``build_*`` method lowers one AST node kind, emitting
    instructions into the current basic block as it goes. The program is
    already known to be well-typed by this point (see the module
    docstring), so these methods don't validate it - where a decision was
    made while type checking, they read it back from the already-computed
    type-checking facts rather than re-deriving it.

    :param typ_check_results: The already-computed type-checking facts for
        the body being lowered (see :mod:`~leech.typcheck`). For a
        generic function instance, these are its generic declaration's
        own results, unsubstituted - see ``typ_arg_mapping``.
    :param fn: The function whose body is being lowered, or ``None`` when
        lowering a module-variable initializer (which isn't part of any
        function).
    :param typ_arg_mapping: The concrete type to substitute for each of
        the lowered function's own type parameters, if it's one instance
        of a generic function - empty otherwise. Almost everything typed
        during lowering already resolves concretely on its own (params
        and locals ultimately trace back to the function's own already-
        substituted signature, or to a type resolved fresh from source
        against an environment where each type parameter is already
        shadowed by its concrete argument - see
        :attr:`~leech.ir_module.FnInstance.env`); the one fact the
        type-checking results can hand back containing a raw type
        parameter is a :class:`~leech.typcheck.NeverDiverge` coercion's
        target, which is why this is consulted in exactly one place
        during lowering.
    """

    @dataclasses.dataclass(frozen=True)
    class _LoopCtx:
        """The ``break``/``continue`` targets for one enclosing ``while`` loop."""

        label: Optional[str]
        cond_bb: ir_values.BasicBlock
        end_bb: ir_values.BasicBlock

    _fn: Final[Optional[ir_module.FnSpec]]
    _typ_check_results: Final[typcheck.TypCheckResults]
    _typ_arg_mapping: Final[Mapping[typs.TypParamTyp, typs.Typ]]
    cfg: Final[ir_values.Cfg]
    _curr_bb: ir_values.BasicBlock
    _generate_bb_name: Final[naming.VarNamer]
    #: The ``while`` loops currently being lowered, innermost last - used
    #: to resolve unlabeled and labeled ``break``/``continue`` targets.
    _loop_stack: Final[list[CfgBuilder._LoopCtx]]

    def __init__(
        self,
        typ_check_results: typcheck.TypCheckResults,
        fn: Optional[ir_module.FnSpec] = None,
        typ_arg_mapping: Optional[Mapping[typs.TypParamTyp, typs.Typ]] = None,
    ) -> None:
        self._fn = fn
        self._typ_check_results = typ_check_results
        self._typ_arg_mapping = opt_util.opt_or_default(typ_arg_mapping, {})
        self.cfg = ir_values.Cfg()
        self._generate_bb_name = naming.VarNamer()
        # Reserve the "entry"/"exit" basenames so a later `add_bb` call
        # never collides with the blocks `Cfg.__init__` already created.
        self._generate_bb_name("entry")
        self._generate_bb_name("exit")
        self._curr_bb = self.cfg.entry
        self._loop_stack = []

    def build_var_initializer(self, defn_ast: ast.VarDefn, e: ir_env.Env) -> None:
        """Lower a module-level ``let`` initializer expression into :attr:`cfg`.

        :param defn_ast: The parsed variable declaration.
        :param e: The scope to resolve names in.
        """
        assert self._fn is None
        e = e.new_child()
        let_ast = defn_ast.let_stmt
        initializer = self._build_let_initializer(let_ast, e, _ExprContext.VALUE)
        self._ret(initializer, let_ast.expr)

    def build_fn(self, fn_ast: ast.FnDefn, e: ir_env.Env) -> None:
        """Lower a function body into :attr:`cfg`.

        :param fn_ast: The parsed function definition.
        :param e: The scope to resolve names in.
        """
        assert self._fn is not None
        e = e.new_child()

        for param in self._fn.params:
            assert param.ast is not None
            alloca = self._curr_bb.alloca(param.typ, typs.CONST, 1, param.ast)
            self._curr_bb.store(param, alloca, param.ast)
            e.add_var(param.ast.name.name, alloca)

        ret_typ = self._fn.fn_typ.ret_typ
        block = self._build_expr(fn_ast.block, e, _ExprContext.VALUE, ret_typ)
        ret_ast = opt_util.opt_or_default(fn_ast.block.expr, fn_ast.block)

        if block.typ != typs.VOID:
            if block.typ == typs.NEVER:
                if not self._curr_bb.terminated:
                    self._curr_bb.unreachable(ret_ast)
                return
            # TypCheck already confirmed block coerces to ret_typ.
            coerced = opt_util.opt_unwrap(self._coerce(block, ret_ast))
            self._ret(coerced, ret_ast)
            return

        if self._curr_bb.terminated:
            return

        # TypCheck already confirmed a void body only reaches here when
        # ret_typ is void too (else it would have raised MissingRetError).
        asserts.assert_eq(ret_typ, typs.VOID)
        self._ret(ir_values.VoidValue(ret_ast), ret_ast)

    def _build_expr(
        self,
        expr_ast: ast.Expr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower any expression, dispatching to the ``build_*_expr`` method
        matching its AST node kind.

        :param expr_ast: The parsed expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower for the expression's value or its
            address.
        :param expected_typ: The type ``expr_ast`` is expected to have, if
            known from the surrounding context (e.g. a call argument's
            declared parameter type). Consulted by expressions that can't
            otherwise infer their own type - an empty array literal, and
            an integer literal written without a type suffix (see
            :meth:`~leech.typcheck.TypCheck._infer_int_lit_typ`) - and passed
            on by the constructs that
            merely pass a value through, such as a block's tail expression
            or an ``if``'s branches. Operator operands deliberately don't
            receive it; ignored everywhere else.
        :return: The lowered expression's value.
        """
        match expr_ast:
            case ast.BlockExpr():
                return self._build_block_expr(expr_ast, e, ctx, expected_typ)
            case ast.IfExpr():
                return self._build_if_expr(expr_ast, e, ctx, expected_typ)
            case ast.WhileExpr():
                return self._build_while_expr(expr_ast, e, ctx)
            case ast.CallExpr():
                return self._build_call_expr(expr_ast, e, ctx)
            case ast.BinOpExpr():
                return self._build_bin_op_expr(expr_ast, e, ctx, expected_typ)
            case ast.UnaryOpExpr():
                return self._build_unary_op_expr(expr_ast, e, ctx, expected_typ)
            case ast.StrLit():
                return self._in_context(ir_values.ComptimeCStr(expr_ast.value, expr_ast), ctx)
            case ast.IntLit():
                return self._in_context(
                    self._build_int_lit(expr_ast, False),
                    ctx,
                )
            case ast.BoolLit():
                return self._in_context(ir_values.ComptimeBool(expr_ast.value, expr_ast), ctx)
            case ast.VarExpr():
                return self._build_var_expr(expr_ast, e, ctx)
            case ast.ArrayExpr():
                return self._build_array_expr(expr_ast, e, ctx, expected_typ)
            case ast.ArrayAccessExpr():
                return self._build_array_access_expr(expr_ast, e, ctx)
            case ast.StructExpr():
                return self._build_struct_expr(expr_ast, e, ctx)
            case ast.StructAccessExpr():
                return self._build_struct_access_expr(expr_ast, e, ctx)
            case ast.DerefExpr():
                return self._build_deref_expr(expr_ast, e, ctx)
            case _:
                raise AssertionError(f"unhandled expression kind {expr_ast}")

    def _build_block_expr(
        self,
        block_ast: ast.BlockExpr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a ``{ ... }`` block expression.

        :param block_ast: The parsed block expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the tail expression for its value or
            its address.
        :param expected_typ: The type the block is expected to have, if
            known; the block's value is its tail expression's, so this is
            passed straight on to it.
        :return: The lowered tail expression's value if the block has one;
            otherwise a void value if its statements complete normally, or
            a never value if they already diverged (e.g. via ``return``).
        """
        e = e.new_child()
        for stmt_ast in block_ast.stmts:
            self._build_stmt(stmt_ast, e)

        if block_ast.expr is None:
            if self._curr_bb.terminated:
                return ir_values.NeverValue(block_ast)
            return ir_values.VoidValue(block_ast)
        return self._build_expr(block_ast.expr, e, ctx, expected_typ)

    def _build_if_expr(
        self,
        if_ast: ast.IfExpr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower an ``if``/``else`` expression.

        :param if_ast: The parsed ``if`` expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the taken branch for its value or its
            address.
        :param expected_typ: The type the expression is expected to have,
            if known; its value comes from whichever branch is taken, so
            this is passed on to both branches (but not the condition,
            which is always ``bool``).
        :return: The value of whichever branch is taken, merged via a phi
            instruction if both branches complete normally.
        """
        cond = self._build_expr(if_ast.condition, e, _ExprContext.VALUE)
        # TypCheck already confirmed cond coerces to bool.
        cond = opt_util.opt_unwrap(self._coerce(cond, if_ast.condition))
        then_bb = self._add_bb("if")
        end_bb = self._add_bb("endif")
        els_bb = None
        if if_ast.els is not None:
            els_bb = self._add_bb("else")
            self._cbranch(cond, then_bb, els_bb, if_ast)
        else:
            self._cbranch(cond, then_bb, end_bb, if_ast)

        if els_bb is None:
            self._build_if_arm(if_ast.then, then_bb, end_bb, e, ctx, expected_typ)
            self._set_position(end_bb)
            return ir_values.VoidValue(if_ast)

        els_ast = if_ast.els
        assert els_ast is not None
        # With no type supplied by the context the two arms are peers, so
        # lower the one whose type is already decided first and let a
        # bare integer literal in the other take its type from it. Only
        # one arm ever runs, and they're in separate blocks, so which is
        # lowered first doesn't change what the program does.
        if (
            expected_typ is None
            and typcheck.is_flexible_int_lit(if_ast.then)
            and not typcheck.is_flexible_int_lit(els_ast)
        ):
            els, els_last_bb, have_els_value = self._build_if_arm(
                els_ast, els_bb, end_bb, e, ctx, expected_typ
            )
            then, then_last_bb, have_then_value = self._build_if_arm(
                if_ast.then,
                then_bb,
                end_bb,
                e,
                ctx,
                typcheck.resolve_peer_typ([els.typ]) if have_els_value else expected_typ,
            )
        else:
            then, then_last_bb, have_then_value = self._build_if_arm(
                if_ast.then, then_bb, end_bb, e, ctx, expected_typ
            )
            els_hint = expected_typ
            if expected_typ is None and have_then_value and typcheck.is_flexible_int_lit(els_ast):
                els_hint = typcheck.resolve_peer_typ([then.typ])
            els, els_last_bb, have_els_value = self._build_if_arm(
                els_ast, els_bb, end_bb, e, ctx, els_hint
            )

        if have_then_value and have_els_value:
            # TypCheck already confirmed then and els have the same
            # type - the phi below requires it.
            asserts.assert_eq(then.typ, els.typ)
            self._set_position(end_bb)
            return self._curr_bb.phi({els_last_bb: els, then_last_bb: then}, if_ast)

        self._set_position(end_bb)
        if have_then_value:
            return then
        if have_els_value:
            return els
        # Neither branch reaches end_bb: it's genuinely unreachable,
        # not just void, and needs a terminator of its own since
        # nothing branches into it.
        return self._curr_bb.unreachable(if_ast)

    def _build_if_arm(
        self,
        arm_ast: ast.Expr,
        arm_bb: ir_values.BasicBlock,
        end_bb: ir_values.BasicBlock,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ],
    ) -> tuple[ir_values.Value, ir_values.BasicBlock, bool]:
        """Lower one arm of an ``if``/``else`` into its own basic block.

        :param arm_ast: The parsed arm.
        :param arm_bb: The block to lower the arm into.
        :param end_bb: The block the arms merge at.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the arm for its value or its address.
        :param expected_typ: The type the arm is expected to have, if
            known.
        :return: The arm's value, the block its lowering ended in (which
            isn't ``arm_bb`` if the arm contained control flow of its
            own), and whether control reaches ``end_bb`` from it.
        """
        self._set_position(arm_bb)
        value = self._build_expr(arm_ast, e, ctx, expected_typ)
        last_bb = self._curr_bb
        if value.typ == typs.NEVER and not last_bb.terminated:
            last_bb.unreachable(arm_ast)
        have_value = not last_bb.terminated
        if have_value:
            self._branch(end_bb, arm_ast)
        return value, last_bb, have_value

    def _build_while_expr(
        self, while_ast: ast.WhileExpr, e: ir_env.Env, _ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a ``while`` loop expression.

        :param while_ast: The parsed ``while`` expression.
        :param e: The scope to resolve names in.
        :return: A void value.
        """
        cond_bb = self._add_bb("while_cond")
        loop_bb = self._add_bb("while_loop")
        end_bb = self._add_bb("while_end")

        self._branch(cond_bb, while_ast)
        self._set_position(cond_bb)
        cond = self._build_expr(while_ast.condition, e, _ExprContext.VALUE)
        # TypCheck already confirmed cond coerces to bool.
        cond = opt_util.opt_unwrap(self._coerce(cond, while_ast.condition))

        self._cbranch(cond, loop_bb, end_bb, while_ast)
        self._set_position(loop_bb)
        self._loop_stack.append(
            CfgBuilder._LoopCtx(
                label=opt_util.opt_map(while_ast.label, lambda x: x.name),
                cond_bb=cond_bb,
                end_bb=end_bb,
            )
        )
        try:
            self._build_expr(while_ast.block, e, _ExprContext.VALUE)
        finally:
            self._loop_stack.pop()

        if not self._curr_bb.terminated:
            self._branch(cond_bb, while_ast.block)

        self._set_position(end_bb)
        return ir_values.VoidValue(while_ast)

    def _build_call_expr(
        self, call_ast: ast.CallExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a function call expression.

        If the callee names a generic function, the call is against one
        instance of it (see :meth:`~leech.ir_module.Fn.instance`) -
        TypCheck already resolved which one, so this only has to fetch
        (and, on the first call site to need it, build) it.

        Otherwise, if the callee is written as ``x.name``, ``name`` is looked up as a
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

        TypCheck has already confirmed the callee resolves to a method or
        a callable field, that a resolved method is accessible and has a
        receiver, and that argument count and types are valid; this only
        has to make the same method-or-field-fallback decision (to know
        how to lower the callee) and emit.

        :param call_ast: The parsed call expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The call's result.
        """
        callee_ast = call_ast.callee
        recv_ast: Optional[ast.Expr] = None
        recv_arg: Optional[ir_values.Value] = None

        generic_call = self._typ_check_results.generic_call(call_ast)
        if generic_call is not None:
            # TypCheck already resolved which generic function this calls
            # and its type arguments (inferred or explicit); this just
            # has to get (building, if this is the first call site to
            # need it) the one instance that pairs them.
            callee: ir_values.Value = self._resolve_fn_instance(*generic_call)
        elif isinstance(callee_ast, ast.StructAccessExpr):
            recv_place = self._build_expr(callee_ast.struct, e, _ExprContext.PLACE)
            recv_ptr_typ = asserts.checked_cast(recv_place.typ, typs.PtrTyp)
            method = ir_traits.lookup_member(
                recv_ptr_typ.pointee_typ,
                callee_ast.field.name,
                e.impl_registry,
                callee_ast.field.span,
            )

            if method is not None:
                recv_arg = recv_place
                recv_ast = callee_ast.struct
                callee = method
            else:
                callee = self._build_struct_field_access(recv_place, callee_ast, _ExprContext.VALUE)
        else:
            callee = self._build_expr(callee_ast, e, _ExprContext.VALUE)

        callee_ptr_typ = asserts.checked_cast(callee.typ, typs.PtrTyp)
        fn_typ = asserts.checked_cast(callee_ptr_typ.pointee_typ, typs.CallableTyp)
        param_typs = fn_typ.param_typs
        offset = 1 if recv_ast is not None else 0
        arg_asts: list[ast.Expr] = ([recv_ast] if recv_ast is not None else []) + list(
            call_ast.args
        )
        lowered_args = tuple(
            self._build_expr(arg_ast, e, _ExprContext.VALUE, param_typs[i])
            for i, arg_ast in enumerate(call_ast.args, start=offset)
        )
        args = ((recv_arg,) if recv_arg is not None else ()) + lowered_args

        # TypCheck already confirmed each argument (including the
        # receiver, if any) coerces to its parameter's type.
        coerced_args = tuple(
            opt_util.opt_unwrap(self._coerce(arg, arg_asts[i])) for i, arg in enumerate(args)
        )

        return self._in_context(self._curr_bb.call(callee, coerced_args, call_ast), ctx)

    def _build_bin_op_expr(
        self,
        op_ast: ast.BinOpExpr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a binary operator expression (arithmetic or comparison).

        :param op_ast: The parsed binary operation.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :param expected_typ: The type the expression is expected to have,
            if known. Only used when *both* operands are bare integer
            literals and so have nothing else to take a type from; when
            either operand's type is already decided, that type is what
            the other has to match.
        :return: The operation's result.
        """
        if op_ast.op.name in ("and", "or"):
            return self._build_logic_bin_op_expr(op_ast, e, ctx)

        # The operands are peers: whichever one's type is already decided
        # is what the other has to match, so lower that one first and let
        # a bare integer literal on either side take its type from it.
        # The deferred operand is always such a literal, which emits no
        # instructions, so left-to-right evaluation is unaffected.
        if typcheck.is_flexible_int_lit(op_ast.lhs) and not typcheck.is_flexible_int_lit(
            op_ast.rhs
        ):
            rhs = self._build_expr(op_ast.rhs, e, _ExprContext.VALUE)
            propagated = self._propagate_never(rhs, op_ast.rhs, ctx)
            if propagated is not None:
                return propagated
            # A bare integer literal always ends up with an integer type,
            # so the left operand needs no separate check here.
            lhs = self._build_expr(
                op_ast.lhs, e, _ExprContext.VALUE, typcheck.resolve_peer_typ([rhs.typ])
            )
        else:
            lhs = self._build_expr(
                op_ast.lhs,
                e,
                _ExprContext.VALUE,
                expected_typ if typcheck.is_flexible_int_lit(op_ast.lhs) else None,
            )
            propagated = self._propagate_never(lhs, op_ast.lhs, ctx)
            if propagated is not None:
                return propagated

            rhs = self._build_expr(
                op_ast.rhs,
                e,
                _ExprContext.VALUE,
                typcheck.resolve_peer_typ([lhs.typ])
                if typcheck.is_flexible_int_lit(op_ast.rhs)
                else None,
            )
            propagated = self._propagate_never(rhs, op_ast.rhs, ctx)
            if propagated is not None:
                return propagated

        # lhs and rhs are already known (by TypCheck) to be equal integer
        # types by this point.
        lhs_typ = asserts.checked_cast(lhs.typ, typs.IntTyp)
        op = op_ast.op.name
        match op:
            case "<" | "<=" | "==" | "!=" | ">=" | ">":
                if lhs_typ.signage == signage.SIGNED:
                    res = self._curr_bb.icmp_signed(op, lhs, rhs, op_ast)
                else:
                    res = self._curr_bb.icmp_unsigned(op, lhs, rhs, op_ast)
            case "+":
                res = self._curr_bb.add(lhs, rhs, op_ast)
            case "-":
                res = self._curr_bb.sub(lhs, rhs, op_ast)
            case "*":
                res = self._curr_bb.mul(lhs, rhs, op_ast)
            case "/":
                if lhs_typ.signage == signage.SIGNED:
                    res = self._curr_bb.sdiv(lhs, rhs, op_ast)
                else:
                    res = self._curr_bb.udiv(lhs, rhs, op_ast)
            case _:
                raise AssertionError(f"unhandled binary operator {op!r}")

        return self._in_context(res, ctx)

    def _build_logic_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a short-circuiting ``and``/``or`` expression.

        Modelled on :meth:`_build_if_expr`: the right operand is only
        evaluated when it can affect the result, in its own basic block,
        merged with the short-circuited outcome via a phi.

        :param op_ast: The parsed binary operation (``op.name`` is
            ``"and"`` or ``"or"``).
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The operation's result.
        """
        is_and = op_ast.op.name == "and"
        lhs = self._build_expr(op_ast.lhs, e, _ExprContext.VALUE)
        # Unlike the rhs below, a diverging lhs can't just be coerced and
        # carried on: lhs_last_bb becomes one of the phi's own incoming
        # predecessors further down, and if lhs diverged, that block's
        # real terminator is whatever caused the divergence, not a branch
        # into the merge block - so the phi would claim a predecessor
        # that doesn't actually branch there, which LLVM rejects. So
        # never is handled separately, before this is allowed to reach
        # _coerce at all.
        propagated = self._propagate_never(lhs, op_ast.lhs, ctx)
        if propagated is not None:
            return propagated
        # TypCheck already confirmed lhs coerces to bool.
        lhs = opt_util.opt_unwrap(self._coerce(lhs, op_ast.lhs))

        rhs_bb = self._add_bb("and_rhs" if is_and else "or_rhs")
        end_bb = self._add_bb("and_end" if is_and else "or_end")
        # `and` evaluates the rhs when the lhs is true; `or`, when it's
        # false. The value reaching end_bb directly is then the lhs's own
        # outcome: false for `and`, true for `or`.
        short_circuit = ir_values.ComptimeBool(not is_and, op_ast)
        if is_and:
            self._cbranch(lhs, rhs_bb, end_bb, op_ast)
        else:
            self._cbranch(lhs, end_bb, rhs_bb, op_ast)
        lhs_last_bb = self._curr_bb  # cbranch doesn't move _curr_bb

        self._set_position(rhs_bb)
        rhs = self._build_expr(op_ast.rhs, e, _ExprContext.VALUE)
        rhs_last_bb = self._curr_bb  # building the rhs may have moved it
        # TypCheck already confirmed rhs coerces to bool.
        coerced_rhs = opt_util.opt_unwrap(self._coerce(rhs, op_ast.rhs))

        if rhs_last_bb.terminated:
            # The rhs diverged, so only the short-circuit path reaches
            # end_bb and there is nothing to merge.
            self._set_position(end_bb)
            return self._in_context(short_circuit, ctx)

        self._branch(end_bb, op_ast.rhs)
        self._set_position(end_bb)
        res = self._curr_bb.phi({lhs_last_bb: short_circuit, rhs_last_bb: coerced_rhs}, op_ast)
        return self._in_context(res, ctx)

    def _build_unary_op_expr(
        self,
        op_ast: ast.UnaryOpExpr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a unary operator expression (``&``, ``-``, or ``not``).

        :param op_ast: The parsed unary operation.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :param expected_typ: The type the expression is expected to have,
            if known. Negation preserves its operand's type, so this is
            passed on to the operand; ``&`` and ``not`` ignore it.
        :return: The operation's result.
        """
        match op_ast.op.name:
            case "&":
                return self._in_context(
                    self._build_expr(op_ast.operand, e, _ExprContext.PLACE),
                    ctx,
                )
            case "not":
                operand = self._build_expr(op_ast.operand, e, _ExprContext.VALUE)
                # TypCheck already confirmed operand coerces to bool.
                coerced_operand = opt_util.opt_unwrap(self._coerce(operand, op_ast.operand))
                return self._in_context(self._curr_bb.not_(coerced_operand, op_ast), ctx)
            case "-":
                # A literal operand is folded into a single negative
                # constant rather than being built and then negated, so
                # that a signed type's minimum value can be written: the
                # positive half of the range stops one short of it, so
                # e.g. the 128 in -128i8 would overflow on its own.
                if isinstance(op_ast.operand, ast.IntLit):
                    return self._in_context(
                        self._build_int_lit(op_ast.operand, True),
                        ctx,
                    )

                operand = self._build_expr(op_ast.operand, e, _ExprContext.VALUE, expected_typ)
                propagated = self._propagate_never(operand, op_ast.operand, ctx)
                if propagated is not None:
                    return propagated
                return self._in_context(self._curr_bb.neg(operand, op_ast), ctx)
            case _:
                raise AssertionError(f"unhandled unary operator {op_ast.op.name!r}")

    def _resolve_fn_instance(
        self, fn: ir_module.Fn, typ_args: tuple[typs.Typ, ...]
    ) -> ir_module.FnInstance:
        """Get the concrete instance a generic call or reference resolves to.

        Shared by :meth:`_build_call_expr` and :meth:`_build_var_expr`:
        TypCheck records ``(fn, typ_args)`` the same way for a call and a
        bare (explicitly-applied) reference alike, so lowering either
        only has to turn that pair into the one instance it names.

        :param fn: The generic function TypCheck resolved.
        :param typ_args: Its type arguments as TypCheck recorded them -
            against ``fn``'s own declaration, so possibly still
            containing a type parameter (e.g. a generic function
            recursing on its own type parameter, or naming another
            generic item applied to its own type parameter). Substituted
            here against this lowering's own type arguments (a no-op
            outside a generic instance) to resolve down to concrete
            types before asking for the instance itself.
        :return: The (possibly newly-built, possibly cached) instance.
        """
        concrete_typ_args = tuple(
            typ_arg.substitute_typ_params(self._typ_arg_mapping) for typ_arg in typ_args
        )
        return fn.instance(concrete_typ_args)

    def _build_var_expr(
        self, var_ast: ast.VarExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a (possibly qualified) variable or function reference.

        If ``var_ast`` names a generic function applied to explicit type
        arguments (e.g. the ``id[i32]`` in ``let f = id[i32];``), the
        result is that instance itself, usable as an ordinary function
        pointer from here on - TypCheck already resolved which one (see
        :meth:`~leech.typcheck.TypCheck._check_var_expr`), so this only has
        to fetch (or build) it, the same way :meth:`_build_call_expr` does
        for a generic call.

        :param var_ast: The parsed variable expression.
        :param e: The scope to resolve the path in.
        :param ctx: Whether to lower the result for its value or its
            address; ignored for function references, which are always
            returned as-is.
        :return: The referenced variable or function.
        :raises ItemNotFoundError: If the path cannot be resolved.
        """
        generic_ref = self._typ_check_results.generic_var_ref(var_ast)
        if generic_ref is not None:
            # TypCheck already resolved which generic function this names
            # and its explicit type arguments; this just has to get
            # (building, if needed) the instance they name.
            return self._resolve_fn_instance(*generic_ref)

        var = e.resolve_path(ir_env.Env.Namespace.VARS, var_ast.path)
        if isinstance(var, ir_module.FnSpec):
            return var
        if ctx == _ExprContext.PLACE:
            return var

        return self._curr_bb.load(var, var_ast)

    def _build_array_expr(
        self,
        arr_expr: ast.ArrayExpr,
        e: ir_env.Env,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
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
        """
        expected_elt_typ = (
            expected_typ.element_typ if isinstance(expected_typ, typs.ArrayTyp) else None
        )

        # Two passes, so that a bare integer literal takes its type from
        # its sibling elements wherever it sits, rather than the first
        # element deciding for everyone. The deferred elements are all
        # bare literals, which emit no instructions, so lowering them
        # second can't reorder any of the others' effects.
        built: list[Optional[ir_values.Value]] = [None] * len(arr_expr.elements)
        for i, elt_ast in enumerate(arr_expr.elements):
            if not typcheck.is_flexible_int_lit(elt_ast):
                built[i] = self._build_expr(elt_ast, e, _ExprContext.VALUE, expected_elt_typ)

        # An element type the surrounding context supplied is what the
        # whole array has to coerce to, so it wins over the elements'
        # own types; without one, the elements decide among themselves.
        if expected_elt_typ is not None:
            elt_typ = expected_elt_typ
        else:
            elt_typ = typcheck.resolve_peer_typ([v.typ for v in built if v is not None])

        elts = [
            v
            if v is not None
            else self._build_expr(arr_expr.elements[i], e, _ExprContext.VALUE, elt_typ)
            for i, v in enumerate(built)
        ]

        arr_typ = typs.ArrayTyp.get_or_create(elt_typ, len(elts))
        arr = ir_values.ComptimeArray(
            arr_typ,
            [ir_values.UndefValue(arr_typ.element_typ, arr_expr)] * len(elts),
            arr_expr,
        )

        # TypCheck already confirmed every element coerces to elt_typ.
        for i, elt in enumerate(elts):
            elt_ast = arr_expr.elements[i]
            coerced = opt_util.opt_unwrap(self._coerce(elt, elt_ast))
            elt_index = ir_values.ComptimeInt(typs.USIZE, i, elt_ast)
            arr = self._curr_bb.insert_value(arr, coerced, (elt_index,), elt_ast)

        return self._in_context(arr, ctx)

    def _build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower an array indexing expression (``arr[index]``).

        :param aa_expr: The parsed array access expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The indexed element, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.
        """
        arr_ptr = self._build_expr(aa_expr.array, e, _ExprContext.PLACE)

        # An index is a coercion point like any other unambiguous target
        # type: a bare literal infers as usize, and a narrower unsigned
        # value widens to it. TypCheck already confirmed it coerces.
        index = self._build_expr(aa_expr.index, e, _ExprContext.VALUE, typs.USIZE)
        index = opt_util.opt_unwrap(self._coerce(index, aa_expr.index))

        # TODO: bounds check

        elt_ptr = self._curr_bb.gep(arr_ptr, index, aa_expr)
        return self._deref_in_context(elt_ptr, ctx, aa_expr)

    def _build_struct_expr(
        self, struct_expr: ast.StructExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a struct literal expression.

        :param struct_expr: The parsed struct expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The constructed struct value.
        :raises ItemNotFoundError: If the named type cannot be resolved.
        """
        resolved_typ = typs.Typ.from_ast(struct_expr.typ, e)
        struct_typ = asserts.checked_cast(resolved_typ, typs.StructTyp)

        struct = ir_values.ComptimeStruct(
            struct_typ,
            {f.name: ir_values.UndefValue(f.typ, struct_expr) for f in struct_typ.fields.values()},
            struct_expr,
        )

        # TypCheck already confirmed every given field name is real,
        # accessible and given only once, and that every field of
        # struct_typ is given a value that coerces to its type.
        field_values: dict[str, ir_values.Value] = {}
        field_value_asts: dict[str, ast.Expr] = {}
        for field_expr in struct_expr.fields:
            field = struct_typ.fields[field_expr.ident.name]
            field_values[field_expr.ident.name] = self._build_expr(
                field_expr.value, e, _ExprContext.VALUE, field.typ
            )
            field_value_asts[field_expr.ident.name] = field_expr.value

        for i, field in enumerate(struct_typ.fields.values()):
            field_value = field_values[field.name]
            coerced = opt_util.opt_unwrap(self._coerce(field_value, field_value_asts[field.name]))
            field_index = ir_values.ComptimeInt(typs.I32, i, field_value.ast)
            struct = self._curr_bb.insert_value(struct, coerced, (field_index,), field_value.ast)

        return self._in_context(struct, ctx)

    def _build_struct_access_expr(
        self, sa_expr: ast.StructAccessExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a struct field access expression (``s.field``).

        :param sa_expr: The parsed struct access expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.
        """
        struct_ptr = self._build_expr(sa_expr.struct, e, _ExprContext.PLACE)
        return self._build_struct_field_access(struct_ptr, sa_expr, ctx)

    def _build_struct_field_access(
        self, struct_ptr: ir_values.Value, sa_expr: ast.StructAccessExpr, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower ``s.field`` given ``s``'s already-lowered place pointer.

        Split out of :meth:`_build_struct_access_expr` so
        :meth:`_build_call_expr` can fall back to plain field access
        (e.g. a struct field that happens to hold a callable value)
        without re-lowering ``sa_expr.struct`` a second time. TypCheck
        already confirmed ``struct_ptr``'s pointee is a struct type with
        an accessible field named ``sa_expr.field``.

        :param struct_ptr: ``sa_expr.struct`` already lowered in
            :attr:`~_ExprContext.PLACE` context.
        :param sa_expr: The parsed struct access expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.
        """
        struct_ptr_typ = asserts.checked_cast(struct_ptr.typ, typs.PtrTyp)
        struct_typ = asserts.checked_cast(struct_ptr_typ.pointee_typ, typs.StructTyp)
        field = struct_typ.fields[sa_expr.field.name]

        index = ir_values.ComptimeInt(typs.I32, field.index, sa_expr)
        field_ptr = self._curr_bb.gep(struct_ptr, index, sa_expr)
        return self._deref_in_context(field_ptr, ctx, sa_expr)

    def _build_deref_expr(
        self, d_expr: ast.DerefExpr, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a pointer dereference expression (``ptr.*``).

        :param d_expr: The parsed dereference expression.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The pointee's value, or the pointer itself unchanged if
            ``ctx`` is :attr:`~_ExprContext.PLACE`.
        """
        ptr = self._build_expr(d_expr.ptr, e, _ExprContext.VALUE)
        return self._deref_in_context(ptr, ctx, d_expr)

    def _build_stmt(self, stmt_ast: ast.Stmt, e: ir_env.Env) -> None:
        """Lower any statement, dispatching to the ``build_*_stmt`` method
        matching its AST node kind.

        :param stmt_ast: The parsed statement.
        :param e: The scope to resolve names in.
        """
        match stmt_ast:
            case ast.ExprStmt():
                value = self._build_expr(stmt_ast.expr, e, _ExprContext.VALUE)
                if value.typ == typs.NEVER and not self._curr_bb.terminated:
                    self._curr_bb.unreachable(stmt_ast.expr)
            case ast.RetStmt():
                self._build_ret_stmt(stmt_ast, e)
            case ast.LetStmt():
                self._build_let_stmt(stmt_ast, e)
            case ast.AssignmentStmt():
                self._build_assignment_stmt(stmt_ast, e)
            case ast.BreakStmt():
                self._build_break_stmt(stmt_ast)
            case ast.ContinueStmt():
                self._build_continue_stmt(stmt_ast)

    def _build_ret_stmt(self, ret_ast: ast.RetStmt, e: ir_env.Env) -> None:
        """Lower a ``return`` statement.

        :param ret_ast: The parsed return statement.
        :param e: The scope to resolve names in.
        """
        assert self._fn is not None
        ret_typ = self._fn.fn_typ.ret_typ

        if ret_ast.expr is not None:
            expr = self._build_expr(ret_ast.expr, e, _ExprContext.VALUE, ret_typ)
            if expr.typ == typs.NEVER:
                if not self._curr_bb.terminated:
                    self._curr_bb.unreachable(ret_ast)
                return
            # TypCheck already confirmed expr coerces to ret_typ.
            coerced = opt_util.opt_unwrap(self._coerce(expr, ret_ast.expr))
            self._ret(coerced, ret_ast)
            return

        # TypCheck already confirmed a bare `return;` only appears where
        # ret_typ is void (else it would have raised InvalidVoidRetError).
        asserts.assert_eq(ret_typ, typs.VOID)
        self._ret(ir_values.VoidValue(ret_ast), ret_ast)

    def _build_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        """Lower a ``break`` statement.

        :param break_ast: The parsed break statement.
        """
        target = self._resolve_loop_label(break_ast.label)
        self._branch(target.end_bb, break_ast)

    def _build_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        """Lower a ``continue`` statement.

        :param continue_ast: The parsed continue statement.
        """
        target = self._resolve_loop_label(continue_ast.label)
        self._branch(target.cond_bb, continue_ast)

    def _resolve_loop_label(self, label: Optional[ast.Ident]) -> CfgBuilder._LoopCtx:
        """Find the loop a ``break``/``continue`` targets.

        TypCheck already confirmed :attr:`_loop_stack` is non-empty and,
        if ``label`` is given, that some enclosing loop has it.

        :param label: The target label, or ``None`` for the innermost
            enclosing loop.
        :return: The named loop, or the innermost one if ``label`` is
            ``None``.
        """
        if label is None:
            return self._loop_stack[-1]
        for ctx in reversed(self._loop_stack):
            if ctx.label == label.name:
                return ctx
        raise AssertionError(f"unresolved loop label {label.name!r}")

    def _build_let_stmt(self, let_ast: ast.LetStmt, e: ir_env.Env) -> None:
        """Lower a local ``let`` statement.

        :param let_ast: The parsed ``let`` statement.
        :param e: The scope to resolve names in, and to bind the new
            variable in.
        """
        expr = self._build_let_initializer(let_ast, e, _ExprContext.VALUE)
        name = let_ast.ident.name
        mut = typs.Mutability.from_ast(let_ast.mut)
        alloca = self._curr_bb.alloca(expr.typ, mut, 1, let_ast)
        e.add_var(name, alloca)
        self._curr_bb.store(expr, alloca, let_ast)

    def _build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: ir_env.Env) -> None:
        """Lower an assignment statement (``place = expr``).

        Leech deliberately evaluates the place expression before the value
        expression (matching JavaScript, Java, C#, and Go; the opposite of
        Rust, Python, and C++17's built-in ``=``). Where the place and
        value expressions both have observable side effects (e.g. a call
        in an array index alongside a call on the right-hand side), the
        place's side effects happen first. This is also what makes the
        place's type available to use as the value expression's
        :meth:`_build_expr`-level ``expected_typ`` hint (needed e.g. to
        resolve an empty array literal assigned into a place of known
        array type).

        :param ass_ast: The parsed assignment statement.
        :param e: The scope to resolve names in.
        """
        place = self._build_expr(ass_ast.place, e, _ExprContext.PLACE)
        place_typ = asserts.checked_cast(place.typ, typs.PtrTyp)
        assert place_typ.pointee_typ != typs.VOID, "assignment place cannot be void"
        expr = self._build_expr(ass_ast.expr, e, _ExprContext.VALUE, place_typ.pointee_typ)

        # TypCheck already confirmed place isn't const and expr coerces.
        asserts.assert_eq(place_typ.mut, typs.MUT)
        coerced = opt_util.opt_unwrap(self._coerce(expr, ass_ast.expr))
        self._curr_bb.store(coerced, place, ass_ast)

    def _build_let_initializer(
        self, let_ast: ast.LetStmt, e: ir_env.Env, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a ``let`` statement's initializer expression, shared by
        :meth:`_build_let_stmt` and :meth:`build_var_initializer`.

        If ``let_ast`` has a declared type, the initializer is coerced to
        it; otherwise the initializer's own type is used as-is, with no
        coercion.

        :param let_ast: The parsed ``let`` statement.
        :param e: The scope to resolve names in.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The (possibly coerced) initializer's value.
        """
        declared_typ_ast = let_ast.typ
        expected_typ = opt_util.opt_map(declared_typ_ast, lambda t: typs.Typ.from_ast(t, e))
        expr = self._build_expr(let_ast.expr, e, ctx, expected_typ)
        if declared_typ_ast is None:
            return expr
        # TypCheck already confirmed expr coerces to expected_typ.
        return opt_util.opt_unwrap(self._coerce(expr, let_ast.expr))

    def _build_int_lit(self, lit_ast: ast.IntLit, negated: bool) -> ir_values.Value:
        """Lower an integer literal to a compile-time-known value.

        Its type was already chosen and overflow-checked while type
        checking (see :meth:`~leech.typcheck.TypCheck._infer_int_lit_typ`);
        this only has to read that decision back and build the value.

        :param lit_ast: The parsed integer literal.
        :param negated: Whether the literal is the operand of a unary
            minus, and so denotes the negation of its written value.
        :return: The literal's value.
        """
        typ = self._typ_check_results.int_lit_typ(lit_ast)
        value = -lit_ast.value if negated else lit_ast.value
        return ir_values.ComptimeInt(typ, value, lit_ast)

    def _coerce(self, value: ir_values.Value, node: ast.Ast) -> Optional[ir_values.Value]:
        """Convert ``value`` to the type its context expects, if allowed.

        The decision - whether a coercion applies, and which kind - was
        already made while type checking (see
        :meth:`~leech.typcheck.TypCheck._record_coercion`); this only has
        to emit it. Callers are the handful of sites with an unambiguous
        target type - call arguments, assignment, ``return`` and a
        function's tail expression, struct literal fields, an array
        access index, and array literal elements whose type comes from
        context. Operator operands deliberately don't coerce.

        Returning ``None`` rather than raising lets each caller report the
        failure with the error class that fits its context.

        :param value: The value to convert.
        :param node: The AST node ``value`` was checked against its
            expected type with (see :meth:`TypCheckResults.coercion`), and
            to attribute any emitted instruction to.
        :return: A value of the expected type, or ``None`` if ``value``'s
            type doesn't coerce to it.
        """
        coercion = self._typ_check_results.coercion(node)
        match coercion:
            case None:
                return value
            case typcheck.Invalid():
                return None
            case typcheck.NeverDiverge(target=target):
                # A diverging expression never actually produces a value,
                # so it coerces to any type - by the time a real value
                # would be read here, control can't have reached this
                # point. target was recorded against the generic
                # declaration's own (possibly type-parameter-typed)
                # signature, so it needs substituting for this lowering's
                # concrete type arguments - a no-op outside a generic
                # instance, where _typ_arg_mapping is empty.
                if not self._curr_bb.terminated:
                    self._curr_bb.unreachable(node)
                return ir_values.UndefValue(
                    target.substitute_typ_params(self._typ_arg_mapping), node
                )
            case typcheck.IntExt(target=target):
                return self._curr_bb.int_ext(value, target, node)
            case typcheck.PtrMutRelax():
                # A *mut T given where a *T is wanted needs nothing
                # emitted - the two have the same representation.
                return value
            case _:
                raise AssertionError(f"unhandled coercion {coercion!r}")

    def _propagate_never(
        self, value: ir_values.Value, ast_node: ast.Ast, ctx: _ExprContext
    ) -> Optional[ir_values.Value]:
        """If ``value`` is ``never``-typed, terminate the current block.

        For operators, which don't coerce toward a target type (see
        :meth:`_coerce`): a diverging operand makes the whole operator
        expression diverge too, the same way :meth:`_build_if_expr`
        already treats a branch that never reaches its end.

        :param value: The already-built operand to check.
        :param ast_node: The AST node to attribute a new terminator to.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: A ``never``-typed result, or ``None`` if ``value`` isn't
            ``never``-typed, so the caller should proceed with ``value``
            normally.
        """
        if value.typ != typs.NEVER:
            return None
        if not self._curr_bb.terminated:
            self._curr_bb.unreachable(ast_node)
        return self._in_context(ir_values.NeverValue(ast_node), ctx)

    def _value_to_ptr(self, value: ir_values.Value) -> ir_values.Value[typs.PtrTyp]:
        alloca = self._curr_bb.alloca(value.typ, typs.CONST, 1, value.ast)
        self._curr_bb.store(value, alloca, value.ast)
        return alloca

    def _in_context(self, value: ir_values.Value, ctx: _ExprContext) -> ir_values.Value:
        if ctx == _ExprContext.PLACE:
            return self._value_to_ptr(value)
        return value

    def _deref_in_context(
        self, ptr: ir_values.Value, ctx: _ExprContext, ast: Optional[ast.Ast]
    ) -> ir_values.Value:
        if ctx == _ExprContext.PLACE:
            return ptr
        return self._curr_bb.load(ptr, ast)

    def _add_bb(self, basename: str) -> ir_values.BasicBlock:
        """Create a new, empty basic block and add it to :attr:`cfg`.

        :param basename: A human-readable name to derive the block's
            (uniquified) name from.
        :return: The new basic block. It is not made the current block;
            use :meth:`_set_position` for that.
        """
        bb = ir_values.BasicBlock(self._generate_bb_name(basename))
        self.cfg.add_node(bb)
        return bb

    def _set_position(self, bb: ir_values.BasicBlock) -> None:
        """Make ``bb`` the block that subsequent instructions are added to.

        :param bb: The basic block to switch to; must already be in
            :attr:`cfg`.
        """
        asserts.assert_in(bb, self.cfg)
        self._curr_bb = bb

    def _branch(self, target: ir_values.BasicBlock, ast: ast.Ast) -> None:
        """Terminate :attr:`_curr_bb` with an unconditional branch.

        Does not change :attr:`_curr_bb`, the same as :meth:`_cbranch` and
        :meth:`_ret`; call :meth:`_set_position` next if what follows
        should build into ``target`` (not every caller wants that - e.g.
        ``break``/``continue`` deliberately leave :attr:`_curr_bb` as the
        block they terminated, so any source code still to come after
        them in the same block is correctly dropped as unreachable
        rather than appended into ``target``).

        :param target: The basic block to branch to; must already be in
            :attr:`cfg`.
        :param ast: The AST node the branch instruction is attributed to.
        """
        asserts.assert_in(target, self.cfg)
        self._curr_bb.branch(target, ast)
        self.cfg.add_edge(self._curr_bb, target)

    def _cbranch(
        self,
        condition: ir_values.Value,
        true_target: ir_values.BasicBlock,
        false_target: ir_values.BasicBlock,
        ast: ast.Ast,
    ) -> None:
        """Terminate :attr:`_curr_bb` with a conditional branch.

        Does not change :attr:`_curr_bb`; the caller is expected to call
        :meth:`_set_position` next.

        :param condition: The boolean value to branch on.
        :param true_target: The block to branch to if ``condition`` is
            true; must already be in :attr:`cfg`.
        :param false_target: The block to branch to if ``condition`` is
            false; must already be in :attr:`cfg`.
        :param ast: The AST node the branch instruction is attributed to.
        """
        asserts.assert_in(true_target, self.cfg)
        asserts.assert_in(false_target, self.cfg)
        self._curr_bb.cbranch(condition, true_target, false_target, ast)
        self.cfg.add_edge(self._curr_bb, true_target)
        self.cfg.add_edge(self._curr_bb, false_target)

    def _ret(self, value: ir_values.Value, ast: Optional[ast.Ast]) -> None:
        """Terminate :attr:`_curr_bb` with a return of ``value``.

        :param value: The value to return.
        :param ast: The AST node the return instruction is attributed to.
        """
        self._curr_bb.ret(value, ast)
        self.cfg.add_edge(self._curr_bb, self.cfg.exit)
