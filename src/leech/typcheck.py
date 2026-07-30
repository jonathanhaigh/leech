"""Type checking of Leech code, kept separate from lowering to LLVM IR.

:class:`TypCheck` mirrors :class:`~leech.ir_builder.CfgBuilder`'s former
``build_*`` method structure one-for-one: each ``check_*`` method here
performs the checks its same-named ``build_*`` counterpart used to
perform inline, recording anything ``CfgBuilder`` still needs (an integer
literal's type, a coercion decision, a resolved call target, and so on)
into a :class:`TypCheckResults` side table instead of a lowered value.
``CfgBuilder`` reads from that table rather than re-deriving the same
facts, and no longer validates anything itself - see its module
docstring.

A function body or module-variable initializer is checked as a whole,
in one pass, before any of it is lowered (forced by
:attr:`~leech.ir_module.Fn.cfg`/:attr:`~leech.ir_module.ModVar.cfg` via
:attr:`~leech.ir_module.Fn.typ_check_results`/
:attr:`~leech.ir_module.ModVar.typ_check_results`). One consequence:
unreachable code is still fully type-checked, unlike
:class:`~leech.ir_builder.CfgBuilder`'s own control-flow handling, which
short-circuits lowering (and so instruction emission) for a
``never``-typed branch - see :func:`resolve_peer_typ` and the ``never``
handling throughout the ``check_*`` methods below.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from leech import ast
from leech.asserts import checked_cast
from leech.errors import (
    AssignToConstError,
    BreakNotInLoopError,
    CannotInferTypArgError,
    ContinueNotInLoopError,
    DerefInvalidTypError,
    DuplicateFieldInStructExprError,
    EmptyArrayTypUnknownError,
    FieldAccessIntoInvalidTypError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleAssignmentTypError,
    IncompatibleBinOpArgTypsError,
    IncompatibleLetTypError,
    IncompatibleStructFieldTypError,
    IncompatibleTypInArrayExprError,
    IndexIntoInvalidTypError,
    IntLitOverflowError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
    InvalidRetTypError,
    InvalidStructFieldError,
    InvalidUnaryOpArgTypError,
    InvalidVoidRetError,
    LoopLabelNotFoundError,
    MissingFieldInStructExprError,
    MissingRetError,
    MissingTypArgsError,
    NotAMethodError,
    NotCallableError,
    NotEnoughArgsError,
    PrivateItemAccessError,
    PrivateStructFieldAccessError,
    RetNotInFnError,
    TooManyArgsError,
    TypArgsOnNonGenericItemError,
    TypeOfStructExprNotStructError,
    VoidArrayElementError,
    VoidVarInitializerError,
    WhileCondNotBoolError,
    WhileTypNotVoidError,
    WrongNumberOfTypArgsError,
)
from leech.ir_env import Env
from leech.ir_values import Param, Value
from leech.opt_util import opt_map, opt_or_default, opt_unwrap
from leech.src import SrcSpan
from leech.typs import (
    BOOL,
    CONST,
    CSTR,
    I32,
    MUT,
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
    TypParamTyp,
)

if TYPE_CHECKING:
    # Annotation-only: `ir_module` imports this module at runtime, so an
    # unconditional import here would be circular. Everywhere this module
    # needs the actual class objects (for `isinstance`), it imports
    # `ir_module` locally instead - see e.g. `check_var_expr`.
    from leech import ir_module


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
        return expr_ast.explicit_width is None
    if isinstance(expr_ast, ast.UnaryOpExpr) and expr_ast.op.name == "-":
        return is_flexible_int_lit(expr_ast.operand)
    if isinstance(expr_ast, ast.BlockExpr):
        return (
            not expr_ast.stmts and expr_ast.expr is not None and is_flexible_int_lit(expr_ast.expr)
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


def _callable_typ(typ: Typ) -> CallableTyp | None:
    """``typ``, unwrapped to the type it's callable as, if it's one.

    :param typ: The type to check.
    :return: ``typ.pointee_typ`` if ``typ`` is a pointer to a callable
        type, otherwise ``None``.
    """
    if isinstance(typ, PtrTyp) and isinstance(typ.pointee_typ, CallableTyp):
        return typ.pointee_typ
    return None


def _struct_field_typ(typ: Typ, name: str) -> Typ | None:
    """The type of ``typ``'s field called ``name``, if it has one.

    :param typ: The type to look ``name`` up on.
    :param name: The field name to look up.
    :return: The field's type, or ``None`` if ``typ`` isn't a struct type
        or has no field called ``name``.
    """
    if not isinstance(typ, StructTyp):
        return None
    return opt_map(typ.fields.get(name), lambda f: f.typ)


class Coercion:
    """How (if at all) a checked expression coerces to the type its context
    expects.

    See :meth:`TypCheck._record_coercion` and
    :meth:`~leech.ir_builder.CfgBuilder._coerce`. ``None`` (rather than a
    :class:`Coercion`) means the expression's type already matched, so
    there's nothing to convert.
    """


@dataclass(frozen=True)
class NeverDiverge(Coercion):
    """The coerced value is ``never``-typed - already unreachable.

    :param target: The type of the placeholder value to produce in its
        place, matching what a real value of the target type would have
        been.
    """

    target: Typ


class PtrMutRelax(Coercion):
    """A ``*mut T`` value given where a ``*T`` is wanted.

    The two share a representation, so nothing needs to be emitted.
    """


@dataclass(frozen=True)
class IntExt(Coercion):
    """Zero/sign-extend an integer value to a wider integer type.

    :param target: The type to extend to.
    """

    target: IntTyp


class Invalid(Coercion):
    """The value's type doesn't coerce to the target at all."""


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
    equality). Only int-literal types and coercion decisions are recorded
    so far; more will be added as further checking logic migrates out of
    :class:`~leech.ir_builder.CfgBuilder`.
    """

    _int_lit_typs: Final[dict[ast.IntLit, IntTyp]]
    _coercions: Final[dict[ast.Ast, Coercion | None]]

    def __init__(self) -> None:
        self._int_lit_typs = {}
        self._coercions = {}

    def int_lit_typ(self, node: ast.IntLit) -> IntTyp:
        """The type chosen (and overflow-checked) for an integer literal.

        :param node: The literal's AST node, as checked by :class:`TypCheck`.
        :return: The literal's type.
        """
        return self._int_lit_typs[node]

    def _set_int_lit_typ(self, node: ast.IntLit, typ: IntTyp) -> None:
        self._int_lit_typs[node] = typ

    def coercion(self, node: ast.Ast) -> Coercion | None:
        """The coercion decision recorded for ``node``.

        :param node: The AST node the coerced expression was checked
            against, as recorded by :meth:`TypCheck._record_coercion`.
        :return: The coercion to apply, or ``None`` if ``node``'s checked
            type already matched what its context expected.
        """
        return self._coercions[node]

    def _set_coercion(self, node: ast.Ast, coercion: Coercion | None) -> None:
        self._coercions[node] = coercion


