from l0.ir import Env, ModVar
from l0.l0errors import VarNotFoundError
from . import l0ast as ast
from .typs import BOOL, CSTR, U8, ArrayTyp, Typ, IntTyp


class Value:
    typ: Typ

    def __init__(self, typ: Typ) -> None:
        self.typ = typ


class Int(Value):
    value: int

    def __init__(self, typ: IntTyp, value: int) -> None:
        super().__init__(typ)
        if value.bit_length() > typ.width:
            raise NotImplementedError
        self.value = value


class Bool(Value):
    value: bool

    def __init__(self, value: bool) -> None:
        super().__init__(BOOL)
        self.value = value


class CStr(Value):
    value: bytearray
    initializer_type: ArrayTyp

    def __init__(self, value: str) -> None:
        super().__init__(CSTR)
        self.value = bytearray(value.encode() + b"\0")
        self.initializer_typ = ArrayTyp(U8, len(self.value))


class Interpreter:
    def eval(self, expr: ast.Expr, e: Env) -> Value:
        match expr:
            case ast.IntLit():
                return expr.value
            case ast.StrLit():
                return expr.value
            case ast.BoolLit():
                return expr.value
            case ast.VarExpr():
                if expr.name not in e.vars:
                    raise VarNotFoundError(expr.name, expr.meta)
                var = e.vars[expr.name]
                assert isinstance(var.value, ModVar)
                return var.value.initializer
            case _:
                raise NotImplementedError
