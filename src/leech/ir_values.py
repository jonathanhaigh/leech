# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The IR data model: values, instructions, basic blocks, and control-flow graphs."""

import abc
import functools
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Final, Optional, Self, override

import more_itertools
import networkx as nx

from leech import asserts, ast, errors, opt_util, src, typs

if TYPE_CHECKING:
    # Importing ir_module at runtime would cycle through its ComptimePtr base.
    from leech import ir_module


def _gep_typ(base_typ: typs.PtrTyp, index: Value) -> typs.PtrTyp:
    """Return an indexed element pointer, tightening const field mutability."""
    mut = base_typ.mut
    typ = base_typ.pointee_typ
    match typ:
        case typs.ArrayTyp():
            typ = typ.element_typ
        case typs.StructTyp():
            index = asserts.checked_cast(
                index, ComptimeInt, "struct indeces must be comptime known"
            )

            field = more_itertools.nth(typ.fields.values(), index.value)
            assert field is not None, f"invalid field index {index.value} into {typ.name}"
            if field.mut == typs.CONST:
                mut = typs.CONST
            typ = field.typ
        case _:
            raise AssertionError(f"can't index into pointee type {typ}")

    return typs.PtrTyp.get_or_create(typ, mut)


class Value[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](abc.ABC):
    """Base class for typed IR values with an optional source node."""

    ast: Final[Optional[AstT_co]]

    def __init__(self, ast_node: Optional[AstT_co]) -> None:
        self.ast = ast_node

    @functools.cached_property
    def typ(self) -> TypT_co:
        """This value's lazily computed type."""
        return self.calculate_typ()

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The source location of this value's AST node, if present."""
        return opt_util.opt_map(self.ast, lambda x: x.span)

    @abc.abstractmethod
    def calculate_typ(self) -> TypT_co:
        """Compute this value's type."""


class ComptimeValue[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](
    Value[TypT_co, AstT_co]
):
    """Base class for values whose contents are known at compile time."""

    def copy(self) -> Self:
        """Return an independent copy, or ``self`` for immutable values."""
        # Most ComptimeValues are immutable so real copying is not required
        return self


