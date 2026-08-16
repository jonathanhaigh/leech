# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""AST-to-IR lowering for already type-checked programs.

Lowering trusts and reuses the decisions recorded in ``TypCheckResults``.
"""

import dataclasses
import enum
from collections.abc import Mapping
from typing import Final, Optional, cast

from leech import (
    asserts,
    ast,
    ir_module,
    ir_traits,
    ir_values,
    naming,
    opt_util,
    resolve,
    signage,
    typcheck,
    typs,
)


class _ExprContext(enum.Enum):
    """Whether an expression is lowered to its value or its address."""

    PLACE = 0
    VALUE = 1


class CfgBuilder:
    """Lower one function body or module initializer to a control-flow graph.

    Generic instances substitute ``typ_arg_mapping`` into recorded facts that still contain
    declaration parameters.
    """

    @dataclasses.dataclass(frozen=True)
    class _LoopCtx:
        """The ``break``/``continue`` targets for one enclosing ``while`` loop."""

        cond_bb: ir_values.BasicBlock
        end_bb: ir_values.BasicBlock

    _fn: Final[Optional[ir_module.FnSpec]]
    _typ_check_results: Final[typcheck.TypCheckResults]
    _impl_registry: Final[ir_traits.ImplRegistry]
    _panic_fn: Final[Optional[ir_module.FnSpec]]
    _typ_arg_mapping: Final[Mapping[typs.TypParamTyp, typs.Typ]]
    cfg: Final[ir_values.Cfg]
    _curr_bb: ir_values.BasicBlock
    _generate_bb_name: Final[naming.VarNamer]
    #: Every while loop lowered so far, keyed by its own AST node - the
    #: same identity TypCheck resolved a break/continue target to.
    _loop_ctxs: Final[dict[ast.WhileExpr, CfgBuilder._LoopCtx]]
    #: Each local binding's lowered storage, keyed by its declaration-site
    #: AST node - the same identity TypCheck resolved a VarExpr to.
    _local_values: Final[dict[resolve.LocalDecl, ir_values.Value]]

    def __init__(
        self,
        typ_check_results: typcheck.TypCheckResults,
        impl_registry: ir_traits.ImplRegistry,
        panic_fn: Optional[ir_module.FnSpec],
        fn: Optional[ir_module.FnSpec] = None,
        typ_arg_mapping: Optional[Mapping[typs.TypParamTyp, typs.Typ]] = None,
    ) -> None:
        self._fn = fn
        self._typ_check_results = typ_check_results
        self._impl_registry = impl_registry
        self._panic_fn = panic_fn
        self._typ_arg_mapping = opt_util.opt_or_default(typ_arg_mapping, {})
        self.cfg = ir_values.Cfg()
        self._generate_bb_name = naming.VarNamer()
        # Reserve the "entry"/"exit" basenames so a later `add_bb` call
        # never collides with the blocks `Cfg.__init__` already created.
        self._generate_bb_name("entry")
        self._generate_bb_name("exit")
        self._curr_bb = self.cfg.entry
        self._loop_ctxs = {}
        self._local_values = {}

    def build_var_initializer(self, defn_ast: ast.VarDefn) -> None:
        """Lower a module-level initializer into ``cfg``."""
        assert self._fn is None
        let_ast = defn_ast.let_stmt
        initializer = self._build_let_initializer(let_ast, _ExprContext.VALUE)
        self._ret(initializer, let_ast.expr)

    def build_fn(self, fn_ast: ast.FnDefn) -> None:
        """Lower a function body into ``cfg``."""
        assert self._fn is not None

        for param in self._fn.params:
            assert param.ast is not None
            alloca = self._curr_bb.alloca(param.typ, typs.CONST, 1, param.ast)
            self._curr_bb.store(param, alloca, param.ast)
            self._local_values[param.ast] = alloca

        ret_typ = self._fn.fn_typ.ret_typ
        block = self._build_expr(fn_ast.block, _ExprContext.VALUE, ret_typ)
        ret_ast = opt_util.opt_or_default(fn_ast.block.expr, fn_ast.block)

        if block.typ != typs.VOID:
            self._finish_ret(block, ret_ast, ret_ast)
            return

        if self._curr_bb.terminated:
            return

        # Only reachable if ret_typ is void too - else TypCheck would have
        # raised MissingRetError.
        asserts.assert_eq(ret_typ, typs.VOID)
        self._ret(ir_values.VoidValue(ret_ast), ret_ast)

    def _finish_ret(
        self, value: ir_values.Value, attribute_ast: ast.Ast, coerce_ast: ast.Ast
    ) -> None:
        """Emit ``unreachable`` for ``never`` or coerce and return ``value``.

        :pre: value coerces to the enclosing function's return type [opt_unwrap]
        """
        if value.typ == typs.NEVER:
            if not self._curr_bb.terminated:
                self._curr_bb.unreachable(attribute_ast)
            return
        coerced = opt_util.opt_unwrap(self._coerce(value, coerce_ast))
        self._ret(coerced, attribute_ast)

    def _build_expr(
        self,
        expr_ast: ast.Expr,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower an expression in value or address context, using an optional type hint."""
        match expr_ast:
            case ast.BlockExpr():
                return self._build_block_expr(expr_ast, ctx, expected_typ)
            case ast.IfExpr():
                return self._build_if_expr(expr_ast, ctx, expected_typ)
            case ast.WhileExpr():
                return self._build_while_expr(expr_ast, ctx)
            case ast.CallExpr():
                return self._build_call_expr(expr_ast, ctx)
            case ast.BinOpExpr():
                return self._build_bin_op_expr(expr_ast, ctx, expected_typ)
            case ast.UnaryOpExpr():
                return self._build_unary_op_expr(expr_ast, ctx, expected_typ)
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
                return self._build_var_expr(expr_ast, ctx)
            case ast.ArrayExpr():
                return self._build_array_expr(expr_ast, ctx, expected_typ)
            case ast.ArrayAccessExpr():
                return self._build_array_access_expr(expr_ast, ctx)
            case ast.StructExpr():
                return self._build_struct_expr(expr_ast, ctx)
            case ast.FieldAccessExpr():
                return self._build_field_access_expr(expr_ast, ctx)
            case ast.DerefExpr():
                return self._build_deref_expr(expr_ast, ctx)
            case _:
                raise AssertionError(f"unhandled expression kind {expr_ast}")

    def _build_block_expr(
        self,
        block_ast: ast.BlockExpr,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a ``{ ... }`` block expression.

        :param block_ast: The parsed block expression.
        :param ctx: Whether to lower the tail expression for its value or
            its address.
        :param expected_typ: The type the block is expected to have, if
            known; the block's value is its tail expression's, so this is
            passed straight on to it.
        :return: The lowered tail expression's value if the block has one;
            otherwise a void value if its statements complete normally, or
            a never value if they already diverged (e.g. via ``return``).
        """
        for stmt_ast in block_ast.stmts:
            self._build_stmt(stmt_ast)

        if block_ast.expr is None:
            if self._curr_bb.terminated:
                return ir_values.NeverValue(block_ast)
            return ir_values.VoidValue(block_ast)
        return self._build_expr(block_ast.expr, ctx, expected_typ)

    def _build_if_expr(
        self,
        if_ast: ast.IfExpr,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower an ``if``/``else`` expression.

        :param if_ast: The parsed ``if`` expression.
        :param ctx: Whether to lower the taken branch for its value or its
            address.
        :param expected_typ: The type the expression is expected to have,
            if known; its value comes from whichever branch is taken, so
            this is passed on to both branches (but not the condition,
            which is always ``bool``).
        :return: The value of whichever branch is taken, merged via a phi
            instruction if both branches complete normally.

        :pre: if_ast.condition coerces to bool [opt_unwrap]
        :pre: if both arms produce a value, they have the same type [assert_eq]
        """
        cond = self._build_expr(if_ast.condition, _ExprContext.VALUE)
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
            self._build_if_arm(if_ast.then, then_bb, end_bb, ctx, expected_typ)
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
                els_ast, els_bb, end_bb, ctx, expected_typ
            )
            then, then_last_bb, have_then_value = self._build_if_arm(
                if_ast.then,
                then_bb,
                end_bb,
                ctx,
                typcheck.resolve_peer_typ([els.typ]) if have_els_value else expected_typ,
            )
        else:
            then, then_last_bb, have_then_value = self._build_if_arm(
                if_ast.then, then_bb, end_bb, ctx, expected_typ
            )
            els_hint = expected_typ
            if expected_typ is None and have_then_value and typcheck.is_flexible_int_lit(els_ast):
                els_hint = typcheck.resolve_peer_typ([then.typ])
            els, els_last_bb, have_els_value = self._build_if_arm(
                els_ast, els_bb, end_bb, ctx, els_hint
            )

        if have_then_value and have_els_value:
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
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ],
    ) -> tuple[ir_values.Value, ir_values.BasicBlock, bool]:
        """Lower one arm of an ``if``/``else`` into its own basic block.

        :param arm_ast: The parsed arm.
        :param arm_bb: The block to lower the arm into.
        :param end_bb: The block the arms merge at.
        :param ctx: Whether to lower the arm for its value or its address.
        :param expected_typ: The type the arm is expected to have, if
            known.
        :return: The arm's value, the block its lowering ended in (which
            isn't ``arm_bb`` if the arm contained control flow of its
            own), and whether control reaches ``end_bb`` from it.
        """
        self._set_position(arm_bb)
        value = self._build_expr(arm_ast, ctx, expected_typ)
        last_bb = self._curr_bb
        if value.typ == typs.NEVER and not last_bb.terminated:
            last_bb.unreachable(arm_ast)
        have_value = not last_bb.terminated
        if have_value:
            self._branch(end_bb, arm_ast)
        return value, last_bb, have_value

    def _build_while_expr(self, while_ast: ast.WhileExpr, _ctx: _ExprContext) -> ir_values.Value:
        """Lower a ``while`` loop expression.

        :param while_ast: The parsed ``while`` expression.
        :return: A void value.

        :pre: while_ast.condition coerces to bool [opt_unwrap]
        """
        cond_bb = self._add_bb("while_cond")
        loop_bb = self._add_bb("while_loop")
        end_bb = self._add_bb("while_end")

        self._branch(cond_bb, while_ast)
        self._set_position(cond_bb)
        cond = self._build_expr(while_ast.condition, _ExprContext.VALUE)
        cond = opt_util.opt_unwrap(self._coerce(cond, while_ast.condition))

        self._cbranch(cond, loop_bb, end_bb, while_ast)
        self._set_position(loop_bb)
        self._loop_ctxs[while_ast] = CfgBuilder._LoopCtx(cond_bb=cond_bb, end_bb=end_bb)
        self._build_expr(while_ast.block, _ExprContext.VALUE)

        if not self._curr_bb.terminated:
            self._branch(cond_bb, while_ast.block)

        self._set_position(end_bb)
        return ir_values.VoidValue(while_ast)

    def _build_call_expr(self, call_ast: ast.CallExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a call, including method receivers and generic instances.

        :param call_ast: The parsed call expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The call's result.

        :pre: every arg, including the receiver if any, coerces to its param type [opt_unwrap]
        """
        callee_ast = call_ast.callee
        recv_ast: Optional[ast.Expr] = None
        recv_arg: Optional[ir_values.Value] = None

        generic_call = self._typ_check_results.generic_call(call_ast)
        if generic_call is not None:
            # TypCheck recorded the resolved function and type arguments.
            callee: ir_values.Value = self._resolve_fn_instance(*generic_call)
        elif isinstance(callee_ast, ast.FieldAccessExpr):
            recv_place = self._build_place(callee_ast.value)
            cached = self._typ_check_results.resolutions.callee(call_ast)
            if cached is resolve.CalleeResolution.FIELD_ACCESS:
                method = None
            elif isinstance(cached, ir_traits.TraitMethod):
                recv_typ = recv_place.typ.pointee_typ
                impl = self._impl_registry.find_impl(cached.trait, recv_typ)
                assert impl is not None
                found = impl.get_method(cached)
                assert found is not None
                method = found.for_recv_typ(recv_typ)
            elif isinstance(cached, ir_module.Fn):
                # Resolved against the impl block's own abstract receiver -
                # map to this build's instance.
                method = self._resolve_sibling(cached)
            else:
                # A no-op unless resolved against a still-abstract receiver.
                method = cached.substitute_typ_params(self._typ_arg_mapping)

            if method is not None:
                recv_arg = recv_place
                recv_ast = callee_ast.value
                callee = method
            else:
                callee = self._build_struct_field_access(recv_place, callee_ast, _ExprContext.VALUE)
        else:
            callee = self._build_expr(callee_ast, _ExprContext.VALUE)

        callee_ptr_typ = asserts.checked_cast(callee.typ, typs.PtrTyp)
        fn_typ = asserts.checked_cast(callee_ptr_typ.pointee_typ, typs.CallableTyp)
        param_typs = fn_typ.param_typs
        offset = 1 if recv_ast is not None else 0
        arg_asts: list[ast.Expr] = ([recv_ast] if recv_ast is not None else []) + list(
            call_ast.args
        )
        lowered_args = tuple(
            self._build_expr(arg_ast, _ExprContext.VALUE, param_typs[i])
            for i, arg_ast in enumerate(call_ast.args, start=offset)
        )
        args = ((recv_arg,) if recv_arg is not None else ()) + lowered_args

        coerced_args = tuple(
            opt_util.opt_unwrap(self._coerce(arg, arg_asts[i])) for i, arg in enumerate(args)
        )

        return self._in_context(self._curr_bb.call(callee, coerced_args, call_ast), ctx)

    def _build_bin_op_expr(
        self,
        op_ast: ast.BinOpExpr,
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a binary operator expression (arithmetic or comparison).

        :param op_ast: The parsed binary operation.
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
            return self._build_logic_bin_op_expr(op_ast, ctx)

        # The operands are peers: whichever one's type is already decided
        # is what the other has to match, so lower that one first and let
        # a bare integer literal on either side take its type from it.
        # The deferred operand is always such a literal, which emits no
        # instructions, so left-to-right evaluation is unaffected.
        if typcheck.is_flexible_int_lit(op_ast.lhs) and not typcheck.is_flexible_int_lit(
            op_ast.rhs
        ):
            rhs = self._build_expr(op_ast.rhs, _ExprContext.VALUE)
            propagated = self._propagate_never(rhs, op_ast.rhs, ctx)
            if propagated is not None:
                return propagated
            # A bare integer literal always ends up with an integer type,
            # so the left operand needs no separate check here.
            lhs = self._build_expr(
                op_ast.lhs, _ExprContext.VALUE, typcheck.resolve_peer_typ([rhs.typ])
            )
        else:
            lhs = self._build_expr(
                op_ast.lhs,
                _ExprContext.VALUE,
                expected_typ if typcheck.is_flexible_int_lit(op_ast.lhs) else None,
            )
            propagated = self._propagate_never(lhs, op_ast.lhs, ctx)
            if propagated is not None:
                return propagated

            rhs = self._build_expr(
                op_ast.rhs,
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
                res = self._build_checked_bin_op("add", lhs, rhs, op_ast)
            case "-":
                res = self._build_checked_bin_op("sub", lhs, rhs, op_ast)
            case "*":
                res = self._build_checked_bin_op("mul", lhs, rhs, op_ast)
            case "/":
                res = self._build_checked_div(lhs, rhs, lhs_typ, op_ast)
            case _:
                raise AssertionError(f"unhandled binary operator {op!r}")

        return self._in_context(res, ctx)

    def _is_typ_min(
        self, value: ir_values.Value, typ: typs.IntTyp, op_ast: ast.Ast
    ) -> ir_values.Value:
        """Whether ``value`` equals ``typ.min_value`` - the one value a
        signed type's negation, or division by ``-1``, overflows on.

        :param value: The value to check; must have type ``typ``.
        :param typ: ``value``'s own (signed) type.
        :param op_ast: The AST node the emitted instruction is attributed to.
        :return: A ``bool``-typed value.
        """
        return self._curr_bb.icmp_signed(
            "==", value, ir_values.ComptimeInt(typ, typ.min_value, op_ast), op_ast
        )

    def _build_checked_bin_op(
        self,
        op: str,
        lhs: ir_values.Value,
        rhs: ir_values.Value,
        op_ast: ast.Ast,
    ) -> ir_values.Value:
        """Lower ``add``/``sub``/``mul``, panicking if the exact result
        overflows ``lhs``/``rhs``'s type.

        Builds a :class:`~leech.ir_values.CheckedAddInstr`/``CheckedSubInstr``/
        ``CheckedMulInstr`` - a plain :class:`~leech.ir_values.BinOpInstr`
        subclass, computing the operation's own (possibly wrapped) result
        exactly like the corresponding unchecked instruction, but paired
        with an :class:`~leech.ir_values.OverflowFlagInstr` that reports
        whether it overflowed - both lower to a single
        ``llvm.*.with.overflow`` intrinsic call between them (see
        :class:`~leech.codegen.Compiler`), rather than computing the
        operation twice. :class:`~leech.comptime.Interpreter` evaluates
        the two exactly as generically (no special-casing arithmetic
        overflow at all) - the checked instruction wraps like its LLVM
        counterpart, and the flag instruction reports whether it did -
        so :meth:`_panic_if`'s ``panic`` call is what reports overflow
        both at runtime and at compile time.

        :param op: Which operation to build: ``"add"``, ``"sub"``, or
            ``"mul"`` - the name of the :class:`~leech.ir_values.BasicBlock`
            ``checked_*`` method to call.
        :param lhs: The left operand.
        :param rhs: The right operand.
        :param op_ast: The AST node the emitted instructions are
            attributed to.
        :return: The operation's result.
        """
        res = getattr(self._curr_bb, f"checked_{op}")(lhs, rhs, op_ast)
        overflowed = self._curr_bb.overflow_flag(res, op_ast)
        self._panic_if(overflowed, "integer overflow", op_ast)
        return res

    def _build_checked_div(
        self,
        lhs: ir_values.Value,
        rhs: ir_values.Value,
        typ: typs.IntTyp,
        op_ast: ast.Ast,
    ) -> ir_values.Value:
        """Lower ``/``, panicking on division by zero or (for a signed
        type) the one case where the exact quotient doesn't fit:
        ``typ.min_value / -1``.

        Unlike :meth:`_build_checked_bin_op`'s operations, an actual
        ``sdiv``/``udiv`` by zero is a trap, not a well-defined wrapping
        result - so, unlike there, the checks here have to run *before*
        the real division, not after.

        :param lhs: The dividend.
        :param rhs: The divisor.
        :param typ: ``lhs`` and ``rhs``'s common type.
        :param op_ast: The AST node the emitted instructions are
            attributed to.
        :return: The quotient, at ``typ``.
        """
        is_signed = typ.signage == signage.SIGNED
        zero = ir_values.ComptimeInt(typ, 0, op_ast)
        rhs_is_zero = (
            self._curr_bb.icmp_signed("==", rhs, zero, op_ast)
            if is_signed
            else self._curr_bb.icmp_unsigned("==", rhs, zero, op_ast)
        )
        self._panic_if(rhs_is_zero, "division by zero", op_ast)

        if not is_signed:
            return self._curr_bb.udiv(lhs, rhs, op_ast)

        if self._curr_bb.terminated:
            # Already-dead code (see _panic_if) - stay inert rather than
            # build the remaining check's own control flow into it.
            return self._curr_bb.sdiv(lhs, rhs, op_ast)

        lhs_is_min = self._is_typ_min(lhs, typ, op_ast)
        maybe_overflow_bb = self._add_bb("div_maybe_overflow")
        ok_bb = self._add_bb("div_ok")
        self._cbranch(lhs_is_min, maybe_overflow_bb, ok_bb, op_ast)

        self._set_position(maybe_overflow_bb)
        rhs_is_neg_one = self._curr_bb.icmp_signed(
            "==", rhs, ir_values.ComptimeInt(typ, -1, op_ast), op_ast
        )
        self._panic_if(rhs_is_neg_one, "integer overflow", op_ast, ok_bb)

        return self._curr_bb.sdiv(lhs, rhs, op_ast)

    def _build_logic_bin_op_expr(self, op_ast: ast.BinOpExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a short-circuiting ``and``/``or`` expression.

        Modelled on :meth:`_build_if_expr`: the right operand is only
        evaluated when it can affect the result, in its own basic block,
        merged with the short-circuited outcome via a phi.

        :param op_ast: The parsed binary operation (``op.name`` is
            ``"and"`` or ``"or"``).
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The operation's result.

        :pre: op_ast.lhs coerces to bool [opt_unwrap]
        :pre: op_ast.rhs coerces to bool [opt_unwrap]
        """
        is_and = op_ast.op.name == "and"
        lhs = self._build_expr(op_ast.lhs, _ExprContext.VALUE)
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
        rhs = self._build_expr(op_ast.rhs, _ExprContext.VALUE)
        rhs_last_bb = self._curr_bb  # building the rhs may have moved it
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
        ctx: _ExprContext,
        expected_typ: Optional[typs.Typ] = None,
    ) -> ir_values.Value:
        """Lower a unary operator expression (``&``, ``-``, or ``not``).

        :param op_ast: The parsed unary operation.
        :param ctx: Whether to lower the result for its value or its
            address.
        :param expected_typ: The type the expression is expected to have,
            if known. Negation preserves its operand's type, so this is
            passed on to the operand; ``&`` and ``not`` ignore it.
        :return: The operation's result.

        :pre: op_ast.op.name == "not" implies operand coerces to bool [opt_unwrap]
        """
        match op_ast.op.name:
            case "&":
                return self._in_context(self._build_place(op_ast.operand), ctx)
            case "not":
                operand = self._build_expr(op_ast.operand, _ExprContext.VALUE)
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

                operand = self._build_expr(op_ast.operand, _ExprContext.VALUE, expected_typ)
                propagated = self._propagate_never(operand, op_ast.operand, ctx)
                if propagated is not None:
                    return propagated
                # Negation is well-defined (just wrapping) however its
                # operand's bit pattern, so - like _build_checked_bin_op,
                # and unlike division - it's safe to compute first and
                # check after. Negation is only well-typed for a signed
                # int (enforced by TypCheck), and only overflows for one
                # value: its own type's minimum, whose positive
                # counterpart doesn't fit.
                res = self._curr_bb.neg(operand, op_ast)
                operand_typ = asserts.checked_cast(operand.typ, typs.IntTyp)
                is_min = self._is_typ_min(operand, operand_typ, op_ast)
                self._panic_if(is_min, "integer overflow", op_ast)
                return self._in_context(res, ctx)
            case _:
                raise AssertionError(f"unhandled unary operator {op_ast.op.name!r}")

    def _resolve_sibling(self, target: ir_module.Fn) -> ir_module.FnSpec:
        """Map a resolved impl-block sibling to this build's own instance.

        A no-op unless this build is itself a method instance - see
        :meth:`~leech.ir_module.FnInstance.sibling_instance`.
        """
        if isinstance(self._fn, ir_module.FnInstance):
            return self._fn.sibling_instance(target)
        return target

    def _resolve_fn_instance(
        self, fn: ir_module.FnTemplate, typ_args: tuple[typs.Typ, ...]
    ) -> ir_module.FnInstance:
        """Get the concrete instance identified by ``fn`` and ``typ_args``.

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

    def _build_var_expr(self, var_ast: ast.VarExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a possibly qualified variable or function reference.

        :param var_ast: The parsed variable expression.
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

        target = self._typ_check_results.resolutions.var(var_ast)
        if isinstance(target, ir_module.Fn):
            return self._resolve_sibling(target)
        if isinstance(target, ir_module.FnSpec):
            return target
        if isinstance(target, ir_values.ComptimeEnum):
            # Not a place (see Env._resolve_path_segment's EnumTyp case)
            # - in PLACE context, copy it into a temporary and address
            # that, the same as any other non-place value (see
            # _in_context/_value_to_ptr).
            return self._in_context(target, ctx)
        if isinstance(target, (ast.Param, ast.Receiver, ast.LetStmt)):
            var = self._local_values[target]
        else:
            # A bare, uncalled GenericFn can't reach here - TypCheck
            # rejects it (MissingTypArgsError) unless generic_ref above
            # already handled it - so only a ModVar remains.
            var = asserts.checked_cast(target, ir_module.ModVar)

        if ctx == _ExprContext.PLACE:
            return var

        return self._curr_bb.load(var, var_ast)

    def _build_array_expr(
        self,
        arr_expr: ast.ArrayExpr,
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
        :param ctx: Whether to lower the result for its value or its
            address.
        :param expected_typ: This array literal's expected type, if known
            from the surrounding context.
        :return: The constructed array value.

        :pre: every arr_expr.elements[i] coerces to the resolved elt_typ [opt_unwrap]
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
                built[i] = self._build_expr(elt_ast, _ExprContext.VALUE, expected_elt_typ)

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
            else self._build_expr(arr_expr.elements[i], _ExprContext.VALUE, elt_typ)
            for i, v in enumerate(built)
        ]

        arr_typ = typs.ArrayTyp.get_or_create(elt_typ, len(elts))
        arr = ir_values.ComptimeArray(
            arr_typ,
            [ir_values.UndefValue(arr_typ.element_typ, arr_expr)] * len(elts),
            arr_expr,
        )

        for i, elt in enumerate(elts):
            elt_ast = arr_expr.elements[i]
            coerced = opt_util.opt_unwrap(self._coerce(elt, elt_ast))
            elt_index = ir_values.ComptimeInt(typs.USIZE, i, elt_ast)
            arr = self._curr_bb.insert_value(arr, coerced, elt_index, elt_ast)

        return self._in_context(arr, ctx)

    def _build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower an array indexing expression (``arr[index]``).

        :param aa_expr: The parsed array access expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The indexed element, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.

        :pre: aa_expr.index coerces to usize [opt_unwrap]
        """
        arr_ptr = self._build_place(aa_expr.array)

        # An index is a coercion point like any other unambiguous target
        # type: a bare literal infers as usize, and a narrower unsigned
        # value widens to it.
        index = self._build_expr(aa_expr.index, _ExprContext.VALUE, typs.USIZE)
        index = opt_util.opt_unwrap(self._coerce(index, aa_expr.index))

        array_typ = asserts.checked_cast(arr_ptr.typ, typs.PtrTyp).pointee_typ
        array_typ = asserts.checked_cast(array_typ, typs.ArrayTyp)
        length = ir_values.ComptimeInt(typs.USIZE, array_typ.length, aa_expr)
        out_of_bounds = self._curr_bb.icmp_unsigned(">=", index, length, aa_expr)
        self._panic_if(out_of_bounds, "index out of bounds", aa_expr)

        elt_ptr = self._curr_bb.gep(arr_ptr, index, aa_expr)
        return self._deref_in_context(elt_ptr, ctx, aa_expr)

    def _build_struct_expr(self, struct_expr: ast.StructExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a struct literal expression.

        :param struct_expr: The parsed struct expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The constructed struct value.
        :raises ItemNotFoundError: If the named type cannot be resolved.

        :pre: each field expression has a resolved field index [TypCheckResults]
        :pre: each struct field has an expression exactly once [TypCheck]
        :pre: each field expression is accessible and its value coerces [TypCheck]
        """
        cached_typ = self._typ_check_results.struct_expr_typ(struct_expr)
        struct_typ = asserts.checked_cast(
            cached_typ.substitute_typ_params(self._typ_arg_mapping), typs.StructTyp
        )

        struct = ir_values.ComptimeStruct(
            struct_typ,
            {f.name: ir_values.UndefValue(f.typ, struct_expr) for f in struct_typ.fields.values()},
            struct_expr,
        )

        field_values: dict[int, ir_values.Value] = {}
        field_value_asts: dict[int, ast.Expr] = {}
        for field_expr in struct_expr.fields:
            field_index = self._typ_check_results.struct_field_index(field_expr)
            field = struct_typ.field_at(field_index)
            field_values[field_index] = self._build_expr(
                field_expr.value, _ExprContext.VALUE, field.typ
            )
            field_value_asts[field_index] = field_expr.value

        for field in struct_typ.fields.values():
            field_index = field.index
            asserts.assert_in(field_index, field_values)
            field_value = field_values[field_index]
            coerced = opt_util.opt_unwrap(self._coerce(field_value, field_value_asts[field_index]))
            field_index_value = ir_values.ComptimeInt(typs.I32, field_index, field_value.ast)
            struct = self._curr_bb.insert_value(struct, coerced, field_index_value, field_value.ast)

        return self._in_context(struct, ctx)

    def _build_field_access_expr(
        self, fa_expr: ast.FieldAccessExpr, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower a field access expression (``s.field``).

        :param fa_expr: The parsed field access expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.
        """
        struct_ptr = self._build_place(fa_expr.value)
        return self._build_struct_field_access(struct_ptr, fa_expr, ctx)

    def _build_struct_field_access(
        self,
        struct_ptr: ir_values.Value[typs.PtrTyp],
        fa_expr: ast.FieldAccessExpr,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        """Lower ``s.field`` given ``s``'s already-lowered place pointer.

        Split out of :meth:`_build_field_access_expr` so
        :meth:`_build_call_expr` can fall back to plain field access
        (e.g. a struct field that happens to hold a callable value)
        without re-lowering ``fa_expr.value`` a second time.

        :param struct_ptr: ``fa_expr.value`` already lowered in
            :attr:`~_ExprContext.PLACE` context.
        :param fa_expr: The parsed field access expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The field's value, or a pointer to it if ``ctx`` is
            :attr:`~_ExprContext.PLACE`.

        :pre: struct_ptr.typ.pointee_typ is a StructTyp [checked_cast]
        :pre: fa_expr has a resolved field index [TypCheckResults]
        """
        struct_typ = asserts.checked_cast(struct_ptr.typ.pointee_typ, typs.StructTyp)
        field_index = self._typ_check_results.struct_field_index(fa_expr)
        field = struct_typ.field_at(field_index)

        index = ir_values.ComptimeInt(typs.I32, field.index, fa_expr)
        field_ptr = self._curr_bb.gep(struct_ptr, index, fa_expr)
        return self._deref_in_context(field_ptr, ctx, fa_expr)

    def _build_deref_expr(self, d_expr: ast.DerefExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a pointer dereference expression (``ptr.*``).

        :param d_expr: The parsed dereference expression.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The pointee's value, or the pointer itself unchanged if
            ``ctx`` is :attr:`~_ExprContext.PLACE`.
        """
        ptr = self._build_expr(d_expr.ptr, _ExprContext.VALUE)
        return self._deref_in_context(ptr, ctx, d_expr)

    def _build_stmt(self, stmt_ast: ast.Stmt) -> None:
        """Lower any statement, dispatching to the ``build_*_stmt`` method
        matching its AST node kind.

        :param stmt_ast: The parsed statement.
        """
        match stmt_ast:
            case ast.ExprStmt():
                value = self._build_expr(stmt_ast.expr, _ExprContext.VALUE)
                if value.typ == typs.NEVER and not self._curr_bb.terminated:
                    self._curr_bb.unreachable(stmt_ast.expr)
            case ast.RetStmt():
                self._build_ret_stmt(stmt_ast)
            case ast.LetStmt():
                self._build_let_stmt(stmt_ast)
            case ast.AssignmentStmt():
                self._build_assignment_stmt(stmt_ast)
            case ast.BreakStmt():
                self._build_break_stmt(stmt_ast)
            case ast.ContinueStmt():
                self._build_continue_stmt(stmt_ast)

    def _build_ret_stmt(self, ret_ast: ast.RetStmt) -> None:
        """Lower a ``return`` statement.

        :param ret_ast: The parsed return statement.
        """
        assert self._fn is not None
        ret_typ = self._fn.fn_typ.ret_typ

        if ret_ast.expr is not None:
            expr = self._build_expr(ret_ast.expr, _ExprContext.VALUE, ret_typ)
            self._finish_ret(expr, ret_ast, ret_ast.expr)
            return

        # Only reachable if ret_typ is void - else TypCheck would have
        # raised InvalidVoidRetError.
        asserts.assert_eq(ret_typ, typs.VOID)
        self._ret(ir_values.VoidValue(ret_ast), ret_ast)

    def _build_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        """Lower a ``break`` statement.

        :param break_ast: The parsed break statement.
        """
        target = self._loop_ctxs[self._typ_check_results.resolutions.loop_target(break_ast)]
        self._branch(target.end_bb, break_ast)

    def _build_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        """Lower a ``continue`` statement.

        :param continue_ast: The parsed continue statement.
        """
        target = self._loop_ctxs[self._typ_check_results.resolutions.loop_target(continue_ast)]
        self._branch(target.cond_bb, continue_ast)

    def _build_let_stmt(self, let_ast: ast.LetStmt) -> None:
        """Lower a local ``let`` statement.

        :param let_ast: The parsed ``let`` statement.
        """
        expr = self._build_let_initializer(let_ast, _ExprContext.VALUE)
        mut = typs.Mutability.from_ast(let_ast.mut)
        alloca = self._curr_bb.alloca(expr.typ, mut, 1, let_ast)
        self._local_values[let_ast] = alloca
        self._curr_bb.store(expr, alloca, let_ast)

    def _build_assignment_stmt(self, ass_ast: ast.AssignmentStmt) -> None:
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

        :pre: ass_ast.place is mut [assert_eq]
        :pre: ass_ast.expr coerces to ass_ast.place's type [opt_unwrap]
        """
        place = self._build_place(ass_ast.place)
        place_typ = place.typ
        assert place_typ.pointee_typ != typs.VOID, "assignment place cannot be void"
        expr = self._build_expr(ass_ast.expr, _ExprContext.VALUE, place_typ.pointee_typ)

        asserts.assert_eq(place_typ.mut, typs.MUT)
        coerced = opt_util.opt_unwrap(self._coerce(expr, ass_ast.expr))
        self._curr_bb.store(coerced, place, ass_ast)

    def _build_let_initializer(self, let_ast: ast.LetStmt, ctx: _ExprContext) -> ir_values.Value:
        """Lower a ``let`` statement's initializer expression, shared by
        :meth:`_build_let_stmt` and :meth:`build_var_initializer`.

        If ``let_ast`` has a declared type, the initializer is coerced to
        it; otherwise the initializer's own type is used as-is, with no
        coercion.

        :param let_ast: The parsed ``let`` statement.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: The (possibly coerced) initializer's value.

        :pre: let_ast.typ is not None implies let_ast.expr coerces to it [opt_unwrap]
        """
        cached_typ = self._typ_check_results.let_declared_typ(let_ast)
        expected_typ = opt_util.opt_map(
            cached_typ, lambda t: t.substitute_typ_params(self._typ_arg_mapping)
        )
        expr = self._build_expr(let_ast.expr, ctx, expected_typ)
        if cached_typ is None:
            return expr
        return opt_util.opt_unwrap(self._coerce(expr, let_ast.expr))

    def _build_int_lit(self, lit_ast: ast.IntLit, negated: bool) -> ir_values.Value:
        """Lower an integer literal to a compile-time-known value.

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

        :param value: The already-built operand to check.
        :param ast_node: The AST node to attribute a new terminator to.
        :param ctx: Whether to lower the result for its value or its
            address.
        :return: A ``never``-typed result, or ``None`` otherwise.
        """
        if value.typ != typs.NEVER:
            return None
        if not self._curr_bb.terminated:
            self._curr_bb.unreachable(ast_node)
        return self._in_context(ir_values.NeverValue(ast_node), ctx)

    def _build_place(self, expr_ast: ast.Expr) -> ir_values.Value[typs.PtrTyp]:
        """Lower ``expr_ast`` for its address rather than its value.

        :param expr_ast: The parsed expression to lower as a place.
        :return: A pointer to ``expr_ast``'s storage.

        :pre: expr_ast is a place expr [TypCheck]
        :post: isinstance(result.typ, PtrTyp) [checked_cast]
        """
        value = self._build_expr(expr_ast, _ExprContext.PLACE)
        asserts.checked_cast(value.typ, typs.PtrTyp)
        return cast(ir_values.Value[typs.PtrTyp], value)

    def _value_to_ptr(self, value: ir_values.Value) -> ir_values.Value[typs.PtrTyp]:
        alloca = self._curr_bb.alloca(value.typ, typs.CONST, 1, value.ast)
        self._curr_bb.store(value, alloca, value.ast)
        return alloca

    def _in_context(self, value: ir_values.Value, ctx: _ExprContext) -> ir_values.Value:
        if ctx == _ExprContext.PLACE:
            return self._value_to_ptr(value)
        return value

    def _deref_in_context(
        self, ptr: ir_values.Value, ctx: _ExprContext, ast_node: Optional[ast.Ast]
    ) -> ir_values.Value:
        if ctx == _ExprContext.PLACE:
            return ptr
        return self._curr_bb.load(ptr, ast_node)

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

    def _branch(self, target: ir_values.BasicBlock, ast_node: ast.Ast) -> None:
        """Terminate :attr:`_curr_bb` with an unconditional branch.

        Leaves :attr:`_curr_bb` unchanged.

        :param target: The basic block to branch to; must already be in
            :attr:`cfg`.
        :param ast_node: The AST node the branch instruction is attributed to.
        """
        asserts.assert_in(target, self.cfg)
        self._curr_bb.branch(target, ast_node)
        self.cfg.add_edge(self._curr_bb, target)

    def _cbranch(
        self,
        condition: ir_values.Value,
        true_target: ir_values.BasicBlock,
        false_target: ir_values.BasicBlock,
        ast_node: ast.Ast,
    ) -> None:
        """Terminate :attr:`_curr_bb` with a conditional branch.

        Leaves :attr:`_curr_bb` unchanged.

        :param condition: The boolean value to branch on.
        :param true_target: The block to branch to if ``condition`` is
            true; must already be in :attr:`cfg`.
        :param false_target: The block to branch to if ``condition`` is
            false; must already be in :attr:`cfg`.
        :param ast_node: The AST node the branch instruction is attributed to.
        """
        asserts.assert_in(true_target, self.cfg)
        asserts.assert_in(false_target, self.cfg)
        self._curr_bb.cbranch(condition, true_target, false_target, ast_node)
        self.cfg.add_edge(self._curr_bb, true_target)
        self.cfg.add_edge(self._curr_bb, false_target)

    def _ret(self, value: ir_values.Value, ast_node: Optional[ast.Ast]) -> None:
        """Terminate :attr:`_curr_bb` with a return of ``value``.

        :param value: The value to return.
        :param ast_node: The AST node the return instruction is attributed to.
        """
        self._curr_bb.ret(value, ast_node)
        self.cfg.add_edge(self._curr_bb, self.cfg.exit)

    def _panic_if(
        self,
        cond: ir_values.Value,
        message: str,
        ast_node: ast.Ast,
        ok_bb: Optional[ir_values.BasicBlock] = None,
    ) -> None:
        """Insert a compiler-synthesized runtime check: call ``panic`` if
        ``cond`` holds, otherwise fall through.

        Used for safety checks with no source-level ``if`` behind them -
        array bounds, integer overflow, division by zero - built directly
        out of basic blocks rather than :meth:`_build_if_expr`, since
        there's no ``ast.IfExpr`` to lower. Always calls the real prelude
        ``panic`` (:attr:`_panic_fn`), never whatever a module's own
        same-named definition might shadow it with locally - these checks
        exist to enforce the language's own safety guarantees, which user
        code shouldn't be able to opt out of by happening to define a
        function called ``panic``.

        Already-dead code (:attr:`_curr_bb` terminated - e.g. this check
        would follow a ``return`` earlier in the same block) is left alone
        rather than given new blocks of its own: every instruction built
        into an already-terminated block is silently dropped (see
        :meth:`~leech.ir_values.BasicBlock._add_instr`), but :meth:`_cbranch`
        unconditionally records a graph edge to wherever it points
        regardless of whether its own instruction actually landed - so
        without this, an unreachable check would still make its ``ok``
        block reachable *in the graph*, resuming real instruction-building
        there and letting anything built afterwards - not just this
        check's own - reference values (e.g. an also-silently-dropped
        ``alloca``) that were never actually compiled.

        :param cond: A ``bool``-typed value; true means the check failed.
        :param message: The literal message to pass to ``panic``.
        :param ast_node: The AST node the emitted instructions are
            attributed to.
        :param ok_bb: An existing continuation block, or ``None`` to
            create one.

        :post: _curr_bb.terminated or (_curr_bb is ok_bb, reached only when cond was false) [ctor]
        """
        if self._curr_bb.terminated:
            return
        fail_bb = self._add_bb("panic")
        if ok_bb is None:
            ok_bb = self._add_bb("ok")
        self._cbranch(cond, fail_bb, ok_bb, ast_node)
        self._set_position(fail_bb)
        panic_fn = opt_util.opt_unwrap(self._panic_fn)
        self._curr_bb.call(panic_fn, (ir_values.ComptimeCStr(message, None),), None)
        self._curr_bb.unreachable(ast_node)
        self._set_position(ok_bb)
