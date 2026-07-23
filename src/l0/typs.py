from abc import ABC, abstractmethod
from collections.abc import Hashable
from enum import Enum
from functools import cached_property
from types import MappingProxyType
from typing import ClassVar, Final, Optional, Self, override
from weakref import WeakValueDictionary

from l0 import ir, l0ast as ast
from l0.asserts import assert_eq, assert_gt, assert_not_in, checked_cast
from l0.l0errors import DuplicateFieldInStructDefnError
from l0.src import SrcSpan


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


class Typ(ABC):
    _cache: ClassVar[WeakValueDictionary[Hashable, Typ]] = WeakValueDictionary()

    @classmethod
    def create(cls, *args: Hashable) -> Self:
        key = cls.cache_key(*args)
        assert_not_in(key, Typ._cache)
        obj = cls(*args)
        Typ._cache[key] = obj
        return obj

    @classmethod
    def get(cls, *args: Hashable) -> Self:
        return checked_cast(Typ._cache[cls.cache_key(*args)], cls)

    @classmethod
    def get_or_create(cls, *args: Hashable) -> Self:
        key = cls.cache_key(*args)
        obj = Typ._cache.get(key)
        if obj is None:
            obj = cls(*args)
            Typ._cache[key] = obj
        return checked_cast(obj, cls)

    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        return (cls, *args)

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @staticmethod
    def from_ast(typ_ast: ast.Typ, e: ir.Env) -> Typ:
        match typ_ast:
            case ast.BasicTyp():
                typ = e.resolve_typ(typ_ast.path)
                return typ
            case ast.PtrTyp():
                return PtrTyp.get_or_create(Typ.from_ast(typ_ast.pointee_typ, e), CONST)
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
            case _:
                raise NotImplementedError(ast)


class IntTyp(Typ):
    width: Final[int]
    signage: Final[Signage]

    def __init__(self, width: int, signage: Signage) -> None:
        self.width = width
        self.signage = signage

    @property
    @override
    def name(self) -> str:
        sign_char = "i" if self.signage == SIGNED else "u"
        return f"{sign_char}{self.width}"


class BoolTyp(Typ):
    @property
    @override
    def name(self) -> str:
        return "bool"


class CallableTyp(Typ):
    ret_typ: Final[Typ]
    param_typs: Final[tuple[Typ, ...]]

    def __init__(self, ret_typ: Typ, param_typs: tuple[Typ, ...]) -> None:
        self.ret_typ = ret_typ
        self.param_typs = param_typs


class FnTyp(CallableTyp):
    @property
    @override
    def name(self) -> str:
        param_strs = ", ".join(typ.name for typ in self.param_typs)
        return f"fn({param_strs}) {self.ret_typ.name}"


class PtrTyp(Typ):
    pointee_typ: Final[Typ]
    mut: Final[Mutability]

    def __init__(self, pointee_typ: Typ, mut: Mutability) -> None:
        self.pointee_typ = pointee_typ
        self.mut = mut

    @property
    @override
    def name(self) -> str:
        mut_str = "mut " if self.mut == MUT else ""
        return f"{mut_str}*{self.pointee_typ.name}"

    def new_with_mut(self, mut: Mutability) -> PtrTyp:
        return PtrTyp.get_or_create(self.pointee_typ, mut)


class ArrayTyp(Typ):
    element_typ: Final[Typ]
    length: Final[int]

    def __init__(self, element_typ: Typ, length: int) -> None:
        self.element_typ = element_typ
        self.length = length

    @property
    @override
    def name(self) -> str:
        return f"[{self.element_typ.name}; {self.length}]"


class StructField:
    index: Final[int]
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


class StructTyp(Typ):
    ast: Final[ast.StructDefn]
    env: Final[ir.Env]

    def __init__(self, ast: ast.StructDefn, e: ir.Env) -> None:
        self.ast = ast
        self.env = e

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        assert_gt(len(args), 0)
        struct_ast = checked_cast(args[0], ast.StructDefn)
        return (cls, struct_ast)

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @cached_property
    def fields(self) -> MappingProxyType[str, StructField]:
        fields = {}
        for i, field_ast in enumerate(self.ast.fields):
            name = field_ast.ident.name
            if name in fields:
                raise DuplicateFieldInStructDefnError(
                    name, field_ast.ident.span, fields[name].ast.ident.span
                )

            fields[name] = StructField(i, field_ast, self.env)

        return fields.items().mapping

    @property
    def span(self) -> SrcSpan:
        return self.ast.span


class VoidTyp(Typ):
    @property
    @override
    def name(self) -> str:
        return "void"


class NeverTyp(Typ):
    @property
    @override
    def name(self) -> str:
        return "never"


U8 = IntTyp.get_or_create(8, UNSIGNED)
I8 = IntTyp.get_or_create(8, SIGNED)
U32 = IntTyp.get_or_create(32, UNSIGNED)
I32 = IntTyp.get_or_create(32, SIGNED)
ADDR_SIZE = 64
USIZE = IntTyp.get_or_create(ADDR_SIZE, UNSIGNED)
ISIZE = IntTyp.get_or_create(ADDR_SIZE, SIGNED)
CINT = I32
BOOL = BoolTyp.get_or_create()
CSTR = PtrTyp.get_or_create(U8, CONST)
VOID = VoidTyp.get_or_create()
NEVER = NeverTyp.get_or_create()
