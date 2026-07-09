from abc import ABC, abstractmethod
from enum import Enum
from functools import cached_property
from types import MappingProxyType
from typing import Any, Final, Generic, Optional, TypeVar, override

from l0 import ir, l0ast as ast
from l0.asserts import assert_eq
from l0.l0errors import DuplicateFieldInStructDefnError
from l0.opt_util import opt_map
from l0.src import SrcSpan

AstT = TypeVar("AstT", bound="ast.Ast", covariant=True, default="ast.Ast")


class Mutability(Enum):
    CONST = 0
    MUT = 1

    @staticmethod
    def from_ast(mut_ast: Optional[ast.Mutability]) -> Mutability:
        if mut_ast is None:
            return CONST
        assert_eq(mut_ast.value, "mut")
        return MUT


CONST = Mutability.CONST
MUT = Mutability.MUT


class Signage(Enum):
    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED


class Typ(ABC, Generic[AstT]):
    ast: Final[Optional[AstT]]

    def __init__(self, ast: Optional[AstT] = None) -> None:
        self.ast = ast

    @abstractmethod
    def __eq__(self, other: Any) -> bool:
        pass

    @abstractmethod
    def __hash__(self) -> int:
        pass

    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def span(self) -> Optional[SrcSpan]:
        return opt_map(self.ast, lambda x: x.span)

    @staticmethod
    def from_ast(typ_ast: ast.Typ, e: ir.Env) -> Typ:
        match typ_ast:
            case ast.BasicTyp():
                typ = e.resolve_typ(typ_ast.path)
                return typ
            case ast.PtrTyp():
                return PtrTyp(Typ.from_ast(typ_ast.pointee_typ, e), CONST)
            case ast.ArrayTyp():
                return ArrayTyp(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
            case _:
                raise NotImplementedError(ast)


class IntTyp(Typ):
    width: Final[int]
    signage: Final[Signage]

    @override
    def __init__(self, width: int, signage: Signage) -> None:
        super().__init__()
        self.width = width
        self.signage = signage

    @override
    def __eq__(self, other: Any) -> bool:
        return (
            type(self) is type(other)
            and self.width == other.width
            and self.signage == other.signage
        )

    @override
    def __hash__(self) -> int:
        return hash((self.width, self.signage))

    @override
    def name(self) -> str:
        sign_char = "i" if self.signage == SIGNED else "u"
        return f"{sign_char}{self.width}"


class BoolTyp(Typ):
    @override
    def __eq__(self, other: Any) -> bool:
        return type(self) is type(other)

    @override
    def __hash__(self) -> int:
        return hash(type(self))

    @override
    def name(self) -> str:
        return "bool"


class CallableTyp(Typ):
    ret_typ: Final[Typ]
    param_typs: Final[tuple[Typ, ...]]

    @override
    def __init__(self, ret_typ: Typ, param_typs: tuple[Typ, ...]) -> None:
        super().__init__()
        self.ret_typ = ret_typ
        self.param_typs = param_typs


class FnTyp(CallableTyp):
    @override
    def __eq__(self, other: Any) -> bool:
        return (
            type(self) is type(other)
            and self.ret_typ == other.ret_typ
            and self.param_typs == other.param_typs
        )

    @override
    def __hash__(self) -> int:
        return hash((self.ret_typ, self.param_typs))

    @override
    def name(self) -> str:
        param_strs = ", ".join(typ.name() for typ in self.param_typs)
        return f"fn({param_strs}) {self.ret_typ.name()}, "


class PtrTyp(Typ):
    pointee_typ: Final[Typ]
    mut: Mutability

    @override
    def __init__(self, pointee_typ: Typ, mut: Mutability) -> None:
        super().__init__()
        self.pointee_typ = pointee_typ
        self.mut = mut

    @override
    def __eq__(self, other: Any) -> bool:
        return (
            type(self) is type(other)
            and self.mut == other.mut
            and self.pointee_typ == other.pointee_typ
        )

    @override
    def __hash__(self) -> int:
        return hash((self.mut, self.pointee_typ))

    @override
    def name(self) -> str:
        mut_str = "mut " if self.mut == MUT else ""
        return f"{mut_str}*{self.pointee_typ.name()}"

    def new_with_mut(self, mut: Mutability) -> PtrTyp:
        return PtrTyp(self.pointee_typ, mut)


class ArrayTyp(Typ):
    element_typ: Final[Typ]
    length: Final[int]

    @override
    def __init__(self, element_typ: Typ, length: int) -> None:
        super().__init__()
        self.element_typ = element_typ
        self.length = length

    @override
    def __eq__(self, other: Any) -> bool:
        return (
            type(self) is type(other)
            and self.element_typ == other.element_typ
            and self.length == other.length
        )

    @override
    def __hash__(self) -> int:
        return hash((self.element_typ, self.length))

    @override
    def name(self) -> str:
        return f"[{self.element_typ.name()}; {self.length}]"


class StructField:
    index: int
    ast: Final[ast.StructFieldDefn]
    env: Final[ir.Env]

    def __init__(self, index: int, ast: ast.StructFieldDefn, e: ir.Env) -> None:
        self.index = index
        self.ast = ast
        self.env = e

    @property
    def name(self) -> str:
        return self.ast.ident.name

    @cached_property
    def typ(self) -> Typ:
        return Typ.from_ast(self.ast.typ, self.env)

    @cached_property
    def access(self) -> ir.Access:
        return ir.Access.from_ast(self.ast.access)

    @cached_property
    def mut(self) -> Mutability:
        return Mutability.from_ast(self.ast.mut)


class StructTyp(Typ["ast.StructDefn"]):
    env: Final[ir.Env]

    @override
    def __init__(self, ast: ast.StructDefn, e: ir.Env) -> None:
        super().__init__(ast)
        self.env = e

    @override
    def __eq__(self, other: Any) -> bool:
        return self is other

    @override
    def __hash__(self) -> int:
        return hash(id(self))

    @override
    def name(self) -> str:
        assert self.ast is not None
        return self.ast.ident.name

    @cached_property
    def fields(self) -> MappingProxyType[str, StructField]:
        assert self.ast is not None
        fields = {}
        for i, field_ast in enumerate(self.ast.fields):
            name = field_ast.ident.name
            if name in fields:
                raise DuplicateFieldInStructDefnError(
                    name, field_ast.ident.span, fields[name].ast.ident.span
                )

            fields[name] = StructField(i, field_ast, self.env)

        return fields.items().mapping


class VoidTyp(Typ):
    @override
    def __eq__(self, other: Any) -> bool:
        return type(self) is type(other)

    @override
    def __hash__(self) -> int:
        return hash(type(self))

    @override
    def name(self) -> str:
        return "void"


class NeverTyp(Typ):
    @override
    def __eq__(self, other: Any) -> bool:
        return type(self) is type(other)

    @override
    def __hash__(self) -> int:
        return hash(type(self))

    @override
    def name(self) -> str:
        return "never"


U8 = IntTyp(8, UNSIGNED)
I8 = IntTyp(8, SIGNED)
U32 = IntTyp(32, UNSIGNED)
I32 = IntTyp(32, SIGNED)
ADDR_SIZE = 64
USIZE = IntTyp(ADDR_SIZE, UNSIGNED)
ISIZE = IntTyp(ADDR_SIZE, SIGNED)
CINT = I32
BOOL = BoolTyp()
CSTR = PtrTyp(U8, CONST)
VOID = VoidTyp()
NEVER = NeverTyp()
