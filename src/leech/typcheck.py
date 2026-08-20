# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Type checking before LLVM lowering.

Each body is checked completely, including unreachable code, and lowering decisions are
recorded by AST-node identity in :class:`TypCheckResults`.
"""

import dataclasses
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Optional

from leech import (
    asserts,
    ast,
    errors,
    ir_env,
    ir_values,
    opt_util,
    reserved,
    resolve,
    signage,
    src,
    typs,
)

if TYPE_CHECKING:
    # Runtime imports are local because ir_module imports this module.
    from leech import ir_module, ir_traits


def is_flexible_int_lit(expr_ast: ast.Expr) -> bool:
    """Return whether an unsuffixed integer literal can take its type from context."""
    if isinstance(expr_ast, ast.IntLit):
        return expr_ast.explicit_width is None
    if isinstance(expr_ast, ast.UnaryOpExpr) and expr_ast.op.name == "-":
        return is_flexible_int_lit(expr_ast.operand)
    if isinstance(expr_ast, ast.BlockExpr):
        return (
            not expr_ast.stmts and expr_ast.expr is not None and is_flexible_int_lit(expr_ast.expr)
        )
    return False


def resolve_peer_typ(fixed_typs: list[typs.Typ]) -> typs.Typ:
    """Choose the first non-diverging fixed peer type, defaulting to ``i32``."""
    for typ in fixed_typs:
        if typ != typs.NEVER:
            return typ
    return typs.I32


def _callable_typ(typ: typs.Typ) -> Optional[typs.CallableTyp]:
    """Return the callable behind a pointer type, if present."""
    if isinstance(typ, typs.PtrTyp) and isinstance(typ.pointee_typ, typs.CallableTyp):
        return typ.pointee_typ
    return None


def _struct_field(typ: typs.Typ, name: str) -> Optional[typs.StructField]:
    """Return the named struct field, if present."""
    if not isinstance(typ, typs.StructTyp):
        return None
    return typ.fields.get(name)


class Coercion:
    """A required conversion to the type an expression's context expects."""


@dataclasses.dataclass(frozen=True)
class NeverDiverge(Coercion):
    """Replace an unreachable ``never`` value with a placeholder of ``target``."""

    target: typs.Typ


class PtrMutRelax(Coercion):
    """A ``*mut T`` value given where a ``*T`` is wanted.

    The two share a representation, so nothing needs to be emitted.
    """


@dataclasses.dataclass(frozen=True)
class IntExt(Coercion):
    """Zero/sign-extend an integer value to a wider integer type.

    :param target: The type to extend to.
    """

    target: typs.IntTyp


class Invalid(Coercion):
    """The value's type doesn't coerce to the target at all."""


class TypCheckResults:
    """Lowering facts for one checked body, keyed by AST-node identity."""

    resolutions: Final[resolve.Resolutions]
    _int_lit_typs: Final[dict[ast.IntLit, typs.IntTyp]]
    _coercions: Final[dict[ast.Ast, Optional[Coercion]]]
    _generic_calls: Final[dict[ast.CallExpr, tuple[ir_module.FnTemplate, tuple[typs.Typ, ...]]]]
    _generic_var_refs: Final[dict[ast.VarExpr, tuple[ir_module.FnTemplate, tuple[typs.Typ, ...]]]]
    _struct_expr_typs: Final[dict[ast.StructExpr, typs.StructTyp]]
    _struct_field_indices: Final[dict[ast.FieldAccessExpr | ast.StructFieldExpr, int]]
    _let_declared_typs: Final[dict[ast.LetStmt, typs.Typ]]

    def __init__(self) -> None:
        self.resolutions = resolve.Resolutions()
        self._int_lit_typs = {}
        self._coercions = {}
        self._generic_calls = {}
        self._generic_var_refs = {}
        self._struct_expr_typs = {}
        self._struct_field_indices = {}
        self._let_declared_typs = {}

    def int_lit_typ(self, node: ast.IntLit) -> typs.IntTyp:
        """Return the type chosen and overflow-checked for an integer literal."""
        return self._int_lit_typs[node]

    def _set_int_lit_typ(self, node: ast.IntLit, typ: typs.IntTyp) -> None:
        self._int_lit_typs[node] = typ

    def coercion(self, node: ast.Ast) -> Optional[Coercion]:
        """Return ``node``'s coercion, or ``None`` when its type already matched."""
        return self._coercions[node]

    def _set_coercion(self, node: ast.Ast, coercion: Optional[Coercion]) -> None:
        self._coercions[node] = coercion

    def generic_call(
        self, node: ast.CallExpr
    ) -> Optional[tuple[ir_module.FnTemplate, tuple[typs.Typ, ...]]]:
        """Return a generic call's resolved function and type arguments, if any."""
        return self._generic_calls.get(node)

    def _set_generic_call(
        self, node: ast.CallExpr, fn: ir_module.FnTemplate, typ_args: tuple[typs.Typ, ...]
    ) -> None:
        self._generic_calls[node] = (fn, typ_args)

    def generic_var_ref(
        self, node: ast.VarExpr
    ) -> Optional[tuple[ir_module.FnTemplate, tuple[typs.Typ, ...]]]:
        """Return an applied generic function reference and its arguments, if any."""
        return self._generic_var_refs.get(node)

    def _set_generic_var_ref(
        self, node: ast.VarExpr, fn: ir_module.FnTemplate, typ_args: tuple[typs.Typ, ...]
    ) -> None:
        self._generic_var_refs[node] = (fn, typ_args)

    def struct_expr_typ(self, node: ast.StructExpr) -> typs.StructTyp:
        """Return ``node``'s resolved (possibly still abstract) type."""
        return self._struct_expr_typs[node]

    def _set_struct_expr_typ(self, node: ast.StructExpr, typ: typs.StructTyp) -> None:
        self._struct_expr_typs[node] = typ

    def struct_field_index(self, node: ast.FieldAccessExpr | ast.StructFieldExpr) -> int:
        """Return the declaration-order index resolved for a struct field expression."""
        return self._struct_field_indices[node]

    def _set_struct_field_index(
        self, node: ast.FieldAccessExpr | ast.StructFieldExpr, index: int
    ) -> None:
        self._struct_field_indices[node] = index

    def let_declared_typ(self, node: ast.LetStmt) -> Optional[typs.Typ]:
        """Return ``node``'s declared type, if any."""
        return self._let_declared_typs.get(node)

    def _set_let_declared_typ(self, node: ast.LetStmt, typ: typs.Typ) -> None:
        self._let_declared_typs[node] = typ


