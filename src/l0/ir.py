from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import enum
from functools import cached_property
import re
from typing import (
    Any,
    ChainMap,
    Final,
    Generic,
    Optional,
    Self,
    TypeVar,
    cast,
    override,
)

from more_itertools import nth
import networkx as nx

from l0 import comptime, main
from l0 import l0ast as ast
from l0.asserts import (
    assert_all_eq,
    assert_eq,
    assert_ge,
    assert_gt,
    assert_in,
    assert_lt,
    checked_cast,
)
from l0.l0errors import (
    AssignToConstError,
    AssignToVoidError,
    DerefInvalidTypError,
    DuplicateFieldInStructExprError,
    DuplicateItemDefnError,
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
    ItemNotFoundError,
    MissingFieldInStructExprError,
    MissingRetError,
    ModDoesNotExistError,
    NotCallableError,
    NotEnoughArgsError,
    RetNotInFnError,
    TooManyArgsError,
    TypeOfStructExprNotStructError,
    UnreachableCodeWarning,
    VoidVarInitializerError,
    WhileCondNotBoolError,
    WhileTypNotVoidError,
    register_error,
)
from l0.naming import VarNamer
from l0.opt_util import opt_map, opt_or_default, opt_unwrap
from l0.src import SrcFile, SrcSpan
from l0.typs import (
    BOOL,
    CONST,
    CSTR,
    I32,
    ISIZE,
    MUT,
    NEVER,
    SIGNED,
    U8,
    UNSIGNED,
    USIZE,
    VOID,
    ArrayTyp,
    BoolTyp,
    CallableTyp,
    FnTyp,
    IntTyp,
    Mutability,
    NeverTyp,
    PtrTyp,
    StructTyp,
    Typ,
    VoidTyp,
)

TypT = TypeVar("TypT", bound=Typ, covariant=True, default=Typ)
AstT = TypeVar("AstT", bound=ast.Ast, covariant=True, default=ast.Ast)
FnAstT = TypeVar("FnAstT", bound=ast.FnSpec, covariant=True)


class Env:
    class Namespace(enum.Enum):
        VARS = 0
        TYPS = 1

        def item_kind(self) -> str:
            match self:
                case Env.Namespace.VARS:
                    return "variable"
                case Env.Namespace.TYPS:
                    return "type"

    items: ChainMap[tuple[Env.Namespace, str], Typ | Value[PtrTyp]]

    def __init__(self, parent: Optional[Env] = None) -> None:
        if parent is None:
            self.items = ChainMap()
        else:
            self.items = parent.items.new_child()

    def new_child(self) -> Env:
        return Env(self)

    def get(self, ns: Env.Namespace, name: str) -> Any:
        key = (ns, name)
        if key in self.items:
            return self.items[key]

        if ns == Env.Namespace.TYPS:
            m = re.fullmatch("([iu])([1-9][0-9]*)", name)
            if m:
                signage = SIGNED if m[1] == "i" else UNSIGNED
                width = int(m[2])
                typ = IntTyp.get_or_create(width, signage)
                self.items.maps[-1][key] = typ
                return typ

        return None

    def add(self, ns: Env.Namespace, name: str, item: Any) -> None:
        key = (ns, name)
        if key in self.items.maps[0]:
            existing = self.items[key]
            raise DuplicateItemDefnError(
                ns.item_kind(), name, item.span, ast.opt_span(existing)
            )
        self.items[key] = item

    def get_var(self, name: str) -> Optional[Value[PtrTyp]]:
        return self.get(Env.Namespace.VARS, name)

    def add_var(self, name: str, var: Value[PtrTyp]) -> None:
        return self.add(Env.Namespace.VARS, name, var)

    def get_typ(self, name: str) -> Optional[Typ]:
        return self.get(Env.Namespace.TYPS, name)

    def add_typ(self, name: str, typ: Typ) -> None:
        return self.add(Env.Namespace.TYPS, name, typ)

    @staticmethod
    def _resolve_path_segment(
        ns: Env.Namespace, container: Env | Mod, ident: ast.Ident
    ) -> Any:
        match container:
            case Env():
                res = container.get(ns, ident.name)
            case Mod():
                res = next(
                    (
                        item.value
                        for item in container.items
                        if item.access == PUBLIC and item.name == ident.name
                    ),
                    None,
                )

        if res is None:
            raise ItemNotFoundError(ns.item_kind(), ident.name, ident.span)

        return res

    def resolve_var(self, path: ast.Path) -> Value[PtrTyp]:
        return cast(Value[PtrTyp], self.resolve_path(Env.Namespace.VARS, path))

    def resolve_typ(self, path: ast.Path) -> Typ:
        return cast(Typ, self.resolve_path(Env.Namespace.TYPS, path))

    def resolve_path(self, ns: Env.Namespace, path: ast.Path) -> Any:
        assert_ge(len(path.idents), 1)

        container = self
        for ident in path.idents[:-1]:
            container = Env._resolve_path_segment(Env.Namespace.TYPS, container, ident)

        return Env._resolve_path_segment(ns, container, path.idents[-1])


