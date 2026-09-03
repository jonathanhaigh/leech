# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""AST-to-IR lowering for already type-checked programs.

Walks the AST in source order and emits instructions, reading every
semantic decision from ``check_results`` rather than re-deriving it.
"""

import dataclasses
import enum
from collections.abc import Mapping
from typing import Final, Optional, cast

from leech import (
    asserts,
    ast,
    check_results,
    ir_module,
    ir_traits,
    ir_values,
    naming,
    opt_util,
    patterns,
    resolve,
    signage,
    typs,
)


class _ExprContext(enum.Enum):
    """Whether an expression is lowered to its value or its address."""

    PLACE = 0
    VALUE = 1


class CfgBuilder:
    """Lower one function body or module initializer to a control-flow graph.

    Generic instances substitute ``comptime_arg_mapping`` into recorded facts that still contain
    declaration parameters.
    """

    @dataclasses.dataclass(frozen=True)
    class _LoopCtx:
        """The ``break``/``continue`` targets for one enclosing ``while`` loop."""

        cond_bb: ir_values.BasicBlock
        end_bb: ir_values.BasicBlock

    _fn: Final[Optional[ir_module.FnInstance]]
    _typ_check_results: Final[check_results.TypCheckResults]
    _impl_registry: Final[ir_traits.ImplRegistry]
    _panic_ref: Final[Optional[ir_module.FnRef]]
    _comptime_arg_mapping: Final[Mapping[typs.ComptimeParamTyp, typs.Typ]]
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
        typ_check_results: check_results.TypCheckResults,
        impl_registry: ir_traits.ImplRegistry,
        panic_ref: Optional[ir_module.FnRef],
        fn: Optional[ir_module.FnInstance] = None,
        comptime_arg_mapping: Optional[Mapping[typs.ComptimeParamTyp, typs.Typ]] = None,
    ) -> None:
        self._fn = fn
        self._typ_check_results = typ_check_results
        self._impl_registry = impl_registry
        self._panic_ref = panic_ref
        self._comptime_arg_mapping = opt_util.opt_or_default(comptime_arg_mapping, {})
        self.cfg = ir_values.Cfg()
        self._generate_bb_name = naming.VarNamer()
        # Reserve the "entry"/"exit" basenames so a later `add_bb` call
        # never collides with the blocks `Cfg.__init__` already created.
        self._generate_bb_name("entry")
        self._generate_bb_name("exit")
        self._curr_bb = self.cfg.entry
        self._loop_ctxs = {}
        self._local_values = {}

    def _local_place_typ(self, decl: resolve.LocalDecl) -> typs.PtrTyp:
        """Return ``decl``'s recorded local pointer type for this instance."""
        local_typ = self._typ_check_results.local_typ(decl)
        return asserts.checked_cast(
            local_typ.substitute_typ_params(self._comptime_arg_mapping), typs.PtrTyp
        )

    def _local_alloca(self, decl: resolve.LocalDecl, ast_node: ast.Ast) -> ir_values.Value:
        """Allocate storage for ``decl`` from its recorded local pointer type."""
        local_typ = self._local_place_typ(decl)
        return self._curr_bb.alloca(local_typ.pointee_typ, local_typ.mut, 1, ast_node)

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
            alloca = self._local_alloca(param.ast, param.ast)
            self._curr_bb.store(param, alloca, param.ast)
            self._local_values[param.ast] = alloca

        ret_typ = self._fn.fn_typ.ret_typ
        block = self._build_expr(fn_ast.block, _ExprContext.VALUE)
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
        if value.typ == typs.NEVER:
            if not self._curr_bb.terminated:
                self._curr_bb.unreachable(attribute_ast)
            return
        coerced = opt_util.opt_unwrap(self._coerce(value, coerce_ast))
        self._ret(coerced, attribute_ast)

    def _build_expr(
        self,
        expr_ast: ast.ExprKind,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        value = self._build_expr_unchecked(expr_ast, ctx)
        self._assert_recorded_typ(value, expr_ast, ctx)
        return value

    def _build_expr_unchecked(
        self,
        expr_ast: ast.ExprKind,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        match expr_ast:
            case ast.BlockExpr():
                return self._build_block_expr(expr_ast, ctx)
            case ast.IfExpr():
                return self._build_if_expr(expr_ast, ctx)
            case ast.WhileExpr():
                return self._build_while_expr(expr_ast, ctx)
            case ast.MatchExpr():
                return self._build_match_expr(expr_ast, ctx)
            case ast.CallExpr():
                return self._build_call_expr(expr_ast, ctx)
            case ast.BinOpExpr():
                return self._build_bin_op_expr(expr_ast, ctx)
            case ast.UnaryOpExpr():
                return self._build_unary_op_expr(expr_ast, ctx)
            case ast.StrLit():
                return self._in_context(ir_values.ComptimeCStr(expr_ast.value, expr_ast), ctx)
            case ast.IntLit():
                typ, value = opt_util.opt_unwrap(self._typ_check_results.folded_int_lit(expr_ast))
                return self._in_context(ir_values.ComptimeInt(typ, value, expr_ast), ctx)
            case ast.BoolLit():
                return self._in_context(ir_values.ComptimeBool(expr_ast.value, expr_ast), ctx)
            case ast.VarExpr():
                return self._build_var_expr(expr_ast, ctx)
            case ast.ArrayAccessExpr():
                return self._build_array_access_expr(expr_ast, ctx)
            case ast.BraceExpr():
                return self._build_brace_expr(expr_ast, ctx)
            case ast.FieldAccessExpr():
                return self._build_field_access_expr(expr_ast, ctx)
            case ast.DerefExpr():
                return self._build_deref_expr(expr_ast, ctx)

    def _assert_recorded_typ(
        self, value: ir_values.Value, expr_ast: ast.ExprKind, ctx: _ExprContext
    ) -> None:
        recorded = (
            self._typ_check_results.expr_typ(expr_ast)
            if ctx == _ExprContext.VALUE
            else self._typ_check_results.place_typ(expr_ast)
        )
        if recorded is None:
            return

        recorded = recorded.substitute_typ_params(self._comptime_arg_mapping)
        if ctx == _ExprContext.VALUE:
            # Lowering discovers divergence structurally, so e.g. `g() + 1i32`
            # can legitimately lower to `never` where the checker recorded i32.
            if value.typ == typs.NEVER:
                return
        else:
            ptr_typ = asserts.checked_cast(value.typ, typs.PtrTyp)
            if ptr_typ.pointee_typ == typs.NEVER:
                return

        asserts.assert_eq(value.typ, recorded)

    def _build_block_expr(
        self,
        block_ast: ast.BlockExpr,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        """Lower a ``{ ... }`` block expression: the tail expression's value
        if it has one, otherwise void if its statements complete normally,
        or never if they already diverged (e.g. via ``return``).
        """
        for stmt_ast in block_ast.stmts:
            self._build_stmt(stmt_ast)

        if block_ast.expr is None:
            if self._curr_bb.terminated:
                return ir_values.NeverValue(block_ast)
            return ir_values.VoidValue(block_ast)
        return self._build_expr(block_ast.expr, ctx)

    def _build_if_expr(
        self,
        if_ast: ast.IfExpr,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        """Lower an ``if``/``else`` expression, merging whichever branch's
        value is taken via a phi when both branches reach ``end_bb`` and the
        recorded result type isn't void.
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
            self._build_if_arm(if_ast.then, then_bb, end_bb, ctx)
            self._set_position(end_bb)
            return ir_values.VoidValue(if_ast)

        els_ast = if_ast.els
        assert els_ast is not None
        then, then_last_bb, have_then_value = self._build_if_arm(if_ast.then, then_bb, end_bb, ctx)
        els, els_last_bb, have_els_value = self._build_if_arm(els_ast, els_bb, end_bb, ctx)
        self._set_position(end_bb)

        if not have_then_value and not have_els_value:
            # Neither branch reaches end_bb: it's genuinely unreachable,
            # not just void, and needs a terminator of its own since
            # nothing branches into it.
            return self._curr_bb.unreachable(if_ast)

        result_typ = opt_util.opt_unwrap(self._typ_check_results.expr_typ(if_ast))
        if result_typ.substitute_typ_params(self._comptime_arg_mapping) == typs.VOID:
            # A void `if` produces no value, so there is nothing to merge.
            # A phi over the arm values would crash codegen (a VoidValue
            # asserts it needs no runtime representation) or emit a
            # `phi void` that LLVM rejects (e.g. a void-call arm).
            return ir_values.VoidValue(if_ast)

        if have_then_value and have_els_value:
            asserts.assert_eq(then.typ, els.typ)
            return self._curr_bb.phi({els_last_bb: els, then_last_bb: then}, if_ast)
        if have_then_value:
            return then
        return els

    def _build_if_arm(
        self,
        arm_ast: ast.ExprKind,
        arm_bb: ir_values.BasicBlock,
        end_bb: ir_values.BasicBlock,
        ctx: _ExprContext,
    ) -> tuple[ir_values.Value, ir_values.BasicBlock, bool]:
        """Lower one arm of an ``if``/``else`` into its own basic block.

        Returns the arm's value, the block its lowering ended in (which
        isn't ``arm_bb`` if the arm contained control flow of its own),
        and whether control reaches ``end_bb`` from it.
        """
        self._set_position(arm_bb)
        value = self._build_expr(arm_ast, ctx)
        last_bb = self._curr_bb
        if value.typ == typs.NEVER and not last_bb.terminated:
            last_bb.unreachable(arm_ast)
        have_value = not last_bb.terminated
        if have_value:
            self._branch(end_bb, arm_ast)
        return value, last_bb, have_value

    def _build_match_expr(
        self,
        match_ast: ast.MatchExpr,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        """Lower a ``match`` as a comparison chain merging arms via a phi."""
        arm_bbs = [self._add_bb("match_arm") for _ in match_ast.arms]
        end_bb = self._add_bb("match_end")

        scrutinee_value = self._build_expr(match_ast.scrutinee, _ExprContext.VALUE)
        propagated = self._propagate_never(scrutinee_value, match_ast.scrutinee, ctx)
        if propagated is not None:
            return propagated
        scrutinee_typ = self._typ_check_results.match_scrutinee_typ(match_ast)
        scrutinee_typ = scrutinee_typ.substitute_typ_params(self._comptime_arg_mapping)
        comparison_value, comparison_typ = self._match_comparison_value(
            scrutinee_value, scrutinee_typ, match_ast
        )

        plan = self._typ_check_results.match_plan(match_ast)
        if plan.tests:
            test_bb = self._add_bb("match_test")
            self._branch(test_bb, match_ast)
            for test in plan.tests:
                arm_ast = match_ast.arms[test.arm_index]
                if test.constructors == ():
                    # Entered unconditionally, and always the final test.
                    self._set_position(test_bb)
                    self._branch(arm_bbs[test.arm_index], arm_ast)
                    break
                for constructor in test.constructors:
                    self._set_position(test_bb)
                    next_bb = self._add_bb("match_test")
                    condition = self._build_match_compare(
                        comparison_value, constructor, comparison_typ, arm_ast
                    )
                    self._cbranch(condition, arm_bbs[test.arm_index], next_bb, arm_ast)
                    test_bb = next_bb
            # The checker rejects a match whose last reachable arm leaves
            # values uncovered, so a recorded plan always ends with an
            # unconditional test: the loop above breaks and test_bb is
            # already terminated.
            assert test_bb.terminated, "a recorded match plan must end with an unconditional test"
        else:
            self._branch(end_bb, match_ast)

        arm_results: dict[int, tuple[ir_values.Value, ir_values.BasicBlock, bool]] = {}
        for i, arm_ast in enumerate(match_ast.arms):
            self._set_position(arm_bbs[i])
            self._build_match_binding(arm_ast.pattern, scrutinee_value)
            arm_results[i] = self._build_if_arm(
                arm_ast.body, arm_bbs[i], end_bb, _ExprContext.VALUE
            )

        self._set_position(end_bb)
        incoming: dict[ir_values.BasicBlock, ir_values.Value] = {}
        for i in plan.reachable_arms:
            value, last_bb, reaches_end = arm_results[i]
            if reaches_end:
                incoming[last_bb] = value

        if not incoming:
            return self._curr_bb.unreachable(match_ast)
        if len(incoming) == 1:
            return self._in_context(next(iter(incoming.values())), ctx)
        if all(value.typ == typs.VOID for value in incoming.values()):
            return ir_values.VoidValue(match_ast)
        return self._in_context(self._curr_bb.phi(incoming, match_ast), ctx)

    def _match_comparison_value(
        self, scrutinee_value: ir_values.Value, scrutinee_typ: typs.Typ, ast_node: ast.Ast
    ) -> tuple[ir_values.Value, typs.Typ]:
        if isinstance(scrutinee_typ, typs.EnumTyp):
            return (
                self._curr_bb.enum_to_int(scrutinee_value, ast_node),
                scrutinee_typ.backing_typ,
            )
        return scrutinee_value, scrutinee_typ

    def _build_match_compare(
        self,
        comparison_value: ir_values.Value,
        constructor: patterns.ConstructorKind,
        comparison_typ: typs.Typ,
        ast_node: ast.Ast,
    ) -> ir_values.Value:
        case_value = self._match_case_value(constructor, comparison_typ, ast_node)
        if isinstance(comparison_typ, typs.BoolTyp):
            bool_value = asserts.checked_cast(case_value, ir_values.ComptimeBool)
            if bool_value.value:
                return comparison_value
            return self._curr_bb.not_(comparison_value, ast_node)
        int_typ = asserts.checked_cast(comparison_typ, typs.IntTyp)
        if int_typ.signage == signage.SIGNED:
            return self._curr_bb.icmp_signed("==", comparison_value, case_value, ast_node)
        return self._curr_bb.icmp_unsigned("==", comparison_value, case_value, ast_node)

    def _match_case_value(
        self, constructor: patterns.ConstructorKind, comparison_typ: typs.Typ, ast_node: ast.Ast
    ) -> ir_values.Value:
        match constructor:
            case patterns.VariantConstructor(discriminant):
                int_typ = asserts.checked_cast(comparison_typ, typs.IntTyp)
                return ir_values.ComptimeInt(int_typ, discriminant, ast_node)
            case patterns.BoolConstructor(value):
                return ir_values.ComptimeBool(value, ast_node)
            case patterns.IntConstructor(value):
                int_typ = asserts.checked_cast(comparison_typ, typs.IntTyp)
                return ir_values.ComptimeInt(int_typ, value, ast_node)

    def _build_match_binding(self, pattern: ast.Pattern, scrutinee_value: ir_values.Value) -> None:
        if not isinstance(pattern, ast.BindingPattern):
            return
        alloca = self._local_alloca(pattern, pattern)
        self._local_values[pattern] = alloca
        self._curr_bb.store(scrutinee_value, alloca, pattern)

    def _build_while_expr(self, while_ast: ast.WhileExpr, _ctx: _ExprContext) -> ir_values.Value:
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
        """Lower a call, including method receivers and generic instances."""
        callee_ast = call_ast.callee
        recv_ast: Optional[ast.ExprKind] = None
        recv_arg: Optional[ir_values.Value] = None

        generic_call = self._typ_check_results.generic_call(call_ast)
        if generic_call is not None:
            # TypCheck recorded the resolved function and comptime arguments.
            callee: ir_values.Value = self._resolve_fn_ref(*generic_call)
        elif isinstance(callee_ast, ast.FieldAccessExpr):
            recv_place = self._build_place(callee_ast.value)
            cached = self._typ_check_results.resolutions.callee(call_ast)
            if cached is resolve.CalleeResolution.FIELD_ACCESS:
                method = None
            elif isinstance(cached, ir_traits.TraitMethod):
                recv_typ = recv_place.typ.pointee_typ
                trait_impl = self._impl_registry.find_trait_impl(cached.trait, recv_typ)
                assert trait_impl is not None
                found = trait_impl.get_trait_method(cached)
                assert found is not None
                impl_args = trait_impl.instantiation_args(recv_typ)
                method = found.instantiate(impl_args).ref
            else:
                method = self._selection_ref(cached)

            if method is not None:
                recv_arg = recv_place
                recv_ast = callee_ast.value
                callee = method
            else:
                callee = self._build_struct_field_access(recv_place, callee_ast, _ExprContext.VALUE)
        else:
            callee = self._build_expr(callee_ast, _ExprContext.VALUE)

        fn_ptr_typ = asserts.checked_cast(callee.typ, typs.PtrTyp)
        asserts.checked_cast(fn_ptr_typ.pointee_typ, typs.CallableTyp)
        arg_asts: list[ast.ExprKind] = ([recv_ast] if recv_ast is not None else []) + list(
            call_ast.args
        )
        lowered_args = tuple(
            self._build_expr(arg_ast, _ExprContext.VALUE) for arg_ast in call_ast.args
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
    ) -> ir_values.Value:
        """Lower a binary operator expression (arithmetic or comparison)."""
        match op_ast.op.name:
            case "and" | "or":
                return self._build_logic_bin_op_expr(op_ast, ctx)
            case "<" | "<=" | "==" | "!=" | ">=" | ">" | "+" | "-" | "*" | "/" as op:
                return self._build_numeric_bin_op_expr(op_ast, op, ctx)

    def _build_numeric_bin_op_expr(
        self,
        op_ast: ast.BinOpExpr,
        op: ast.CmpOpName | ast.ArithmeticOpName,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        lhs = self._build_expr(op_ast.lhs, _ExprContext.VALUE)
        propagated = self._propagate_never(lhs, op_ast.lhs, ctx)
        if propagated is not None:
            return propagated
        rhs = self._build_expr(op_ast.rhs, _ExprContext.VALUE)
        propagated = self._propagate_never(rhs, op_ast.rhs, ctx)
        if propagated is not None:
            return propagated

        # lhs and rhs are already known (by TypCheck) to be equal integer
        # types by this point.
        lhs_typ = asserts.checked_cast(lhs.typ, typs.IntTyp)
        match op:
            case "<" | "<=" | "==" | "!=" | ">=" | ">":
                if lhs_typ.signage == signage.SIGNED:
                    res = self._curr_bb.icmp_signed(op, lhs, rhs, op_ast)
                else:
                    res = self._curr_bb.icmp_unsigned(op, lhs, rhs, op_ast)
            case "+":
                checked = self._curr_bb.checked_add(lhs, rhs, op_ast)
                res = self._build_checked_bin_op(checked, op_ast)
            case "-":
                checked = self._curr_bb.checked_sub(lhs, rhs, op_ast)
                res = self._build_checked_bin_op(checked, op_ast)
            case "*":
                checked = self._curr_bb.checked_mul(lhs, rhs, op_ast)
                res = self._build_checked_bin_op(checked, op_ast)
            case "/":
                res = self._build_checked_div(lhs, rhs, lhs_typ, op_ast)

        return self._in_context(res, ctx)

    def _is_typ_min(
        self, value: ir_values.Value, typ: typs.IntTyp, op_ast: ast.Ast
    ) -> ir_values.Value:
        """Whether ``value`` equals ``typ.min_value`` - the one value a
        signed type's negation, or division by ``-1``, overflows on.
        """
        return self._curr_bb.icmp_signed(
            "==", value, ir_values.ComptimeInt(typ, typ.min_value, op_ast), op_ast
        )

    def _build_checked_bin_op(
        self,
        res: ir_values.CheckedBinOpInstr,
        op_ast: ast.Ast,
    ) -> ir_values.Value:
        """Panic when ``res``'s exact result overflows its operand type.

        ``res`` is a CheckedAddInstr/CheckedSubInstr/CheckedMulInstr - a plain
        BinOpInstr subclass, computing the operation's own (possibly
        wrapped) result exactly like the corresponding unchecked
        instruction, but paired with an OverflowFlagInstr that reports
        whether it overflowed - both lower to a single
        ``llvm.*.with.overflow`` intrinsic call between them (see the
        Compiler class in codegen), rather than computing the operation
        twice. The comptime Interpreter evaluates the two exactly as
        generically (no special-casing arithmetic overflow at all) - the
        checked instruction wraps like its LLVM counterpart, and the flag
        instruction reports whether it did - so ``_panic_if``'s ``panic``
        call is what reports overflow both at runtime and at compile time.
        """
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

        Unlike ``_build_checked_bin_op``'s operations, an actual
        ``sdiv``/``udiv`` by zero is a trap, not a well-defined wrapping
        result - so, unlike there, the checks here have to run *before*
        the real division, not after.
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

        Modelled on ``_build_if_expr``: the right operand is only
        evaluated when it can affect the result, in its own basic block,
        merged with the short-circuited outcome via a phi.
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
    ) -> ir_values.Value:
        """Lower a unary operator expression (``&``, ``-``, or ``not``)."""
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
                folded = self._typ_check_results.folded_int_lit(op_ast)
                if folded is not None:
                    typ, value = folded
                    return self._in_context(
                        ir_values.ComptimeInt(typ, value, op_ast.operand),
                        ctx,
                    )

                operand = self._build_expr(op_ast.operand, _ExprContext.VALUE)
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

    def _instantiate_src_fn(self, target: ir_module.SrcFnSymbol) -> ir_module.FnRef:
        """Return a reference to the source function selected by this lowering.

        A sibling in a generic impl inherits the current impl arguments;
        every other non-generic source function has an empty argument tuple.
        Emission ownership is decided later by monomorphization.
        """
        if self._fn is not None:
            sibling = self._fn.sibling_instance(target)
            if sibling is not None:
                return sibling.ref
        return target.instantiate(()).ref

    def _resolve_fn_ref(
        self, fn: ir_module.FnSymbol, comptime_args: tuple[typs.Typ, ...]
    ) -> ir_module.FnRef:
        """Return a reference to the instance identified by ``fn`` and ``comptime_args``.

        ``comptime_args`` is recorded against ``fn``'s own declaration, so
        it may still contain a comptime parameter (e.g. a generic function
        recursing on its own comptime parameter, or naming another generic
        item applied to its own comptime parameter); it's substituted here
        against this lowering's own comptime arguments (a no-op outside a
        generic instance) to resolve down to concrete types before asking
        for the instance itself.
        """
        concrete_comptime_args = tuple(
            typ_arg.substitute_typ_params(self._comptime_arg_mapping) for typ_arg in comptime_args
        )
        return fn.instantiate(concrete_comptime_args).ref

    def _selection_ref(self, selection: ir_traits.ImplFnSelection) -> ir_module.FnRef:
        """Instantiate an impl-function selection for this concrete body."""
        args = tuple(
            arg.substitute_typ_params(self._comptime_arg_mapping) for arg in selection.impl_args
        )
        return selection.fn.instantiate(args).ref

    def _build_var_expr(self, var_ast: ast.VarExpr, ctx: _ExprContext) -> ir_values.Value:
        """Lower a possibly qualified variable or function reference.

        ``ctx`` is ignored for function references, which are always
        returned as-is.
        """
        generic_ref = self._typ_check_results.generic_var_ref(var_ast)
        if generic_ref is not None:
            # TypCheck already resolved which generic function this names
            # and its explicit comptime arguments; this just has to get the
            # reference belonging to the instance they name.
            return self._resolve_fn_ref(*generic_ref)

        target = self._typ_check_results.resolutions.var(var_ast)
        if isinstance(target, ir_traits.ImplFnSelection):
            return self._selection_ref(target)
        if isinstance(target, ir_module.SrcFnSymbol):
            return self._instantiate_src_fn(target)
        if isinstance(target, ir_module.ExternFnSymbol):
            return target.instantiate(()).ref
        if isinstance(target, ir_values.ComptimeEnum):
            # Not a place (see Env._lookup_path_seg's EnumTyp case)
            # - in PLACE context, copy it into a temporary and address
            # that, the same as any other non-place value (see
            # _in_context/_value_to_ptr).
            return self._in_context(target, ctx)
        if isinstance(target, typs.ValueParamTyp):
            concrete = asserts.checked_cast(
                target.substitute_typ_params(self._comptime_arg_mapping), typs.ComptimeValueTyp
            )
            return self._in_context(concrete.to_comptime_value(var_ast), ctx)
        if isinstance(target, (ast.Param, ast.Receiver, ast.LetStmt, ast.BindingPattern)):
            var = self._local_values[target]
        else:
            # A bare generic declaration can't reach here: TypCheck rejects it
            # unless generic_ref above already handled it.
            var = asserts.checked_cast(target, ir_module.ModVar)

        if ctx == _ExprContext.PLACE:
            return var

        return self._curr_bb.load(var, var_ast)

    def _build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, ctx: _ExprContext
    ) -> ir_values.Value:
        arr_ptr = self._build_place(aa_expr.array)

        # An index is a coercion point like any other unambiguous target
        # type: a bare literal infers as usize, and a narrower unsigned
        # value widens to it.
        index = self._build_expr(aa_expr.index, _ExprContext.VALUE)
        index = opt_util.opt_unwrap(self._coerce(index, aa_expr.index))

        array_typ = asserts.checked_cast(arr_ptr.typ, typs.PtrTyp).pointee_typ
        array_typ = asserts.checked_cast(array_typ, typs.ArrayTyp)
        length = ir_values.ComptimeInt(typs.USIZE, array_typ.length_value, aa_expr)
        out_of_bounds = self._curr_bb.icmp_unsigned(">=", index, length, aa_expr)
        self._panic_if(out_of_bounds, "index out of bounds", aa_expr)

        elt_ptr = self._curr_bb.gep(arr_ptr, index, aa_expr)
        return self._deref_in_context(elt_ptr, ctx, aa_expr)

    def _build_brace_expr(self, brace_expr: ast.BraceExpr, ctx: _ExprContext) -> ir_values.Value:
        cached_typ = self._typ_check_results.brace_expr_typ(brace_expr)
        match cached_typ:
            case typs.ArrayTyp():
                return self._build_array_lit_expr(brace_expr, cached_typ, ctx)
            case typs.StructTyp():
                return self._build_struct_lit_expr(brace_expr, cached_typ, ctx)

    def _build_struct_lit_expr(
        self, brace_expr: ast.BraceExpr, cached_typ: typs.StructTyp, ctx: _ExprContext
    ) -> ir_values.Value:
        struct_typ = asserts.checked_cast(
            cached_typ.substitute_typ_params(self._comptime_arg_mapping), typs.StructTyp
        )

        struct = ir_values.ComptimeStruct(
            struct_typ,
            {f.name: ir_values.UndefValue(f.typ, brace_expr) for f in struct_typ.fields.values()},
            brace_expr,
        )

        field_values: dict[int, ir_values.Value] = {}
        field_value_asts: dict[int, ast.ExprKind] = {}
        for element in brace_expr.elements:
            field_expr = asserts.checked_cast(element, ast.StructFieldExpr)
            field_index = self._typ_check_results.struct_field_index(field_expr)
            field_values[field_index] = self._build_expr(field_expr.value, _ExprContext.VALUE)
            field_value_asts[field_index] = field_expr.value

        for field in struct_typ.fields.values():
            field_index = field.index
            asserts.assert_in(field_index, field_values)
            field_value = field_values[field_index]
            coerced = opt_util.opt_unwrap(self._coerce(field_value, field_value_asts[field_index]))
            field_index_value = ir_values.ComptimeInt(typs.I32, field_index, field_value.ast)
            struct = self._curr_bb.insert_value(struct, coerced, field_index_value, field_value.ast)

        return self._in_context(struct, ctx)

    def _build_array_lit_expr(
        self, brace_expr: ast.BraceExpr, cached_typ: typs.ArrayTyp, ctx: _ExprContext
    ) -> ir_values.Value:
        """Lower an array literal.

        Every element's type is always already known from the literal's
        own explicit ``array[T, N]``, so - unlike a struct literal's
        by-name field matching - elements are simply lowered positionally.
        """
        arr_typ = asserts.checked_cast(
            cached_typ.substitute_typ_params(self._comptime_arg_mapping), typs.ArrayTyp
        )
        arr = ir_values.ComptimeArray(
            arr_typ,
            [ir_values.UndefValue(arr_typ.element_typ, brace_expr)] * arr_typ.length_value,
            brace_expr,
        )
        for i, elt_ast in enumerate(brace_expr.elements):
            if isinstance(elt_ast, ast.StructFieldExpr):
                raise AssertionError("type checking accepted a named field in an array literal")
            elt_value = self._build_expr(elt_ast, _ExprContext.VALUE)
            coerced = opt_util.opt_unwrap(self._coerce(elt_value, elt_ast))
            elt_index = ir_values.ComptimeInt(typs.USIZE, i, elt_ast)
            arr = self._curr_bb.insert_value(arr, coerced, elt_index, elt_ast)
        return self._in_context(arr, ctx)

    def _build_field_access_expr(
        self, fa_expr: ast.FieldAccessExpr, ctx: _ExprContext
    ) -> ir_values.Value:
        struct_ptr = self._build_place(fa_expr.value)
        return self._build_struct_field_access(struct_ptr, fa_expr, ctx)

    def _build_struct_field_access(
        self,
        struct_ptr: ir_values.Value[typs.PtrTyp],
        fa_expr: ast.FieldAccessExpr,
        ctx: _ExprContext,
    ) -> ir_values.Value:
        """Lower ``s.field`` given ``s``'s already-lowered place pointer.

        Split out of ``_build_field_access_expr`` so ``_build_call_expr``
        can fall back to plain field access (e.g. a struct field that
        happens to hold a callable value) without re-lowering
        ``fa_expr.value`` a second time.
        """
        struct_typ = asserts.checked_cast(struct_ptr.typ.pointee_typ, typs.StructTyp)
        field_index = self._typ_check_results.struct_field_index(fa_expr)
        field = struct_typ.field_at(field_index)

        index = ir_values.ComptimeInt(typs.I32, field.index, fa_expr)
        field_ptr = self._curr_bb.gep(struct_ptr, index, fa_expr)
        return self._deref_in_context(field_ptr, ctx, fa_expr)

    def _build_deref_expr(self, d_expr: ast.DerefExpr, ctx: _ExprContext) -> ir_values.Value:
        ptr = self._build_expr(d_expr.ptr, _ExprContext.VALUE)
        return self._deref_in_context(ptr, ctx, d_expr)

    def _build_stmt(self, stmt_ast: ast.StmtKind) -> None:
        """Lower any statement, dispatching to the ``build_*_stmt`` method
        matching its AST node kind.
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
        assert self._fn is not None
        ret_typ = self._fn.fn_typ.ret_typ

        if ret_ast.expr is not None:
            expr = self._build_expr(ret_ast.expr, _ExprContext.VALUE)
            self._finish_ret(expr, ret_ast, ret_ast.expr)
            return

        # Only reachable if ret_typ is void - else TypCheck would have
        # raised InvalidVoidRetError.
        asserts.assert_eq(ret_typ, typs.VOID)
        self._ret(ir_values.VoidValue(ret_ast), ret_ast)

    def _build_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        target = self._loop_ctxs[self._typ_check_results.resolutions.loop_target(break_ast)]
        self._branch(target.end_bb, break_ast)

    def _build_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        target = self._loop_ctxs[self._typ_check_results.resolutions.loop_target(continue_ast)]
        self._branch(target.cond_bb, continue_ast)

    def _build_let_stmt(self, let_ast: ast.LetStmt) -> None:
        expr = self._build_let_initializer(let_ast, _ExprContext.VALUE)
        alloca = self._local_alloca(let_ast, let_ast)
        self._local_values[let_ast] = alloca
        # A diverging ``and``/``or`` initializer lowers to ``never`` while its
        # recorded local type is the operator's result type (``bool``). There
        # is no value to store, and the current block is already terminated.
        if expr.typ != typs.NEVER:
            self._curr_bb.store(expr, alloca, let_ast)

    def _build_assignment_stmt(self, ass_ast: ast.AssignmentStmt) -> None:
        """Lower an assignment statement (``place = expr``).

        Leech deliberately evaluates the place expression before the value
        expression (matching JavaScript, Java, C#, and Go; the opposite of
        Rust, Python, and C++17's built-in ``=``). Where the place and
        value expressions both have observable side effects (e.g. a call
        in an array index alongside a call on the right-hand side), the
        place's side effects happen first.
        """
        place = self._build_place(ass_ast.place)
        place_typ = place.typ
        assert place_typ.pointee_typ != typs.VOID, "assignment place cannot be void"
        expr = self._build_expr(ass_ast.expr, _ExprContext.VALUE)

        asserts.assert_eq(place_typ.mut, typs.MUT)
        coerced = opt_util.opt_unwrap(self._coerce(expr, ass_ast.expr))
        self._curr_bb.store(coerced, place, ass_ast)

    def _build_let_initializer(self, let_ast: ast.LetStmt, ctx: _ExprContext) -> ir_values.Value:
        """Lower a ``let`` statement's initializer expression.

        If ``let_ast`` has a declared type, the initializer is coerced to
        it; otherwise the initializer's own type is used as-is, with no
        coercion.
        """
        cached_typ = self._typ_check_results.let_declared_typ(let_ast)
        expr = self._build_expr(let_ast.expr, ctx)
        if cached_typ is None:
            return expr
        return opt_util.opt_unwrap(self._coerce(expr, let_ast.expr))

    def _coerce(self, value: ir_values.Value, node: ast.Ast) -> Optional[ir_values.Value]:
        """Convert ``value`` to the type its context expects, if allowed.

        ``node`` is the AST node ``value`` was checked against its expected
        type with (see ``TypCheckResults.coercion``), and to attribute any
        emitted instruction to.
        """
        coercion = self._typ_check_results.coercion(node)
        match coercion:
            case None:
                return value
            case check_results.Invalid():
                return None
            case check_results.NeverDiverge(target=target):
                # A diverging expression never actually produces a value,
                # so it coerces to any type - by the time a real value
                # would be read here, control can't have reached this
                # point. target was recorded against the generic
                # declaration's own (possibly type-parameter-typed)
                # signature, so it needs substituting for this lowering's
                # concrete comptime arguments - a no-op outside a generic
                # instance, where _comptime_arg_mapping is empty.
                if not self._curr_bb.terminated:
                    self._curr_bb.unreachable(node)
                return ir_values.UndefValue(
                    target.substitute_typ_params(self._comptime_arg_mapping), node
                )
            case check_results.IntExt(target=target):
                return self._curr_bb.int_ext(value, target, node)
            case check_results.PtrMutRelax():
                return self._curr_bb.ptr_mut_relax(value, node)

    def _propagate_never(
        self, value: ir_values.Value, ast_node: ast.Ast, ctx: _ExprContext
    ) -> Optional[ir_values.Value]:
        """If ``value`` is ``never``-typed, terminate the current block."""
        if value.typ != typs.NEVER:
            return None
        if not self._curr_bb.terminated:
            self._curr_bb.unreachable(ast_node)
        return self._in_context(ir_values.NeverValue(ast_node), ctx)

    def _build_place(self, expr_ast: ast.ExprKind) -> ir_values.Value[typs.PtrTyp]:
        """Lower ``expr_ast`` for its address rather than its value."""
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
        """Create a new, empty basic block and add it to ``cfg``.

        It is not made the current block; use ``_set_position`` for that.
        """
        bb = ir_values.BasicBlock(self._generate_bb_name(basename))
        self.cfg.add_node(bb)
        return bb

    def _set_position(self, bb: ir_values.BasicBlock) -> None:
        asserts.assert_in(bb, self.cfg)
        self._curr_bb = bb

    def _branch(self, target: ir_values.BasicBlock, ast_node: ast.Ast) -> None:
        """Terminate ``_curr_bb`` with an unconditional branch, leaving
        ``_curr_bb`` itself unchanged.
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
        """Terminate ``_curr_bb`` with a conditional branch, leaving
        ``_curr_bb`` itself unchanged.
        """
        asserts.assert_in(true_target, self.cfg)
        asserts.assert_in(false_target, self.cfg)
        self._curr_bb.cbranch(condition, true_target, false_target, ast_node)
        self.cfg.add_edge(self._curr_bb, true_target)
        self.cfg.add_edge(self._curr_bb, false_target)

    def _ret(self, value: ir_values.Value, ast_node: Optional[ast.Ast]) -> None:
        """Terminate ``_curr_bb`` with a return of ``value``."""
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
        out of basic blocks rather than ``_build_if_expr``, since there's
        no ``ast.IfExpr`` to lower. Always calls the real prelude ``panic``
        (``_panic_ref``), never whatever a module's own same-named
        definition might shadow it with locally - these checks exist to
        enforce the language's own safety guarantees, which user code
        shouldn't be able to opt out of by happening to define a function
        called ``panic``.

        Already-dead code (``_curr_bb`` terminated - e.g. this check would
        follow a ``return`` earlier in the same block) is left alone
        rather than given new blocks of its own: every instruction built
        into an already-terminated block is silently dropped, but
        ``_cbranch`` unconditionally records a graph edge to wherever it
        points regardless of whether its own instruction actually landed -
        so without this, an unreachable check would still make its ``ok``
        block reachable *in the graph*, resuming real instruction-building
        there and letting anything built afterwards - not just this
        check's own - reference values (e.g. an also-silently-dropped
        ``alloca``) that were never actually compiled.

        Afterwards, ``_curr_bb`` is either terminated or is ``ok_bb``
        (reached only when ``cond`` was false), so callers can keep
        building into it.
        """
        if self._curr_bb.terminated:
            return
        fail_bb = self._add_bb("panic")
        if ok_bb is None:
            ok_bb = self._add_bb("ok")
        self._cbranch(cond, fail_bb, ok_bb, ast_node)
        self._set_position(fail_bb)
        panic_ref = opt_util.opt_unwrap(self._panic_ref)
        self._curr_bb.call(panic_ref, (ir_values.ComptimeCStr(message, None),), None)
        self._curr_bb.unreachable(ast_node)
        self._set_position(ok_bb)