class TypCheck:
    """Type-checks a function body or module-variable initializer.

    Walks the same shape of tree :class:`~leech.ir_builder.CfgBuilder`
    lowers, checking it fully and recording into :attr:`results` whatever
    ``CfgBuilder`` needs back to lower it without re-deriving anything.
    """

    results: Final[TypCheckResults]
    _ret_typ: Typ | None
    _ret_typ_span: SrcSpan | None
    _fn_name: str | None
    #: The enclosing ``while`` loops' labels, innermost last - mirrors
    #: :attr:`~leech.ir_builder.CfgBuilder._loop_stack`, minus the basic
    #: blocks (a lowering-only concern).
    _loop_labels: list[str | None]

    def __init__(self) -> None:
        self.results = TypCheckResults()
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        self._loop_labels = []

    def check_fn(
        self, fn_ast: ast.FnDefn, e: Env, ret_typ: Typ, params: tuple[Param, ...]
    ) -> TypCheckResults:
        """Type-check a function body.

        :param fn_ast: The parsed function definition.
        :param e: The scope to resolve names in.
        :param ret_typ: The function's declared return type.
        :param params: The function's formal parameters.
        :return: :attr:`results`.
        :raises InvalidRetTypError: If the body's tail expression's type
            doesn't match ``ret_typ``.
        :raises MissingRetError: If ``ret_typ`` isn't ``void`` but the
            body doesn't end in a return or a diverging expression.
        """
        self._ret_typ = ret_typ
        # Matches CfgBuilder.build_ret_stmt's own (whole-function, not
        # return-type-annotation) span, for consistency with it.
        self._ret_typ_span = fn_ast.span
        self._fn_name = fn_ast.name.name

        e = e.new_child()
        for param in params:
            assert param.ast is not None
            param_typ = PtrTyp.get_or_create(param.typ, CONST)
            e.add_var(param.ast.name.name, _TypedPlace(param_typ))
        block_typ = self.check_expr(fn_ast.block, e, ret_typ)
        ret_ast = opt_or_default(fn_ast.block.expr, fn_ast.block)

        ret_typ_ast_span = opt_map(fn_ast.ret_typ, lambda t: t.span)
        if block_typ != VOID:
            # never is exempt here too - it's recorded (as NeverDiverge)
            # but that's never Invalid, matching CfgBuilder's own
            # never-typed early return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast, block_typ, ret_typ)
            if isinstance(coercion, Invalid):
                raise InvalidRetTypError(
                    self._fn_name,
                    ret_typ.name,
                    ret_typ_ast_span,
                    block_typ.name,
                    ret_ast.span,
                )
        elif ret_typ != VOID:
            raise MissingRetError(self._fn_name, fn_ast.span, ret_typ.name, ret_typ_ast_span)

        return self.results

    def check_var_initializer(self, defn_ast: ast.VarDefn, e: Env) -> TypCheckResults:
        """Type-check a module-level ``let`` initializer expression.

        :param defn_ast: The parsed variable declaration.
        :param e: The scope to resolve names in.
        :return: :attr:`results`.
        :raises VoidVarInitializerError: If the initializer expression has
            type ``void``.
        :raises IncompatibleLetTypError: If the initializer's type doesn't
            match, and doesn't coerce to, a declared type.
        """
        self._ret_typ = None
        self._ret_typ_span = None
        self._fn_name = None
        e = e.new_child()
        self._check_let_initializer(defn_ast.let_stmt, e)
        return self.results

    def check_expr(self, expr_ast: ast.Expr, e: Env, expected_typ: Typ | None) -> Typ:
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
                raise AssertionError(f"unhandled expression kind {expr_ast}")

    def check_block_expr(self, block_ast: ast.BlockExpr, e: Env, expected_typ: Typ | None) -> Typ:
        e = e.new_child()
        diverged = False
        for stmt_ast in block_ast.stmts:
            if self.check_stmt(stmt_ast, e):
                diverged = True
        if block_ast.expr is None:
            return NEVER if diverged else VOID
        return self.check_expr(block_ast.expr, e, expected_typ)

    def check_if_expr(self, if_ast: ast.IfExpr, e: Env, expected_typ: Typ | None) -> Typ:
        cond_typ = self.check_expr(if_ast.condition, e, None)
        cond_coercion = self._record_coercion(if_ast.condition, cond_typ, BOOL)
        if isinstance(cond_coercion, Invalid):
            raise IfCondNotBoolError(
                if_ast.condition.diag_str(),
                cond_typ.name,
                if_ast.condition.span,
            )

        if if_ast.els is None:
            then_typ = self.check_expr(if_ast.then, e, expected_typ)
            if then_typ != NEVER and then_typ != VOID:
                raise IfTypNotVoidError(then_typ.name, if_ast.then.span)
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
            if expected_typ is None and then_typ != NEVER and is_flexible_int_lit(els_ast):
                els_hint = resolve_peer_typ([then_typ])
            els_typ = self.check_expr(els_ast, e, els_hint)

        if then_typ != NEVER and els_typ != NEVER and then_typ != els_typ:
            raise IfElsTypMismatchError(then_typ.name, if_ast.then.span, els_typ.name, els_ast.span)

        if then_typ != NEVER:
            return then_typ
        if els_typ != NEVER:
            return els_typ
        return NEVER

    def check_while_expr(self, while_ast: ast.WhileExpr, e: Env) -> Typ:
        cond_typ = self.check_expr(while_ast.condition, e, None)
        cond_coercion = self._record_coercion(while_ast.condition, cond_typ, BOOL)
        if isinstance(cond_coercion, Invalid):
            raise WhileCondNotBoolError(
                while_ast.condition.diag_str(),
                cond_typ.name,
                while_ast.condition.span,
            )

        self._loop_labels.append(opt_map(while_ast.label, lambda x: x.name))
        try:
            block_typ = self.check_expr(while_ast.block, e, None)
        finally:
            self._loop_labels.pop()
        if block_typ not in (NEVER, VOID):
            raise WhileTypNotVoidError(block_typ.name, while_ast.block.span)

        return VOID

    def check_call_expr(self, call_ast: ast.CallExpr, e: Env) -> Typ:
        fn_typ, recv_ast, recv_typ = self._resolve_callee(call_ast, e)
        callee_diag_str = call_ast.callee.diag_str()
        param_typs = fn_typ.param_typs

        num_args = len(call_ast.args) + (1 if recv_typ is not None else 0)
        num_params = len(param_typs)
        if num_args < num_params:
            raise NotEnoughArgsError(callee_diag_str, call_ast.span, num_args, num_params)
        if num_args > num_params:
            raise TooManyArgsError(
                callee_diag_str,
                call_ast.span,
                call_ast.args[-1].span,
                num_args,
                num_params,
            )

        if recv_ast is not None and recv_typ is not None:
            recv_coercion = self._record_coercion(recv_ast, recv_typ, param_typs[0])
            if isinstance(recv_coercion, Invalid):
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.span,
                    1,
                    recv_typ.name,
                    param_typs[0].name,
                    recv_ast.span,
                )

        offset = 1 if recv_typ is not None else 0
        for i, arg_ast in enumerate(call_ast.args, start=offset):
            arg_typ = self.check_expr(arg_ast, e, param_typs[i])
            arg_coercion = self._record_coercion(arg_ast, arg_typ, param_typs[i])
            if isinstance(arg_coercion, Invalid):
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.span,
                    i + 1,
                    arg_typ.name,
                    param_typs[i].name,
                    arg_ast.span,
                )

        return fn_typ.ret_typ

    def _resolve_callee(
        self, call_ast: ast.CallExpr, e: Env
    ) -> tuple[CallableTyp, ast.Expr | None, Typ | None]:
        """The callee's type, and its receiver's expression and type, if any.

        If the callee is written as ``x.name``, ``name`` is looked up as a
        method of ``x``'s struct type first; otherwise (and if that
        lookup fails) it falls back to ordinary field access, exactly as
        :meth:`~leech.ir_builder.CfgBuilder.build_call_expr` does. A bare
        reference to a generic function is resolved through
        :meth:`_resolve_generic_call` instead, since it isn't itself a
        value with one fixed type the way every other callee is (see
        :class:`~leech.ir_module.GenericFn`).

        :param call_ast: The parsed call expression.
        :param e: The scope to resolve names in.
        :return: ``(fn_typ, recv_ast, recv_typ)``. ``recv_ast`` and
            ``recv_typ`` are ``None`` unless the callee names a method, in
            which case they're the receiver expression and its *place*
            type (a pointer, with the receiver's own mutability - see
            :meth:`check_place`, since a method receiver is always passed
            by pointer).
        :raises NotAMethodError: If the callee is ``x.name`` and ``name``
            names a real associated function of ``x``'s struct type that
            has no ``self`` receiver.
        :raises PrivateItemAccessError: If the callee is ``x.name`` and
            ``name`` names a private method, called from outside the
            module its struct is defined in.
        :raises NotCallableError: If the callee doesn't resolve to a
            callable type.
        :raises WrongNumberOfTypArgsError: If the callee is a generic
            function given the wrong number of explicit type arguments.
        :raises CannotInferTypArgError: If the callee is a generic
            function whose type arguments aren't all given explicitly or
            determined by the call's arguments' types.
        """
        callee_ast = call_ast.callee
        if isinstance(callee_ast, ast.VarExpr):
            # Real circular import; see check_var_expr.
            from leech import ir_module  # noqa: PLC0415

            var = e.resolve_path(Env.Namespace.VARS, callee_ast.path)
            if isinstance(var, ir_module.GenericFn):
                return self._resolve_generic_call(var.fn, call_ast, e), None, None

        if not isinstance(callee_ast, ast.StructAccessExpr):
            callee_typ = self.check_expr(callee_ast, e, None)
            fn_typ = _callable_typ(callee_typ)
            if fn_typ is None:
                raise NotCallableError(callee_ast.diag_str(), callee_typ.name, callee_ast.span)
            return fn_typ, None, None

        recv_typ = self.check_place(callee_ast.struct, e)
        struct_typ = recv_typ.pointee_typ if isinstance(recv_typ, PtrTyp) else VOID
        method = (
            struct_typ.get_assoc_fn(callee_ast.field.name)
            if isinstance(struct_typ, StructTyp)
            else None
        )
        if method is not None:
            if not method.is_accessible_from(callee_ast.span.file):
                raise PrivateItemAccessError(
                    "function",
                    callee_ast.field.name,
                    callee_ast.field.span,
                    method.span,
                )
            assert method.ast is not None
            if method.ast.receiver is None:
                raise NotAMethodError(
                    callee_ast.field.name,
                    struct_typ.name,
                    callee_ast.field.span,
                    method.span,
                )
            return method.fn_typ, callee_ast.struct, recv_typ

        field_typ = _struct_field_typ(struct_typ, callee_ast.field.name)
        callee_typ = opt_or_default(field_typ, VOID)
        fn_typ = _callable_typ(callee_typ)
        if fn_typ is None:
            raise NotCallableError(callee_ast.diag_str(), callee_typ.name, callee_ast.span)
        return fn_typ, None, None

    def _resolve_generic_call(self, fn: ir_module.Fn, call_ast: ast.CallExpr, e: Env) -> FnTyp:
        """Resolve a call to a generic function to its concrete instantiated type.

        Explicit type arguments (``f[i32](x)``) are resolved positionally
        against ``fn``'s own type parameters; without any, each type
        parameter is instead inferred from the call's arguments (see
        :meth:`_infer_typ_args`). The two aren't mixed - a call gives type
        arguments entirely explicitly or leaves them entirely inferred.

        :param fn: The generic function being called.
        :param call_ast: The parsed call expression, whose callee names
            ``fn``.
        :param e: The scope to resolve names in.
        :return: ``fn``'s signature with every type parameter replaced by
            its resolved type argument.
        :raises WrongNumberOfTypArgsError: If explicit type arguments are
            given and their count doesn't match ``fn``'s declared type
            parameters.
        :raises CannotInferTypArgError: If a type parameter is bound by
            neither an explicit type argument nor any call argument's type.
        """
        callee_ast = call_ast.callee
        assert isinstance(callee_ast, ast.VarExpr)
        fn_ast = opt_unwrap(fn.ast)
        typ_params = [
            TypParamTyp.get_or_create(fn_ast, i, p) for i, p in enumerate(fn_ast.generic_params)
        ]

        if callee_ast.generic_args:
            if len(callee_ast.generic_args) != len(typ_params):
                raise WrongNumberOfTypArgsError(
                    fn.name,
                    len(callee_ast.generic_args),
                    len(typ_params),
                    callee_ast.span,
                )
            mapping = {
                typ_param: Typ.from_ast(arg_ast, e)
                for typ_param, arg_ast in zip(typ_params, callee_ast.generic_args, strict=True)
            }
        else:
            mapping = self._infer_typ_args(fn, call_ast, e, typ_params)

        return checked_cast(fn.fn_typ.substitute_typ_params(mapping), FnTyp)

    def _infer_typ_args(
        self,
        fn: ir_module.Fn,
        call_ast: ast.CallExpr,
        e: Env,
        typ_params: list[TypParamTyp],
    ) -> dict[TypParamTyp, Typ]:
        """Infer a generic call's type arguments from its arguments' types.

        Structural, left to right, with no unification: each non-literal
        argument is checked (with no expected type, since none is known
        yet) and matched against its parameter's declared type (see
        :meth:`~leech.typs.Typ.infer_typ_args`). A bare integer literal is
        skipped - it has no type of its own to offer until it has a
        target, so it can't drive inference.

        :param fn: The generic function being called.
        :param call_ast: The parsed call expression.
        :param e: The scope to resolve names in.
        :param typ_params: ``fn``'s own type parameters, in declaration
            order.
        :return: The inferred type-parameter-to-concrete-type map.
        :raises CannotInferTypArgError: If a type parameter is left
            unbound by every argument.
        """
        bindings: dict[TypParamTyp, Typ] = {}
        for declared_typ, arg_ast in zip(fn.fn_typ.param_typs, call_ast.args, strict=False):
            if is_flexible_int_lit(arg_ast):
                continue
            arg_typ = self.check_expr(arg_ast, e, None)
            declared_typ.infer_typ_args(arg_typ, bindings)

        for typ_param in typ_params:
            if typ_param not in bindings:
                raise CannotInferTypArgError(fn.name, typ_param.name, call_ast.span)

        return bindings

    def check_bin_op_expr(self, op_ast: ast.BinOpExpr, e: Env, expected_typ: Typ | None) -> Typ:
        if op_ast.op.name in ("and", "or"):
            return self.check_logic_bin_op_expr(op_ast, e)

        # Both operands are always checked, even where CfgBuilder itself
        # would short-circuit on a diverging one (see _propagate_never) -
        # unreachable code still gets type-checked. never is exempt from
        # the checks below, rather than short-circuiting them: it stands
        # in for a value of any type, on either side.
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

        if lhs_typ != NEVER and not isinstance(lhs_typ, IntTyp):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs_typ.name,
                "an integer type",
                op_ast.lhs.span,
            )
        if lhs_typ != NEVER and rhs_typ != NEVER and lhs_typ != rhs_typ:
            raise IncompatibleBinOpArgTypsError(
                op_ast.op.name,
                op_ast.op.span,
                lhs_typ.name,
                op_ast.lhs.span,
                rhs_typ.name,
                op_ast.rhs.span,
            )

        if op_ast.op.name in ("<", "<=", "==", "!=", ">=", ">"):
            return BOOL
        return lhs_typ if lhs_typ != NEVER else rhs_typ

    def check_logic_bin_op_expr(self, op_ast: ast.BinOpExpr, e: Env) -> Typ:
        # Both operands are always checked; never's coercion to bool
        # always succeeds (see _record_coercion), so it's naturally
        # exempt from the checks below without a special case.
        lhs_typ = self.check_expr(op_ast.lhs, e, None)
        lhs_coercion = self._record_coercion(op_ast.lhs, lhs_typ, BOOL)
        if isinstance(lhs_coercion, Invalid):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs_typ.name,
                "bool",
                op_ast.lhs.span,
            )

        rhs_typ = self.check_expr(op_ast.rhs, e, None)
        rhs_coercion = self._record_coercion(op_ast.rhs, rhs_typ, BOOL)
        if isinstance(rhs_coercion, Invalid):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "right",
                rhs_typ.name,
                "bool",
                op_ast.rhs.span,
            )
        return BOOL

    def check_unary_op_expr(self, op_ast: ast.UnaryOpExpr, e: Env, expected_typ: Typ | None) -> Typ:
        match op_ast.op.name:
            case "&":
                return self._check_addr_of_expr(op_ast, e)
            case "not":
                return self._check_not_expr(op_ast, e)
            case "-":
                return self._check_neg_expr(op_ast, e, expected_typ)
            case _:
                raise AssertionError(f"unhandled unary operator {op_ast.op.name!r}")

    def _check_addr_of_expr(self, op_ast: ast.UnaryOpExpr, e: Env) -> Typ:
        return self.check_place(op_ast.operand, e)

    def _check_not_expr(self, op_ast: ast.UnaryOpExpr, e: Env) -> Typ:
        operand_typ = self.check_expr(op_ast.operand, e, None)
        coercion = self._record_coercion(op_ast.operand, operand_typ, BOOL)
        if isinstance(coercion, Invalid):
            raise InvalidUnaryOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                operand_typ.name,
                "bool",
                op_ast.operand.span,
            )
        return BOOL

    def _check_neg_expr(self, op_ast: ast.UnaryOpExpr, e: Env, expected_typ: Typ | None) -> Typ:
        if isinstance(op_ast.operand, ast.IntLit):
            typ = self._infer_int_lit_typ(op_ast.operand, expected_typ)
            if typ.signage != SIGNED:
                raise InvalidUnaryOpArgTypError(
                    op_ast.op.name,
                    op_ast.op.span,
                    typ.name,
                    "a signed integer type",
                    op_ast.operand.span,
                )
            value = -op_ast.operand.value
            if not typ.fits(value):
                raise IntLitOverflowError(value, typ.name, op_ast.span)
            self.results._set_int_lit_typ(op_ast.operand, typ)
            return typ

        operand_typ = self.check_expr(op_ast.operand, e, expected_typ)
        # never is exempt: it stands in for a value of any type.
        if operand_typ != NEVER and (
            not isinstance(operand_typ, IntTyp) or operand_typ.signage != SIGNED
        ):
            raise InvalidUnaryOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                operand_typ.name,
                "a signed integer type",
                op_ast.operand.span,
            )
        return operand_typ

    def check_var_expr(self, var_ast: ast.VarExpr, e: Env) -> Typ:
        # Real circular import: `ir_module` imports `typcheck` at module
        # level, and the `isinstance` checks below need the actual class
        # objects at runtime, so this can't be deferred to a TYPE_CHECKING
        # guard the way an annotation-only reference could be.
        from leech import ir_module  # noqa: PLC0415

        var = e.resolve_path(Env.Namespace.VARS, var_ast.path)
        if isinstance(var, ir_module.GenericFn):
            raise MissingTypArgsError(var.name, var_ast.span)
        if var_ast.generic_args:
            raise TypArgsOnNonGenericItemError(var_ast.path.str(), var_ast.span)
        if isinstance(var, ir_module.FnSpec):
            return var.typ
        return var.typ.pointee_typ

    def check_array_expr(self, arr_expr: ast.ArrayExpr, e: Env, expected_typ: Typ | None) -> Typ:
        expected_elt_typ = expected_typ.element_typ if isinstance(expected_typ, ArrayTyp) else None
        if not arr_expr.elements and expected_elt_typ is None:
            raise EmptyArrayTypUnknownError(arr_expr.span)

        built: list[Typ | None] = [None] * len(arr_expr.elements)
        for i, elt_ast in enumerate(arr_expr.elements):
            if not is_flexible_int_lit(elt_ast):
                built[i] = self.check_expr(elt_ast, e, expected_elt_typ)

        if expected_elt_typ is not None:
            elt_typ = expected_elt_typ
        else:
            elt_typ = resolve_peer_typ([t for t in built if t is not None])

        for i, t in enumerate(built):
            if t is None:
                built[i] = self.check_expr(arr_expr.elements[i], e, elt_typ)

        if elt_typ == VOID:
            first_span = arr_expr.span
            if arr_expr.elements:
                first_span = arr_expr.elements[0].span
            raise VoidArrayElementError(first_span)

        arr_typ = ArrayTyp.get_or_create(elt_typ, len(arr_expr.elements))
        for i, elt_ast in enumerate(arr_expr.elements):
            elt_typ_i = opt_unwrap(built[i])
            # Elements only coerce towards an element type the context
            # supplied, which is an unambiguous target. When the elements
            # settled on one between themselves it isn't: coercing to it
            # would let their order decide whether a narrower element is
            # accepted, so they have to agree exactly instead. A
            # diverging element is exempt - it stands in for a value of
            # any type.
            if expected_elt_typ is None and elt_typ_i not in (elt_typ, NEVER):
                raise IncompatibleTypInArrayExprError(elt_typ_i.name, i, elt_ast.span, arr_typ.name)
            coercion = self._record_coercion(elt_ast, elt_typ_i, elt_typ)
            if isinstance(coercion, Invalid):
                raise IncompatibleTypInArrayExprError(elt_typ_i.name, i, elt_ast.span, arr_typ.name)

        return arr_typ

    def check_array_access_expr(self, aa_expr: ast.ArrayAccessExpr, e: Env) -> Typ:
        arr_typ = self.check_expr(aa_expr.array, e, None)
        if not isinstance(arr_typ, ArrayTyp):
            raise IndexIntoInvalidTypError(arr_typ.name, aa_expr.array.span)

        index_typ = self.check_expr(aa_expr.index, e, USIZE)
        index_coercion = self._record_coercion(aa_expr.index, index_typ, USIZE)
        if isinstance(index_coercion, Invalid):
            raise InvalidIndexTypError(index_typ.name, aa_expr.index.span)

        return arr_typ.element_typ

    def check_struct_expr(self, struct_expr: ast.StructExpr, e: Env) -> Typ:
        struct_typ = Typ.from_ast(struct_expr.typ, e)
        if not isinstance(struct_typ, StructTyp):
            raise TypeOfStructExprNotStructError(struct_typ.name, struct_expr.typ.span)

        field_value_asts: dict[str, ast.Expr] = {}
        field_value_typs: dict[str, Typ] = {}
        for field_expr in struct_expr.fields:
            name = field_expr.ident.name
            field = struct_typ.fields.get(name)
            if field is None:
                raise InvalidStructFieldError(
                    name, field_expr.ident.span, struct_typ.name, struct_typ.span
                )
            if not field.is_accessible_from(struct_expr.span.file):
                raise PrivateStructFieldAccessError(
                    field.name, field_expr.ident.span, struct_typ.name, field.ast.span
                )
            if name in field_value_asts:
                raise DuplicateFieldInStructExprError(
                    name, field_expr.ident.span, field_value_asts[name].span
                )
            field_value_asts[name] = field_expr.value
            field_value_typs[name] = self.check_expr(field_expr.value, e, field.typ)

        for field in struct_typ.fields.values():
            if field.name not in field_value_asts:
                raise MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name, struct_expr.span
                )
            value_ast = field_value_asts[field.name]
            value_typ = field_value_typs[field.name]
            coercion = self._record_coercion(value_ast, value_typ, field.typ)
            if isinstance(coercion, Invalid):
                raise IncompatibleStructFieldTypError(
                    field.name,
                    struct_typ.name,
                    value_typ.name,
                    value_ast.span,
                    field.typ.name,
                    field.ast.span,
                )

        return struct_typ

    def check_struct_access_expr(self, sa_expr: ast.StructAccessExpr, e: Env) -> Typ:
        struct_typ = self.check_expr(sa_expr.struct, e, None)
        if not isinstance(struct_typ, StructTyp):
            raise FieldAccessIntoInvalidTypError(struct_typ.name, sa_expr.struct.span)

        field_name = sa_expr.field.name
        field = struct_typ.fields.get(field_name)
        if field is None:
            raise InvalidStructFieldError(
                field_name, sa_expr.field.span, struct_typ.name, struct_typ.span
            )
        if not field.is_accessible_from(sa_expr.span.file):
            raise PrivateStructFieldAccessError(
                field_name, sa_expr.field.span, struct_typ.name, field.ast.span
            )
        return field.typ

    def check_deref_expr(self, d_expr: ast.DerefExpr, e: Env) -> Typ:
        ptr_typ = self.check_expr(d_expr.ptr, e, None)
        if not isinstance(ptr_typ, PtrTyp) or isinstance(ptr_typ.pointee_typ, FnTyp):
            raise DerefInvalidTypError(ptr_typ.name, d_expr.ptr.span)
        return ptr_typ.pointee_typ

    def check_place(self, expr_ast: ast.Expr, e: Env) -> Typ:
        """The type of ``&expr_ast`` - the type of ``expr_ast``'s address.

        Only ``&``'s operand needs this: every other context cares about
        an expression's *value* type, which :meth:`check_expr` already
        gives (place vs. value is otherwise a lowering-only concern - see
        :attr:`~leech.ir_builder.ExprContext.PLACE`). For a real place
        expression (a variable, array access, struct access, or deref)
        the result's mutability follows the place; for anything else,
        address-of lowers by copying the value into a fresh ``const``
        temporary and taking its address (see
        :meth:`~leech.ir_builder.CfgBuilder._value_to_ptr`), which the
        fallback here mirrors.

        :param expr_ast: The parsed operand of ``&``.
        :param e: The scope to resolve names in.
        :return: The pointer type ``&expr_ast`` produces.
        :raises MissingTypArgsError: If ``expr_ast`` bare-names a generic
            function.
        """
        if isinstance(expr_ast, ast.VarExpr):
            # Real circular import; see check_var_expr.
            from leech import ir_module  # noqa: PLC0415

            # A variable's own type already *is* its place's pointer type
            # (see check_var_expr) - a function reference doubly so, since
            # taking its address is a no-op rather than an extra
            # indirection (see CfgBuilder.build_var_expr's PLACE case).
            var = e.resolve_path(Env.Namespace.VARS, expr_ast.path)
            if isinstance(var, ir_module.GenericFn):
                raise MissingTypArgsError(var.name, expr_ast.span)
            return var.typ

        value_typ = self.check_expr(expr_ast, e, None)
        return PtrTyp.get_or_create(value_typ, self._place_mut(expr_ast, e))

    def _place_mut(self, expr_ast: ast.Expr, e: Env) -> Mutability:
        """The mutability of ``expr_ast``'s place; see :meth:`check_place`."""
        match expr_ast:
            case ast.VarExpr():
                var = e.resolve_path(Env.Namespace.VARS, expr_ast.path)
                if isinstance(var.typ, PtrTyp):
                    return var.typ.mut
                return CONST
            case ast.ArrayAccessExpr():
                return self._place_mut(expr_ast.array, e)
            case ast.StructAccessExpr():
                struct_typ = self.check_expr(expr_ast.struct, e, None)
                field = (
                    struct_typ.fields.get(expr_ast.field.name)
                    if isinstance(struct_typ, StructTyp)
                    else None
                )
                if field is not None and field.mut == MUT:
                    return self._place_mut(expr_ast.struct, e)
                return CONST
            case ast.DerefExpr():
                ptr_typ = self.check_expr(expr_ast.ptr, e, None)
                if isinstance(ptr_typ, PtrTyp):
                    return ptr_typ.mut
                return CONST
            case _:
                return CONST

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
                self.check_break_stmt(stmt_ast)
                return True
            case ast.ContinueStmt():
                self.check_continue_stmt(stmt_ast)
                return True
            case _:
                raise AssertionError(f"unhandled statement kind {stmt_ast}")

    def check_ret_stmt(self, ret_ast: ast.RetStmt, e: Env) -> None:
        if self._ret_typ is None:
            raise RetNotInFnError(ret_ast.span)
        ret_typ = self._ret_typ
        fn_name = opt_unwrap(self._fn_name)

        if ret_ast.expr is not None:
            expr_typ = self.check_expr(ret_ast.expr, e, ret_typ)
            # never is exempt (recorded as NeverDiverge, never Invalid),
            # matching CfgBuilder.build_ret_stmt's own never-typed early
            # return, which skips coercion entirely.
            coercion = self._record_coercion(ret_ast.expr, expr_typ, ret_typ)
            if isinstance(coercion, Invalid):
                raise InvalidRetTypError(
                    fn_name,
                    ret_typ.name,
                    self._ret_typ_span,
                    expr_typ.name,
                    ret_ast.expr.span,
                )
            return

        if ret_typ != VOID:
            raise InvalidVoidRetError(fn_name, ret_typ.name, self._ret_typ_span, ret_ast.span)

    def check_break_stmt(self, break_ast: ast.BreakStmt) -> None:
        if not self._loop_labels:
            raise BreakNotInLoopError(break_ast.span)
        self._resolve_loop_label(break_ast.label)

    def check_continue_stmt(self, continue_ast: ast.ContinueStmt) -> None:
        if not self._loop_labels:
            raise ContinueNotInLoopError(continue_ast.span)
        self._resolve_loop_label(continue_ast.label)

    def _resolve_loop_label(self, label: ast.Ident | None) -> None:
        """Confirm a ``break``/``continue`` label names an enclosing loop.

        Mirrors :meth:`~leech.ir_builder.CfgBuilder._resolve_loop_label`,
        minus finding the actual target - callers there trust this
        already succeeded and just need the block to branch to.

        :param label: The target label, or ``None`` for the innermost
            enclosing loop (always found - callers check
            :attr:`_loop_labels` is non-empty first).
        :raises LoopLabelNotFoundError: If ``label`` is given but no
            enclosing loop has it.
        """
        if label is None:
            return
        for loop_label in reversed(self._loop_labels):
            if loop_label == label.name:
                return
        raise LoopLabelNotFoundError(label.name, label.span)

    def check_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> bool:
        expr_typ, declared_typ = self._check_let_initializer(let_ast, e)
        bound_typ = opt_or_default(declared_typ, expr_typ)
        mut = Mutability.from_ast(let_ast.mut)
        place_typ = PtrTyp.get_or_create(bound_typ, mut)
        e.add_var(let_ast.ident.name, _TypedPlace(place_typ))
        return expr_typ == NEVER

    def _check_let_initializer(self, let_ast: ast.LetStmt, e: Env) -> tuple[Typ, Typ | None]:
        """Check a ``let`` initializer, shared by :meth:`check_let_stmt`
        and :meth:`check_var_initializer` - mirrors
        :meth:`~leech.ir_builder.CfgBuilder._build_let_initializer`.

        :param let_ast: The parsed ``let`` statement.
        :param e: The scope to resolve names in.
        :return: ``(expr_typ, declared_typ)`` - the initializer's own
            checked type, and the ``let``'s declared type, if it has one.
        :raises VoidVarInitializerError: If the initializer expression has
            type ``void``.
        :raises IncompatibleLetTypError: If the initializer's type doesn't
            match, and doesn't coerce to, a declared type.
        """
        declared_typ_ast = let_ast.typ
        declared_typ = opt_map(declared_typ_ast, lambda t: Typ.from_ast(t, e))
        expr_typ = self.check_expr(let_ast.expr, e, declared_typ)
        if expr_typ == VOID:
            raise VoidVarInitializerError(let_ast.expr.span)
        if declared_typ_ast is None:
            return expr_typ, None

        assert declared_typ is not None
        coercion = self._record_coercion(let_ast.expr, expr_typ, declared_typ)
        if isinstance(coercion, Invalid):
            raise IncompatibleLetTypError(
                let_ast.ident.name,
                declared_typ.name,
                expr_typ.name,
                let_ast.expr.span,
                declared_typ_ast.span,
            )
        return expr_typ, declared_typ

    def check_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> bool:
        place_typ = self.check_expr(ass_ast.place, e, None)
        place_mut = self._place_mut(ass_ast.place, e)
        expr_typ = self.check_expr(ass_ast.expr, e, place_typ)

        if place_mut == CONST:
            raise AssignToConstError(ass_ast.place.span)

        coercion = self._record_coercion(ass_ast.expr, expr_typ, place_typ)
        if isinstance(coercion, Invalid):
            raise IncompatibleAssignmentTypError(
                expr_typ.name, place_typ.name, ass_ast.expr.span, ass_ast.place.span
            )
        return place_typ == NEVER or expr_typ == NEVER

    def _infer_int_lit_typ(self, lit_ast: ast.IntLit, expected_typ: Typ | None) -> IntTyp:
        """Choose an integer literal's type.

        A literal written with a type suffix always has that type. One
        written without takes the type the surrounding context expects,
        if that's an integer type, and falls back to ``i32`` otherwise.

        :param lit_ast: The parsed integer literal.
        :param expected_typ: The type the context expects, if known.
        :return: The type to give the literal.
        """
        if lit_ast.explicit_width is not None:
            assert lit_ast.explicit_signage is not None
            return IntTyp.get_or_create(lit_ast.explicit_width, lit_ast.explicit_signage)
        if isinstance(expected_typ, IntTyp):
            return expected_typ
        return I32

    def _record_coercion(self, node: ast.Ast, value_typ: Typ, target: Typ) -> Coercion | None:
        """Decide, and record, how a checked expression coerces to a target type.

        Mirrors the decision :meth:`~leech.ir_builder.CfgBuilder._coerce`
        used to make itself; that method now just reads the result back
        (keyed by ``node``) and performs the emission.

        :param node: The AST node the coerced expression was checked
            against - the same node :meth:`~TypCheckResults.coercion` will
            later be called with.
        :param value_typ: The expression's checked type.
        :param target: The type its context expects.
        :return: The recorded decision, for callers that need to check it
            (e.g. raise if it's :class:`Invalid`) rather than just have it
            available for lowering later.
        """
        if value_typ == target:
            coercion = None
        elif value_typ == NEVER:
            coercion = NeverDiverge(target)
        elif not value_typ.coerces_to(target):
            coercion = Invalid()
        elif isinstance(target, IntTyp):
            coercion = IntExt(target)
        else:
            coercion = PtrMutRelax()
        self.results._set_coercion(node, coercion)
        return coercion