def gep_typ(base_typ: PtrTyp, indeces: Sequence[Value]) -> PtrTyp:
    mut = MUT
    typ = base_typ
    for index in indeces:
        match typ:
            case PtrTyp():
                mut = typ.mut
                typ = typ.pointee_typ
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
            raise NotImplementedError
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
    indeces: Final[tuple[ComptimeInt, ...]]

    @override
    def __init__(
        self, base: ComptimePtr, indeces: Sequence[ComptimeInt], ast: Optional[ast.Ast]
    ) -> None:
        assert_gt(len(indeces), 0)
        assert_eq(indeces[0].value, 0)
        super().__init__(ast)
        self.base = base
        self.indeces = tuple(indeces)

    @override
    def calculate_typ(self) -> PtrTyp:
        return gep_typ(self.base.typ, self.indeces)

    @override
    def load(self) -> ComptimeValue:
        indeces = [i.value for i in self.indeces]
        value = self.base.load()
        for index in indeces[1:]:
            value = checked_cast(value, ComptimeAggregate)
            value = value.get_element(index)
        return value

    @override
    def store(self, value: ComptimeValue) -> None:
        indeces = [i.value for i in self.indeces]
        if len(indeces) == 1:
            self.base.store(value)
            return
        agg = self.base.load()
        for index in indeces[1:-1]:
            agg = checked_cast(agg, ComptimeAggregate)
            agg = agg.get_element(index)
        agg = checked_cast(agg, ComptimeAggregate)
        agg.set_element(value, indeces[-1])

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
    fn: Final[FnSpec]
    pos: Final[int]

    @override
    def __init__(self, fn: FnSpec, pos: int, ast: Optional[ast.Param]) -> None:
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
    indeces: tuple[Value, ...]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        base: Value,
        indeces: Sequence[Value],
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.base = base
        self.indeces = tuple(indeces)

    @override
    def calculate_typ(self) -> PtrTyp:
        base_typ = checked_cast(self.base.typ, PtrTyp)
        return gep_typ(base_typ, self.indeces)


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

    def gep(
        self, base: Value, indeces: list[Value], ast: Optional[ast.Ast]
    ) -> GepInstr:
        return self._add_instr(GepInstr(self, base, indeces, ast))

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


class ExprContext(enum.Enum):
    PLACE = 0
    VALUE = 1


class CfgBuilder:
    fn: Optional[FnSpec]
    cfg: Final[Cfg]
    curr_bb: BasicBlock
    _generate_bb_name: Final[VarNamer]

    def __init__(self, fn: Optional[FnSpec] = None) -> None:
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
        if isinstance(var, FnSpec):
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

        zero = ComptimeInt(USIZE, 0, aa_expr)
        elt_ptr = self.curr_bb.gep(arr_ptr, [zero, index], aa_expr)
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

        zero = ComptimeInt(USIZE, 0, sa_expr)
        index = ComptimeInt(I32, struct_typ.fields[field_name].index, sa_expr)
        field_ptr = self.curr_bb.gep(struct_ptr, [zero, index], sa_expr)

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

        if place.typ == VOID:
            raise AssignToVoidError(ass_ast.place.span)
        place_typ = checked_cast(place.typ, PtrTyp)
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

    def add_edge(self, frm: BasicBlock, to: BasicBlock) -> None:
        assert_in(frm, self.cfg)
        assert_in(to, self.cfg)
        self.cfg.add_edge(frm, to)

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

    def at_exit(self) -> bool:
        return self.curr_bb == self.cfg.exit


class Access(enum.Enum):
    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(ast: Optional[ast.Access]) -> Access:
        if ast is None:
            return PRIVATE
        assert_eq(ast.value, "pub")
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC


@dataclass
class ModItem:
    mod: Final[Mod]
    name: Final[str]
    access: Final[Access]
    value: Final[Value[PtrTyp] | Typ]
    qualify_name: Final[bool] = True

    @property
    def qualified_name(self) -> str:
        if self.qualify_name:
            return f"{self.mod.name}.{self.name}"
        else:
            return self.name


class FnSpec(Generic[FnAstT], ComptimePtr[FnAstT]):
    @override
    def load(self) -> ComptimeValue:
        assert False, "Can't dereference a function pointer"

    @override
    def store(self, value: ComptimeValue) -> None:
        assert False, "Can't dereference a function pointer"

    @override
    def is_temporary(self) -> bool:
        return False

    @cached_property
    def params(self) -> tuple[Param, ...]:
        return self.calculate_params()

    @abstractmethod
    def calculate_params(self) -> tuple[Param, ...]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def fn_typ(self) -> FnTyp:
        return checked_cast(self.typ.pointee_typ, FnTyp)


