from more_itertools import nth
from l0 import ir
from l0.l0errors import (
    ArrayIndexOutOfBoundsError,
    CannotTakeAddressOfComtimeValue,
    DerefInvalidTypError,
    DuplicateFieldInStructExprError,
    FieldAccessIntoInvalidTypError,
    IncompatibleStructFieldTypError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidIndexTypError,
    InvalidStructFieldError,
    MissingFieldInStructExprError,
    TypeOfStructExprNotStructError,
)
from . import l0ast as ast
from .typs import I32, USIZE, ArrayTyp, PtrTyp, StructTyp, Typ


class Interpreter:
    def eval(self, expr: ast.Expr, e: ir.Env, ctx: ir.ExprContext) -> ir.ComptimeValue:
        match expr:
            case ast.IntLit():
                self._check_value_context(expr, ctx)
                return ir.ComptimeInt(expr.typ, expr.value, expr)
            case ast.StrLit():
                self._check_value_context(expr, ctx)
                return ir.ComptimeCStr(expr.value, expr)
            case ast.BoolLit():
                self._check_value_context(expr, ctx)
                return ir.ComptimeBool(expr.value, expr)
            case ast.VarExpr():
                var = e.resolve_var(expr.path)
                match var:
                    case ir.ModVar():
                        if ctx == ir.ExprContext.PLACE:
                            return var
                        else:
                            return var.initializer
                    case ir.FnSpec():
                        return var
                    case _:
                        raise NotImplementedError(var)
            case ast.ArrayExpr():
                self._check_value_context(expr, ctx)
                return self.eval_array(expr, e)
            case ast.ArrayAccessExpr():
                return self.eval_array_access(expr, e, ctx)
            case ast.StructExpr():
                self._check_value_context(expr, ctx)
                return self.eval_struct(expr, e)
            case ast.StructAccessExpr():
                return self.eval_struct_access(expr, e, ctx)
            case ast.UnaryOpExpr():
                self._check_value_context(expr, ctx)
                return self.eval_unary_op(expr, e)
            case ast.DerefExpr():
                return self.eval_deref(expr, e, ctx)
            case _:
                raise NotImplementedError(expr)

    def _check_value_context(self, expr: ast.Expr, ctx: ir.ExprContext) -> None:
        if ctx == ir.ExprContext.PLACE:
            raise CannotTakeAddressOfComtimeValue(expr.span)

    def eval_array(self, expr: ast.ArrayExpr, e: ir.Env) -> ir.ComptimeArray:
        elts = [self.eval(elt, e, ir.ExprContext.VALUE) for elt in expr.elements]
        if not elts:
            raise NotImplementedError("empty array expression")
        elt_typ = elts[0].typ
        arr_typ = ArrayTyp(elt_typ, len(elts))
        for i, elt in enumerate(elts):
            if elt.typ != elt_typ:
                raise IncompatibleTypInArrayExpr(
                    elt.typ.name(), i, expr.elements[i].span, arr_typ.name()
                )
        return ir.ComptimeArray(arr_typ, elts, expr)

    def eval_array_access(
        self, expr: ast.ArrayAccessExpr, e: ir.Env, ctx: ir.ExprContext
    ) -> ir.ComptimeValue:

        # We're evaluating the index expression before the array expression
        # (unlike at runtime), but since this is happening at comptime there
        # shouldn't be any side effects so the order shouldn't matter.
        index = self.eval(expr.index, e, ir.ExprContext.VALUE)

        if ctx == ir.ExprContext.PLACE:
            array_ptr = self.eval(expr.array, e, ctx)
            assert isinstance(array_ptr, ir.ComptimePtr)
            array_typ = array_ptr.typ.pointee_typ
            self._check_array_access(expr, array_typ, index)
            assert isinstance(index, ir.ComptimeInt)
            zero = ir.ComptimeInt(USIZE, 0, None)
            return ir.ComptimeGep(array_ptr, (zero, index), expr)
        else:
            array = self.eval(expr.array, e, ir.ExprContext.VALUE)
            self._check_array_access(expr, array.typ, index)
            assert isinstance(array, ir.ComptimeArray)
            assert isinstance(index, ir.ComptimeInt)
            assert len(array.elements) > index.value
            return array.elements[index.value]

    def _check_array_access(
        self, expr: ast.ArrayAccessExpr, array_typ: Typ, index: ir.ComptimeValue
    ) -> None:
        if not isinstance(array_typ, ArrayTyp):
            raise IndexIntoInvalidTypError(array_typ.name(), expr.array.span)

        if not isinstance(index, ir.ComptimeInt) or index.typ != USIZE:
            raise InvalidIndexTypError(index.typ.name(), expr.index.span)

        if index.value >= array_typ.length:
            raise ArrayIndexOutOfBoundsError(
                index.value, expr.index.span, array_typ.name()
            )

    def eval_struct(self, expr: ast.StructExpr, e: ir.Env) -> ir.ComptimeStruct:
        struct_typ = Typ.from_ast(expr.typ, e)
        if not isinstance(struct_typ, StructTyp):
            raise TypeOfStructExprNotStructError(struct_typ.name(), expr.typ.span)

        field_values = {}
        for field_expr in expr.fields:
            if field_expr.ident.name not in struct_typ.fields:
                raise InvalidStructFieldError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    struct_typ.name(),
                    struct_typ.span,
                )
            if field_expr.ident.name in field_values:
                raise DuplicateFieldInStructExprError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    field_values[field_expr.ident.name].span,
                )
            field_values[field_expr.ident.name] = self.eval(
                field_expr.value, e, ir.ExprContext.VALUE
            )

        for field in struct_typ.fields.values():
            if field.name not in field_values:
                raise MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name(), expr.span
                )
            field_value = field_values[field.name]
            if field.typ != field_value.typ:
                raise IncompatibleStructFieldTypError(
                    field.name,
                    struct_typ.name(),
                    field_value.typ.name(),
                    field_value.span,
                    field.typ.name(),
                    field.ast.span,
                )

        return ir.ComptimeStruct(struct_typ, field_values, expr)

    def _check_struct_access(self, expr: ast.StructAccessExpr, struct_typ: Typ) -> None:
        if not isinstance(struct_typ, StructTyp):
            raise FieldAccessIntoInvalidTypError(struct_typ.name(), expr.span)

        if expr.field.name not in struct_typ.fields:
            raise InvalidStructFieldError(
                expr.field.name, expr.field.span, struct_typ.name(), struct_typ.span
            )

    def eval_struct_access(
        self, expr: ast.StructAccessExpr, e: ir.Env, ctx: ir.ExprContext
    ) -> ir.ComptimeValue:
        if ctx == ir.ExprContext.PLACE:
            struct_ptr = self.eval(expr.struct, e, ir.ExprContext.PLACE)
            assert isinstance(struct_ptr, ir.ComptimePtr)
            struct_typ = struct_ptr.typ.pointee_typ
            self._check_struct_access(expr, struct_typ)
            assert isinstance(struct_typ, StructTyp)
            index = ir.ComptimeInt(
                I32, struct_typ.fields[expr.field.name].index, expr.field
            )
            zero = ir.ComptimeInt(USIZE, 0, None)
            return ir.ComptimeGep(struct_ptr, (zero, index), expr)
        else:
            struct = self.eval(expr.struct, e, ir.ExprContext.VALUE)
            self._check_struct_access(expr, struct.typ)
            assert isinstance(struct, ir.ComptimeStruct)
            assert expr.field.name in struct.fields
            return struct.fields[expr.field.name]

    def eval_unary_op(self, expr: ast.UnaryOpExpr, e: ir.Env) -> ir.ComptimeValue:
        match expr.op.name:
            case "&":
                return self.eval(expr.operand, e, ir.ExprContext.PLACE)
            case _:
                raise NotImplementedError(expr.op.name)

    def eval_deref(
        self, expr: ast.DerefExpr, e: ir.Env, ctx: ir.ExprContext
    ) -> ir.ComptimeValue:
        ptr = self.eval(expr.ptr, e, ir.ExprContext.VALUE)
        if not isinstance(ptr.typ, PtrTyp):
            raise DerefInvalidTypError(ptr.typ.name(), expr.ptr.span)
        assert isinstance(ptr, ir.ComptimePtr)

        if ctx == ir.ExprContext.PLACE:
            return ptr

        match ptr:
            case ir.ModVar():
                return ptr.initializer
            case ir.FnSpec():
                raise DerefInvalidTypError(ptr.typ.name(), expr.ptr.span)
            case ir.ComptimeGep():
                return self.eval_deref_gep(ptr, e)
            case _:
                raise NotImplementedError(ptr)

    def eval_deref_gep(self, gep: ir.ComptimeGep, e: ir.Env) -> ir.ComptimeValue:
        value = gep.base
        for index in gep.indeces:
            match value:
                case ir.ModVar():
                    assert index.value == 0
                    value = value.initializer
                case ir.ComptimeGep():
                    assert index.value == 0
                    value = self.eval_deref_gep(value, e)
                case ir.ComptimeArray():
                    assert index.value < value.typ.length, (
                        f"invalid index {index.value} into array of length {value.typ.length}"
                    )
                    assert index.value < len(value.elements)
                    value = value.elements[index.value]
                case ir.ComptimeStruct():
                    field = nth(value.typ.fields.values(), index.value)
                    assert field is not None, (
                        f"invalid field index {index.value} into {value.typ.name()}"
                    )
                    assert field.name in value.fields
                    value = value.fields[field.name]
                case _:
                    raise NotImplementedError(value)

        return value
