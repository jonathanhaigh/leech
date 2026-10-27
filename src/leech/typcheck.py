# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Type checking before LLVM lowering.

Type checking is the sole semantic decider: it checks each body
completely, including unreachable code, and records every decision by
AST-node identity in its ``check_results.TypCheckResults``, the interface
to lowering.
"""

import contextlib
import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Final, Optional, overload

from leech import (
    asserts,
    ast,
    check_results,
    compilation,
    diag,
    diag_kinds,
    ir_env,
    ir_values,
    opt_util,
    patterns,
    reserved,
    resolve,
    signage,
    src,
    typs,
)

if TYPE_CHECKING:
    # Runtime imports are local because ir_module imports this module.
    from leech import ir_module, ir_traits


def _is_flexible_int_lit(expr_ast: ast.ExprKind) -> bool:
    """Return whether an unsuffixed integer literal can take its type from context."""
    if isinstance(expr_ast, ast.IntLit):
        return expr_ast.explicit_width is None
    if isinstance(expr_ast, ast.UnaryOpExpr) and expr_ast.op.name == "-":
        return _is_flexible_int_lit(expr_ast.operand)
    if isinstance(expr_ast, ast.BlockExpr):
        return (
            not expr_ast.stmts and expr_ast.expr is not None and _is_flexible_int_lit(expr_ast.expr)
        )
    return False


def _resolve_peer_typ(fixed_typs: list[typs.Typ]) -> typs.Typ:
    """Choose the first non-diverging fixed peer type, defaulting to ``i32``."""
    for typ in fixed_typs:
        if typ != typs.NEVER:
            return typ
    return typs.I32


def _match_arm_check_order(arms: Sequence[ast.MatchArm]) -> tuple[list[int], list[int]]:
    """Return match-arm indices grouped so flexible literal bodies are checked last."""
    fixed_indices = [i for i, arm in enumerate(arms) if not _is_flexible_int_lit(arm.body)]
    flexible_indices = [i for i, arm in enumerate(arms) if _is_flexible_int_lit(arm.body)]
    return fixed_indices, flexible_indices


def _callable_typ(typ: typs.Typ) -> Optional[typs.CallableTyp]:
    """Return the callable behind a pointer type, if present."""
    if isinstance(typ, typs.PtrTyp) and isinstance(typ.pointee_typ, typs.CallableTyp):
        return typ.pointee_typ
    return None


def _settled_typ(
    typ: typs.Typ,
    comptime_params: Sequence[typs.ComptimeParamTyp],
    bindings: Mapping[typs.ComptimeParamTyp, typs.Typ],
) -> Optional[typs.Typ]:
    """Return ``typ`` with ``bindings`` substituted, or ``None`` if it names a parameter of
    ``comptime_params`` that ``bindings`` leave unbound."""
    unbound = frozenset(comptime_params) - bindings.keys()
    if not unbound.isdisjoint(typ.free_comptime_params()):
        return None
    return typ.substitute_typ_params(bindings)


def _poison_unbound_params(
    declared_typ: typs.Typ,
    reported: diag.ReportProof,
    bindings: dict[typs.ComptimeParamTyp, typs.Typ],
) -> None:
    """Bind each parameter in ``declared_typ`` that ``bindings`` leave unbound to poison for
    ``reported``, keeping any existing binding."""
    for param in declared_typ.free_comptime_params():
        bindings.setdefault(param, typs.PoisonTyp(reported))


def _struct_field(typ: typs.Typ, name: str) -> Optional[typs.StructField]:
    """Return the named struct field, if present."""
    if not isinstance(typ, typs.StructTyp):
        return None
    return typ.fields.get(name)


def _binding_patterns(pat: ast.PatternKind) -> Iterator[ast.BindingPattern]:
    match pat:
        case ast.BindingPattern():
            yield pat
        case ast.PathPattern():
            for subpattern in pat.payload:
                yield from _binding_patterns(subpattern)
        case ast.OrPattern():
            for alternative in pat.alternatives:
                yield from _binding_patterns(alternative)
        case ast.WildcardPattern() | ast.IntLitPattern() | ast.BoolLitPattern():
            return


@dataclasses.dataclass(frozen=True)
class _Callee:
    """A call's resolved callee.

    ``typ`` is a callable type, or poison. ``args_checked`` is whether the call's arguments
    have already been checked, which only happens when resolving the callee failed.
    """

    typ: typs.Typ
    recv_ast: Optional[ast.ExprKind] = None
    recv_typ: Optional[typs.PtrTyp] = None
    args_checked: bool = False


class TypCheck:
    """Type-check a function body or module-variable initializer.

    Checking continues after an error, giving whatever failed poison type, and the check
    fails at the end with the first error it reported or found poison from.
    """

    results: Final[check_results.TypCheckResults]
    _ctx: Final[compilation.Ctx]
    _ret_typ: Optional[typs.Typ]
    _ret_typ_span: Optional[src.SrcSpan]
    _fn_name: Optional[str]
    #: Enclosing while loops' labels and own AST nodes, innermost last.
    _loop_labels: Final[list[tuple[Optional[str], ast.WhileExpr]]]
    #: The proof of each error the check has reported or found poison from, in order, with
    #: repeats.
    _failures: Final[list[diag.ReportProof]]
    #: The proof of the error behind poison reachable from each type noted, if any.
    _reachable_poison: Final[dict[typs.Typ, Optional[diag.ReportProof]]]

    def __init__(self, ctx: compilation.Ctx) -> None:
        self.results = check_results.TypCheckResults()
        self._ctx = ctx
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        self._loop_labels = []
        self._failures = []
        self._reachable_poison = {}

    def _fail(self, reported: diag.ReportProof) -> diag.ReportProof:
        """Make the check fail with ``reported`` once it finishes, and return ``reported``."""
        self._failures.append(reported)
        return reported

    def _fail_and_poison(self, reported: diag.ReportProof) -> typs.PoisonTyp:
        """Make the check fail with ``reported`` as ``_fail`` does, and return poison for it."""
        return typs.PoisonTyp(self._fail(reported))

    def _fail_if_poison_reachable(self, typ: typs.Typ) -> None:
        """Fail the check if poison is reachable from ``typ``, even through a struct field,
        since lowering never handles poison."""
        if typ not in self._reachable_poison:
            self._reachable_poison[typ] = typ.reachable_poison_proof()
        reported = self._reachable_poison[typ]
        if reported is not None:
            self._fail(reported)

    @overload
    def _report_and_fail(self, err: diag.Diag, /) -> diag.ReportProof:
        pass

    @overload
    def _report_and_fail(
        self, kind: diag.DiagKind, span: Optional[src.SrcSpan], /, **args: diag.DiagArgValue
    ) -> diag.ReportProof:
        pass

    def _report_and_fail(
        self,
        err: diag.Diag | diag.DiagKind,
        span: Optional[src.SrcSpan] = None,
        /,
        **args: diag.DiagArgValue,
    ) -> diag.ReportProof:
        """Report an error, given as ``diag.Diags.error`` takes one, failing the check, and
        return its proof."""
        return self._fail(self._ctx.diags.error(diag.as_diag(err, span, args)))

    @overload
    def _report_and_poison(self, err: diag.Diag, /) -> typs.PoisonTyp:
        pass

    @overload
    def _report_and_poison(
        self, kind: diag.DiagKind, span: Optional[src.SrcSpan], /, **args: diag.DiagArgValue
    ) -> typs.PoisonTyp:
        pass

    def _report_and_poison(
        self,
        err: diag.Diag | diag.DiagKind,
        span: Optional[src.SrcSpan] = None,
        /,
        **args: diag.DiagArgValue,
    ) -> typs.PoisonTyp:
        """Report an error as ``_report_and_fail`` does, and return poison for it."""
        return typs.PoisonTyp(self._report_and_fail(diag.as_diag(err, span, args)))

    def _finish(self) -> check_results.TypCheckResults:
        if self._failures:
            raise diag.ReportedError(self._failures[0])
        return self.results

    def _set_local_typ(self, decl: resolve.LocalDecl, typ: typs.PtrTyp) -> None:
        self._fail_if_poison_reachable(typ)
        self.results._set_local_typ(decl, typ)

    def _set_applied_fn(self, node: ast.VarExpr, applied: ir_module.AppliedFn) -> None:
        for arg in applied.args:
            self._fail_if_poison_reachable(arg)
        self.results._set_applied_fn(node, applied)

    @contextlib.contextmanager
    def _speculating(self) -> Iterator[None]:
        """Speculate in the block: record no lowering facts, and report nothing, as
        ``compilation.Ctx.speculating`` does."""
        previous = self.results._recording
        self.results._recording = False
        try:
            with self._ctx.speculating():
                yield
        finally:
            self.results._recording = previous

    def _probe_arg_typs(
        self,
        declared_typs: Sequence[typs.Typ],
        arg_asts: Sequence[ast.ExprKind],
        e: ir_env.Env,
    ) -> dict[typs.ComptimeParamTyp, typs.Typ]:
        """Infer comptime arguments from each argument's type, matched against its declared
        type from left to right, reporting nothing.

        Unsuffixed integer literals are skipped because they need an expected type, and an
        argument that fails to check contributes nothing. A poisoned argument binds each
        parameter in its declared type that no other argument binds to poison, so that
        nothing more is reported about it.
        """
        bindings: dict[typs.ComptimeParamTyp, typs.Typ] = {}
        poisoned: list[tuple[typs.Typ, diag.ReportProof]] = []
        with self._speculating():
            for declared_typ, arg_ast in zip(declared_typs, arg_asts, strict=False):
                if _is_flexible_int_lit(arg_ast):
                    continue
                try:
                    arg_typ = self._check_expr(arg_ast, e, None)
                except diag.SpeculativeError:
                    continue
                reported = arg_typ.report_proof()
                if reported is not None:
                    poisoned.append((declared_typ, reported))
                    continue
                declared_typ.infer_typ_args(arg_typ, bindings)
        for declared_typ, reported in poisoned:
            _poison_unbound_params(declared_typ, reported, bindings)
        return bindings

    def _replay_probed_args(
        self,
        declared_typs: Sequence[typs.Typ],
        arg_asts: Sequence[ast.ExprKind],
        comptime_params: Sequence[typs.ComptimeParamTyp],
        bindings: Mapping[typs.ComptimeParamTyp, typs.Typ],
        e: ir_env.Env,
    ) -> None:
        """Check the arguments ``_probe_arg_typs`` probed, without speculating, so that what
        it dropped is reported.

        Each argument is checked against its declared type if the bindings settle every
        parameter of ``comptime_params`` in it, and otherwise without an expected type,
        except that an unsuffixed integer literal, which needs one, is skipped. The
        bindings start as ``bindings``, and each argument adds to them for the arguments
        after it, as ``_probe_arg_typs`` does.
        """
        bindings = dict(bindings)
        for declared_typ, arg_ast in zip(declared_typs, arg_asts, strict=False):
            expected_typ = _settled_typ(declared_typ, comptime_params, bindings)
            if expected_typ is None and _is_flexible_int_lit(arg_ast):
                continue
            arg_typ = self._check_expr(arg_ast, e, expected_typ)
            reported = arg_typ.report_proof()
            if reported is None:
                declared_typ.infer_typ_args(arg_typ, bindings)
            else:
                _poison_unbound_params(declared_typ, reported, bindings)

    def _infer_comptime_args(
        self,
        item_kind: str,
        item: str,
        comptime_params: Sequence[typs.ComptimeParamTyp],
        declared_typs: Sequence[typs.Typ],
        arg_asts: Sequence[ast.ExprKind],
        e: ir_env.Env,
        span: Optional[src.SrcSpan],
    ) -> tuple[typs.Typ, ...] | diag.ReportProof:
        """Infer an item's comptime arguments as ``_probe_arg_typs`` does, and check their
        bounds.

        If that fails, the arguments are checked as ``_replay_probed_args`` does, so that an
        argument's own error is reported, and the proof of the failure is returned. A
        parameter that is left unbound is reported as uninferable, unless checking the
        arguments failed, which explains it.
        """
        bindings = self._probe_arg_typs(declared_typs, arg_asts, e)
        reported = None
        if all(param in bindings for param in comptime_params):
            comptime_args = tuple(bindings[param] for param in comptime_params)
            try:
                typs.check_comptime_arg_bounds(comptime_params, comptime_args, e, span)
            except diag.ReportedError as err:
                reported = err.reported
            else:
                return comptime_args

        failure_count = len(self._failures)
        self._replay_probed_args(declared_typs, arg_asts, comptime_params, bindings, e)
        if reported is not None:
            return self._fail(reported)
        if len(self._failures) > failure_count:
            return self._failures[failure_count]
        unbound = next(param for param in comptime_params if param not in bindings)
        return self._report_uninferable(span, item_kind, item, unbound)

    def check_fn(
        self,
        fn_ast: ast.FnDefn,
        e: ir_env.Env,
        ret_typ: typs.Typ,
        params: tuple[ir_values.Param, ...],
    ) -> check_results.TypCheckResults:
        """Type-check a function body and return its lowering facts."""
        self._ret_typ = ret_typ
        self._ret_typ_span = opt_util.opt_map(fn_ast.ret_typ, lambda t: t.span)
        self._fn_name = fn_ast.name.name

        e = e.new_child()
        self._fail_if_poison_reachable(ret_typ)
        for param in params:
            assert param.ast is not None
            param_typ = typs.PtrTyp(param.typ, typs.CONST)
            self._set_local_typ(param.ast, param_typ)
            e.add_var(param.ast.name.name, param.ast, param.ast.name.span)
        block_typ = self._check_expr(fn_ast.block, e, ret_typ)
        ret_ast = opt_util.opt_or_default(fn_ast.block.expr, fn_ast.block)

        if block_typ != typs.VOID:
            # never is exempt here too - it's recorded (as NeverDiverge)
            # but that's never Invalid, matching CfgBuilder's own
            # never-typed early return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast, block_typ, ret_typ)
            if isinstance(coercion, check_results.Invalid):
                self._report_ret_typ_error(
                    diag_kinds.RETURN_TYPE_MISMATCH, ret_ast.span, given_typ=block_typ
                )
        elif ret_typ != typs.VOID:
            # A missing return concerns the whole function.
            self._report_ret_typ_error(diag_kinds.MISSING_RETURN, fn_ast.span)

        return self._finish()

    def check_var_initializer(
        self, defn_ast: ast.VarDefn, e: ir_env.Env
    ) -> check_results.TypCheckResults:
        """Type-check a module-level initializer and return its lowering facts."""
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        e = e.new_child()
        self._check_let_initializer(defn_ast.let_stmt, e)
        return self._finish()

    def _check_expr(
        self, expr_ast: ast.ExprKind, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        """Return an expression's checked type, which is poison if checking it raised
        ``diag.ReportedError``."""
        try:
            typ = self._check_expr_inner(expr_ast, e, expected_typ)
        except diag.ReportedError as err:
            typ = typs.PoisonTyp(err.reported)
        self._fail_if_poison_reachable(typ)
        self.results._set_expr_typ(expr_ast, typ)
        return typ

    def _check_expr_inner(
        self, expr_ast: ast.ExprKind, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        """Return an expression's checked type, using ``expected_typ`` as context."""
        match expr_ast:
            case ast.BlockExpr():
                return self._check_block_expr(expr_ast, e, expected_typ)
            case ast.IfExpr():
                return self._check_if_expr(expr_ast, e, expected_typ)
            case ast.WhileExpr():
                return self._check_while_expr(expr_ast, e)
            case ast.MatchExpr():
                return self._check_match_expr(expr_ast, e, expected_typ)
            case ast.CallExpr():
                return self._check_call_expr(expr_ast, e, expected_typ)
            case ast.BinOpExpr():
                return self._check_bin_op_expr(expr_ast, e, expected_typ)
            case ast.UnaryOpExpr():
                return self._check_unary_op_expr(expr_ast, e, expected_typ)
            case ast.StrLit():
                return typs.CSTR
            case ast.IntLit():
                typ = self._infer_int_lit_typ(expr_ast, expected_typ)
                if not isinstance(typ, typs.IntTyp):
                    return typ
                if not typ.fits(expr_ast.value):
                    self._report_and_fail(
                        diag_kinds.INTEGER_LITERAL_OVERFLOW,
                        expr_ast.span,
                        value=expr_ast.value,
                        typ=typ,
                    )
                self.results._set_folded_int_lit(expr_ast, typ, expr_ast.value)
                return typ
            case ast.BoolLit():
                return typs.BOOL
            case ast.VarExpr():
                return self._check_var_expr(expr_ast, e, expected_typ)
            case ast.ArrayAccessExpr():
                return self._check_array_access_expr(expr_ast, e)
            case ast.BraceExpr():
                return self._check_brace_expr(expr_ast, e)
            case ast.FieldAccessExpr():
                return self._check_field_access_expr(expr_ast, e)
            case ast.DerefExpr():
                return self._check_deref_expr(expr_ast, e)

    def _check_block_expr(
        self, block_ast: ast.BlockExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        e = e.new_child()
        diverged = False
        for stmt_ast in block_ast.stmts:
            try:
                if self._check_stmt(stmt_ast, e):
                    diverged = True
            except diag.ReportedError as err:
                self._fail(err.reported)
        if block_ast.expr is None:
            return typs.NEVER if diverged else typs.VOID
        return self._check_expr(block_ast.expr, e, expected_typ)

    def _check_if_expr(
        self, if_ast: ast.IfExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        cond_typ = self._check_expr(if_ast.condition, e, None)
        cond_coercion = self._record_coercion(if_ast.condition, cond_typ, typs.BOOL)
        if isinstance(cond_coercion, check_results.Invalid):
            self._report_and_fail(
                diag_kinds.IF_CONDITION_TYPE_MISMATCH,
                if_ast.condition.span,
                expr=if_ast.condition.diag_str(),
                typ=cond_typ,
            )

        if if_ast.els is None:
            then_typ = self._check_expr(if_ast.then, e, expected_typ)
            if then_typ != typs.NEVER and then_typ != typs.VOID:
                self._report_and_fail(
                    diag_kinds.IF_WITHOUT_ELSE_TYPE_MISMATCH, if_ast.then.span, typ=then_typ
                )
            return typs.VOID

        els_ast = if_ast.els
        if (
            expected_typ is None
            and _is_flexible_int_lit(if_ast.then)
            and not _is_flexible_int_lit(els_ast)
        ):
            els_typ = self._check_expr(els_ast, e, expected_typ)
            then_hint = expected_typ
            if els_typ != typs.NEVER:
                then_hint = _resolve_peer_typ([els_typ])
            then_typ = self._check_expr(if_ast.then, e, then_hint)
        else:
            then_typ = self._check_expr(if_ast.then, e, expected_typ)
            els_hint = expected_typ
            if expected_typ is None and then_typ != typs.NEVER and _is_flexible_int_lit(els_ast):
                els_hint = _resolve_peer_typ([then_typ])
            els_typ = self._check_expr(els_ast, e, els_hint)

        if then_typ != typs.NEVER and els_typ != typs.NEVER and then_typ != els_typ:
            return self._report_and_poison(
                diag.Diag.new(diag_kinds.CONFLICTING_BRANCH_TYPES, if_ast.span)
                .with_label(diag_kinds.IF_TYP, if_ast.then.span, typ=then_typ)
                .with_label(diag_kinds.ELSE_TYP, els_ast.span, typ=els_typ)
            )

        if then_typ != typs.NEVER:
            return then_typ
        if els_typ != typs.NEVER:
            return els_typ
        return typs.NEVER

    def _check_match_expr(
        self, match_ast: ast.MatchExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        scrutinee_typ = self._check_expr(match_ast.scrutinee, e, None)
        self.results._set_match_scrutinee_typ(match_ast, scrutinee_typ)

        arm_envs: dict[int, ir_env.Env] = {}
        arm_patterns: list[Optional[patterns.PatternKind]] = []
        for i, arm_ast in enumerate(match_ast.arms):
            arm_env = e.new_child()
            arm_envs[i] = arm_env
            arm_patterns.append(self._check_pattern(arm_ast.pattern, scrutinee_typ, arm_env))

        fixed_indices, flexible_indices = _match_arm_check_order(match_ast.arms)
        arm_typs: dict[int, typs.Typ] = {}
        for i in fixed_indices:
            arm_ast = match_ast.arms[i]
            arm_typs[i] = self._check_expr(arm_ast.body, arm_envs[i], expected_typ)

        peer_hint = (
            expected_typ
            if expected_typ is not None
            else _resolve_peer_typ([arm_typs[i] for i in fixed_indices])
        )
        for i in flexible_indices:
            arm_ast = match_ast.arms[i]
            arm_typs[i] = self._check_expr(arm_ast.body, arm_envs[i], peer_hint)

        result_typ = typs.NEVER
        result_span: Optional[src.SrcSpan] = None
        for i, arm_ast in enumerate(match_ast.arms):
            arm_typ = arm_typs[i]
            if arm_typ == typs.NEVER:
                continue
            if result_typ == typs.NEVER:
                result_typ = arm_typ
                result_span = arm_ast.body.span
            elif arm_typ != result_typ:
                result_typ = self._report_and_poison(
                    diag.Diag.new(diag_kinds.CONFLICTING_MATCH_ARM_TYPES, match_ast.span)
                    .with_label(diag_kinds.MATCH_ARM_TYP, result_span, typ=result_typ)
                    .with_label(diag_kinds.MATCH_ARM_TYP, arm_ast.body.span, typ=arm_typ)
                )

        checked_patterns = [pattern for pattern in arm_patterns if pattern is not None]
        if scrutinee_typ.report_proof() is None and len(checked_patterns) == len(arm_patterns):
            self._check_match_coverage(match_ast, checked_patterns, scrutinee_typ, e)
        return result_typ

    def _check_match_coverage(
        self,
        match_ast: ast.MatchExpr,
        arm_patterns: list[patterns.PatternKind],
        scrutinee_typ: typs.Typ,
        e: ir_env.Env,
    ) -> None:
        """Check a match's exhaustiveness and unreachable arms, and record its plan."""
        space = self._match_constructor_space(scrutinee_typ, e)
        plan = patterns.build_match_plan(arm_patterns, space)
        reachable_arms = set(plan.reachable_arms)
        for i, arm_ast in enumerate(match_ast.arms):
            if i not in reachable_arms:
                self._ctx.diags.warn(diag_kinds.UNREACHABLE_MATCH_ARM, arm_ast.span)
        if plan.missing:
            d = diag.Diag.new(diag_kinds.NON_EXHAUSTIVE_MATCH, match_ast.span)
            for witness in plan.missing:
                d = d.with_note(diag_kinds.UNCOVERED_PATTERN, pattern=witness.render())
            self._report_and_fail(d)
        self.results._set_match_plan(match_ast, plan)

    def _check_while_expr(self, while_ast: ast.WhileExpr, e: ir_env.Env) -> typs.Typ:
        cond_typ = self._check_expr(while_ast.condition, e, None)
        cond_coercion = self._record_coercion(while_ast.condition, cond_typ, typs.BOOL)
        if isinstance(cond_coercion, check_results.Invalid):
            self._report_and_fail(
                diag_kinds.WHILE_CONDITION_TYPE_MISMATCH,
                while_ast.condition.span,
                expr=while_ast.condition.diag_str(),
                typ=cond_typ,
            )

        if while_ast.label is not None and reserved.is_reserved(while_ast.label.name):
            self._report_and_fail(
                diag_kinds.RESERVED_NAME, while_ast.label.span, name=while_ast.label.name
            )
        self._loop_labels.append((opt_util.opt_map(while_ast.label, lambda x: x.name), while_ast))
        try:
            block_typ = self._check_expr(while_ast.block, e, None)
        finally:
            self._loop_labels.pop()
        if block_typ not in (typs.NEVER, typs.VOID):
            self._report_and_fail(
                diag_kinds.WHILE_BODY_TYPE_MISMATCH, while_ast.block.span, typ=block_typ
            )

        return typs.VOID

    def _check_pattern(
        self, pat: ast.PatternKind, column_typ: typs.Typ, e: ir_env.Env
    ) -> Optional[patterns.PatternKind]:
        """Check a pattern against its column's type, binding the names it binds.

        Returns ``None`` if the pattern has an error, or its column is poisoned, since then
        it can't take part in a match plan.
        """
        match pat:
            case ast.WildcardPattern():
                return patterns.WildcardPattern()
            case ast.BindingPattern():
                return self._check_binding_pattern(pat, column_typ, e)
            case ast.IntLitPattern():
                return self._check_int_lit_pattern(pat, column_typ, e)
            case ast.BoolLitPattern():
                return self._check_bool_lit_pattern(pat, column_typ)
            case ast.PathPattern():
                return self._check_path_pattern(pat, column_typ, e)
            case ast.OrPattern():
                return self._check_or_pattern(pat, column_typ, e)

    def _check_binding_pattern(
        self, pat: ast.BindingPattern, column_typ: typs.Typ, e: ir_env.Env
    ) -> Optional[patterns.WildcardPattern]:
        failed = False
        if column_typ == typs.VOID:
            column_typ = self._report_and_poison(diag_kinds.VOID_INITIALIZER, pat.span)
            failed = True
        mut = typs.Mutability.from_ast(pat.mut)
        self._set_local_typ(pat, typs.PtrTyp(column_typ, mut))
        if reserved.is_reserved(pat.ident.name):
            self._report_and_fail(diag_kinds.RESERVED_NAME, pat.ident.span, name=pat.ident.name)
            failed = True
        e.add_var(pat.ident.name, pat, pat.ident.span)
        if failed or column_typ.report_proof() is not None:
            return None
        return patterns.WildcardPattern()

    def _check_int_lit_pattern(
        self, pat: ast.IntLitPattern, column_typ: typs.Typ, e: ir_env.Env
    ) -> Optional[patterns.ConstructorPattern]:
        if column_typ.report_proof() is not None:
            return None
        lit_typ = self._infer_int_lit_typ(
            pat.lit,
            column_typ if isinstance(column_typ, typs.IntTyp) else None,
        )
        assert isinstance(lit_typ, typs.IntTyp)
        value = -pat.lit.value if pat.negative else pat.lit.value
        if not lit_typ.fits(value):
            self._report_and_fail(
                diag_kinds.INTEGER_LITERAL_OVERFLOW, pat.span, value=value, typ=lit_typ
            )
            return None
        if not isinstance(column_typ, typs.IntTyp) or lit_typ != column_typ:
            self._report_pattern_typ_mismatch(pat, lit_typ, column_typ)
            return None
        constructor = patterns.IntConstructor(value)
        self.results._set_pattern_constructor(pat, constructor)
        return patterns.ConstructorPattern(constructor, ())

    def _check_bool_lit_pattern(
        self, pat: ast.BoolLitPattern, column_typ: typs.Typ
    ) -> Optional[patterns.ConstructorPattern]:
        if column_typ != typs.BOOL:
            self._report_pattern_typ_mismatch(pat, typs.BOOL, column_typ)
            return None
        constructor = patterns.BoolConstructor(pat.lit.value)
        self.results._set_pattern_constructor(pat, constructor)
        return patterns.ConstructorPattern(constructor, ())

    def _check_path_pattern(
        self, pat: ast.PathPattern, column_typ: typs.Typ, e: ir_env.Env
    ) -> Optional[patterns.ConstructorPattern]:
        try:
            target = e.resolve_var(pat.path)
        except diag.ReportedError as err:
            self._check_payload_against_poison(pat, self._fail(err.reported), e)
            return None
        match target:
            case ir_values.ComptimeEnum() as variant:
                return self._check_enum_variant_pattern(pat, variant, column_typ, e)
            case typs.UnionVariantRef() as ref:
                return self._check_union_variant_pattern(pat, ref, column_typ, e)
            case _:
                reported = self._report_and_fail(
                    diag_kinds.NON_PATTERN_PATH, pat.span, path=pat.path.str()
                )
                self._check_payload_against_poison(pat, reported, e)
                return None

    def _check_payload_against_poison(
        self, pat: ast.PathPattern, reported: diag.ReportProof, e: ir_env.Env
    ) -> None:
        """Check a path pattern's payload against poisoned columns, so that the names it
        binds are bound."""
        for subpattern in pat.payload:
            self._check_pattern(subpattern, typs.PoisonTyp(reported), e)

    def _check_enum_variant_pattern(
        self,
        pat: ast.PathPattern,
        variant: ir_values.ComptimeEnum,
        column_typ: typs.Typ,
        e: ir_env.Env,
    ) -> Optional[patterns.ConstructorPattern]:
        """Check an enum variant pattern, which carries no payload to destructure."""
        if pat.payload:
            reported = self._report_and_fail(
                diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,
                pat.span,
                variant=pat.path.str(),
                given=len(pat.payload),
                expected=0,
            )
            self._check_payload_against_poison(pat, reported, e)
            return None
        if variant.typ != column_typ:
            self._report_pattern_typ_mismatch(pat, variant.typ, column_typ)
            return None
        constructor = patterns.VariantConstructor(variant.value, pat.path.str())
        self.results._set_pattern_constructor(pat, constructor)
        return patterns.ConstructorPattern(constructor, ())

    def _check_union_variant_pattern(
        self,
        pat: ast.PathPattern,
        ref: typs.UnionVariantRef,
        column_typ: typs.Typ,
        e: ir_env.Env,
    ) -> Optional[patterns.ConstructorPattern]:
        """Check a union variant pattern against the column it destructures.

        Comptime arguments written on the pattern's own path are redundant,
        since the column type already fixes the instance, but they are
        checked to name that same instance.
        """
        if not isinstance(column_typ, typs.UnionTyp) or column_typ.template is not ref.template:
            reported = self._report_pattern_typ_mismatch(pat, ref.owner.name, column_typ)
            self._check_payload_against_poison(pat, reported, e)
            return None
        if isinstance(ref.owner, typs.UnionTyp) and ref.owner is not column_typ:
            reported = self._report_pattern_typ_mismatch(pat, ref.owner, column_typ)
            self._check_payload_against_poison(pat, reported, e)
            return None

        variant = column_typ.variant_at(ref.variant.index)
        payload_typs: list[typs.Typ] = list(variant.payload_typs)
        failed = False
        if len(pat.payload) != variant.arity:
            reported = self._report_and_fail(
                diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,
                pat.span,
                variant=pat.path.str(),
                given=len(pat.payload),
                expected=variant.arity,
            )
            payload_typs.extend([typs.PoisonTyp(reported)] * (len(pat.payload) - variant.arity))
            failed = True
        subpatterns = [
            self._check_pattern(subpattern, payload_typ, e)
            for subpattern, payload_typ in zip(pat.payload, payload_typs, strict=False)
        ]
        checked_subpatterns = tuple(sub for sub in subpatterns if sub is not None)
        if failed or len(checked_subpatterns) != len(subpatterns):
            return None

        constructor = self._union_variant_constructors(column_typ, e)[variant.index]
        self.results._set_pattern_constructor(pat, constructor)
        return patterns.ConstructorPattern(constructor, checked_subpatterns)

    def _check_or_pattern(
        self, pat: ast.OrPattern, column_typ: typs.Typ, e: ir_env.Env
    ) -> Optional[patterns.OrPattern]:
        alternatives: list[Optional[patterns.PatternKind]] = []
        for alternative in pat.alternatives:
            bindings = list(_binding_patterns(alternative))
            if not bindings:
                alternatives.append(self._check_pattern(alternative, column_typ, e))
                continue
            alternatives.append(None)
            for binding in bindings:
                reported = self._report_and_fail(diag_kinds.BINDING_IN_OR_PATTERN, binding.span)
                self._bind_name_to_poison(binding, reported, e)
        checked = tuple(alternative for alternative in alternatives if alternative is not None)
        if len(checked) != len(alternatives):
            return None
        return patterns.OrPattern(checked)

    def _bind_name_to_poison(
        self, binding: ast.BindingPattern, reported: diag.ReportProof, e: ir_env.Env
    ) -> None:
        """Bind a name an or-pattern alternative may not bind with poison, unless another
        alternative already bound it, so that its uses report nothing more."""
        if e.is_bound_here(ir_env.Env.Namespace.VARS, binding.ident.name):
            return
        self._set_local_typ(binding, typs.PtrTyp(typs.PoisonTyp(reported), typs.CONST))
        e.add_var(binding.ident.name, binding, binding.ident.span)

    def _report_pattern_typ_mismatch(
        self, pat: ast.PatternKind, pattern_typ: diag.DiagArgValue, scrutinee_typ: typs.Typ
    ) -> diag.ReportProof:
        return self._report_and_fail(
            diag_kinds.PATTERN_TYPE_MISMATCH,
            pat.span,
            pattern=pat.diag_str(),
            pattern_typ=pattern_typ,
            scrutinee_typ=scrutinee_typ,
        )

    def _union_variant_constructors(
        self, union_typ: typs.UnionTyp, e: ir_env.Env
    ) -> tuple[patterns.VariantConstructor, ...]:
        """Return one constructor per variant, in declaration order."""
        return e.ctx.union_variant_constructors(
            union_typ, lambda: self._build_variant_constructors(union_typ, e)
        )

    def _build_variant_constructors(
        self, union_typ: typs.UnionTyp, e: ir_env.Env
    ) -> tuple[patterns.VariantConstructor, ...]:
        return tuple(
            patterns.VariantConstructor(
                variant.index,
                f"{union_typ.template.name}::{variant.name}",
                field_spaces=tuple(
                    self._match_constructor_space(payload_typ, e)
                    for payload_typ in variant.payload_typs
                ),
            )
            for variant in union_typ.variants
        )

    def _match_constructor_space(
        self, column_typ: typs.Typ, e: ir_env.Env
    ) -> patterns.ConstructorSpace:
        """Return the complete constructor space for a match column's type."""
        match column_typ:
            case typs.EnumTyp():
                constructors = [
                    patterns.VariantConstructor(
                        discriminant,
                        f"{column_typ.name}::{variant_name}",
                    )
                    for variant_name, discriminant in column_typ.variants.items()
                ]
                return patterns.ConstructorSpace.from_constructors(constructors, False)
            case typs.UnionTyp():
                return patterns.ConstructorSpace.from_constructors(
                    self._union_variant_constructors(column_typ, e), False
                )
            case typs.BoolTyp():
                return patterns.ConstructorSpace.from_constructors(
                    [patterns.BoolConstructor(False), patterns.BoolConstructor(True)],
                    False,
                )
            case typs.NeverTyp():
                return patterns.ConstructorSpace.from_constructors([], False)
            case _:
                # All other current and future types have an open constructor space.
                return patterns.ConstructorSpace.from_constructors([], True)

    def _check_call_expr(
        self, call_ast: ast.CallExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        try:
            callee = self._resolve_callee(call_ast, e, expected_typ)
        except diag.ReportedError as err:
            callee = _Callee(self._fail_and_poison(err.reported))
        fn_typ = callee.typ
        if not isinstance(fn_typ, typs.CallableTyp):
            if not callee.args_checked:
                for arg_ast in call_ast.args:
                    self._check_expr(arg_ast, e, fn_typ)
            return fn_typ
        assert not callee.args_checked
        callee_diag_str = call_ast.callee.diag_str()
        param_typs = fn_typ.param_typs
        recv_ast = callee.recv_ast
        recv_typ = callee.recv_typ

        num_args = len(call_ast.args) + (1 if recv_typ is not None else 0)
        num_params = len(param_typs)
        miscounted = self._check_arg_count(callee_diag_str, call_ast, num_args, num_params)

        if recv_ast is not None and recv_typ is not None:
            recv_coercion = self._record_coercion(recv_ast, recv_typ, param_typs[0])
            if isinstance(recv_coercion, check_results.Invalid):
                self._report_and_fail(
                    diag_kinds.ARGUMENT_TYPE_MISMATCH,
                    recv_ast.span,
                    arg_num=1,
                    callee=callee_diag_str,
                    given_typ=recv_typ,
                    expected_typ=param_typs[0],
                )

        offset = 1 if recv_typ is not None else 0
        for i, arg_ast in enumerate(call_ast.args, start=offset):
            if miscounted is not None and i >= num_params:
                self._check_expr(arg_ast, e, typs.PoisonTyp(miscounted))
                continue
            arg_typ = self._check_expr(arg_ast, e, param_typs[i])
            arg_coercion = self._record_coercion(arg_ast, arg_typ, param_typs[i])
            if isinstance(arg_coercion, check_results.Invalid):
                self._report_and_fail(
                    diag_kinds.ARGUMENT_TYPE_MISMATCH,
                    arg_ast.span,
                    arg_num=i + 1,
                    callee=callee_diag_str,
                    given_typ=arg_typ,
                    expected_typ=param_typs[i],
                )

        return fn_typ.ret_typ

    def _check_arg_count(
        self, callee_diag_str: str, call_ast: ast.CallExpr, num_args: int, num_params: int
    ) -> Optional[diag.ReportProof]:
        if num_args == num_params:
            return None
        # Too few arguments are reported at the call, and too many at the last one.
        span = call_ast.span
        if num_args > num_params:
            span = call_ast.args[-1].span
        return self._report_and_fail(
            diag_kinds.ARGUMENT_COUNT_MISMATCH,
            span,
            callee=callee_diag_str,
            given=num_args,
            expected=num_params,
        )

    def _resolve_callee(
        self, call_ast: ast.CallExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> _Callee:
        """Resolve a callee and its optional pointer-typed method receiver.

        ``x.name`` prefers a method and otherwise falls back to field access.
        """
        callee_ast = call_ast.callee
        if isinstance(callee_ast, ast.VarExpr):
            # Local to avoid the ir_module import cycle.
            from leech import ir_module  # noqa: PLC0415

            var = self._resolve_var(callee_ast, e)
            if isinstance(var, typs.PoisonTyp):
                return _Callee(var)
            if isinstance(var, ir_module.FnCandidate):
                return self._resolve_fn_call(var, call_ast, e)
            if isinstance(var, typs.UnionVariantRef):
                return self._resolve_variant_callee(var, callee_ast, call_ast, e, expected_typ)

        if not isinstance(callee_ast, ast.FieldAccessExpr):
            callee_typ = self._check_expr(callee_ast, e, None)
            fn_typ = _callable_typ(callee_typ)
            if fn_typ is None:
                return _Callee(
                    self._report_and_poison(
                        diag_kinds.NON_FUNCTION_CALL,
                        callee_ast.span,
                        callee=callee_ast.diag_str(),
                        typ=callee_typ,
                    )
                )
            return _Callee(fn_typ)

        recv_typ = self._check_place(callee_ast.value, e)
        pointee_typ = recv_typ.pointee_typ
        poisoned_by = pointee_typ.report_proof()
        if poisoned_by is not None:
            return _Callee(self._fail_and_poison(poisoned_by))

        if isinstance(pointee_typ, typs.TypParamTyp):
            # An unsubstituted type parameter has no inherent members and
            # method lookup inside a generic body deliberately does not
            # select registry impls, including blanket impls. What it does
            # have is its own declared bounds, which is the only thing a
            # method call on one can resolve against here (see
            # _resolve_bound_method). This makes trait bounds actually
            # enforced rather than duck-typed: only a method from a bound
            # the parameter itself declares is callable.
            trait_method = self._resolve_bound_method(
                pointee_typ, callee_ast.field.name, callee_ast.field.span, e
            )
            if trait_method is not None:
                self.results.resolutions.set_trait_bound_callee(call_ast, trait_method)
                return _Callee(
                    trait_method.fn_typ_for_self(pointee_typ), callee_ast.value, recv_typ
                )
        else:
            method = e.ctx.impl_registry.lookup_member(
                pointee_typ, callee_ast.field.name, callee_ast.field.span
            )
            self.results.resolutions.set_callee(call_ast, method)
            if method is not None:
                selected_fn = method.fn
                if not selected_fn.is_accessible_from(callee_ast.span.file):
                    name = callee_ast.field.name
                    d = diag.Diag.new(
                        diag_kinds.PRIVATE_ITEM_ACCESS,
                        callee_ast.field.span,
                        item_kind="function",
                        name=name,
                    )
                    self._report_and_fail(
                        d.with_label(diag_kinds.DEFINED_HERE, selected_fn.span, name=name)
                    )
                assert selected_fn.ast is not None
                if selected_fn.ast.receiver is None:
                    name = callee_ast.field.name
                    d = diag.Diag.new(
                        diag_kinds.ASSOCIATED_FUNCTION_USED_AS_METHOD,
                        callee_ast.field.span,
                        fn=name,
                        struct_typ=pointee_typ,
                    )
                    return _Callee(
                        self._report_and_poison(
                            d.with_label(diag_kinds.DEFINED_HERE, selected_fn.span, name=name)
                        )
                    )
                return _Callee(method.fn_typ, callee_ast.value, recv_typ)

        field = _struct_field(pointee_typ, callee_ast.field.name)
        if field is not None:
            self.results._set_struct_field_index(callee_ast, field.index)
        callee_typ = opt_util.opt_or_default(opt_util.opt_map(field, lambda f: f.typ), typs.VOID)
        fn_typ = _callable_typ(callee_typ)
        if fn_typ is None:
            return _Callee(
                self._report_and_poison(
                    diag_kinds.NON_FUNCTION_CALL,
                    callee_ast.span,
                    callee=callee_ast.diag_str(),
                    typ=callee_typ,
                )
            )
        return _Callee(fn_typ)

    def _resolve_bound_method(
        self, typ_param: typs.TypParamTyp, name: str, span: Optional[src.SrcSpan], e: ir_env.Env
    ) -> Optional[ir_traits.TraitMethod]:
        """Resolve a type parameter's method against only its declared trait bounds."""
        # Local to avoid the ir_traits import cycle.
        from leech import ir_traits  # noqa: PLC0415

        matches: list[ir_traits.TraitMethod] = []
        for bound in typ_param.bounds:
            application = e.resolve_trait(bound.path)
            method = application.trait.get_trait_method(name)
            if method is not None:
                if application.args:
                    # The signature would still name the trait's own type
                    # parameters, not the bound's arguments; substituting
                    # them needs generic traits.
                    raise NotImplementedError(
                        "calling a method through a bound with generic arguments"
                        " isn't supported yet"
                    )
                matches.append(method)
        return ir_traits.disambiguate(matches, name, typ_param, span, self._ctx.diags)

    def _resolve_fn_call(
        self, candidate: ir_module.FnCandidate, call_ast: ast.CallExpr, e: ir_env.Env
    ) -> _Callee:
        """Complete and record a path-selected function call."""
        callee_ast = asserts.checked_cast(call_ast.callee, ast.VarExpr)
        fn = candidate.fn
        if candidate.explicit_fn_args is None:
            fn_args = self._infer_comptime_args(
                "function",
                fn.name,
                fn.comptime_params,
                fn.fn_typ.param_typs,
                call_ast.args,
                e,
                call_ast.span,
            )
            if isinstance(fn_args, diag.ReportProof):
                return _Callee(self._fail_and_poison(fn_args), args_checked=True)
        else:
            fn_args = candidate.explicit_fn_args
        applied = candidate.apply(fn_args)
        self._set_applied_fn(callee_ast, applied)
        return _Callee(applied.fn_typ)

    def _check_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        match op_ast.op.name:
            case "and" | "or":
                return self._check_logic_bin_op_expr(op_ast, e)
            case "<" | "<=" | "==" | "!=" | ">=" | ">" | "+" | "-" | "*" | "/" as op:
                return self._check_numeric_bin_op_expr(op_ast, op, e, expected_typ)

    def _check_numeric_bin_op_expr(
        self,
        op_ast: ast.BinOpExpr,
        op: ast.CmpOpName | ast.ArithmeticOpName,
        e: ir_env.Env,
        expected_typ: Optional[typs.Typ],
    ) -> typs.Typ:
        # Unreachable operands are still checked; never stands in for either value type.
        if _is_flexible_int_lit(op_ast.lhs) and not _is_flexible_int_lit(op_ast.rhs):
            rhs_typ = self._check_expr(op_ast.rhs, e, None)
            lhs_typ = self._check_expr(op_ast.lhs, e, _resolve_peer_typ([rhs_typ]))
        else:
            lhs_hint = None
            if _is_flexible_int_lit(op_ast.lhs):
                lhs_hint = expected_typ
            lhs_typ = self._check_expr(op_ast.lhs, e, lhs_hint)

            rhs_hint = None
            if _is_flexible_int_lit(op_ast.rhs):
                rhs_hint = _resolve_peer_typ([lhs_typ])
            rhs_typ = self._check_expr(op_ast.rhs, e, rhs_hint)

        for operand_typ in (lhs_typ, rhs_typ):
            poisoned_by = operand_typ.report_proof()
            if poisoned_by is not None:
                return self._fail_and_poison(poisoned_by)

        failure = None
        if lhs_typ != typs.NEVER and not isinstance(lhs_typ, typs.IntTyp):
            failure = self._report_bin_operand_typ_error(op_ast, "left", lhs_typ, "an integer type")
        elif lhs_typ != typs.NEVER and rhs_typ != typs.NEVER and lhs_typ != rhs_typ:
            failure = self._report_and_fail(
                diag.Diag.new(diag_kinds.CONFLICTING_OPERAND_TYPES, op_ast.op.span, op=op)
                .with_label(diag_kinds.LEFT_OPERAND_TYP, op_ast.lhs.span, typ=lhs_typ)
                .with_label(diag_kinds.RIGHT_OPERAND_TYP, op_ast.rhs.span, typ=rhs_typ)
            )

        match op:
            case "<" | "<=" | "==" | "!=" | ">=" | ">":
                return typs.BOOL
            case "+" | "-" | "*" | "/":
                if failure is not None:
                    return typs.PoisonTyp(failure)
                return lhs_typ if lhs_typ != typs.NEVER else rhs_typ

    def _check_logic_bin_op_expr(self, op_ast: ast.BinOpExpr, e: ir_env.Env) -> typs.Typ:
        # Both operands are checked; never coerces to bool without a special case.
        lhs_typ = self._check_expr(op_ast.lhs, e, None)
        lhs_coercion = self._record_coercion(op_ast.lhs, lhs_typ, typs.BOOL)
        if isinstance(lhs_coercion, check_results.Invalid):
            self._report_bin_operand_typ_error(op_ast, "left", lhs_typ, typs.BOOL)

        rhs_typ = self._check_expr(op_ast.rhs, e, None)
        rhs_coercion = self._record_coercion(op_ast.rhs, rhs_typ, typs.BOOL)
        if isinstance(rhs_coercion, check_results.Invalid):
            self._report_bin_operand_typ_error(op_ast, "right", rhs_typ, typs.BOOL)

        for operand_typ in (lhs_typ, rhs_typ):
            poisoned_by = operand_typ.report_proof()
            if poisoned_by is not None:
                return self._fail_and_poison(poisoned_by)
        return typs.BOOL

    def _report_bin_operand_typ_error(
        self,
        op_ast: ast.BinOpExpr,
        side: str,
        given_typ: typs.Typ,
        expected_typ: diag.DiagArgValue,
    ) -> diag.ReportProof:
        operand = op_ast.lhs
        if side == "right":
            operand = op_ast.rhs
        return self._report_and_fail(
            diag.Diag.new(
                diag_kinds.BINARY_OPERAND_TYPE_MISMATCH,
                operand.span,
                given_typ=given_typ,
                side=side,
                op=op_ast.op.name,
                expected_typ=expected_typ,
            ).with_label(diag_kinds.OP_HERE, op_ast.op.span, op=op_ast.op.name)
        )

    def _report_unary_operand_typ_error(
        self, op_ast: ast.UnaryOpExpr, given_typ: typs.Typ, expected_typ: diag.DiagArgValue
    ) -> diag.ReportProof:
        return self._report_and_fail(
            diag.Diag.new(
                diag_kinds.UNARY_OPERAND_TYPE_MISMATCH,
                op_ast.operand.span,
                given_typ=given_typ,
                op=op_ast.op.name,
                expected_typ=expected_typ,
            ).with_label(diag_kinds.OP_HERE, op_ast.op.span, op=op_ast.op.name)
        )

    def _check_unary_op_expr(
        self, op_ast: ast.UnaryOpExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        match op_ast.op.name:
            case "&":
                return self._check_addr_of_expr(op_ast, e)
            case "not":
                return self._check_not_expr(op_ast, e)
            case "-":
                return self._check_neg_expr(op_ast, e, expected_typ)

    def _check_addr_of_expr(self, op_ast: ast.UnaryOpExpr, e: ir_env.Env) -> typs.Typ:
        return self._check_place(op_ast.operand, e)

    def _check_not_expr(self, op_ast: ast.UnaryOpExpr, e: ir_env.Env) -> typs.Typ:
        operand_typ = self._check_expr(op_ast.operand, e, None)
        coercion = self._record_coercion(op_ast.operand, operand_typ, typs.BOOL)
        if isinstance(coercion, check_results.Invalid):
            self._report_unary_operand_typ_error(op_ast, operand_typ, typs.BOOL)

        poisoned_by = operand_typ.report_proof()
        if poisoned_by is not None:
            return self._fail_and_poison(poisoned_by)
        return typs.BOOL

    def _check_neg_expr(
        self, op_ast: ast.UnaryOpExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        if isinstance(op_ast.operand, ast.IntLit):
            typ = self._infer_int_lit_typ(op_ast.operand, expected_typ)
            if not isinstance(typ, typs.IntTyp):
                return typ
            if typ.signage != signage.SIGNED:
                return typs.PoisonTyp(
                    self._report_unary_operand_typ_error(op_ast, typ, "a signed integer type")
                )
            value = -op_ast.operand.value
            if not typ.fits(value):
                self._report_and_fail(
                    diag_kinds.INTEGER_LITERAL_OVERFLOW, op_ast.span, value=value, typ=typ
                )
            self.results._set_folded_int_lit(op_ast, typ, value)
            return typ

        operand_typ = self._check_expr(op_ast.operand, e, expected_typ)
        # never is exempt: it stands in for a value of any type.
        if operand_typ != typs.NEVER and (
            not isinstance(operand_typ, typs.IntTyp) or operand_typ.signage != signage.SIGNED
        ):
            return typs.PoisonTyp(
                self._report_unary_operand_typ_error(op_ast, operand_typ, "a signed integer type")
            )
        return operand_typ

    def _resolve_var(
        self, var_ast: ast.VarExpr, e: ir_env.Env
    ) -> resolve.VarTarget | typs.PoisonTyp:
        """Resolve ``var_ast``'s path, memoizing the result across repeat visits, or return
        poison if it fails."""
        try:
            return self.results.resolutions.var(var_ast)
        except KeyError:
            pass
        try:
            target = e.resolve_var(var_ast.path)
        except diag.ReportedError as err:
            return self._fail_and_poison(err.reported)
        self.results.resolutions.set_var(var_ast, target)
        return target

    def _check_var_expr(
        self, var_ast: ast.VarExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        # Local because runtime isinstance checks cannot use the TYPE_CHECKING import.
        from leech import ir_module  # noqa: PLC0415

        var = self._resolve_var(var_ast, e)
        if isinstance(var, typs.PoisonTyp):
            return var
        if isinstance(var, ir_module.FnCandidate):
            return self._fn_candidate_ptr_typ(var_ast, var)
        if isinstance(var, ir_values.ComptimeEnum):
            # An enum variant is an immediate value with no address.
            return var.typ
        if isinstance(var, typs.UnionVariantRef):
            return self._check_variant_ref(var, var_ast, e, expected_typ)
        if isinstance(var, typs.ValueParamTyp):
            # A value parameter is an immediate value with no address.
            return var.value_typ
        if isinstance(var, ast.Param | ast.Receiver | ast.LetStmt | ast.BindingPattern):
            return self.results.local_typ(var).pointee_typ
        return var.typ.pointee_typ

    def _check_variant_ref(
        self,
        ref: typs.UnionVariantRef,
        var_ast: ast.VarExpr,
        e: ir_env.Env,
        expected_typ: Optional[typs.Typ],
    ) -> typs.Typ:
        """Check a variant named outside a call, which must carry no payload."""
        if ref.variant.arity != 0:
            return self._report_and_poison(
                diag_kinds.MISSING_VARIANT_PAYLOAD, var_ast.span, variant=var_ast.path.str()
            )
        union_typ = self._variant_union_typ(ref, (), e, expected_typ, var_ast.span)
        if isinstance(union_typ, diag.ReportProof):
            return self._fail_and_poison(union_typ)
        self.results._set_variant_construction(var_ast, union_typ, ref.variant.index)
        return union_typ

    def _resolve_variant_callee(
        self,
        ref: typs.UnionVariantRef,
        callee_ast: ast.VarExpr,
        call_ast: ast.CallExpr,
        e: ir_env.Env,
        expected_typ: Optional[typs.Typ],
    ) -> _Callee:
        """Synthesize the constructor type of a variant applied to its payload."""
        if ref.variant.arity == 0:
            return _Callee(
                self._report_and_poison(
                    diag_kinds.UNEXPECTED_VARIANT_PAYLOAD,
                    call_ast.span,
                    variant=callee_ast.path.str(),
                )
            )
        # The arity is checked here rather than left to _check_call_expr,
        # which compares counts only after the callee resolves: inferring
        # this variant's comptime arguments reads the very arguments a
        # miscounted call is missing, so the count has to settle first.
        miscounted = self._check_arg_count(
            callee_ast.diag_str(), call_ast, len(call_ast.args), ref.variant.arity
        )
        if miscounted is not None:
            return _Callee(typs.PoisonTyp(miscounted))
        union_typ = self._variant_union_typ(ref, call_ast.args, e, expected_typ, call_ast.span)
        if isinstance(union_typ, diag.ReportProof):
            return _Callee(self._fail_and_poison(union_typ), args_checked=True)
        self.results._set_variant_construction(callee_ast, union_typ, ref.variant.index)
        variant = union_typ.variant_at(ref.variant.index)
        return _Callee(typs.FnTyp(union_typ, variant.payload_typs))

    def _variant_union_typ(
        self,
        ref: typs.UnionVariantRef,
        arg_asts: Sequence[ast.ExprKind],
        e: ir_env.Env,
        expected_typ: Optional[typs.Typ],
        span: Optional[src.SrcSpan],
    ) -> typs.UnionTyp | diag.ReportProof:
        """Settle the union instance a variant reference constructs.

        A path that spelled the comptime arguments out has already applied
        them. Otherwise they come from an expected type of the same union,
        or from inferring the declared payload types against the argument
        types, as ``_infer_comptime_args`` does, returning the proof of its failure if it
        fails.
        """
        if isinstance(ref.owner, typs.UnionTyp):
            return ref.owner

        template = ref.owner
        if isinstance(expected_typ, typs.UnionTyp) and expected_typ.template is template:
            return expected_typ

        comptime_args = self._infer_comptime_args(
            "union",
            template.name,
            template.comptime_params,
            ref.variant.payload_typs,
            arg_asts,
            e,
            span,
        )
        if isinstance(comptime_args, diag.ReportProof):
            return comptime_args
        return template.instantiate(comptime_args)

    def _fn_candidate_ptr_typ(
        self, var_ast: ast.VarExpr, candidate: ir_module.FnCandidate
    ) -> typs.Typ:
        """Complete and record a function used without call inference context."""
        if candidate.explicit_fn_args is None:
            return self._report_and_poison(
                diag_kinds.MISSING_COMPTIME_ARGUMENT, var_ast.span, item=candidate.fn.name
            )
        applied = candidate.apply(candidate.explicit_fn_args)
        self._set_applied_fn(var_ast, applied)
        return applied.ptr_typ

    def _check_array_access_expr(self, aa_expr: ast.ArrayAccessExpr, e: ir_env.Env) -> typs.Typ:
        arr_typ = self._check_expr(aa_expr.array, e, None)
        index_typ = self._check_expr(aa_expr.index, e, typs.USIZE)
        index_coercion = self._record_coercion(aa_expr.index, index_typ, typs.USIZE)
        if isinstance(index_coercion, check_results.Invalid):
            self._report_and_fail(diag_kinds.INDEX_TYPE_MISMATCH, aa_expr.index.span, typ=index_typ)

        if not isinstance(arr_typ, typs.ArrayTyp):
            return self._report_and_poison(
                diag_kinds.NON_ARRAY_INDEX, aa_expr.array.span, typ=arr_typ
            )
        return arr_typ.element_typ

    def _check_brace_expr(self, brace_expr: ast.BraceExpr, e: ir_env.Env) -> typs.Typ:
        try:
            typ = typs.Typ.from_ast(brace_expr.typ, e)
        except diag.ReportedError as err:
            typ = self._fail_and_poison(err.reported)
        match typ:
            case typs.ArrayTyp():
                self.results._set_brace_expr_typ(brace_expr, typ)
                return self._check_array_lit_expr(brace_expr, typ, e)
            case typs.StructTyp():
                self.results._set_brace_expr_typ(brace_expr, typ)
                return self._check_struct_lit_expr(brace_expr, typ, e)
            case _:
                # Brace expressions reject every other current or future type.
                result_typ = self._report_and_poison(
                    diag_kinds.NON_STRUCT_OR_ARRAY_LITERAL, brace_expr.typ.span, typ=typ
                )
                for element in brace_expr.elements:
                    self._check_element_against_poison(element, result_typ.reported, e)
                return result_typ

    def _check_element_against_poison(
        self, element: ast.BraceElement, reported: diag.ReportProof, e: ir_env.Env
    ) -> None:
        """Check a brace literal element's value where an error left no type to expect, so
        that nothing more is reported about it."""
        value = element.value if isinstance(element, ast.StructFieldExpr) else element
        self._check_expr(value, e, typs.PoisonTyp(reported))

    def _check_struct_lit_expr(
        self, brace_expr: ast.BraceExpr, struct_typ: typs.StructTyp, e: ir_env.Env
    ) -> typs.Typ:
        try:
            fields = struct_typ.fields
        except diag.ReportedError as err:
            reported = self._fail(err.reported)
            for element in brace_expr.elements:
                self._check_element_against_poison(element, reported, e)
            return typs.PoisonTyp(reported)

        field_value_asts: dict[str, ast.ExprKind] = {}
        field_value_typs: dict[str, typs.Typ] = {}
        # A value given without naming a known field may be the missing one, so missing
        # fields are reported only if every value names one.
        unnamed_value = False
        for element in brace_expr.elements:
            if not isinstance(element, ast.StructFieldExpr):
                reported = self._report_and_fail(
                    diag_kinds.POSITIONAL_VALUE_IN_STRUCT_EXPRESSION,
                    element.span,
                    struct_typ=struct_typ,
                )
                self._check_element_against_poison(element, reported, e)
                unnamed_value = True
                continue
            name = element.ident.name
            field = fields.get(name)
            if field is None:
                reported = self._report_unknown_field(struct_typ, name, element.ident.span)
                self._check_element_against_poison(element, reported, e)
                unnamed_value = True
                continue
            self.results._set_struct_field_index(element, field.index)
            if not field.is_accessible_from(brace_expr.span.file):
                self._report_private_field(struct_typ, field, element.ident.span)
            if name in field_value_asts:
                self._report_and_fail(
                    diag.Diag.new(
                        diag_kinds.DUPLICATE_STRUCT_FIELD_VALUE, element.ident.span, field=name
                    ).with_label(diag_kinds.PREVIOUS_VALUE_HERE, field_value_asts[name].span)
                )
                self._check_expr(element.value, e, field.typ)
                continue
            field_value_asts[name] = element.value
            field_value_typs[name] = self._check_expr(element.value, e, field.typ)

        for field in fields.values():
            if field.name not in field_value_asts:
                if unnamed_value:
                    continue
                self._report_and_fail(
                    diag.Diag.new(
                        diag_kinds.MISSING_STRUCT_FIELD,
                        brace_expr.span,
                        field=field.name,
                        struct_typ=struct_typ,
                    ).with_label(diag_kinds.DEFINED_HERE, field.ast.span, name=field.name)
                )
                continue
            value_ast = field_value_asts[field.name]
            value_typ = field_value_typs[field.name]
            coercion = self._record_coercion(value_ast, value_typ, field.typ)
            if isinstance(coercion, check_results.Invalid):
                self._report_and_fail(
                    diag.Diag.new(
                        diag_kinds.STRUCT_FIELD_TYPE_MISMATCH,
                        value_ast.span,
                        field=field.name,
                        struct_typ=struct_typ,
                        field_typ=field.typ,
                        given_typ=value_typ,
                    ).with_label(diag_kinds.DEFINED_HERE, field.ast.span, name=field.name)
                )

        return struct_typ

    def _check_array_lit_expr(
        self, brace_expr: ast.BraceExpr, arr_typ: typs.ArrayTyp, e: ir_env.Env
    ) -> typs.Typ:
        elements: list[ast.ExprKind] = []
        for element in brace_expr.elements:
            if isinstance(element, ast.StructFieldExpr):
                self._report_and_fail(
                    diag_kinds.NAMED_FIELD_IN_ARRAY_LITERAL, element.span, field=element.ident.name
                )
                elements.append(element.value)
            else:
                elements.append(element)

        length = arr_typ.length
        if length.report_proof() is None:
            if not isinstance(length, typs.ComptimeValueTyp):
                self._report_and_fail(diag_kinds.GENERIC_ARRAY_LITERAL_LENGTH, brace_expr.span)
            elif len(elements) != arr_typ.length_value:
                self._report_and_fail(
                    diag_kinds.ARRAY_ELEMENT_COUNT_MISMATCH,
                    brace_expr.span,
                    array_typ=arr_typ,
                    given=len(elements),
                    expected=arr_typ.length_value,
                )

        elt_typ = arr_typ.element_typ
        for i, elt_ast in enumerate(elements):
            elt_typ_i = self._check_expr(elt_ast, e, elt_typ)
            coercion = self._record_coercion(elt_ast, elt_typ_i, elt_typ)
            if isinstance(coercion, check_results.Invalid):
                self._report_and_fail(
                    diag_kinds.ARRAY_ELEMENT_TYPE_MISMATCH,
                    elt_ast.span,
                    index=i,
                    element_typ=elt_typ_i,
                    array_typ=arr_typ,
                )

        return arr_typ

    def _check_field_access_expr(self, fa_expr: ast.FieldAccessExpr, e: ir_env.Env) -> typs.Typ:
        struct_typ = self._check_expr(fa_expr.value, e, None)
        if not isinstance(struct_typ, typs.StructTyp):
            return self._report_and_poison(
                diag_kinds.NON_STRUCT_FIELD_ACCESS, fa_expr.value.span, typ=struct_typ
            )

        field_name = fa_expr.field.name
        field = struct_typ.fields.get(field_name)
        if field is None:
            return typs.PoisonTyp(
                self._report_unknown_field(struct_typ, field_name, fa_expr.field.span)
            )
        self.results._set_struct_field_index(fa_expr, field.index)
        if not field.is_accessible_from(fa_expr.span.file):
            self._report_private_field(struct_typ, field, fa_expr.field.span)
        return field.typ

    def _report_unknown_field(
        self, struct_typ: typs.StructTyp, name: str, span: src.SrcSpan
    ) -> diag.ReportProof:
        return self._report_and_fail(
            diag.Diag.new(
                diag_kinds.UNKNOWN_STRUCT_FIELD, span, struct_typ=struct_typ, field=name
            ).with_label(diag_kinds.DEFINED_HERE, struct_typ.span, name=struct_typ.name)
        )

    def _report_private_field(
        self, struct_typ: typs.StructTyp, field: typs.StructField, span: src.SrcSpan
    ) -> None:
        self._report_and_fail(
            diag.Diag.new(
                diag_kinds.PRIVATE_FIELD_ACCESS, span, field=field.name, struct_typ=struct_typ
            ).with_label(diag_kinds.DEFINED_HERE, field.ast.span, name=field.name)
        )

    def _check_deref_expr(self, d_expr: ast.DerefExpr, e: ir_env.Env) -> typs.Typ:
        ptr_typ = self._check_expr(d_expr.ptr, e, None)
        if not isinstance(ptr_typ, typs.PtrTyp) or isinstance(ptr_typ.pointee_typ, typs.FnTyp):
            return self._report_and_poison(
                diag_kinds.NON_POINTER_DEREFERENCE, d_expr.ptr.span, typ=ptr_typ
            )
        return ptr_typ.pointee_typ

    def _check_place(self, expr_ast: ast.ExprKind, e: ir_env.Env) -> typs.PtrTyp:
        typ = self._check_place_inner(expr_ast, e)
        self._fail_if_poison_reachable(typ)
        self.results._set_place_typ(expr_ast, typ)
        return typ

    def _check_place_inner(self, expr_ast: ast.ExprKind, e: ir_env.Env) -> typs.PtrTyp:
        """Return the pointer type produced by taking ``expr_ast``'s address.

        Real places retain their mutability; other values use a const temporary, as does
        poison.
        """
        if isinstance(expr_ast, ast.VarExpr):
            # Local to avoid the ir_module import cycle.
            from leech import ir_module  # noqa: PLC0415

            # Function applications produce their function-pointer type directly:
            # taking their address is a no-op rather than another indirection.
            var = self._resolve_var(expr_ast, e)
            if isinstance(var, typs.PoisonTyp):
                return typs.PtrTyp(var, typs.CONST)
            if isinstance(var, ir_module.FnCandidate):
                typ = self._fn_candidate_ptr_typ(expr_ast, var)
                if isinstance(typ, typs.PtrTyp):
                    return typ
                return typs.PtrTyp(typ, typs.CONST)
            if isinstance(var, ast.Param | ast.Receiver | ast.LetStmt | ast.BindingPattern):
                return self.results.local_typ(var)
            # Immediate values fall through to a const temporary.
            if not isinstance(
                var, ir_values.ComptimeEnum | typs.UnionVariantRef | typs.ValueParamTyp
            ):
                try:
                    return var.typ
                except diag.ReportedError as err:
                    return typs.PtrTyp(self._fail_and_poison(err.reported), typs.CONST)

        value_typ = self._check_expr(expr_ast, e, None)
        if value_typ.report_proof() is not None:
            return typs.PtrTyp(value_typ, typs.CONST)
        return typs.PtrTyp(value_typ, self._place_mut(expr_ast, e))

    def _place_mut(self, expr_ast: ast.ExprKind, e: ir_env.Env) -> typs.Mutability:
        """Return the mutability of ``expr_ast``'s place, whose type must not be poisoned."""
        match expr_ast:
            case ast.VarExpr():
                # Local to avoid the ir_module import cycle.
                from leech import ir_module  # noqa: PLC0415

                var = self._resolve_var(expr_ast, e)
                assert not isinstance(var, typs.PoisonTyp)
                # Function applications are const, while an enum
                # variant or a value parameter is a temporary rather than
                # a place.
                if isinstance(
                    var,
                    ir_module.FnCandidate
                    | ir_values.ComptimeEnum
                    | typs.UnionVariantRef
                    | typs.ValueParamTyp,
                ):
                    return typs.CONST
                if isinstance(var, ast.Param | ast.Receiver | ast.LetStmt | ast.BindingPattern):
                    return self.results.local_typ(var).mut
                # The only remaining binding is a ModVar.
                return asserts.checked_cast(var.typ, typs.PtrTyp).mut
            case ast.ArrayAccessExpr():
                return self._place_mut(expr_ast.array, e)
            case ast.FieldAccessExpr():
                value_typ = self._check_expr(expr_ast.value, e, None)
                field = (
                    value_typ.fields.get(expr_ast.field.name)
                    if isinstance(value_typ, typs.StructTyp)
                    else None
                )
                if field is not None and field.mut == typs.MUT:
                    return self._place_mut(expr_ast.value, e)
                return typs.CONST
            case ast.DerefExpr():
                # _check_place already ran _check_expr on this same DerefExpr
                # before calling here, which only succeeds (via
                # _check_deref_expr) if .ptr is a PtrTyp.
                ptr_typ = self._check_expr(expr_ast.ptr, e, None)
                return asserts.checked_cast(ptr_typ, typs.PtrTyp).mut
            case _:
                # A non-place expression's temporary is always const.
                return typs.CONST

    def _check_stmt(self, stmt_ast: ast.StmtKind, e: ir_env.Env) -> bool:
        """Check a statement and return whether control cannot fall through it."""
        match stmt_ast:
            case ast.ExprStmt():
                return self._check_expr(stmt_ast.expr, e, None) == typs.NEVER
            case ast.RetStmt():
                self._check_ret_stmt(stmt_ast, e)
                return True
            case ast.LetStmt():
                return self._check_let_stmt(stmt_ast, e)
            case ast.AssignmentStmt():
                return self._check_assignment_stmt(stmt_ast, e)
            case ast.BreakStmt():
                self._check_break_stmt(stmt_ast)
                return True
            case ast.ContinueStmt():
                self._check_continue_stmt(stmt_ast)
                return True

    def _check_ret_stmt(self, ret_ast: ast.RetStmt, e: ir_env.Env) -> None:
        if self._ret_typ is None:
            poison = self._report_and_poison(diag_kinds.RETURN_OUTSIDE_FUNCTION, ret_ast.span)
            if ret_ast.expr is not None:
                self._check_expr(ret_ast.expr, e, poison)
            return
        ret_typ = self._ret_typ

        if ret_ast.expr is not None:
            expr_typ = self._check_expr(ret_ast.expr, e, ret_typ)
            # never is exempt (recorded as NeverDiverge, never Invalid),
            # matching CfgBuilder._build_ret_stmt's own never-typed early
            # return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast.expr, expr_typ, ret_typ)
            if isinstance(coercion, check_results.Invalid):
                self._report_ret_typ_error(
                    diag_kinds.RETURN_TYPE_MISMATCH, ret_ast.expr.span, given_typ=expr_typ
                )
            return

        if ret_typ != typs.VOID:
            self._report_ret_typ_error(diag_kinds.MISSING_RETURN_VALUE, ret_ast.span)

    def _report_ret_typ_error(
        self, kind: diag.DiagKind, span: src.SrcSpan, /, **args: diag.DiagArgValue
    ) -> None:
        """Report an error about the function's return type, which is labelled if written."""
        d = diag.Diag.new(
            kind,
            span,
            fn=opt_util.opt_unwrap(self._fn_name),
            ret_typ=opt_util.opt_unwrap(self._ret_typ),
            **args,
        )
        self._report_and_fail(d.with_label(diag_kinds.RET_TYP_HERE, self._ret_typ_span))

    def _report_uninferable(
        self,
        span: Optional[src.SrcSpan],
        item_kind: str,
        item: str,
        param: typs.ComptimeParamTyp,
    ) -> diag.ReportProof:
        return self._report_and_fail(
            diag.Diag.new(
                diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT,
                span,
                param=param.name,
                item_kind=item_kind,
                item=item,
            ).with_note(diag_kinds.GIVE_COMPTIME_ARGS, item=item)
        )

    def _check_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        if not self._loop_labels:
            self._report_and_fail(diag_kinds.BREAK_OUTSIDE_LOOP, break_ast.span)
            return
        target = self._resolve_loop_label(break_ast.label)
        if target is not None:
            self.results.resolutions.set_loop_target(break_ast, target)

    def _check_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        if not self._loop_labels:
            self._report_and_fail(diag_kinds.CONTINUE_OUTSIDE_LOOP, continue_ast.span)
            return
        target = self._resolve_loop_label(continue_ast.label)
        if target is not None:
            self.results.resolutions.set_loop_target(continue_ast, target)

    def _resolve_loop_label(self, label: Optional[ast.Ident]) -> Optional[ast.WhileExpr]:
        """Return the while loop a ``break``/``continue`` label names, or the
        innermost enclosing one if ``label`` is ``None``, reporting an unknown label.
        """
        if label is None:
            return self._loop_labels[-1][1]
        for loop_label, while_ast in reversed(self._loop_labels):
            if loop_label == label.name:
                return while_ast
        self._report_and_fail(diag_kinds.UNKNOWN_LOOP_LABEL, label.span, label=label.name)
        return None

    def _check_let_stmt(self, let_ast: ast.LetStmt, e: ir_env.Env) -> bool:
        expr_typ, declared_typ = self._check_let_initializer(let_ast, e)
        bound_typ = opt_util.opt_or_default(declared_typ, expr_typ)
        mut = typs.Mutability.from_ast(let_ast.mut)
        place_typ = typs.PtrTyp(bound_typ, mut)
        self._set_local_typ(let_ast, place_typ)
        if reserved.is_reserved(let_ast.ident.name):
            self._report_and_fail(
                diag_kinds.RESERVED_NAME, let_ast.ident.span, name=let_ast.ident.name
            )
        e.add_var(let_ast.ident.name, let_ast, let_ast.ident.span)
        return expr_typ == typs.NEVER

    def _check_let_initializer(
        self, let_ast: ast.LetStmt, e: ir_env.Env
    ) -> tuple[typs.Typ, Optional[typs.Typ]]:
        """Return a ``let`` initializer's checked and optional declared types.

        A declared type that fails to resolve is poison, and so is a ``void`` initializer.
        """
        declared_typ_ast = let_ast.typ
        declared_typ = None
        if declared_typ_ast is not None:
            try:
                declared_typ = typs.Typ.from_ast(declared_typ_ast, e)
            except diag.ReportedError as err:
                declared_typ = self._fail_and_poison(err.reported)
        expr_typ = self._check_expr(let_ast.expr, e, declared_typ)
        if expr_typ == typs.VOID:
            expr_typ = self._report_and_poison(diag_kinds.VOID_INITIALIZER, let_ast.expr.span)
        if declared_typ_ast is None:
            return expr_typ, None

        assert declared_typ is not None
        self._fail_if_poison_reachable(declared_typ)
        self.results._set_let_declared_typ(let_ast, declared_typ)
        coercion = self._record_coercion(let_ast.expr, expr_typ, declared_typ)
        if isinstance(coercion, check_results.Invalid):
            self._report_and_fail(
                diag.Diag.new(
                    diag_kinds.LET_TYPE_MISMATCH,
                    let_ast.expr.span,
                    var=let_ast.ident.name,
                    declared_typ=declared_typ,
                    given_typ=expr_typ,
                ).with_label(diag_kinds.DECLARED_TYP_HERE, declared_typ_ast.span, typ=declared_typ)
            )
        return expr_typ, declared_typ

    def _check_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: ir_env.Env) -> bool:
        place_typ = self._check_expr(ass_ast.place, e, None)
        expr_typ = self._check_expr(ass_ast.expr, e, place_typ)

        if place_typ.report_proof() is None and self._place_mut(ass_ast.place, e) == typs.CONST:
            self._report_and_fail(diag_kinds.ASSIGNMENT_TO_IMMUTABLE_PLACE, ass_ast.place.span)

        coercion = self._record_coercion(ass_ast.expr, expr_typ, place_typ)
        if isinstance(coercion, check_results.Invalid):
            self._report_and_fail(
                diag.Diag.new(
                    diag_kinds.ASSIGNMENT_TYPE_MISMATCH,
                    ass_ast.expr.span,
                    given_typ=expr_typ,
                    place_typ=place_typ,
                ).with_label(diag_kinds.PLACE_TYP, ass_ast.place.span, typ=place_typ)
            )
        return place_typ == typs.NEVER or expr_typ == typs.NEVER

    def _infer_int_lit_typ(self, lit_ast: ast.IntLit, expected_typ: Optional[typs.Typ]) -> typs.Typ:
        """Use a literal's suffix, an expected integer type, or finally ``i32``.

        An unsuffixed literal expected to have a poisoned type has that type.
        """
        if lit_ast.explicit_width is not None:
            assert lit_ast.explicit_signage is not None
            return typs.IntTyp(lit_ast.explicit_width, lit_ast.explicit_signage)
        if isinstance(expected_typ, typs.IntTyp):
            return expected_typ
        if expected_typ is not None and expected_typ.report_proof() is not None:
            return expected_typ
        return typs.I32

    def _record_coercion(
        self, node: ast.Ast, value_typ: typs.Typ, target: typs.Typ
    ) -> Optional[check_results.Coercion]:
        """Decide and record how ``node`` converts from ``value_typ`` to ``target``.

        Poison converts to and from every type, recorded as needing no conversion, though
        the check fails, so it is never lowered.
        """
        if value_typ == target:
            coercion = None
        elif value_typ.report_proof() is not None or target.report_proof() is not None:
            self._fail_if_poison_reachable(value_typ)
            self._fail_if_poison_reachable(target)
            coercion = None
        elif value_typ == typs.NEVER:
            coercion = check_results.NeverDiverge(target)
        elif not value_typ.coerces_to(target):
            coercion = check_results.Invalid()
        elif isinstance(target, typs.IntTyp):
            coercion = check_results.IntExt(target)
        else:
            coercion = check_results.PtrMutRelax()
        self.results._set_coercion(node, coercion)
        return coercion
