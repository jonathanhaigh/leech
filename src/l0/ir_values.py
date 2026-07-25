from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import cached_property
from typing import TYPE_CHECKING, Final, Generic, Optional, Self, TypeVar, override

from more_itertools import nth
import networkx as nx

from l0 import l0ast as ast
from l0.asserts import (
    assert_all_eq,
    assert_eq,
    assert_ge,
    assert_gt,
    assert_lt,
    checked_cast,
)
from l0.l0errors import IntLitOverflowError, UnreachableCodeWarning, register_error
from l0.opt_util import opt_map, opt_unwrap
from l0.src import SrcSpan
from l0.typs import (
    BOOL,
    CONST,
    CSTR,
    NEVER,
    U8,
    VOID,
    ArrayTyp,
    BoolTyp,
    FnTyp,
    IntTyp,
    Mutability,
    NeverTyp,
    PtrTyp,
    StructTyp,
    Typ,
    VoidTyp,
)

if TYPE_CHECKING:
    # `ir_module.FnSpec` (used only in a type annotation below, in `Param`) would
    # otherwise be a real circular import: `ir_module.FnSpec` is defined as a
    # subclass of `ComptimePtr` from this module, so `ir_module` needs this
    # module to have fully executed before it can even be defined. Since the
    # reverse reference here is annotation-only, guarding it behind
    # TYPE_CHECKING avoids the runtime import entirely while keeping the
    # annotation available to the type checker.
    from l0 import ir_module

TypT = TypeVar("TypT", bound=Typ, covariant=True, default=Typ)
AstT = TypeVar("AstT", bound=ast.Ast, covariant=True, default=ast.Ast)


def gep_typ(base_typ: PtrTyp, index: Value) -> PtrTyp:
    mut = base_typ.mut
    typ = base_typ.pointee_typ
    match typ:
        case ArrayTyp():
            typ = typ.element_typ
        case StructTyp():
            index = checked_cast(
                index, ComptimeInt, "struct indeces must be comptime known"
            )

            field = nth(typ.fields.values(), index.value)
            assert field is not None, (
                f"invalid field index {index.value} into {typ.name}"
            )
            if field.mut == CONST:
                mut = CONST
            typ = field.typ
        case _:
            raise NotImplementedError(typ)

    return PtrTyp.get_or_create(typ, mut)


class Value(ABC, Generic[TypT, AstT]):
    ast: Final[Optional[AstT]]

    def __init__(self, ast: Optional[AstT]) -> None:
        self.ast = ast

    @cached_property
    def typ(self) -> TypT:
        return self.calculate_typ()

    @property
    def span(self) -> Optional[SrcSpan]:
        return opt_map(self.ast, lambda x: x.span)

    @abstractmethod
    def calculate_typ(self) -> TypT:
        pass


class ComptimeValue(Generic[TypT, AstT], Value[TypT, AstT]):
    def copy(self) -> Self:
        # Most ComptimeValues are immutable so real copying is not required
        return self


