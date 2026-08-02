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
    # `ir_module.FnSpec` (used only in a type annotation below, in `Param`) would
    # otherwise be a real circular import: `ir_module.FnSpec` is defined as a
    # subclass of `ComptimePtr` from this module, so `ir_module` needs this
    # module to have fully executed before it can even be defined. Since the
    # reverse reference here is annotation-only, guarding it behind
    # TYPE_CHECKING avoids the runtime import entirely while keeping the
    # annotation available to the type checker.
    from leech import ir_module


def _gep_typ(base_typ: typs.PtrTyp, index: Value) -> typs.PtrTyp:
    """Compute the pointer type produced by indexing into ``base_typ``.

    :param base_typ: The pointer type being indexed into (pointing to an
        array or struct type).
    :param index: The index value; must be a :class:`ComptimeInt` if
        ``base_typ`` points to a struct.
    :return: A pointer to the indexed array element type or struct field
        type, with the pointer's mutability tightened to const if the
        indexed struct field is const.
    """
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
    """Base class for anything that has a type and a source location: IR
    values, instructions, and function parameters.

    :param ast: The AST node this value was built from, if any (builtin
        values may have none).
    """

    ast: Final[Optional[AstT_co]]

    def __init__(self, ast: Optional[AstT_co]) -> None:
        self.ast = ast

    @functools.cached_property
    def typ(self) -> TypT_co:
        """This value's type, computed once (via :meth:`calculate_typ`) and cached."""
        return self.calculate_typ()

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The source location of :attr:`ast`, if it has one."""
        return opt_util.opt_map(self.ast, lambda x: x.span)

    @abc.abstractmethod
    def calculate_typ(self) -> TypT_co:
        """Compute this value's type; backs the cached :attr:`typ` property."""


class ComptimeValue[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](
    Value[TypT_co, AstT_co]
):
    """Base class for values whose contents are known at compile time."""

    def copy(self) -> Self:
        """Return an independent copy of this value.

        Only meaningfully overridden by mutable aggregates
        (:class:`ComptimeArray`, :class:`ComptimeStruct`); other
        subclasses are immutable and return ``self`` unchanged.
        """
        # Most ComptimeValues are immutable so real copying is not required
        return self