class BuiltinFn(FnSpec):
    _name: Final[str]
    _fn_typ: Final[FnTyp]
    _build: Final[Callable[[CfgBuilder, Env], None]]
    env: Final[Env]

    @override
    def __init__(
        self,
        name: str,
        typ: FnTyp,
        build: Callable[[CfgBuilder, Env], None],
        e: Env,
    ) -> None:
        super().__init__(ast=None)
        self._name = name
        self._fn_typ = typ
        self._build = build
        self.env = e.new_child()

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self._fn_typ, CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        return tuple(
            Param(self, pos, None) for pos in range(len(self._fn_typ.param_typs))
        )

    @override
    def name(self) -> str:
        return self._name

    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        self._build(builder, self.env)
        return builder.cfg


class NonBuiltinFnSpec(Generic[FnAstT], FnSpec[FnAstT]):
    env: Final[Env]

    @override
    def __init__(self, ast: FnAstT, e: Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()

    @override
    def calculate_typ(self) -> PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            ret_typ = Typ.from_ast(self.ast.ret_typ, self.env)

        param_typs = (
            Typ.from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        )
        return PtrTyp.get_or_create(
            FnTyp.get_or_create(ret_typ, tuple(param_typs)), CONST
        )

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        return tuple(
            Param(self, pos, param_ast) for pos, param_ast in enumerate(self.ast.params)
        )

    @override
    def name(self) -> str:
        assert self.ast is not None
        return self.ast.name.name


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    pass


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        return builder.cfg


class ModVar(ComptimePtr[ast.VarDefn]):
    env: Final[Env]
    mut: Final[Mutability]

    @override
    def __init__(self, ast: ast.VarDefn, e: Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()
        self.mut = Mutability.from_ast(ast.let_stmt.mut)

    @override
    def load(self) -> ComptimeValue:
        return self.initializer

    @override
    def store(self, value: ComptimeValue) -> None:
        assert False, "Cannot set mod var at comptime"

    @override
    def is_temporary(self) -> bool:
        return False

    @cached_property
    def initializer(self) -> ComptimeValue:
        # TODO: detect recursion
        assert self.ast is not None
        return comptime.Interpreter(self.cfg, (), ()).eval()

    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder()
        builder.build_var_initializer(opt_unwrap(self.ast), self.env)
        return builder.cfg

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self.initializer.typ, self.mut)


class Mod(Typ):
    _name: Final[str]
    ast: Final[ast.Mod]
    items: Final[list[ModItem]]
    env: Final[Env]

    @override
    def __init__(self, name: str, mod_ast: ast.Mod) -> None:
        builtin_env = Env()
        self._name = name
        self.ast = mod_ast
        self.items = []
        self.env = builtin_env.new_child()

        builtin_env.add_typ("usize", USIZE)
        builtin_env.add_typ("isize", ISIZE)
        builtin_env.add_typ("bool", BOOL)

        for defn_ast in mod_ast.defns:
            self._build_defn(defn_ast)

    @property
    @override
    def name(self) -> str:
        return self._name

    def _build_defn(self, defn_ast: ast.Defn) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(
                    defn_ast.let_stmt.ident.name,
                    Access.from_ast(defn_ast.access),
                    ModVar(defn_ast, self.env),
                )
            case ast.FnDecl():
                self._add_item(
                    defn_ast.name.name,
                    Access.PUBLIC,
                    FnDecl(defn_ast, self.env),
                    False,
                )
            case ast.FnDefn():
                qualify_name = self.name != "main" or defn_ast.name.name != "main"
                self._add_item(
                    defn_ast.name.name,
                    Access.from_ast(defn_ast.access),
                    Fn(defn_ast, self.env),
                    qualify_name,
                )
            case ast.StructDefn():
                self._add_item(
                    defn_ast.ident.name,
                    Access.from_ast(defn_ast.access),
                    StructTyp.create(defn_ast, self.env),
                )
            case ast.Import():
                mod_name = defn_ast.ident.name
                mod_path = defn_ast.span.file.path.with_stem(mod_name)
                if not mod_path.exists():
                    raise ModDoesNotExistError(mod_name, defn_ast.ident.span)
                mod = main.compile_to_ir(SrcFile(mod_path))
                self._add_item(mod_name, PRIVATE, mod)
            case _:
                raise NotImplementedError

    def _add_item(
        self,
        name: str,
        access: Access,
        value: Value[PtrTyp] | Typ,
        qualify_name: bool = True,
    ) -> None:
        v = ModItem(self, name, access, value, qualify_name)
        self.items.append(v)
        if isinstance(value, Value):
            self.env.add_var(name, value)
        else:
            value = checked_cast(value, Typ)
            self.env.add_typ(name, value)
