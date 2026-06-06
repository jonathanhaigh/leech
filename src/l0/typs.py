from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Final, override


class Mutability(Enum):
    CONST = 0
    MUT = 1


CONST = Mutability.CONST
MUT = Mutability.MUT


class Signage(Enum):
    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED


class Typ(ABC):
    @abstractmethod
    def __eq__(self, other: Any) -> bool:
        pass

    @abstractmethod
    def __hash__(self) -> int:
        pass

    @abstractmethod
    def name(self) -> str:
        pass


class IntTyp(Typ):
    width: Final[int]
    signage: Final[Signage]

    def __init__(self, width: int, signage: Signage) -> None:
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

    def __init__(self, ret_typ: Typ, param_typs: tuple[Typ, ...]) -> None:
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

    def __init__(self, pointee_typ: Typ, mut: Mutability) -> None:
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

    def __init__(self, element_typ: Typ, length: int) -> None:
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