class ComptimeInt(ComptimeValue[typs.IntTyp]):
    """A compile-time-known integer already canonicalized into its type's range."""

    _typ: Final[typs.IntTyp]
    value: Final[int]

    @override
    def __init__(self, typ: typs.IntTyp, value: int, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self._typ = typ
        assert typ.fits(value), f"{value} does not fit in type {typ.name}"
        self.value = value

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return self._typ


class ComptimeEnum(ComptimeValue[typs.EnumTyp]):
    """A compile-time-known enum value - one of its type's variants."""

    _typ: Final[typs.EnumTyp]
    value: Final[int]

    @override
    def __init__(self, typ: typs.EnumTyp, value: int, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self._typ = typ
        self.value = value

    @override
    def calculate_typ(self) -> typs.EnumTyp:
        return self._typ


class ComptimeBool(ComptimeValue[typs.BoolTyp]):
    """A compile-time-known boolean value."""

    value: Final[bool]

    def __init__(self, value: bool, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self.value = value

    @override
    def calculate_typ(self) -> typs.BoolTyp:
        return typs.BOOL


class ComptimeCStr(ComptimeValue[typs.PtrTyp]):
    """A compile-time-known, nul-terminated C string constant."""

    value: Final[bytearray]
    initializer_typ: Final[typs.ArrayTyp]

    def __init__(self, value: str, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self.value = bytearray(value.encode() + b"\0")
        self.initializer_typ = typs.ArrayTyp.of_length(typs.U8, len(self.value))

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.CSTR


class ComptimeAggregate[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](
    ComptimeValue[TypT_co, AstT_co]
):
    """Base class for compile-time-known array and struct values."""

    @abc.abstractmethod
    def __len__(self) -> int:
        pass

    @abc.abstractmethod
    def get_element(self, index: int) -> ComptimeValue:
        """Return the element at an array or struct index."""

    @abc.abstractmethod
    def set_element(self, value: ComptimeValue, index: int) -> None:
        """Set the element at an array or struct index."""

    def values(self) -> Sequence[ComptimeValue]:
        """All of this aggregate's elements, in index order."""
        return [self.get_element(i) for i in range(len(self))]

    def _check_index(self, index: int) -> None:
        asserts.assert_ge(index, 0)
        asserts.assert_lt(index, len(self))


class ComptimeArray(ComptimeAggregate[typs.ArrayTyp]):
    """A compile-time-known array value."""

    elements: Final[list[ComptimeValue]]
    _typ: Final[typs.ArrayTyp]

    @override
    def __init__(
        self, typ: typs.ArrayTyp, elements: list[ComptimeValue], ast_node: Optional[ast.Ast]
    ) -> None:
        asserts.assert_eq(typ.length_value, len(elements))
        asserts.assert_all_eq([elt.typ for elt in elements], typ.element_typ)
        super().__init__(ast_node)
        self._typ = typ
        self.elements = elements

    @override
    def __len__(self) -> int:
        return len(self.elements)

    @override
    def calculate_typ(self) -> typs.ArrayTyp:
        return self._typ

    @override
    def get_element(self, index: int) -> ComptimeValue:
        self._check_index(index)
        return self.elements[index]

    @override
    def set_element(self, value: ComptimeValue, index: int) -> None:
        self._check_index(index)
        self.elements[index] = value

    @override
    def copy(self) -> ComptimeArray:
        return ComptimeArray(self._typ, [elt.copy() for elt in self.elements], self.ast)


class ComptimeStruct(ComptimeAggregate[typs.StructTyp]):
    """A compile-time-known struct value."""

    fields: Final[dict[str, ComptimeValue]]
    _typ: Final[typs.StructTyp]

    @override
    def __init__(
        self, typ: typs.StructTyp, fields: dict[str, ComptimeValue], ast_node: Optional[ast.Ast]
    ) -> None:
        asserts.assert_eq(
            {k: v.typ for k, v in typ.fields.items()},
            {k: v.typ for k, v in fields.items()},
        )
        super().__init__(ast_node)
        self._typ = typ
        self.fields = fields

    @override
    def __len__(self) -> int:
        return len(self.fields)

    @override
    def values(self) -> Sequence[ComptimeValue]:
        return [self.fields[name] for name in self._typ.fields]

    @override
    def calculate_typ(self) -> typs.StructTyp:
        return self._typ

    @override
    def get_element(self, index: int) -> ComptimeValue:
        self._check_index(index)
        field_spec = more_itertools.nth(self._typ.fields.values(), index)
        assert field_spec is not None
        return self.fields[field_spec.name]

    @override
    def set_element(self, value: ComptimeValue, index: int) -> None:
        self._check_index(index)
        field_spec = more_itertools.nth(self._typ.fields.values(), index)
        assert field_spec is not None
        self.fields[field_spec.name] = value

    @override
    def copy(self) -> ComptimeStruct:
        return ComptimeStruct(self._typ, {k: v.copy() for k, v in self.fields.items()}, self.ast)


class ComptimePtr[AstT_co: ast.Ast = ast.Ast](ComptimeValue[typs.PtrTyp, AstT_co]):
    """Base class for compile-time-known pointer values.

    Unlike a runtime pointer (see ``AllocaInstr``), a compile-time
    pointer can be directly loaded from and stored to, since the pointee
    it refers to is itself tracked as a Python object rather than living
    in memory.
    """

    @abc.abstractmethod
    def load(self) -> ComptimeValue:
        """Read the value currently pointed to."""

    @abc.abstractmethod
    def store(self, value: ComptimeValue) -> None:
        """Overwrite the value pointed to."""

    @abc.abstractmethod
    def is_temporary(self) -> bool:
        """Whether this pointer refers to a value with no address at runtime.

        Used to reject taking the address of, or returning a pointer
        into, a compile-time-only temporary (see
        ``errors.CannotTakeAddressOfComptimeValueError``).
        """


class ComptimeAlloc(ComptimePtr):
    """A compile-time-known pointer to a freshly-allocated, uninitialized value."""

    value: ComptimeValue
    mut: Final[typs.Mutability]

    @override
    def __init__(self, typ: typs.Typ, mut: typs.Mutability, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self.mut = mut
        self.value = UndefValue(typ, ast_node)

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp.get_or_create(self.value.typ, self.mut)

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
    """A compile-time-known pointer into an aggregate, e.g. ``&arr[i]`` or
    ``&s.field``.
    """

    base: Final[ComptimePtr]
    index: Final[ComptimeInt]

    @override
    def __init__(self, base: ComptimePtr, index: ComptimeInt, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self.base = base
        self.index = index

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return _gep_typ(self.base.typ, self.index)

    @override
    def load(self) -> ComptimeValue:
        agg = asserts.checked_cast(self.base.load(), ComptimeAggregate)
        return agg.get_element(self.index.value)

    @override
    def store(self, value: ComptimeValue) -> None:
        agg = asserts.checked_cast(self.base.load(), ComptimeAggregate)
        agg.set_element(value, self.index.value)

    @override
    def is_temporary(self) -> bool:
        return self.base.is_temporary()


class VoidValue(ComptimeValue[typs.VoidTyp]):
    """The single, valueless result of a void-typed expression."""

    @override
    def __init__(self, ast_node: Optional[ast.Ast]):
        super().__init__(ast_node)

    @override
    def calculate_typ(self) -> typs.VoidTyp:
        return typs.VOID


class NeverValue(ComptimeValue[typs.NeverTyp]):
    """The valueless result of a block that ends already terminated.

    Used for a block with no tail expression whose statements already
    diverged (e.g. ended in ``return``), as opposed to ``VoidValue``
    for one that genuinely falls off the end. Unlike the ``never``-typed
    terminator instructions (``RetInstr``, ``BranchInstr``,
    ``UnreachableInstr``), this adds nothing to the block: one of
    those has already terminated it by the time this is used.
    """

    @override
    def __init__(self, ast_node: Optional[ast.Ast]):
        super().__init__(ast_node)

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class UndefValue(ComptimeValue):
    """A placeholder for a not-yet-initialized element of an aggregate being built."""

    _typ: Final[typs.Typ]

    @override
    def __init__(self, typ: typs.Typ, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(ast_node)
        self._typ = typ

    @override
    def calculate_typ(self) -> typs.Typ:
        return self._typ


class Param(Value[typs.Typ, ast.Param | ast.Receiver]):
    """A formal parameter of a function, including a method's receiver.

    Its AST node is an ``ast.Receiver`` at position 0 for a method's
    ``self``, otherwise an ``ast.Param``.
    """

    fn: Final[ir_module.FnSymbol | ir_module.FnInstance]
    pos: Final[int]

    @override
    def __init__(
        self,
        fn: ir_module.FnSymbol | ir_module.FnInstance,
        pos: int,
        ast_node: Optional[ast.Param | ast.Receiver],
    ) -> None:
        super().__init__(ast_node)
        self.fn = fn
        self.pos = pos

    @override
    def calculate_typ(self) -> typs.Typ:
        asserts.assert_gt(len(self.fn.fn_typ.param_typs), self.pos)
        return self.fn.fn_typ.param_typs[self.pos]


class Instr[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](Value[TypT_co, AstT_co]):
    """Base class for a single instruction within a ``BasicBlock``."""

    bb: Final[BasicBlock]

    @override
    def __init__(self, bb: BasicBlock, ast_node: Optional[AstT_co]) -> None:
        super().__init__(ast_node)
        self.bb = bb

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        """This instruction's kind, as used in ``BasicBlock``'s textual dump."""
        return type(self).__name__


class BinOpInstr(Instr):
    """Base class for binary arithmetic instructions."""

    lhs: Final[Value]
    rhs: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> typs.Typ:
        asserts.assert_eq(self.lhs.typ, self.rhs.typ)
        return self.lhs.typ


class AddInstr(BinOpInstr):
    """Integer addition."""


class SubInstr(BinOpInstr):
    """Integer subtraction."""


class MulInstr(BinOpInstr):
    """Integer multiplication."""


class SdivInstr(BinOpInstr):
    """Signed integer division."""


class UdivInstr(BinOpInstr):
    """Unsigned integer division."""


class NegInstr(Instr):
    """Integer negation."""

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.Typ:
        return self.operand.typ


class NotInstr(Instr):
    """Boolean negation."""

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.Typ:
        return typs.BOOL


class IcmpInstr(Instr):
    """Base class for integer comparison instructions (``op`` is e.g. ``"<"`` or ``"=="``)."""

    op: Final[str]
    lhs: Final[Value]
    rhs: Final[Value]

    @override
    def __init__(
        self, bb: BasicBlock, op: str, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast_node)
        self.op = op
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> typs.Typ:
        return typs.BOOL


class IcmpSignedInstr(IcmpInstr):
    """A comparison between signed integers."""


class IcmpUnsignedInstr(IcmpInstr):
    """A comparison between unsigned integers."""


class CheckedAddInstr(AddInstr):
    """Integer addition, paired with an ``OverflowFlagInstr`` that
    reports whether it overflowed.

    Behaves exactly like a plain ``AddInstr`` - comptime evaluation,
    in particular, treats it as one unchanged (inherited, not overridden) -
    and exists purely so codegen can tell the two apart: this lowers to
    ``llvm.{s,u}add.with.overflow`` instead of plain ``add``, computing the
    result and detecting overflow in a single operation (see
    ``ir_builder.CfgBuilder._build_checked_bin_op`` and
    ``codegen.Compiler``), rather than needing two separate
    instructions to do each.
    """


class CheckedSubInstr(SubInstr):
    """Integer subtraction; see ``CheckedAddInstr``."""


class CheckedMulInstr(MulInstr):
    """Integer multiplication; see ``CheckedAddInstr``."""


#: Any instruction ``BasicBlock.checked_add``/``checked_sub``/``checked_mul``
#: can build - the only instructions ``OverflowFlagInstr`` can pair with.
type CheckedBinOpInstr = CheckedAddInstr | CheckedSubInstr | CheckedMulInstr


class OverflowFlagInstr(Instr[typs.BoolTyp]):
    """Whether a ``CheckedBinOpInstr`` overflowed.

    ``checked_op`` must already be in ``bb``'s ``Cfg``.
    """

    checked_op: Final[CheckedBinOpInstr]

    @override
    def __init__(
        self, bb: BasicBlock, checked_op: CheckedBinOpInstr, ast_node: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast_node)
        self.checked_op = checked_op

    @override
    def calculate_typ(self) -> typs.BoolTyp:
        return typs.BOOL


class LoadInstr(Instr):
    """Reads the value pointed to by a pointer."""

    src: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, src_ptr: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        asserts.checked_cast(src_ptr.typ, typs.PtrTyp)
        self.src = src_ptr

    @override
    def calculate_typ(self) -> typs.Typ:
        src_typ = asserts.checked_cast(self.src.typ, typs.PtrTyp)
        return src_typ.pointee_typ


class IntExtInstr(Instr[typs.IntTyp]):
    """Widens an integer to a type that can represent all of its values.

    Only ever built for a legal widening coercion (see
    ``typs.IntTyp.coerces_to``), so it never changes the value -
    which of LLVM's sign- and zero-extend it lowers to follows from
    whether ``value``'s type is signed.
    """

    value: Final[Value]
    _typ: Final[typs.IntTyp]

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, typ: typs.IntTyp, ast_node: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast_node)
        src_typ = asserts.checked_cast(value.typ, typs.IntTyp)
        assert src_typ.coerces_to(typ), f'"{src_typ.name}" does not widen to "{typ.name}"'
        self.value = value
        self._typ = typ

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return self._typ


class AllocaInstr(Instr[typs.PtrTyp]):
    """Allocates space for a value on the stack and returns a pointer to it."""

    allocated_typ: Final[typs.Typ]
    mut: Final[typs.Mutability]
    count: Final[int]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        typ: typs.Typ,
        mut: typs.Mutability,
        count: int,
        ast_node: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast_node)
        self.allocated_typ = typ
        self.mut = mut
        self.count = count

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp.get_or_create(self.allocated_typ, self.mut)


class StoreInstr(Instr[typs.VoidTyp]):
    """Writes a value through a pointer."""

    value: Final[Value]
    dest: Final[Value]

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, dest: Value, ast_node: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast_node)
        dest_typ = asserts.checked_cast(dest.typ, typs.PtrTyp)
        asserts.assert_eq(value.typ, dest_typ.pointee_typ)
        self.value = value
        self.dest = dest

    @override
    def calculate_typ(self) -> typs.VoidTyp:
        return typs.VOID


class GepInstr(Instr[typs.PtrTyp]):
    """Computes a pointer to an element of an array or struct, without reading it.

    Corresponds to LLVM's ``getelementptr`` instruction, but simplified to
    a single index: unlike ``getelementptr``, there is no separate leading
    index into ``base`` itself (which LLVM requires to always be ``0`` for
    non-array pointer accesses); ``index`` indexes directly into the
    pointee.
    """

    base: Final[Value]
    index: Final[Value]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        base: Value,
        index: Value,
        ast_node: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast_node)
        self.base = base
        self.index = index

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        base_typ = asserts.checked_cast(self.base.typ, typs.PtrTyp)
        return _gep_typ(base_typ, self.index)


class SizeOfInstr(Instr[typs.IntTyp]):
    """Computes the size in bytes of a type.

    Backs the ``__size_of`` compiler intrinsic (see
    ``ir_builtins.SizeOfIntrinsicFn``). Lowers (see
    ``codegen``) to the portable ``getelementptr``/``ptrtoint``
    idiom, rather than a hand-computed size, so it always agrees with
    LLVM's own (platform-dependent, padding-including) notion of a type's
    size.
    """

    sized_typ: Final[typs.Typ]

    @override
    def __init__(self, bb: BasicBlock, sized_typ: typs.Typ, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.sized_typ = sized_typ

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return typs.USIZE


class PtrCastInstr(Instr[typs.PtrTyp]):
    """Reinterprets a pointer as a different pointer type.

    Backs the ``__ptr_cast_mut`` compiler intrinsic (see
    ``ir_builtins.PtrCastMutIntrinsicFn``). Lowers to a real
    LLVM ``bitcast``, not a no-op - unlike a mut-to-const pointer coercion,
    the source and target LLVM pointer types can genuinely differ here.
    """

    operand: Final[Value]
    target_typ: Final[typs.PtrTyp]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        operand: Value,
        target_typ: typs.PtrTyp,
        ast_node: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast_node)
        asserts.checked_cast(operand.typ, typs.PtrTyp)
        self.operand = operand
        self.target_typ = target_typ

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return self.target_typ


class EnumToIntInstr(Instr[typs.IntTyp]):
    """Reads an enum value as its backing integer type.

    Backs the ``__enum_to_int`` compiler intrinsic (see
    ``ir_builtins.EnumToIntIntrinsicFn``). A no-op at codegen: an
    enum's own LLVM type already *is* its backing type's.
    """

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        asserts.checked_cast(operand.typ, typs.EnumTyp)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return asserts.checked_cast(self.operand.typ, typs.EnumTyp).backing_typ


class IsNullInstr(Instr[typs.BoolTyp]):
    """Tests whether a pointer is null.

    Backs the ``__is_null`` compiler intrinsic (see
    ``ir_builtins.IsNullIntrinsicFn``) - the only way a null
    pointer is ever produced or observed in Leech, since there is no
    null-pointer literal or ``==``/``!=`` operator on pointers.
    """

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        asserts.checked_cast(operand.typ, typs.PtrTyp)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.BoolTyp:
        return typs.BOOL


class InsertValueInstr(Instr):
    """Returns a copy of an aggregate value with one element replaced."""

    aggregate: Final[Value]
    value: Final[Value]
    index: Final[ComptimeInt]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        aggregate: Value,
        value: Value,
        index: ComptimeInt,
        ast_node: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast_node)
        self.aggregate = aggregate
        self.value = value
        self.index = index

    @override
    def calculate_typ(self) -> typs.Typ:
        return self.aggregate.typ


class CallInstr(Instr[typs.Typ, ast.CallExpr]):
    """Calls a function."""

    callee: Final[Value]
    args: Final[tuple[Value, ...]]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        callee: Value,
        args: tuple[Value, ...],
        ast_node: Optional[ast.CallExpr],
    ) -> None:
        super().__init__(bb, ast_node)
        self.callee = callee
        self.args = args

    @override
    def calculate_typ(self) -> typs.Typ:
        callee_typ = asserts.checked_cast(self.callee.typ, typs.PtrTyp)
        fn_typ = asserts.checked_cast(callee_typ.pointee_typ, typs.FnTyp)
        return fn_typ.ret_typ


class PhiInstr(Instr):
    """Selects a value depending on which predecessor block control arrived from."""

    incoming: Final[dict[BasicBlock, Value]]

    @override
    def __init__(
        self, bb: BasicBlock, incoming: dict[BasicBlock, Value], ast_node: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast_node)
        self.incoming = incoming

    @override
    def calculate_typ(self) -> typs.Typ:
        values = list(self.incoming.values())
        asserts.assert_gt(len(values), 0)
        first_typ = values[0].typ
        for value in values:
            asserts.assert_eq(value.typ, first_typ)
        return first_typ


class BranchInstr(Instr[typs.NeverTyp]):
    """An unconditional branch to another basic block; terminates its block."""

    target: Final[BasicBlock]

    @override
    def __init__(self, bb: BasicBlock, target: BasicBlock, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.target = target

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class CbranchInstr(Instr[typs.NeverTyp]):
    """A conditional branch to one of two basic blocks; terminates its block."""

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
        ast_node: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast_node)
        self.condition = condition
        self.true_target = true_target
        self.false_target = false_target

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class RetInstr(Instr[typs.NeverTyp]):
    """Returns a value from the current function; terminates its block."""

    value: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, value: Value, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)
        self.value = value

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class UnreachableInstr(Instr[typs.NeverTyp]):
    """Marks a point control flow can never reach; terminates its block."""

    @override
    def __init__(self, bb: BasicBlock, ast_node: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast_node)

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class BasicBlock:
    """A straight-line sequence of instructions ending in at most one terminator.

    Provides one factory method per instruction kind (``add``,
    ``load``, ``branch``, etc.), each of which constructs the
    instruction, appends it to ``instrs``, and returns it. Once a
    terminator instruction (a branch, return, or unreachable) has been
    added, further additions are silently dropped (after a one-time
    unreachable-code warning) rather than appended, since LLVM does not
    allow instructions after a block's terminator.

    ``name`` is used as this block's label in the generated LLVM IR.
    """

    name: Final[str]
    instrs: Final[list[Instr]]
    terminated: bool
    _warned_unreachable: bool

    def __init__(self, name: str) -> None:
        self.name = name
        self.instrs = []
        self.terminated = False
        self._warned_unreachable = False

    def __str__(self) -> str:
        """A textual dump of this block's label and instructions, one per line."""
        instrs = "".join(f"\n{instr}" for instr in self.instrs)
        return f"{self.name}:{instrs}"

    def _add_instr[T: Instr](self, instr: T, terminate: bool = False) -> T:
        if self.terminated:
            if not self._warned_unreachable:
                assert instr.ast is not None, "Shouldn't get unreachable code in builtins"
                errors.register_error(
                    errors.UnreachableCodeWarning(instr.ast.diag_str(), instr.ast.span)
                )
                self._warned_unreachable = True

            return instr
        self.instrs.append(instr)
        if terminate:
            self.terminated = True
        return instr

    def add(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> AddInstr:
        return self._add_instr(AddInstr(self, lhs, rhs, ast_node))

    def sub(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> SubInstr:
        return self._add_instr(SubInstr(self, lhs, rhs, ast_node))

    def mul(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> MulInstr:
        return self._add_instr(MulInstr(self, lhs, rhs, ast_node))

    def checked_add(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> CheckedAddInstr:
        return self._add_instr(CheckedAddInstr(self, lhs, rhs, ast_node))

    def checked_sub(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> CheckedSubInstr:
        return self._add_instr(CheckedSubInstr(self, lhs, rhs, ast_node))

    def checked_mul(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> CheckedMulInstr:
        return self._add_instr(CheckedMulInstr(self, lhs, rhs, ast_node))

    def sdiv(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> SdivInstr:
        return self._add_instr(SdivInstr(self, lhs, rhs, ast_node))

    def udiv(self, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]) -> UdivInstr:
        return self._add_instr(UdivInstr(self, lhs, rhs, ast_node))

    def neg(self, operand: Value, ast_node: Optional[ast.Ast]) -> NegInstr:
        return self._add_instr(NegInstr(self, operand, ast_node))

    def not_(self, operand: Value, ast_node: Optional[ast.Ast]) -> NotInstr:
        return self._add_instr(NotInstr(self, operand, ast_node))

    def icmp_signed(
        self, op: str, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]
    ) -> IcmpSignedInstr:
        return self._add_instr(IcmpSignedInstr(self, op, lhs, rhs, ast_node))

    def icmp_unsigned(
        self, op: str, lhs: Value, rhs: Value, ast_node: Optional[ast.Ast]
    ) -> IcmpUnsignedInstr:
        return self._add_instr(IcmpUnsignedInstr(self, op, lhs, rhs, ast_node))

    def overflow_flag(
        self, checked_op: CheckedBinOpInstr, ast_node: Optional[ast.Ast]
    ) -> OverflowFlagInstr:
        return self._add_instr(OverflowFlagInstr(self, checked_op, ast_node))

    def phi(self, incoming: dict[BasicBlock, Value], ast_node: Optional[ast.Ast]) -> PhiInstr:
        return self._add_instr(PhiInstr(self, incoming, ast_node))

    def load(self, src_ptr: Value, ast_node: Optional[ast.Ast]) -> LoadInstr:
        return self._add_instr(LoadInstr(self, src_ptr, ast_node))

    def int_ext(self, value: Value, typ: typs.IntTyp, ast_node: Optional[ast.Ast]) -> IntExtInstr:
        return self._add_instr(IntExtInstr(self, value, typ, ast_node))

    def alloca(
        self, typ: typs.Typ, mut: typs.Mutability, count: int, ast_node: Optional[ast.Ast]
    ) -> AllocaInstr:
        return self._add_instr(AllocaInstr(self, typ, mut, count, ast_node))

    def store(self, value: Value, dest: Value, ast_node: Optional[ast.Ast]) -> StoreInstr:
        return self._add_instr(StoreInstr(self, value, dest, ast_node))

    def gep(self, base: Value, index: Value, ast_node: Optional[ast.Ast]) -> GepInstr:
        return self._add_instr(GepInstr(self, base, index, ast_node))

    def size_of(self, sized_typ: typs.Typ, ast_node: Optional[ast.Ast]) -> SizeOfInstr:
        return self._add_instr(SizeOfInstr(self, sized_typ, ast_node))

    def ptr_cast(
        self, operand: Value, target_typ: typs.PtrTyp, ast_node: Optional[ast.Ast]
    ) -> PtrCastInstr:
        return self._add_instr(PtrCastInstr(self, operand, target_typ, ast_node))

    def is_null(self, operand: Value, ast_node: Optional[ast.Ast]) -> IsNullInstr:
        return self._add_instr(IsNullInstr(self, operand, ast_node))

    def enum_to_int(self, operand: Value, ast_node: Optional[ast.Ast]) -> EnumToIntInstr:
        return self._add_instr(EnumToIntInstr(self, operand, ast_node))

    def insert_value(
        self,
        aggregate: Value,
        value: Value,
        index: ComptimeInt,
        ast_node: Optional[ast.Ast],
    ) -> InsertValueInstr:
        return self._add_instr(InsertValueInstr(self, aggregate, value, index, ast_node))

    def call(
        self, callee: Value, args: tuple[Value, ...], ast_node: Optional[ast.CallExpr]
    ) -> CallInstr:
        return self._add_instr(CallInstr(self, callee, args, ast_node))

    def branch(self, target: BasicBlock, ast_node: Optional[ast.Ast]) -> BranchInstr:
        return self._add_instr(BranchInstr(self, target, ast_node), terminate=True)

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast_node: Optional[ast.Ast],
    ) -> CbranchInstr:
        return self._add_instr(
            CbranchInstr(self, condition, true_target, false_target, ast_node),
            terminate=True,
        )

    def ret(self, value: Value, ast_node: Optional[ast.Ast]) -> RetInstr:
        return self._add_instr(RetInstr(self, value, ast_node), terminate=True)

    def unreachable(self, ast_node: Optional[ast.Ast]) -> UnreachableInstr:
        return self._add_instr(UnreachableInstr(self, ast_node), terminate=True)


class Cfg(nx.DiGraph):
    """A function's or initializer's control-flow graph of basic blocks.

    A thin wrapper around a NetworkX directed graph, adding a distinguished
    entry and exit block.
    """

    _entry: Final[BasicBlock]
    _exit: Final[BasicBlock]

    @override
    def __init__(self) -> None:
        super().__init__()
        self._entry = BasicBlock("entry")
        self._exit = BasicBlock("exit")
        self.add_node(self._entry)
        self.add_node(self._exit)

    @override
    def copy(self, as_view: bool = False) -> Cfg:
        # NetworkX's default Graph.copy() builds the result via
        # `self.__class__()`, which - since Cfg.__init__ always creates
        # fresh entry/exit blocks - would silently produce a copy whose
        # .entry/.exit aren't the copied nodes at all. Same hazard for
        # subgraph() and reverse() below (both also construct a new
        # instance via `self.__class__()`, directly or through a
        # NetworkX view). Nothing needs to copy/view a Cfg today; fail
        # loudly instead of building a graph with a dangling entry/exit.
        raise NotImplementedError("Cfg does not support copy()")

    @override
    def subgraph(self, nodes: Optional[Iterable[BasicBlock]]) -> Cfg:
        raise NotImplementedError("Cfg does not support subgraph()")

    @override
    def reverse(self, copy: bool = True) -> Cfg:
        raise NotImplementedError("Cfg does not support reverse()")

    @property
    def entry(self) -> BasicBlock:
        """The block execution starts at."""
        return self._entry

    @property
    def exit(self) -> BasicBlock:
        """The block all returns are wired to, for reachability analysis."""
        return self._exit
