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
    UndefValue,
    Value,
    VoidValue,
)
from l0.l0errors import (
    AssignToConstError,
    DerefInvalidTypError,
    DuplicateFieldInStructExprError,
    FieldAccessIntoInvalidTypError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleBinOpArgTypsError,
    IncompatibleStructFieldTypError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
    InvalidRetTypError,
    InvalidStructFieldError,
    InvalidVoidRetError,
    MissingFieldInStructExprError,
    MissingRetError,
    NotCallableError,
    NotEnoughArgsError,
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
    PLACE = 0
    VALUE = 1


class CfgBuilder:
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
        assert self.fn is None
        e = e.new_child()
        initializer = self.build_expr(defn_ast.let_stmt.expr, e, ExprContext.VALUE)
        if initializer.typ == VOID:
            raise VoidVarInitializerError(initializer.span)
        self.ret(initializer, defn_ast.let_stmt.expr)

    def build_fn(self, fn_ast: ast.FnDefn, e: Env) -> None:
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
            if block.typ != ret_typ:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name,
                    ret_typ_span,
                    block.typ.name,
                    ret_ast.span,
                )
            self.ret(block, ret_ast)
            return

        if self.curr_bb.terminated:
            return

        if ret_typ != VOID:
            raise MissingRetError(self.fn.name, fn_ast.span, ret_typ.name, ret_typ_span)

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_expr(self, expr_ast: ast.Expr, e: Env, ctx: ExprContext) -> Value:
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
                return self._in_context(
                    ComptimeInt(expr_ast.typ, expr_ast.value, expr_ast), ctx
                )
            case ast.BoolLit():
                return self._in_context(ComptimeBool(expr_ast.value, expr_ast), ctx)
            case ast.VarExpr():
                return self.build_var_expr(expr_ast, e, ctx)
            case ast.ArrayExpr():
                return self.build_array_expr(expr_ast, e, ctx)
            case ast.ArrayAccessExpr():
                return self.build_array_access_expr(expr_ast, e, ctx)
            case ast.StructExpr():
                return self.build_struct_expr(expr_ast, e, ctx)
            case ast.StructAccessExpr():
                return self.build_struct_access_expr(expr_ast, e, ctx)
            case ast.DerefExpr():
                return self.build_deref_expr(expr_ast, e, ctx)
            case _:
                raise NotImplementedError(expr_ast)

    def build_block_expr(
        self, block_ast: ast.BlockExpr, e: Env, ctx: ExprContext
    ) -> Value:
        e = e.new_child()
        for stmt_ast in block_ast.stmts:
            self.build_stmt(stmt_ast, e)

        if block_ast.expr is None:
            return VoidValue(block_ast)
        return self.build_expr(block_ast.expr, e, ctx)

    def build_if_expr(self, if_ast: ast.IfExpr, e: Env, ctx: ExprContext) -> Value:
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
            self.curr_bb.unreachable(if_ast)

        if have_then_value:
            if then.typ != VOID:
                raise IfTypNotVoidError(then.typ.name, if_ast.then.span)

        self.set_position(end_bb)
        return VoidValue(if_ast)

    def build_while_expr(
        self, while_ast: ast.WhileExpr, e: Env, _ctx: ExprContext
    ) -> Value:
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
        callee = self.build_expr(call_ast.callee, e, ExprContext.VALUE)
        callee_diag_str = call_ast.callee.diag_str()
        if not (
            isinstance(callee.typ, PtrTyp)
            and isinstance(callee.typ.pointee_typ, CallableTyp)
        ):
            raise NotCallableError(
                callee_diag_str, callee.typ.name, call_ast.callee.span
            )

        args = tuple(
            self.build_expr(arg_ast, e, ExprContext.VALUE) for arg_ast in call_ast.args
        )

        num_args = len(args)
        fn_typ = callee.typ.pointee_typ
        param_typs = fn_typ.param_typs
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
        for i, arg in enumerate(args):
            if arg.typ != param_typs[i]:
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.span,
                    i + 1,
                    arg.typ.name,
                    param_typs[i].name,
                    call_ast.args[i].span,
                )

        return self._in_context(self.curr_bb.call(callee, args, call_ast), ctx)

    def build_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: Env, ctx: ExprContext
    ) -> Value:
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
                raise NotImplementedError(op)

        return self._in_context(res, ctx)

    def build_unary_op_expr(
        self, op_ast: ast.UnaryOpExpr, e: Env, ctx: ExprContext
    ) -> Value:
        match op_ast.op.name:
            case "&":
                return self._in_context(
                    self.build_expr(op_ast.operand, e, ExprContext.PLACE),
                    ctx,
                )
            case _:
                raise NotImplementedError(op_ast.op.name)

    def build_var_expr(self, var_ast: ast.VarExpr, e: Env, ctx: ExprContext) -> Value:
        var = e.resolve_path(Env.Namespace.VARS, var_ast.path)
        if isinstance(var, ir_module.FnSpec):
            return var
        if ctx == ExprContext.PLACE:
            return var

        return self.curr_bb.load(var, var_ast)

    def build_array_expr(
        self, arr_expr: ast.ArrayExpr, e: Env, ctx: ExprContext
    ) -> Value:
        elts = [self.build_expr(elt, e, ExprContext.VALUE) for elt in arr_expr.elements]
        if not elts:
            raise NotImplementedError("empty array expression")

        elt_typ = elts[0].typ
        if elt_typ == VOID:
            raise VoidArrayElementError(elts[0].span)
        arr_typ = ArrayTyp.get_or_create(elt_typ, len(elts))
        arr = ComptimeArray(
            arr_typ,
            [UndefValue(arr_typ.element_typ, arr_expr)] * len(elts),
            arr_expr,
        )

        for i, elt in enumerate(elts):
            elt_ast = arr_expr.elements[i]
            if elt.typ != elt_typ:
                raise IncompatibleTypInArrayExpr(
                    elt.typ.name, i, elt_ast.span, arr_typ.name
                )
            elt_index = ComptimeInt(USIZE, i, elt_ast)
            arr = self.curr_bb.insert_value(arr, elt, (elt_index,), elt_ast)

        return self._in_context(arr, ctx)

    def build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
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
        if ctx == ExprContext.PLACE:
            return elt_ptr
        else:
            return self.curr_bb.load(elt_ptr, aa_expr)

    def build_struct_expr(
        self, struct_expr: ast.StructExpr, e: Env, ctx: ExprContext
    ) -> Value:
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
            if field_expr.ident.name in field_values:
                raise DuplicateFieldInStructExprError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    field_values[field_expr.ident.name].span,
                )
            field_values[field_expr.ident.name] = self.build_expr(
                field_expr.value, e, ExprContext.VALUE
            )

        for i, field in enumerate(struct_typ.fields.values()):
            if field.name not in field_values:
                raise MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name, struct_expr.span
                )
            field_value = field_values[field.name]
            if field.typ != field_value.typ:
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
                struct, field_value, (field_index,), field_value.ast
            )

            # TODO: check field access specifiers

        return self._in_context(struct, ctx)

    def build_struct_access_expr(
        self, sa_expr: ast.StructAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
        struct_ptr = self.build_expr(sa_expr.struct, e, ExprContext.PLACE)
        struct_ptr_typ = checked_cast(struct_ptr.typ, PtrTyp)
        struct_typ = struct_ptr_typ.pointee_typ
        if not isinstance(struct_typ, StructTyp):
            raise FieldAccessIntoInvalidTypError(struct_typ.name, sa_expr.struct.span)

        field_name = sa_expr.field.name
        if field_name not in struct_typ.fields:
            raise InvalidStructFieldError(
                field_name, sa_expr.field.span, struct_typ.name, struct_typ.span
            )

        index = ComptimeInt(I32, struct_typ.fields[field_name].index, sa_expr)
        field_ptr = self.curr_bb.gep(struct_ptr, index, sa_expr)

        if ctx == ExprContext.PLACE:
            return field_ptr
        else:
            return self.curr_bb.load(field_ptr, sa_expr)

    def build_deref_expr(
        self, d_expr: ast.DerefExpr, e: Env, ctx: ExprContext
    ) -> Value:
        ptr = self.build_expr(d_expr.ptr, e, ExprContext.VALUE)
        if not isinstance(ptr.typ, PtrTyp) or isinstance(ptr.typ.pointee_typ, FnTyp):
            raise DerefInvalidTypError(ptr.typ.name, d_expr.ptr.span)
        if ctx == ExprContext.PLACE:
            return ptr
        else:
            return self.curr_bb.load(ptr, d_expr)

    def build_stmt(self, stmt_ast: ast.Stmt, e: Env) -> None:
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
        if self.fn is None:
            raise RetNotInFnError(ret_ast.span)

        ret_typ = self.fn.fn_typ.ret_typ
        ret_typ_span = None
        if self.fn.ast is not None and self.fn.ast.span is not None:
            ret_typ_span = self.fn.ast.span

        if ret_ast.expr is not None:
            expr = self.build_expr(ret_ast.expr, e, ExprContext.VALUE)
            if expr.typ == NEVER:
                if not self.curr_bb.terminated:
                    self.curr_bb.unreachable(ret_ast)
                return
            if expr.typ != ret_typ:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name,
                    ret_typ_span,
                    expr.typ.name,
                    ret_ast.expr.span,
                )
            self.ret(expr, ret_ast)
            return

        if ret_typ != VOID:
            raise InvalidVoidRetError(
                self.fn.name, ret_typ.name, ret_typ_span, ret_ast.span
            )

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> None:
        expr = self.build_expr(let_ast.expr, e, ExprContext.VALUE)
        if expr.typ == VOID:
            raise VoidVarInitializerError(expr.span)
        name = let_ast.ident.name
        mut = Mutability.from_ast(let_ast.mut)
        alloca = self.curr_bb.alloca(expr.typ, mut, 1, let_ast)
        e.add_var(name, alloca)
        self.curr_bb.store(expr, alloca, let_ast)

    def build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> None:
        expr = self.build_expr(ass_ast.expr, e, ExprContext.VALUE)
        place = self.build_expr(ass_ast.place, e, ExprContext.PLACE)

        place_typ = checked_cast(place.typ, PtrTyp)
        assert place_typ.pointee_typ != VOID, "assignment place cannot be void"
        if place_typ.mut == CONST:
            raise AssignToConstError(ass_ast.place.span)

        self.curr_bb.store(expr, place, ass_ast)

    def _value_to_ptr(self, value: Value) -> Value[PtrTyp]:
        alloca = self.curr_bb.alloca(value.typ, CONST, 1, value.ast)
        self.curr_bb.store(value, alloca, value.ast)
        return alloca

    def _in_context(self, value: Value, ctx: ExprContext) -> Value:
        if ctx == ExprContext.PLACE:
            return self._value_to_ptr(value)
        else:
            return value

    def add_bb(self, basename: str) -> BasicBlock:
        bb = BasicBlock(self._generate_bb_name(basename))
        self.cfg.add_node(bb)
        return bb

    def set_position(self, bb: BasicBlock) -> None:
        assert_in(bb, self.cfg)
        self.curr_bb = bb

    def branch(self, target: BasicBlock, ast: ast.Ast) -> None:
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
        assert_in(true_target, self.cfg)
        assert_in(false_target, self.cfg)
        self.curr_bb.cbranch(condition, true_target, false_target, ast)
        self.cfg.add_edge(self.curr_bb, true_target)
        self.cfg.add_edge(self.curr_bb, false_target)

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> None:
        self.curr_bb.ret(value, ast)
        self.cfg.add_edge(self.curr_bb, self.cfg.exit)