class ComptimeInt(ComptimeValue[IntTyp]):
    _typ: Final[IntTyp]
    value: Final[int]

    @override
    def __init__(self, typ: IntTyp, value: int, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self._typ = typ
        if value.bit_length() > typ.width:
            raise IntLitOverflowError(value, typ.name, self.span)
        self.value = value

    @override
    def calculate_typ(self) -> IntTyp:
        return self._typ


class ComptimeBool(ComptimeValue[BoolTyp]):
    value: Final[bool]

    def __init__(self, value: bool, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.value = value

    @override
    def calculate_typ(self) -> BoolTyp:
        return BOOL


class ComptimeCStr(ComptimeValue[PtrTyp]):
    value: Final[bytearray]
    initializer_typ: Final[ArrayTyp]

    def __init__(self, value: str, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.value = bytearray(value.encode() + b"\0")
        self.initializer_typ = ArrayTyp.get_or_create(U8, len(self.value))

    @override
    def calculate_typ(self) -> PtrTyp:
        return CSTR


class ComptimeAggregate(Generic[TypT, AstT], ComptimeValue[TypT, AstT]):
    @abstractmethod
    def __len__(self) -> int:
        pass

    @abstractmethod
    def get_element(self, index: int) -> ComptimeValue:
        pass

    @abstractmethod
    def set_element(self, value: ComptimeValue, index: int) -> None:
        pass

    def values(self) -> Sequence[ComptimeValue]:
        return [self.get_element(i) for i in range(len(self))]

    def check_index(self, index: int) -> None:
        assert_ge(index, 0)
        assert_lt(index, len(self))


class ComptimeArray(ComptimeAggregate[ArrayTyp]):
    elements: list[ComptimeValue]
    _typ: Final[ArrayTyp]

    @override
    def __init__(
        self, typ: ArrayTyp, elements: list[ComptimeValue], ast: Optional[ast.Ast]
    ) -> None:
        assert_eq(typ.length, len(elements))
        assert_all_eq(list(map(lambda x: x.typ, elements)), typ.element_typ)
        super().__init__(ast)
        self._typ = typ
        self.elements = elements

    @override
    def __len__(self) -> int:
        return len(self.elements)

    @override
    def calculate_typ(self) -> ArrayTyp:
        return self._typ

    @override
    def get_element(self, index: int) -> ComptimeValue:
        self.check_index(index)
        return self.elements[index]

    @override
    def set_element(self, value: ComptimeValue, index: int) -> None:
        self.check_index(index)
        self.elements[index] = value

    @override
    def copy(self) -> ComptimeArray:
        return ComptimeArray(self._typ, [elt.copy() for elt in self.elements], self.ast)


class ComptimeStruct(ComptimeAggregate[StructTyp]):
    fields: dict[str, ComptimeValue]
    _typ: Final[StructTyp]

    @override
    def __init__(
        self, typ: StructTyp, fields: dict[str, ComptimeValue], ast: Optional[ast.Ast]
    ) -> None:
        assert_eq(
            {k: v.typ for k, v in typ.fields.items()},
            {k: v.typ for k, v in fields.items()},
        )
        super().__init__(ast)
        self._typ = typ
        self.fields = fields

    @override
    def __len__(self) -> int:
        return len(self.fields)

    @override
    def calculate_typ(self) -> StructTyp:
        return self._typ

    @override
    def get_element(self, index: int) -> ComptimeValue:
        self.check_index(index)
        field_spec = nth(self._typ.fields.values(), index)
        assert field_spec is not None
        return self.fields[field_spec.name]

    @override
    def set_element(self, value: ComptimeValue, index: int) -> None:
        self.check_index(index)
        field_spec = nth(self._typ.fields.values(), index)
        assert field_spec is not None
        self.fields[field_spec.name] = value

    @override
    def copy(self) -> ComptimeStruct:
        return ComptimeStruct(
            self._typ, {k: v.copy() for k, v in self.fields.items()}, self.ast
        )


class ComptimePtr(Generic[AstT], ComptimeValue[PtrTyp, AstT]):
    @abstractmethod
    def load(self) -> ComptimeValue:
        pass

    @abstractmethod
    def store(self, value: ComptimeValue) -> None:
        pass

    @abstractmethod
    def is_temporary(self) -> bool:
        pass


class ComptimeAlloc(ComptimePtr):
    value: ComptimeValue
    mut: Final[Mutability]

    @override
    def __init__(self, typ: Typ, mut: Mutability, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.mut = mut
        self.value = UndefValue(typ, ast)

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self.value.typ, self.mut)

    @override
    def load(self) -> ComptimeValue:
        return self.value

    @override
    def store(self, value: ComptimeValue) -> None:
        self.value = value

    @override
    def is_temporary(self) -> bool:
        return True


class ComptimeGep(ComptimePtr):
    base: Final[ComptimePtr]
    index: Final[ComptimeInt]

    @override
    def __init__(
        self, base: ComptimePtr, index: ComptimeInt, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(ast)
        self.base = base
        self.index = index

    @override
    def calculate_typ(self) -> PtrTyp:
        return gep_typ(self.base.typ, self.index)

    @override
    def load(self) -> ComptimeValue:
        agg = checked_cast(self.base.load(), ComptimeAggregate)
        return agg.get_element(self.index.value)

    @override
    def store(self, value: ComptimeValue) -> None:
        agg = checked_cast(self.base.load(), ComptimeAggregate)
        agg.set_element(value, self.index.value)

    @override
    def is_temporary(self) -> bool:
        return self.base.is_temporary()


class VoidValue(ComptimeValue[VoidTyp]):
    @override
    def __init__(self, ast: Optional[ast.Ast]):
        super().__init__(ast)

    @override
    def calculate_typ(self) -> VoidTyp:
        return VOID


class UndefValue(ComptimeValue):
    _typ: Final[Typ]

    @override
    def __init__(self, typ: Typ, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self._typ = typ

    @override
    def calculate_typ(self) -> Typ:
        return self._typ


class Param(Value[Typ, ast.Param]):
    fn: Final[ir_module.FnSpec]
    pos: Final[int]

    @override
    def __init__(
        self, fn: ir_module.FnSpec, pos: int, ast: Optional[ast.Param]
    ) -> None:
        super().__init__(ast)
        self.fn = fn
        self.pos = pos

    @override
    def calculate_typ(self) -> Typ:
        assert_gt(len(self.fn.fn_typ.param_typs), self.pos)
        return self.fn.fn_typ.param_typs[self.pos]


class Instr(Value[TypT, AstT], Generic[TypT, AstT]):
    bb: Final[BasicBlock]

    @override
    def __init__(self, bb: BasicBlock, ast: Optional[AstT]) -> None:
        super().__init__(ast)
        self.bb = bb

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return type(self).__name__


class BinOpInstr(Instr):
    lhs: Final[Value]
    rhs: Final[Value]

    @override
    def __init__(
        self, bb: BasicBlock, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> Typ:
        assert_eq(self.lhs.typ, self.rhs.typ)
        return self.lhs.typ


class AddInstr(BinOpInstr):
    pass


class SubInstr(BinOpInstr):
    pass


class MulInstr(BinOpInstr):
    pass


class SdivInstr(BinOpInstr):
    pass


class UdivInstr(BinOpInstr):
    pass


class IcmpInstr(Instr):
    op: Final[str]
    lhs: Final[Value]
    rhs: Final[Value]

    @override
    def __init__(
        self, bb: BasicBlock, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.op = op
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> Typ:
        return BOOL


class IcmpSignedInstr(IcmpInstr):
    pass


class IcmpUnsignedInstr(IcmpInstr):
    pass


class LoadInstr(Instr):
    src: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, src: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        checked_cast(src.typ, PtrTyp)
        self.src = src

    @override
    def calculate_typ(self) -> Typ:
        src_typ = checked_cast(self.src.typ, PtrTyp)
        return src_typ.pointee_typ


class AllocaInstr(Instr[PtrTyp]):
    allocated_typ: Final[Typ]
    mut: Final[Mutability]
    count: Final[int]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        typ: Typ,
        mut: Mutability,
        count: int,
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.allocated_typ = typ
        self.mut = mut
        self.count = count

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self.allocated_typ, self.mut)


class StoreInstr(Instr[VoidTyp]):
    value: Final[Value]
    dest: Final[Value]

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, dest: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        dest_typ = checked_cast(dest.typ, PtrTyp)
        assert_eq(value.typ, dest_typ.pointee_typ)
        self.value = value
        self.dest = dest

    @override
    def calculate_typ(self) -> VoidTyp:
        return VOID


class GepInstr(Instr[PtrTyp]):
    base: Final[Value]
    index: Value

    @override
    def __init__(
        self,
        bb: BasicBlock,
        base: Value,
        index: Value,
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.base = base
        self.index = index

    @override
    def calculate_typ(self) -> PtrTyp:
        base_typ = checked_cast(self.base.typ, PtrTyp)
        return gep_typ(base_typ, self.index)


class InsertValueInstr(Instr):
    aggregate: Final[Value]
    value: Final[Value]
    indeces: tuple[ComptimeInt, ...]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        aggregate: Value,
        value: Value,
        indeces: tuple[ComptimeInt, ...],
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.aggregate = aggregate
        self.value = value
        self.indeces = indeces

    @override
    def calculate_typ(self) -> Typ:
        return self.aggregate.typ


class CallInstr(Instr[Typ, ast.CallExpr]):
    callee: Final[Value]
    args: tuple[Value, ...]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        callee: Value,
        args: tuple[Value, ...],
        ast: Optional[ast.CallExpr],
    ) -> None:
        super().__init__(bb, ast)
        self.callee = callee
        self.args = args

    @override
    def calculate_typ(self) -> Typ:
        callee_typ = checked_cast(self.callee.typ, PtrTyp)
        fn_typ = checked_cast(callee_typ.pointee_typ, FnTyp)
        return fn_typ.ret_typ


class PhiInstr(Instr):
    incoming: Final[dict["BasicBlock", Value]]

    @override
    def __init__(
        self, bb: BasicBlock, incoming: dict[BasicBlock, Value], ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.incoming = incoming

    @override
    def calculate_typ(self) -> Typ:
        values = list(self.incoming.values())
        assert_gt(len(values), 0)
        first_typ = values[0].typ
        for value in values:
            assert_eq(value.typ, first_typ)
        return first_typ


class BranchInstr(Instr[NeverTyp]):
    target: Final[BasicBlock]

    @override
    def __init__(
        self, bb: BasicBlock, target: BasicBlock, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.target = target

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class CbranchInstr(Instr[NeverTyp]):
    condition: Final[Value]
    true_target: Final[BasicBlock]
    false_target: Final[BasicBlock]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.condition = condition
        self.true_target = true_target
        self.false_target = false_target

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class RetInstr(Instr[NeverTyp]):
    value: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, value: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.value = value

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class UnreachableInstr(Instr[NeverTyp]):
    @override
    def __init__(self, bb: BasicBlock, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class BasicBlock:
    name: Final[str]
    instrs: Final[list[Instr]]
    terminated: bool
    warned_unreachable: bool

    def __init__(self, name: str) -> None:
        self.name = name
        self.instrs = []
        self.terminated = False
        self.warned_unreachable = False

    def __str__(self) -> str:
        instrs = "".join(f"\n{instr}" for instr in self.instrs)
        return f"{self.name}:{instrs}"

    def _add_instr[T: Instr](self, instr: T, terminate: bool = False) -> T:
        if self.terminated:
            if not self.warned_unreachable:
                assert instr.ast is not None, (
                    "Shouldn't get unreachable code in builtins"
                )
                register_error(
                    UnreachableCodeWarning(instr.ast.diag_str(), instr.ast.span)
                )
                self.warned_unreachable = True

            return instr
        self.instrs.append(instr)
        if terminate:
            self.terminated = True
        return instr

    def add(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> AddInstr:
        return self._add_instr(AddInstr(self, lhs, rhs, ast))

    def sub(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SubInstr:
        return self._add_instr(SubInstr(self, lhs, rhs, ast))

    def mul(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> MulInstr:
        return self._add_instr(MulInstr(self, lhs, rhs, ast))

    def sdiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SdivInstr:
        return self._add_instr(SdivInstr(self, lhs, rhs, ast))

    def udiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> UdivInstr:
        return self._add_instr(UdivInstr(self, lhs, rhs, ast))

    def icmp_signed(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpSignedInstr:
        return self._add_instr(IcmpSignedInstr(self, op, lhs, rhs, ast))

    def icmp_unsigned(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpUnsignedInstr:
        return self._add_instr(IcmpUnsignedInstr(self, op, lhs, rhs, ast))

    def phi(
        self, incoming: dict[BasicBlock, Value], ast: Optional[ast.Ast]
    ) -> PhiInstr:
        return self._add_instr(PhiInstr(self, incoming, ast))

    def load(self, src: Value, ast: Optional[ast.Ast]) -> LoadInstr:
        return self._add_instr(LoadInstr(self, src, ast))

    def alloca(
        self, typ: Typ, mut: Mutability, count: int, ast: Optional[ast.Ast]
    ) -> AllocaInstr:
        return self._add_instr(AllocaInstr(self, typ, mut, count, ast))

    def store(self, value: Value, dest: Value, ast: Optional[ast.Ast]) -> StoreInstr:
        return self._add_instr(StoreInstr(self, value, dest, ast))

    def gep(self, base: Value, index: Value, ast: Optional[ast.Ast]) -> GepInstr:
        return self._add_instr(GepInstr(self, base, index, ast))

    def insert_value(
        self,
        aggregate: Value,
        value: Value,
        indeces: tuple[ComptimeInt, ...],
        ast: Optional[ast.Ast],
    ) -> InsertValueInstr:
        return self._add_instr(InsertValueInstr(self, aggregate, value, indeces, ast))

    def call(
        self, callee: Value, args: tuple[Value, ...], ast: Optional[ast.CallExpr]
    ) -> CallInstr:
        return self._add_instr(CallInstr(self, callee, args, ast))

    def branch(self, target: BasicBlock, ast: Optional[ast.Ast]) -> BranchInstr:
        return self._add_instr(BranchInstr(self, target, ast), terminate=True)

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: Optional[ast.Ast],
    ) -> CbranchInstr:
        return self._add_instr(
            CbranchInstr(self, condition, true_target, false_target, ast),
            terminate=True,
        )

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> RetInstr:
        return self._add_instr(RetInstr(self, value, ast), terminate=True)

    def unreachable(self, ast: Optional[ast.Ast]) -> UnreachableInstr:
        return self._add_instr(UnreachableInstr(self, ast), terminate=True)


class Cfg(nx.DiGraph):
    _entry: Optional[BasicBlock]
    _exit: Optional[BasicBlock]

    @property
    def entry(self) -> BasicBlock:
        return opt_unwrap(self._entry)

    @property
    def exit(self) -> BasicBlock:
        return opt_unwrap(self._exit)