class TypCheck:
    """Type-check a function body or module-variable initializer."""

    results: Final[TypCheckResults]
    _ret_typ: Optional[typs.Typ]
    _ret_typ_span: Optional[src.SrcSpan]
    _fn_name: Optional[str]
    #: Enclosing while loops' labels and own AST nodes, innermost last.
    _loop_labels: Final[list[tuple[Optional[str], ast.WhileExpr]]]
    #: Local bindings' pointer types, keyed by their declaration-site AST node.
    _local_typs: Final[dict[resolve.LocalDecl, typs.PtrTyp]]

    def __init__(self) -> None:
        self.results = TypCheckResults()
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        self._loop_labels = []
        self._local_typs = {}

    def check_fn(
        self,
        fn_ast: ast.FnDefn,
        e: ir_env.Env,
        ret_typ: typs.Typ,
        params: tuple[ir_values.Param, ...],
    ) -> TypCheckResults:
        """Type-check a function body and return its lowering facts."""
        self._ret_typ = ret_typ
        # Missing-return diagnostics concern the whole function.
        self._ret_typ_span = fn_ast.span
        self._fn_name = fn_ast.name.name

        e = e.new_child()
        for param in params:
            assert param.ast is not None
            param_typ = typs.PtrTyp.get_or_create(param.typ, typs.CONST)
            self._local_typs[param.ast] = param_typ
            e.add_var(param.ast.name.name, param.ast)
        block_typ = self._check_expr(fn_ast.block, e, ret_typ)
        ret_ast = opt_util.opt_or_default(fn_ast.block.expr, fn_ast.block)

        ret_typ_ast_span = opt_util.opt_map(fn_ast.ret_typ, lambda t: t.span)
        if block_typ != typs.VOID:
            # never is exempt here too - it's recorded (as NeverDiverge)
            # but that's never Invalid, matching CfgBuilder's own
            # never-typed early return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast, block_typ, ret_typ)
            if isinstance(coercion, Invalid):
                raise errors.InvalidRetTypError(
                    self._fn_name,
                    ret_typ.name,
                    ret_typ_ast_span,
                    block_typ.name,
                    ret_ast.span,
                )
        elif ret_typ != typs.VOID:
            raise errors.MissingRetError(self._fn_name, fn_ast.span, ret_typ.name, ret_typ_ast_span)

        return self.results

    def check_var_initializer(self, defn_ast: ast.VarDefn, e: ir_env.Env) -> TypCheckResults:
        """Type-check a module-level initializer and return its lowering facts."""
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        e = e.new_child()
        self._check_let_initializer(defn_ast.let_stmt, e)
        return self.results

    def _check_expr(
        self, expr_ast: ast.Expr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        """Return an expression's checked type, using ``expected_typ`` as context."""
        match expr_ast:
            case ast.BlockExpr():
                return self._check_block_expr(expr_ast, e, expected_typ)
            case ast.IfExpr():
                return self._check_if_expr(expr_ast, e, expected_typ)
            case ast.WhileExpr():
                return self._check_while_expr(expr_ast, e)
            case ast.CallExpr():
                return self._check_call_expr(expr_ast, e)
            case ast.BinOpExpr():
                return self._check_bin_op_expr(expr_ast, e, expected_typ)
            case ast.UnaryOpExpr():
                return self._check_unary_op_expr(expr_ast, e, expected_typ)
            case ast.StrLit():
                return typs.CSTR
            case ast.IntLit():
                typ = self._infer_int_lit_typ(expr_ast, expected_typ)
                if not typ.fits(expr_ast.value):
                    raise errors.IntLitOverflowError(expr_ast.value, typ.name, expr_ast.span)
                self.results._set_int_lit_typ(expr_ast, typ)
                return typ
            case ast.BoolLit():
                return typs.BOOL
            case ast.VarExpr():
                return self._check_var_expr(expr_ast, e)
            case ast.ArrayExpr():
                return self._check_array_expr(expr_ast, e, expected_typ)
            case ast.ArrayAccessExpr():
                return self._check_array_access_expr(expr_ast, e)
            case ast.StructExpr():
                return self._check_struct_expr(expr_ast, e)
            case ast.FieldAccessExpr():
                return self._check_field_access_expr(expr_ast, e)
            case ast.DerefExpr():
                return self._check_deref_expr(expr_ast, e)
            case _:
                raise AssertionError(f"unhandled expression kind {expr_ast}")

    def _check_block_expr(
        self, block_ast: ast.BlockExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        e = e.new_child()
        diverged = False
        for stmt_ast in block_ast.stmts:
            if self._check_stmt(stmt_ast, e):
                diverged = True
        if block_ast.expr is None:
            return typs.NEVER if diverged else typs.VOID
        return self._check_expr(block_ast.expr, e, expected_typ)

    def _check_if_expr(
        self, if_ast: ast.IfExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        cond_typ = self._check_expr(if_ast.condition, e, None)
        cond_coercion = self._record_coercion(if_ast.condition, cond_typ, typs.BOOL)
        if isinstance(cond_coercion, Invalid):
            raise errors.IfCondNotBoolError(
                if_ast.condition.diag_str(),
                cond_typ.name,
                if_ast.condition.span,
            )

        if if_ast.els is None:
            then_typ = self._check_expr(if_ast.then, e, expected_typ)
            if then_typ != typs.NEVER and then_typ != typs.VOID:
                raise errors.IfTypNotVoidError(then_typ.name, if_ast.then.span)
            return typs.VOID

        els_ast = if_ast.els
        if (
            expected_typ is None
            and is_flexible_int_lit(if_ast.then)
            and not is_flexible_int_lit(els_ast)
        ):
            els_typ = self._check_expr(els_ast, e, expected_typ)
            then_hint = expected_typ
            if els_typ != typs.NEVER:
                then_hint = resolve_peer_typ([els_typ])
            then_typ = self._check_expr(if_ast.then, e, then_hint)
        else:
            then_typ = self._check_expr(if_ast.then, e, expected_typ)
            els_hint = expected_typ
            if expected_typ is None and then_typ != typs.NEVER and is_flexible_int_lit(els_ast):
                els_hint = resolve_peer_typ([then_typ])
            els_typ = self._check_expr(els_ast, e, els_hint)

        if then_typ != typs.NEVER and els_typ != typs.NEVER and then_typ != els_typ:
            raise errors.IfElsTypMismatchError(
                then_typ.name, if_ast.then.span, els_typ.name, els_ast.span
            )

        if then_typ != typs.NEVER:
            return then_typ
        if els_typ != typs.NEVER:
            return els_typ
        return typs.NEVER

    def _check_while_expr(self, while_ast: ast.WhileExpr, e: ir_env.Env) -> typs.Typ:
        cond_typ = self._check_expr(while_ast.condition, e, None)
        cond_coercion = self._record_coercion(while_ast.condition, cond_typ, typs.BOOL)
        if isinstance(cond_coercion, Invalid):
            raise errors.WhileCondNotBoolError(
                while_ast.condition.diag_str(),
                cond_typ.name,
                while_ast.condition.span,
            )

        if while_ast.label is not None and reserved.is_reserved(while_ast.label.name):
            raise errors.ReservedNameError(while_ast.label.name, while_ast.label.span)
        self._loop_labels.append((opt_util.opt_map(while_ast.label, lambda x: x.name), while_ast))
        try:
            block_typ = self._check_expr(while_ast.block, e, None)
        finally:
            self._loop_labels.pop()
        if block_typ not in (typs.NEVER, typs.VOID):
            raise errors.WhileTypNotVoidError(block_typ.name, while_ast.block.span)

        return typs.VOID

    def _check_call_expr(self, call_ast: ast.CallExpr, e: ir_env.Env) -> typs.Typ:
        fn_typ, recv_ast, recv_typ = self._resolve_callee(call_ast, e)
        callee_diag_str = call_ast.callee.diag_str()
        param_typs = fn_typ.param_typs

        num_args = len(call_ast.args) + (1 if recv_typ is not None else 0)
        num_params = len(param_typs)
        if num_args < num_params:
            raise errors.NotEnoughArgsError(callee_diag_str, call_ast.span, num_args, num_params)
        if num_args > num_params:
            raise errors.TooManyArgsError(
                callee_diag_str,
                call_ast.args[-1].span,
                num_args,
                num_params,
            )

        if recv_ast is not None and recv_typ is not None:
            recv_coercion = self._record_coercion(recv_ast, recv_typ, param_typs[0])
            if isinstance(recv_coercion, Invalid):
                raise errors.InvalidArgTypError(
                    callee_diag_str,
                    1,
                    recv_typ.name,
                    param_typs[0].name,
                    recv_ast.span,
                )

        offset = 1 if recv_typ is not None else 0
        for i, arg_ast in enumerate(call_ast.args, start=offset):
            arg_typ = self._check_expr(arg_ast, e, param_typs[i])
            arg_coercion = self._record_coercion(arg_ast, arg_typ, param_typs[i])
            if isinstance(arg_coercion, Invalid):
                raise errors.InvalidArgTypError(
                    callee_diag_str,
                    i + 1,
                    arg_typ.name,
                    param_typs[i].name,
                    arg_ast.span,
                )

        return fn_typ.ret_typ

    def _resolve_callee(
        self, call_ast: ast.CallExpr, e: ir_env.Env
    ) -> tuple[typs.CallableTyp, Optional[ast.Expr], Optional[typs.Typ]]:
        """Resolve a callee and its optional pointer-typed method receiver.

        ``x.name`` prefers a method and otherwise falls back to field access.
        """
        callee_ast = call_ast.callee
        if isinstance(callee_ast, ast.VarExpr):
            # Local to avoid the ir_module import cycle.
            from leech import ir_module  # noqa: PLC0415

            var = self._resolve_var(callee_ast, e)
            if isinstance(var, ir_module.GenericFn):
                return self._resolve_generic_call(var.fn, call_ast, e), None, None

        if not isinstance(callee_ast, ast.FieldAccessExpr):
            callee_typ = self._check_expr(callee_ast, e, None)
            fn_typ = _callable_typ(callee_typ)
            if fn_typ is None:
                raise errors.NotCallableError(
                    callee_ast.diag_str(), callee_typ.name, callee_ast.span
                )
            return fn_typ, None, None

        recv_typ = self._check_place(callee_ast.value, e)
        pointee_typ = recv_typ.pointee_typ

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
                return trait_method.fn_typ_for_self(pointee_typ), callee_ast.value, recv_typ
        else:
            method = e.impl_registry.lookup_member(
                pointee_typ, callee_ast.field.name, callee_ast.field.span
            )
            self.results.resolutions.set_callee(call_ast, method)
            if method is not None:
                if not method.is_accessible_from(callee_ast.span.file):
                    raise errors.PrivateItemAccessError(
                        "function",
                        callee_ast.field.name,
                        callee_ast.field.span,
                        method.span,
                    )
                assert method.ast is not None
                if method.ast.receiver is None:
                    raise errors.NotAMethodError(
                        callee_ast.field.name,
                        pointee_typ.name,
                        callee_ast.field.span,
                        method.span,
                    )
                return method.fn_typ, callee_ast.value, recv_typ

        field = _struct_field(pointee_typ, callee_ast.field.name)
        if field is not None:
            self.results._set_struct_field_index(callee_ast, field.index)
        callee_typ = opt_util.opt_or_default(opt_util.opt_map(field, lambda f: f.typ), typs.VOID)
        fn_typ = _callable_typ(callee_typ)
        if fn_typ is None:
            raise errors.NotCallableError(callee_ast.diag_str(), callee_typ.name, callee_ast.span)
        return fn_typ, None, None

    def _resolve_bound_method(
        self, typ_param: typs.TypParamTyp, name: str, span: Optional[src.SrcSpan], e: ir_env.Env
    ) -> Optional[ir_traits.TraitMethod]:
        """Resolve a type parameter's method against only its declared trait bounds."""
        # Local to avoid the ir_traits import cycle.
        from leech import ir_traits  # noqa: PLC0415

        matches: list[ir_traits.TraitMethod] = []
        for bound in typ_param.bounds:
            item = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
            if not isinstance(item, ir_traits.Trait):
                raise errors.BoundNotATraitError(bound.path.str(), bound.path.span)
            method = item.get_method(name)
            if method is not None:
                if bound.generic_args:
                    # The signature would still name the trait's own type
                    # parameters, not the bound's arguments; substituting
                    # them needs generic traits.
                    raise NotImplementedError(
                        "calling a method through a bound with generic arguments"
                        " isn't supported yet"
                    )
                matches.append(method)
        return ir_traits.disambiguate(matches, name, typ_param.name, span)

    def _resolve_generic_call(
        self, fn: ir_module.FnTemplate, call_ast: ast.CallExpr, e: ir_env.Env
    ) -> typs.FnTyp:
        """Resolve and record a generic call's concrete function type."""
        callee_ast = asserts.checked_cast(call_ast.callee, ast.VarExpr)
        typ_params = self._typ_params_of(fn)

        if callee_ast.generic_args:
            mapping = self._resolve_explicit_typ_args(
                fn, typ_params, callee_ast.generic_args, callee_ast.span, e
            )
        else:
            mapping = self._infer_typ_args(fn, call_ast, e, typ_params)

        typ_args = tuple(mapping[typ_param] for typ_param in typ_params)
        typs.check_typ_arg_bounds(typ_params, typ_args, e, call_ast.span)
        self.results._set_generic_call(call_ast, fn, typ_args)
        return asserts.checked_cast(fn.fn_typ.substitute_typ_params(mapping), typs.FnTyp)

    def _typ_params_of(self, fn: ir_module.FnTemplate) -> list[typs.TypParamTyp]:
        """Return ``fn``'s interned type parameters in declaration order."""
        return list(fn.typ_params)

    def _resolve_explicit_typ_args(
        self,
        fn: ir_module.FnTemplate,
        typ_params: list[typs.TypParamTyp],
        generic_args: Sequence[ast.Typ],
        span: Optional[src.SrcSpan],
        e: ir_env.Env,
    ) -> dict[typs.TypParamTyp, typs.Typ]:
        """Resolve explicit type arguments against parameters positionally."""
        if len(generic_args) != len(typ_params):
            raise errors.WrongNumberOfTypArgsError(
                fn.name, len(generic_args), len(typ_params), span
            )
        return {
            typ_param: typs.Typ.from_ast(arg_ast, e)
            for typ_param, arg_ast in zip(typ_params, generic_args, strict=True)
        }

    def _infer_typ_args(
        self,
        fn: ir_module.FnTemplate,
        call_ast: ast.CallExpr,
        e: ir_env.Env,
        typ_params: list[typs.TypParamTyp],
    ) -> dict[typs.TypParamTyp, typs.Typ]:
        """Infer type arguments structurally from left to right.

        Unsuffixed integer literals are skipped because they need an expected type.
        """
        bindings: dict[typs.TypParamTyp, typs.Typ] = {}
        for declared_typ, arg_ast in zip(fn.fn_typ.param_typs, call_ast.args, strict=False):
            if is_flexible_int_lit(arg_ast):
                continue
            arg_typ = self._check_expr(arg_ast, e, None)
            declared_typ.infer_typ_args(arg_typ, bindings)

        for typ_param in typ_params:
            if typ_param not in bindings:
                raise errors.CannotInferTypArgError(fn.name, typ_param.name, call_ast.span)

        return bindings

    def _check_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        if op_ast.op.name in ("and", "or"):
            return self._check_logic_bin_op_expr(op_ast, e)

        # Unreachable operands are still checked; never stands in for either value type.
        if is_flexible_int_lit(op_ast.lhs) and not is_flexible_int_lit(op_ast.rhs):
            rhs_typ = self._check_expr(op_ast.rhs, e, None)
            lhs_typ = self._check_expr(op_ast.lhs, e, resolve_peer_typ([rhs_typ]))
        else:
            lhs_hint = None
            if is_flexible_int_lit(op_ast.lhs):
                lhs_hint = expected_typ
            lhs_typ = self._check_expr(op_ast.lhs, e, lhs_hint)

            rhs_hint = None
            if is_flexible_int_lit(op_ast.rhs):
                rhs_hint = resolve_peer_typ([lhs_typ])
            rhs_typ = self._check_expr(op_ast.rhs, e, rhs_hint)

        if lhs_typ != typs.NEVER and not isinstance(lhs_typ, typs.IntTyp):
            raise errors.InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs_typ.name,
                "an integer type",
                op_ast.lhs.span,
            )
        if lhs_typ != typs.NEVER and rhs_typ != typs.NEVER and lhs_typ != rhs_typ:
            raise errors.IncompatibleBinOpArgTypsError(
                op_ast.op.name,
                op_ast.op.span,
                lhs_typ.name,
                op_ast.lhs.span,
                rhs_typ.name,
                op_ast.rhs.span,
            )

        if op_ast.op.name in ("<", "<=", "==", "!=", ">=", ">"):
            return typs.BOOL
        return lhs_typ if lhs_typ != typs.NEVER else rhs_typ

    def _check_logic_bin_op_expr(self, op_ast: ast.BinOpExpr, e: ir_env.Env) -> typs.Typ:
        # Both operands are checked; never coerces to bool without a special case.
        lhs_typ = self._check_expr(op_ast.lhs, e, None)
        lhs_coercion = self._record_coercion(op_ast.lhs, lhs_typ, typs.BOOL)
        if isinstance(lhs_coercion, Invalid):
            raise errors.InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs_typ.name,
                "bool",
                op_ast.lhs.span,
            )

        rhs_typ = self._check_expr(op_ast.rhs, e, None)
        rhs_coercion = self._record_coercion(op_ast.rhs, rhs_typ, typs.BOOL)
        if isinstance(rhs_coercion, Invalid):
            raise errors.InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "right",
                rhs_typ.name,
                "bool",
                op_ast.rhs.span,
            )
        return typs.BOOL

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
            case _:
                raise AssertionError(f"unhandled unary operator {op_ast.op.name!r}")

    def _check_addr_of_expr(self, op_ast: ast.UnaryOpExpr, e: ir_env.Env) -> typs.Typ:
        return self._check_place(op_ast.operand, e)

    def _check_not_expr(self, op_ast: ast.UnaryOpExpr, e: ir_env.Env) -> typs.Typ:
        operand_typ = self._check_expr(op_ast.operand, e, None)
        coercion = self._record_coercion(op_ast.operand, operand_typ, typs.BOOL)
        if isinstance(coercion, Invalid):
            raise errors.InvalidUnaryOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                operand_typ.name,
                "bool",
                op_ast.operand.span,
            )
        return typs.BOOL

    def _check_neg_expr(
        self, op_ast: ast.UnaryOpExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        if isinstance(op_ast.operand, ast.IntLit):
            typ = self._infer_int_lit_typ(op_ast.operand, expected_typ)
            if typ.signage != signage.SIGNED:
                raise errors.InvalidUnaryOpArgTypError(
                    op_ast.op.name,
                    op_ast.op.span,
                    typ.name,
                    "a signed integer type",
                    op_ast.operand.span,
                )
            value = -op_ast.operand.value
            if not typ.fits(value):
                raise errors.IntLitOverflowError(value, typ.name, op_ast.span)
            self.results._set_int_lit_typ(op_ast.operand, typ)
            return typ

        operand_typ = self._check_expr(op_ast.operand, e, expected_typ)
        # never is exempt: it stands in for a value of any type.
        if operand_typ != typs.NEVER and (
            not isinstance(operand_typ, typs.IntTyp) or operand_typ.signage != signage.SIGNED
        ):
            raise errors.InvalidUnaryOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                operand_typ.name,
                "a signed integer type",
                op_ast.operand.span,
            )
        return operand_typ

    def _resolve_var(self, var_ast: ast.VarExpr, e: ir_env.Env) -> resolve.VarTarget:
        """Resolve ``var_ast``'s path, memoizing the result across repeat visits."""
        try:
            return self.results.resolutions.var(var_ast)
        except KeyError:
            target = e.resolve_path(ir_env.Env.Namespace.VARS, var_ast.path)
            self.results.resolutions.set_var(var_ast, target)
            return target

    def _check_var_expr(self, var_ast: ast.VarExpr, e: ir_env.Env) -> typs.Typ:
        # Local because runtime isinstance checks cannot use the TYPE_CHECKING import.
        from leech import ir_module  # noqa: PLC0415

        var = self._resolve_var(var_ast, e)
        if isinstance(var, ir_module.GenericFn):
            return self._generic_var_ref_typ(var_ast, var, e)
        if var_ast.generic_args:
            raise errors.TypArgsOnNonGenericItemError(var_ast.path.str(), var_ast.span)
        if isinstance(var, (ir_module.FnSpec, ir_values.ComptimeEnum)):
            # Neither has a place to unwrap: a function reference's own
            # type already is its pointer type, and an enum variant has
            # no address at all - see `Env._resolve_path_segment`'s
            # `EnumTyp` case.
            return var.typ
        if isinstance(var, (ast.Param, ast.Receiver, ast.LetStmt)):
            return self._local_typs[var].pointee_typ
        return var.typ.pointee_typ

    def _generic_var_ref_typ(
        self, var_ast: ast.VarExpr, var: ir_module.GenericFn, e: ir_env.Env
    ) -> typs.PtrTyp:
        """Return an explicitly applied generic function reference's pointer type."""
        if not var_ast.generic_args:
            raise errors.MissingTypArgsError(var.name, var_ast.span)
        # An uncalled reference has no arguments from which to infer types.
        typ_params = self._typ_params_of(var.fn)
        mapping = self._resolve_explicit_typ_args(
            var.fn, typ_params, var_ast.generic_args, var_ast.span, e
        )
        typ_args = tuple(mapping[typ_param] for typ_param in typ_params)
        self.results._set_generic_var_ref(var_ast, var.fn, typ_args)
        substituted = asserts.checked_cast(var.fn.fn_typ.substitute_typ_params(mapping), typs.FnTyp)
        return typs.PtrTyp.get_or_create(substituted, typs.CONST)

    def _check_array_expr(
        self, arr_expr: ast.ArrayExpr, e: ir_env.Env, expected_typ: Optional[typs.Typ]
    ) -> typs.Typ:
        expected_elt_typ = (
            expected_typ.element_typ if isinstance(expected_typ, typs.ArrayTyp) else None
        )
        if not arr_expr.elements and expected_elt_typ is None:
            raise errors.EmptyArrayTypUnknownError(arr_expr.span)

        built: list[Optional[typs.Typ]] = [None] * len(arr_expr.elements)
        for i, elt_ast in enumerate(arr_expr.elements):
            if not is_flexible_int_lit(elt_ast):
                built[i] = self._check_expr(elt_ast, e, expected_elt_typ)

        if expected_elt_typ is not None:
            elt_typ = expected_elt_typ
        else:
            elt_typ = resolve_peer_typ([t for t in built if t is not None])

        for i, t in enumerate(built):
            if t is None:
                built[i] = self._check_expr(arr_expr.elements[i], e, elt_typ)

        if elt_typ == typs.VOID:
            first_span = arr_expr.span
            if arr_expr.elements:
                first_span = arr_expr.elements[0].span
            raise errors.VoidArrayElementError(first_span)

        arr_typ = typs.ArrayTyp.get_or_create(elt_typ, len(arr_expr.elements))
        for i, elt_ast in enumerate(arr_expr.elements):
            elt_typ_i = opt_util.opt_unwrap(built[i])
            # Elements only coerce towards an element type the context
            # supplied, which is an unambiguous target. When the elements
            # settled on one between themselves it isn't: coercing to it
            # would let their order decide whether a narrower element is
            # accepted, so they have to agree exactly instead. A
            # diverging element is exempt - it stands in for a value of
            # any type.
            if expected_elt_typ is None and elt_typ_i not in (elt_typ, typs.NEVER):
                raise errors.IncompatibleTypInArrayExprError(
                    elt_typ_i.name, i, elt_ast.span, arr_typ.name
                )
            coercion = self._record_coercion(elt_ast, elt_typ_i, elt_typ)
            if isinstance(coercion, Invalid):
                raise errors.IncompatibleTypInArrayExprError(
                    elt_typ_i.name, i, elt_ast.span, arr_typ.name
                )

        return arr_typ

    def _check_array_access_expr(self, aa_expr: ast.ArrayAccessExpr, e: ir_env.Env) -> typs.Typ:
        arr_typ = self._check_expr(aa_expr.array, e, None)
        if not isinstance(arr_typ, typs.ArrayTyp):
            raise errors.IndexIntoInvalidTypError(arr_typ.name, aa_expr.array.span)

        index_typ = self._check_expr(aa_expr.index, e, typs.USIZE)
        index_coercion = self._record_coercion(aa_expr.index, index_typ, typs.USIZE)
        if isinstance(index_coercion, Invalid):
            raise errors.InvalidIndexTypError(index_typ.name, aa_expr.index.span)

        return arr_typ.element_typ

    def _check_struct_expr(self, struct_expr: ast.StructExpr, e: ir_env.Env) -> typs.Typ:
        struct_typ = typs.Typ.from_ast(struct_expr.typ, e)
        if not isinstance(struct_typ, typs.StructTyp):
            raise errors.TypeOfStructExprNotStructError(struct_typ.name, struct_expr.typ.span)
        self.results._set_struct_expr_typ(struct_expr, struct_typ)

        field_value_asts: dict[str, ast.Expr] = {}
        field_value_typs: dict[str, typs.Typ] = {}
        for field_expr in struct_expr.fields:
            name = field_expr.ident.name
            field = struct_typ.fields.get(name)
            if field is None:
                raise errors.InvalidStructFieldError(
                    name, field_expr.ident.span, struct_typ.name, struct_typ.span
                )
            self.results._set_struct_field_index(field_expr, field.index)
            if not field.is_accessible_from(struct_expr.span.file):
                raise errors.PrivateStructFieldAccessError(
                    field.name, field_expr.ident.span, struct_typ.name, field.ast.span
                )
            if name in field_value_asts:
                raise errors.DuplicateFieldInStructExprError(
                    name, field_expr.ident.span, field_value_asts[name].span
                )
            field_value_asts[name] = field_expr.value
            field_value_typs[name] = self._check_expr(field_expr.value, e, field.typ)

        for field in struct_typ.fields.values():
            if field.name not in field_value_asts:
                raise errors.MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name, struct_expr.span
                )
            value_ast = field_value_asts[field.name]
            value_typ = field_value_typs[field.name]
            coercion = self._record_coercion(value_ast, value_typ, field.typ)
            if isinstance(coercion, Invalid):
                raise errors.IncompatibleStructFieldTypError(
                    field.name,
                    struct_typ.name,
                    value_typ.name,
                    value_ast.span,
                    field.typ.name,
                    field.ast.span,
                )

        return struct_typ

    def _check_field_access_expr(self, fa_expr: ast.FieldAccessExpr, e: ir_env.Env) -> typs.Typ:
        struct_typ = self._check_expr(fa_expr.value, e, None)
        if not isinstance(struct_typ, typs.StructTyp):
            raise errors.FieldAccessIntoInvalidTypError(struct_typ.name, fa_expr.value.span)

        field_name = fa_expr.field.name
        field = struct_typ.fields.get(field_name)
        if field is None:
            raise errors.InvalidStructFieldError(
                field_name, fa_expr.field.span, struct_typ.name, struct_typ.span
            )
        self.results._set_struct_field_index(fa_expr, field.index)
        if not field.is_accessible_from(fa_expr.span.file):
            raise errors.PrivateStructFieldAccessError(
                field_name, fa_expr.field.span, struct_typ.name, field.ast.span
            )
        return field.typ

    def _check_deref_expr(self, d_expr: ast.DerefExpr, e: ir_env.Env) -> typs.Typ:
        ptr_typ = self._check_expr(d_expr.ptr, e, None)
        if not isinstance(ptr_typ, typs.PtrTyp) or isinstance(ptr_typ.pointee_typ, typs.FnTyp):
            raise errors.DerefInvalidTypError(ptr_typ.name, d_expr.ptr.span)
        return ptr_typ.pointee_typ

    def _check_place(self, expr_ast: ast.Expr, e: ir_env.Env) -> typs.PtrTyp:
        """Return the pointer type produced by taking ``expr_ast``'s address.

        Real places retain their mutability; other values use a const temporary.
        """
        if isinstance(expr_ast, ast.VarExpr):
            # Local to avoid the ir_module import cycle.
            from leech import ir_module  # noqa: PLC0415

            # A variable's own type already *is* its place's pointer type -
            # a function reference doubly so, since taking its address is
            # a no-op rather than an extra indirection (see
            # CfgBuilder._build_var_expr's PLACE case) - true of an
            # explicitly-applied generic one too, so delegate to
            # _generic_var_ref_typ for that case (including its
            # MissingTypArgsError if it's named bare instead).
            var = self._resolve_var(expr_ast, e)
            if isinstance(var, ir_module.GenericFn):
                return self._generic_var_ref_typ(expr_ast, var, e)
            if isinstance(var, (ast.Param, ast.Receiver, ast.LetStmt)):
                return self._local_typs[var]
            # An enum variant (see Env._resolve_path_segment's EnumTyp
            # case) isn't a place either - it falls through to the
            # general, value-copying case below, same as any other
            # non-place expression.
            if not isinstance(var, ir_values.ComptimeEnum):
                return var.typ

        value_typ = self._check_expr(expr_ast, e, None)
        return typs.PtrTyp.get_or_create(value_typ, self._place_mut(expr_ast, e))

    def _place_mut(self, expr_ast: ast.Expr, e: ir_env.Env) -> typs.Mutability:
        """Return the mutability of ``expr_ast``'s place."""
        match expr_ast:
            case ast.VarExpr():
                # Local to avoid the ir_module import cycle.
                from leech import ir_module  # noqa: PLC0415

                var = self._resolve_var(expr_ast, e)
                # An (explicitly-applied, since this is only reached once
                # an earlier _check_expr/_check_place pass over the same
                # node has already succeeded) GenericFn has no .typ of
                # its own to check - but like any other function
                # reference, its place is const regardless. An enum
                # variant isn't a place at all (see _check_place), so it
                # falls into the same "temporary, always const" case.
                if isinstance(var, (ir_module.GenericFn, ir_values.ComptimeEnum)):
                    return typs.CONST
                if isinstance(var, (ast.Param, ast.Receiver, ast.LetStmt)):
                    return self._local_typs[var].mut
                # Every remaining binding (a ModVar or function reference)
                # has a PtrTyp .typ - see _check_place.
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
                return typs.CONST

    def _check_stmt(self, stmt_ast: ast.Stmt, e: ir_env.Env) -> bool:
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
            case _:
                raise AssertionError(f"unhandled statement kind {stmt_ast}")

    def _check_ret_stmt(self, ret_ast: ast.RetStmt, e: ir_env.Env) -> None:
        if self._ret_typ is None:
            raise errors.RetNotInFnError(ret_ast.span)
        ret_typ = self._ret_typ
        fn_name = opt_util.opt_unwrap(self._fn_name)

        if ret_ast.expr is not None:
            expr_typ = self._check_expr(ret_ast.expr, e, ret_typ)
            # never is exempt (recorded as NeverDiverge, never Invalid),
            # matching CfgBuilder._build_ret_stmt's own never-typed early
            # return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast.expr, expr_typ, ret_typ)
            if isinstance(coercion, Invalid):
                raise errors.InvalidRetTypError(
                    fn_name,
                    ret_typ.name,
                    self._ret_typ_span,
                    expr_typ.name,
                    ret_ast.expr.span,
                )
            return

        if ret_typ != typs.VOID:
            raise errors.InvalidVoidRetError(
                fn_name, ret_typ.name, self._ret_typ_span, ret_ast.span
            )

    def _check_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        if not self._loop_labels:
            raise errors.BreakNotInLoopError(break_ast.span)
        target = self._resolve_loop_label(break_ast.label)
        self.results.resolutions.set_loop_target(break_ast, target)

    def _check_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        if not self._loop_labels:
            raise errors.ContinueNotInLoopError(continue_ast.span)
        target = self._resolve_loop_label(continue_ast.label)
        self.results.resolutions.set_loop_target(continue_ast, target)

    def _resolve_loop_label(self, label: Optional[ast.Ident]) -> ast.WhileExpr:
        """Return the while loop a ``break``/``continue`` label names, or the
        innermost enclosing one if ``label`` is ``None``.
        """
        if label is None:
            return self._loop_labels[-1][1]
        for loop_label, while_ast in reversed(self._loop_labels):
            if loop_label == label.name:
                return while_ast
        raise errors.LoopLabelNotFoundError(label.name, label.span)

    def _check_let_stmt(self, let_ast: ast.LetStmt, e: ir_env.Env) -> bool:
        expr_typ, declared_typ = self._check_let_initializer(let_ast, e)
        bound_typ = opt_util.opt_or_default(declared_typ, expr_typ)
        mut = typs.Mutability.from_ast(let_ast.mut)
        place_typ = typs.PtrTyp.get_or_create(bound_typ, mut)
        self._local_typs[let_ast] = place_typ
        if reserved.is_reserved(let_ast.ident.name):
            raise errors.ReservedNameError(let_ast.ident.name, let_ast.ident.span)
        e.add_var(let_ast.ident.name, let_ast)
        return expr_typ == typs.NEVER

    def _check_let_initializer(
        self, let_ast: ast.LetStmt, e: ir_env.Env
    ) -> tuple[typs.Typ, Optional[typs.Typ]]:
        """Return a ``let`` initializer's checked and optional declared types."""
        declared_typ_ast = let_ast.typ
        declared_typ = opt_util.opt_map(declared_typ_ast, lambda t: typs.Typ.from_ast(t, e))
        expr_typ = self._check_expr(let_ast.expr, e, declared_typ)
        if expr_typ == typs.VOID:
            raise errors.VoidVarInitializerError(let_ast.expr.span)
        if declared_typ_ast is None:
            return expr_typ, None

        assert declared_typ is not None
        self.results._set_let_declared_typ(let_ast, declared_typ)
        coercion = self._record_coercion(let_ast.expr, expr_typ, declared_typ)
        if isinstance(coercion, Invalid):
            raise errors.IncompatibleLetTypError(
                let_ast.ident.name,
                declared_typ.name,
                expr_typ.name,
                let_ast.expr.span,
                declared_typ_ast.span,
            )
        return expr_typ, declared_typ

    def _check_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: ir_env.Env) -> bool:
        place_typ = self._check_expr(ass_ast.place, e, None)
        place_mut = self._place_mut(ass_ast.place, e)
        expr_typ = self._check_expr(ass_ast.expr, e, place_typ)

        if place_mut == typs.CONST:
            raise errors.AssignToConstError(ass_ast.place.span)

        coercion = self._record_coercion(ass_ast.expr, expr_typ, place_typ)
        if isinstance(coercion, Invalid):
            raise errors.IncompatibleAssignmentTypError(
                expr_typ.name, place_typ.name, ass_ast.expr.span, ass_ast.place.span
            )
        return place_typ == typs.NEVER or expr_typ == typs.NEVER

    def _infer_int_lit_typ(
        self, lit_ast: ast.IntLit, expected_typ: Optional[typs.Typ]
    ) -> typs.IntTyp:
        """Use a literal's suffix, an expected integer type, or finally ``i32``."""
        if lit_ast.explicit_width is not None:
            assert lit_ast.explicit_signage is not None
            return typs.IntTyp.get_or_create(lit_ast.explicit_width, lit_ast.explicit_signage)
        if isinstance(expected_typ, typs.IntTyp):
            return expected_typ
        return typs.I32

    def _record_coercion(
        self, node: ast.Ast, value_typ: typs.Typ, target: typs.Typ
    ) -> Optional[Coercion]:
        """Decide and record how ``node`` converts from ``value_typ`` to ``target``."""
        if value_typ == target:
            coercion = None
        elif value_typ == typs.NEVER:
            coercion = NeverDiverge(target)
        elif not value_typ.coerces_to(target):
            coercion = Invalid()
        elif isinstance(target, typs.IntTyp):
            coercion = IntExt(target)
        else:
            coercion = PtrMutRelax()
        self.results._set_coercion(node, coercion)
        return coercion
