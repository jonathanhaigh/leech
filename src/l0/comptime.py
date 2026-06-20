from l0 import ir
from l0.l0errors import (
    ArrayIndexOutOfBoundsError,
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
from .typs import USIZE, ArrayTyp, StructTyp, Typ


class Interpreter:
    def eval(self, expr: ast.Expr, e: ir.Env) -> ir.ComptimeValue:
        match expr:
            case ast.IntLit():
                return ir.ComptimeInt(expr.typ, expr.value, expr)
            case ast.StrLit():
                return ir.ComptimeCStr(expr.value, expr)
            case ast.BoolLit():
                return ir.ComptimeBool(expr.value, expr)
            case ast.VarExpr():
                var = e.resolve_var(expr.path)
                assert isinstance(var, ir.ModVar)
                return var.initializer
            case ast.ArrayExpr():
                return self.eval_array(expr, e)
            case ast.ArrayAccessExpr():
                return self.eval_array_access(expr, e)
            case ast.StructExpr():
                return self.eval_struct(expr, e)
            case ast.StructAccessExpr():
                return self.eval_struct_access(expr, e)
            case _:
                raise NotImplementedError

    def eval_array(self, expr: ast.ArrayExpr, e: ir.Env) -> ir.ComptimeArray:
        elts = [self.eval(elt, e) for elt in expr.elements]
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
        self, expr: ast.ArrayAccessExpr, e: ir.Env
    ) -> ir.ComptimeValue:

        array = self.eval(expr.array, e)
        if not isinstance(array, ir.ComptimeArray):
            raise IndexIntoInvalidTypError(array.typ.name(), expr.array.span)

        index = self.eval(expr.index, e)
        if not isinstance(index, ir.ComptimeInt) or index.typ != USIZE:
            raise InvalidIndexTypError(index.typ.name(), expr.index.span)

        if index.value >= array.typ.length:
            raise ArrayIndexOutOfBoundsError(
                index.value, expr.index.span, array.typ.name()
            )

        assert len(array.elements) > index.value
        return array.elements[index.value]

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
            field_values[field_expr.ident.name] = self.eval(field_expr.value, e)

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

    def eval_struct_access(
        self, expr: ast.StructAccessExpr, e: ir.Env
    ) -> ir.ComptimeValue:

        struct = self.eval(expr.struct, e)
        if not isinstance(struct, ir.ComptimeStruct):
            raise FieldAccessIntoInvalidTypError(struct.typ.name(), expr.span)

        if expr.field.name not in struct.typ.fields:
            raise InvalidStructFieldError(
                expr.field.name, expr.field.span, struct.typ.name(), struct.typ.span
            )

        return struct.fields[expr.field.name]
