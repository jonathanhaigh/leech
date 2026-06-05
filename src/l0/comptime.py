from l0 import ir
from l0.l0errors import IncompatibleTypInArrayExpr, VarNotFoundError
from . import l0ast as ast
from .typs import ArrayTyp


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
                if expr.name not in e.vars:
                    raise VarNotFoundError(expr.name, expr.span)
                var = e.vars[expr.name]
                assert isinstance(var.value, ir.ModVar)
                return var.value.initializer
            case ast.ArrayExpr():
                return self.eval_array_expr(expr, e)
            case _:
                raise NotImplementedError

    def eval_array_expr(self, expr: ast.ArrayExpr, e: ir.Env) -> ir.ComptimeArray:
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