class ComptimeInt(ComptimeValue[typs.IntTyp]):
    """A compile-time-known integer value.

    Callers that construct a ``ComptimeInt`` from a value that isn't
    already known to fit ``typ`` (e.g. from a source-level integer
    literal, or the result of a compile-time arithmetic operation) are
    responsible for validating it themselves first, and raising the
    appropriate diagnostic (:class:`~leech.errors.IntLitOverflowError` or
    :class:`~leech.errors.IntOverflowAtComptimeError` respectively) -
    :meth:`~leech.typs.IntTyp.fits` does the check. This constructor only
    asserts that ``value`` fits, as a last-resort invariant check.

    :param typ: The integer type.
    :param value: The integer value.
    :param ast: The AST node this value was built from, if any.
    """

    _typ: Final[typs.IntTyp]
    value: Final[int]

    @override
    def __init__(self, typ: typs.IntTyp, value: int, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self._typ = typ
        assert typ.fits(value), f"{value} does not fit in type {typ.name}"
        self.value = value

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return self._typ


class ComptimeBool(ComptimeValue[typs.BoolTyp]):
    """A compile-time-known boolean value.

    :param value: The boolean value.
    :param ast: The AST node this value was built from, if any.
    """

    value: Final[bool]

    def __init__(self, value: bool, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.value = value

    @override
    def calculate_typ(self) -> typs.BoolTyp:
        return typs.BOOL


class ComptimeCStr(ComptimeValue[typs.PtrTyp]):
    """A compile-time-known, nul-terminated C string constant.

    :param value: The string's contents (excluding the nul terminator).
    :param ast: The AST node this value was built from, if any.
    """

    value: Final[bytearray]
    initializer_typ: Final[typs.ArrayTyp]

    def __init__(self, value: str, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.value = bytearray(value.encode() + b"\0")
        self.initializer_typ = typs.ArrayTyp.get_or_create(typs.U8, len(self.value))

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
        """Get the element at ``index`` (an array index or struct field index).

        :param index: The zero-based element index.
        :return: The element's value.
        """

    @abc.abstractmethod
    def set_element(self, value: ComptimeValue, index: int) -> None:
        """Set the element at ``index`` (an array index or struct field index).

        :param value: The value to set the element to.
        :param index: The zero-based element index.
        """

    def values(self) -> Sequence[ComptimeValue]:
        """All of this aggregate's elements, in index order."""
        return [self.get_element(i) for i in range(len(self))]

    def _check_index(self, index: int) -> None:
        """Assert that ``index`` is in bounds for this aggregate.

        :param index: The zero-based element index to check.
        :raises AssertionError: If ``index`` is out of bounds.
        """
        asserts.assert_ge(index, 0)
        asserts.assert_lt(index, len(self))


class ComptimeArray(ComptimeAggregate[typs.ArrayTyp]):
    """A compile-time-known array value.

    :param typ: The array type.
    :param elements: The element values, in index order; must match
        ``typ`` in length and element type.
    :param ast: The AST node this value was built from, if any.
    """

    elements: Final[list[ComptimeValue]]
    _typ: Final[typs.ArrayTyp]

    @override
    def __init__(
        self, typ: typs.ArrayTyp, elements: list[ComptimeValue], ast: Optional[ast.Ast]
    ) -> None:
        asserts.assert_eq(typ.length, len(elements))
        asserts.assert_all_eq([elt.typ for elt in elements], typ.element_typ)
        super().__init__(ast)
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
    """A compile-time-known struct value.

    :param typ: The struct type.
    :param fields: The field values, keyed by field name; must match
        ``typ``'s fields in names and types.
    :param ast: The AST node this value was built from, if any.
    """

    fields: Final[dict[str, ComptimeValue]]
    _typ: Final[typs.StructTyp]

    @override
    def __init__(
        self, typ: typs.StructTyp, fields: dict[str, ComptimeValue], ast: Optional[ast.Ast]
    ) -> None:
        asserts.assert_eq(
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

    Unlike a runtime pointer (see :class:`AllocaInstr`), a compile-time
    pointer can be directly loaded from and stored to, since the pointee
    it refers to is itself tracked as a Python object rather than living
    in memory.
    """

    @abc.abstractmethod
    def load(self) -> ComptimeValue:
        """Read the value currently pointed to."""

    @abc.abstractmethod
    def store(self, value: ComptimeValue) -> None:
        """Overwrite the value pointed to.

        :param value: The value to store.
        """

    @abc.abstractmethod
    def is_temporary(self) -> bool:
        """Whether this pointer refers to a value with no address at runtime.

        Used to reject taking the address of, or returning a pointer
        into, a compile-time-only temporary (see
        :class:`~leech.errors.CannotTakeAddressOfComptimeValueError`).
        """


class ComptimeAlloc(ComptimePtr):
    """A compile-time-known pointer to a freshly-allocated, uninitialized value.

    :param typ: The type of the allocated value.
    :param mut: The mutability of the allocated value.
    :param ast: The AST node this value was built from, if any.
    """

    value: ComptimeValue
    mut: Final[typs.Mutability]

    @override
    def __init__(self, typ: typs.Typ, mut: typs.Mutability, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self.mut = mut
        self.value = UndefValue(typ, ast)

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

    :param base: The pointer being indexed into.
    :param index: The array or struct field index.
    :param ast: The AST node this value was built from, if any.
    """

    base: Final[ComptimePtr]
    index: Final[ComptimeInt]

    @override
    def __init__(self, base: ComptimePtr, index: ComptimeInt, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
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
    """The single, valueless result of a void-typed expression.

    :param ast: The AST node this value was built from, if any.
    """

    @override
    def __init__(self, ast: Optional[ast.Ast]):
        super().__init__(ast)

    @override
    def calculate_typ(self) -> typs.VoidTyp:
        return typs.VOID


class NeverValue(ComptimeValue[typs.NeverTyp]):
    """The valueless result of a block that ends already terminated.

    Used for a block with no tail expression whose statements already
    diverged (e.g. ended in ``return``), as opposed to :class:`VoidValue`
    for one that genuinely falls off the end. Unlike the ``never``-typed
    terminator instructions (:class:`RetInstr`, :class:`BranchInstr`,
    :class:`UnreachableInstr`), this adds nothing to the block: one of
    those has already terminated it by the time this is used.

    :param ast: The AST node this value was built from, if any.
    """

    @override
    def __init__(self, ast: Optional[ast.Ast]):
        super().__init__(ast)

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class UndefValue(ComptimeValue):
    """A placeholder for a not-yet-initialized element of an aggregate being built.

    :param typ: The type the eventual value will have.
    :param ast: The AST node this value was built from, if any.
    """

    _typ: Final[typs.Typ]

    @override
    def __init__(self, typ: typs.Typ, ast: Optional[ast.Ast]) -> None:
        super().__init__(ast)
        self._typ = typ

    @override
    def calculate_typ(self) -> typs.Typ:
        return self._typ


class Param(Value[typs.Typ, ast.Param | ast.Receiver]):
    """A formal parameter of a function, including a method's receiver.

    :param fn: The function this is a parameter of.
    :param pos: The parameter's zero-based position in the parameter
        list.
    :param ast: The AST node this value was built from, if any - a
        :class:`~leech.ast.Receiver` at position 0 for a method's ``self``,
        otherwise a :class:`~leech.ast.Param`.
    """

    fn: Final[ir_module.FnSpec]
    pos: Final[int]

    @override
    def __init__(
        self, fn: ir_module.FnSpec, pos: int, ast: Optional[ast.Param | ast.Receiver]
    ) -> None:
        super().__init__(ast)
        self.fn = fn
        self.pos = pos

    @override
    def calculate_typ(self) -> typs.Typ:
        asserts.assert_gt(len(self.fn.fn_typ.param_typs), self.pos)
        return self.fn.fn_typ.param_typs[self.pos]


class Instr[TypT_co: typs.Typ = typs.Typ, AstT_co: ast.Ast = ast.Ast](Value[TypT_co, AstT_co]):
    """Base class for a single instruction within a :class:`BasicBlock`.

    :param bb: The basic block this instruction belongs to.
    :param ast: The AST node this instruction was built from, if any.
    """

    bb: Final[BasicBlock]

    @override
    def __init__(self, bb: BasicBlock, ast: Optional[AstT_co]) -> None:
        super().__init__(ast)
        self.bb = bb

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        """This instruction's kind, as used in :class:`BasicBlock`'s textual dump."""
        return type(self).__name__


class BinOpInstr(Instr):
    """Base class for binary arithmetic instructions.

    :param bb: The basic block this instruction belongs to.
    :param lhs: The left-hand operand; must have the same type as ``rhs``.
    :param rhs: The right-hand operand.
    :param ast: The AST node this instruction was built from, if any.
    """

    lhs: Final[Value]
    rhs: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
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
    """Integer negation.

    :param bb: The basic block this instruction belongs to.
    :param operand: The value to negate.
    :param ast: The AST node this instruction was built from, if any.
    """

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.Typ:
        return self.operand.typ


class NotInstr(Instr):
    """Boolean negation.

    :param bb: The basic block this instruction belongs to.
    :param operand: The value to negate.
    :param ast: The AST node this instruction was built from, if any.
    """

    operand: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, operand: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.operand = operand

    @override
    def calculate_typ(self) -> typs.Typ:
        return typs.BOOL


class IcmpInstr(Instr):
    """Base class for integer comparison instructions.

    :param bb: The basic block this instruction belongs to.
    :param op: The comparison operator, e.g. ``"<"`` or ``"=="``.
    :param lhs: The left-hand operand; must have the same type as ``rhs``.
    :param rhs: The right-hand operand.
    :param ast: The AST node this instruction was built from, if any.
    """

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
    def calculate_typ(self) -> typs.Typ:
        return typs.BOOL


class IcmpSignedInstr(IcmpInstr):
    """A comparison between signed integers."""


class IcmpUnsignedInstr(IcmpInstr):
    """A comparison between unsigned integers."""


class LoadInstr(Instr):
    """Reads the value pointed to by a pointer.

    :param bb: The basic block this instruction belongs to.
    :param src: The pointer to read through.
    :param ast: The AST node this instruction was built from, if any.
    """

    src: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, src: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        asserts.checked_cast(src.typ, typs.PtrTyp)
        self.src = src

    @override
    def calculate_typ(self) -> typs.Typ:
        src_typ = asserts.checked_cast(self.src.typ, typs.PtrTyp)
        return src_typ.pointee_typ


class IntExtInstr(Instr[typs.IntTyp]):
    """Widens an integer to a type that can represent all of its values.

    Only ever built for a legal widening coercion (see
    :meth:`~leech.typs.IntTyp.coerces_to`), so it never changes the value -
    which of LLVM's sign- and zero-extend it lowers to follows from
    whether :attr:`value`'s type is signed.

    :param bb: The basic block this instruction belongs to.
    :param value: The integer to widen.
    :param typ: The integer type to widen it to.
    :param ast: The AST node this instruction was built from, if any.
    """

    value: Final[Value]
    _typ: Final[typs.IntTyp]

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, typ: typs.IntTyp, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        src_typ = asserts.checked_cast(value.typ, typs.IntTyp)
        assert src_typ.coerces_to(typ), f'"{src_typ.name}" does not widen to "{typ.name}"'
        self.value = value
        self._typ = typ

    @override
    def calculate_typ(self) -> typs.IntTyp:
        return self._typ


class AllocaInstr(Instr[typs.PtrTyp]):
    """Allocates space for a value on the stack and returns a pointer to it.

    :param bb: The basic block this instruction belongs to.
    :param typ: The type of value to allocate space for.
    :param mut: The mutability of the returned pointer.
    :param count: The number of contiguous values to allocate space for.
    :param ast: The AST node this instruction was built from, if any.
    """

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
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.allocated_typ = typ
        self.mut = mut
        self.count = count

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp.get_or_create(self.allocated_typ, self.mut)


class StoreInstr(Instr[typs.VoidTyp]):
    """Writes a value through a pointer.

    :param bb: The basic block this instruction belongs to.
    :param value: The value to write; must match ``dest``'s pointee type.
    :param dest: The pointer to write through.
    :param ast: The AST node this instruction was built from, if any.
    """

    value: Final[Value]
    dest: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, value: Value, dest: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
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

    :param bb: The basic block this instruction belongs to.
    :param base: The pointer to the array or struct being indexed into.
    :param index: The array or struct field index into ``base``'s pointee type.
    :param ast: The AST node this instruction was built from, if any.
    """

    base: Final[Value]
    index: Final[Value]

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
    def calculate_typ(self) -> typs.PtrTyp:
        base_typ = asserts.checked_cast(self.base.typ, typs.PtrTyp)
        return _gep_typ(base_typ, self.index)


class InsertValueInstr(Instr):
    """Returns a copy of an aggregate value with one element replaced.

    :param bb: The basic block this instruction belongs to.
    :param aggregate: The aggregate value to copy.
    :param value: The value to insert.
    :param indeces: The path of indices identifying the element to
        replace within nested aggregates.
    :param ast: The AST node this instruction was built from, if any.
    """

    aggregate: Final[Value]
    value: Final[Value]
    indeces: Final[tuple[ComptimeInt, ...]]

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
    def calculate_typ(self) -> typs.Typ:
        return self.aggregate.typ


class CallInstr(Instr[typs.Typ, ast.CallExpr]):
    """Calls a function.

    :param bb: The basic block this instruction belongs to.
    :param callee: The function pointer to call.
    :param args: The argument values, in parameter order.
    :param ast: The AST node this instruction was built from, if any.
    """

    callee: Final[Value]
    args: Final[tuple[Value, ...]]

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
    def calculate_typ(self) -> typs.Typ:
        callee_typ = asserts.checked_cast(self.callee.typ, typs.PtrTyp)
        fn_typ = asserts.checked_cast(callee_typ.pointee_typ, typs.FnTyp)
        return fn_typ.ret_typ


class PhiInstr(Instr):
    """Selects a value depending on which predecessor block control arrived from.

    :param bb: The basic block this instruction belongs to.
    :param incoming: The value to select for each possible predecessor
        block; all values must have the same type.
    :param ast: The AST node this instruction was built from, if any.
    """

    incoming: Final[dict[BasicBlock, Value]]

    @override
    def __init__(
        self, bb: BasicBlock, incoming: dict[BasicBlock, Value], ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
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
    """An unconditional branch to another basic block; terminates its block.

    :param bb: The basic block this instruction belongs to.
    :param target: The block to branch to.
    :param ast: The AST node this instruction was built from, if any.
    """

    target: Final[BasicBlock]

    @override
    def __init__(self, bb: BasicBlock, target: BasicBlock, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.target = target

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class CbranchInstr(Instr[typs.NeverTyp]):
    """A conditional branch to one of two basic blocks; terminates its block.

    :param bb: The basic block this instruction belongs to.
    :param condition: The boolean value to branch on.
    :param true_target: The block to branch to if ``condition`` is true.
    :param false_target: The block to branch to if ``condition`` is
        false.
    :param ast: The AST node this instruction was built from, if any.
    """

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
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class RetInstr(Instr[typs.NeverTyp]):
    """Returns a value from the current function; terminates its block.

    :param bb: The basic block this instruction belongs to.
    :param value: The value to return.
    :param ast: The AST node this instruction was built from, if any.
    """

    value: Final[Value]

    @override
    def __init__(self, bb: BasicBlock, value: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.value = value

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class UnreachableInstr(Instr[typs.NeverTyp]):
    """Marks a point control flow can never reach; terminates its block.

    :param bb: The basic block this instruction belongs to.
    :param ast: The AST node this instruction was built from, if any.
    """

    @override
    def __init__(self, bb: BasicBlock, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)

    @override
    def calculate_typ(self) -> typs.NeverTyp:
        return typs.NEVER


class BasicBlock:
    """A straight-line sequence of instructions ending in at most one terminator.

    Provides one factory method per instruction kind (:meth:`add`,
    :meth:`load`, :meth:`branch`, etc.), each of which constructs the
    instruction, appends it to :attr:`instrs`, and returns it. Once a
    terminator instruction (a branch, return, or unreachable) has been
    added, further additions are silently dropped (after a one-time
    unreachable-code warning) rather than appended, since LLVM does not
    allow instructions after a block's terminator.

    :param name: This block's name, used as its label in the generated
        LLVM IR.
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

    def add(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> AddInstr:
        """Append an :class:`AddInstr` to this block."""
        return self._add_instr(AddInstr(self, lhs, rhs, ast))

    def sub(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SubInstr:
        """Append a :class:`SubInstr` to this block."""
        return self._add_instr(SubInstr(self, lhs, rhs, ast))

    def mul(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> MulInstr:
        """Append a :class:`MulInstr` to this block."""
        return self._add_instr(MulInstr(self, lhs, rhs, ast))

    def sdiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SdivInstr:
        """Append a :class:`SdivInstr` to this block."""
        return self._add_instr(SdivInstr(self, lhs, rhs, ast))

    def udiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> UdivInstr:
        """Append a :class:`UdivInstr` to this block."""
        return self._add_instr(UdivInstr(self, lhs, rhs, ast))

    def neg(self, operand: Value, ast: Optional[ast.Ast]) -> NegInstr:
        """Append a :class:`NegInstr` to this block."""
        return self._add_instr(NegInstr(self, operand, ast))

    def not_(self, operand: Value, ast: Optional[ast.Ast]) -> NotInstr:
        """Append a :class:`NotInstr` to this block."""
        return self._add_instr(NotInstr(self, operand, ast))

    def icmp_signed(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpSignedInstr:
        """Append an :class:`IcmpSignedInstr` to this block."""
        return self._add_instr(IcmpSignedInstr(self, op, lhs, rhs, ast))

    def icmp_unsigned(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpUnsignedInstr:
        """Append an :class:`IcmpUnsignedInstr` to this block."""
        return self._add_instr(IcmpUnsignedInstr(self, op, lhs, rhs, ast))

    def phi(self, incoming: dict[BasicBlock, Value], ast: Optional[ast.Ast]) -> PhiInstr:
        """Append a :class:`PhiInstr` to this block."""
        return self._add_instr(PhiInstr(self, incoming, ast))

    def load(self, src: Value, ast: Optional[ast.Ast]) -> LoadInstr:
        """Append a :class:`LoadInstr` to this block."""
        return self._add_instr(LoadInstr(self, src, ast))

    def int_ext(self, value: Value, typ: typs.IntTyp, ast: Optional[ast.Ast]) -> IntExtInstr:
        """Append an :class:`IntExtInstr` to this block."""
        return self._add_instr(IntExtInstr(self, value, typ, ast))

    def alloca(
        self, typ: typs.Typ, mut: typs.Mutability, count: int, ast: Optional[ast.Ast]
    ) -> AllocaInstr:
        """Append an :class:`AllocaInstr` to this block."""
        return self._add_instr(AllocaInstr(self, typ, mut, count, ast))

    def store(self, value: Value, dest: Value, ast: Optional[ast.Ast]) -> StoreInstr:
        """Append a :class:`StoreInstr` to this block."""
        return self._add_instr(StoreInstr(self, value, dest, ast))

    def gep(self, base: Value, index: Value, ast: Optional[ast.Ast]) -> GepInstr:
        """Append a :class:`GepInstr` to this block."""
        return self._add_instr(GepInstr(self, base, index, ast))

    def insert_value(
        self,
        aggregate: Value,
        value: Value,
        indeces: tuple[ComptimeInt, ...],
        ast: Optional[ast.Ast],
    ) -> InsertValueInstr:
        """Append an :class:`InsertValueInstr` to this block."""
        return self._add_instr(InsertValueInstr(self, aggregate, value, indeces, ast))

    def call(
        self, callee: Value, args: tuple[Value, ...], ast: Optional[ast.CallExpr]
    ) -> CallInstr:
        """Append a :class:`CallInstr` to this block."""
        return self._add_instr(CallInstr(self, callee, args, ast))

    def branch(self, target: BasicBlock, ast: Optional[ast.Ast]) -> BranchInstr:
        """Append a terminating :class:`BranchInstr` to this block."""
        return self._add_instr(BranchInstr(self, target, ast), terminate=True)

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: Optional[ast.Ast],
    ) -> CbranchInstr:
        """Append a terminating :class:`CbranchInstr` to this block."""
        return self._add_instr(
            CbranchInstr(self, condition, true_target, false_target, ast),
            terminate=True,
        )

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> RetInstr:
        """Append a terminating :class:`RetInstr` to this block."""
        return self._add_instr(RetInstr(self, value, ast), terminate=True)

    def unreachable(self, ast: Optional[ast.Ast]) -> UnreachableInstr:
        """Append a terminating :class:`UnreachableInstr` to this block."""
        return self._add_instr(UnreachableInstr(self, ast), terminate=True)


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
