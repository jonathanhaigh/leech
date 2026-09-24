# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""How LLVM types sit in memory on the compilation target.

Answers where the parts of an aggregate are placed, and builds padded
stand-ins for a layout an ordinary aggregate type cannot express. Knows
nothing about Leech, so it depends only on ``target`` and ``llvmlite.ir``.
"""

import dataclasses
from collections.abc import Iterator, Sequence
from typing import Optional

from llvmlite import ir as ll

from leech import asserts, target


def align_up(offset: int, align: int) -> int:
    """Round ``offset`` up to the next multiple of ``align``."""
    return -(-offset // align) * align


def abi_size(ll_typ: ll.Type, ctx: ll.Context) -> int:
    """How many bytes a value of ``ll_typ`` occupies."""
    return ll_typ.get_abi_size(target.target_data(), context=ctx)


def abi_align(ll_typ: ll.Type, ctx: ll.Context) -> int:
    """How many bytes a value of ``ll_typ`` must be aligned to."""
    return ll_typ.get_abi_alignment(target.target_data(), context=ctx)


def storage_typ(payload_typs: Sequence[ll.Type], ctx: ll.Context) -> ll.ArrayType:
    """A field able to hold any one of ``payload_typs``, keeping its alignment.

    ``[K x iA]`` for the widest alignment ``A`` and enough elements to
    cover the largest payload. An ``iA*8`` element rather than ``i8`` is
    what carries the alignment requirement into a containing struct: a
    byte array would under-align whatever this field belongs to.
    """
    align = 1
    size = 0
    for payload_typ in payload_typs:
        align = max(align, abi_align(payload_typ, ctx))
        size = max(size, abi_size(payload_typ, ctx))
    return ll.ArrayType(ll.IntType(align * 8), -(-size // align))


@dataclasses.dataclass(frozen=True)
class Part:
    """One member of an aggregate: its type, and where it starts."""

    typ: ll.Type
    offset: int


@dataclasses.dataclass(frozen=True)
class Layout:
    """Where each part of an aggregate sits, and how big the whole is.

    Parts are in increasing offset order and need not cover the whole: the
    gaps between them, and any tail, are the padding an ordinary aggregate
    type supplies implicitly and a packed stand-in has to spell out.
    """

    parts: tuple[Part, ...]
    size: int
    #: The context the part types belong to, which measuring them needs.
    ctx: ll.Context = dataclasses.field(compare=False)
    #: The ordinary aggregate type that still describes these parts, when
    #: one does. Handed straight back below, so a packed stand-in is built
    #: only where the parts have outgrown any type LLVM can spell.
    ordinary_typ: Optional[ll.Type] = None

    @staticmethod
    def of_typ(ll_typ: ll.Type, ctx: ll.Context) -> Layout:
        """How an ordinary LLVM aggregate places its elements or fields."""
        match ll_typ:
            case ll.ArrayType():
                stride = abi_size(ll_typ.element, ctx)
                return Layout(
                    tuple(Part(ll_typ.element, index * stride) for index in range(ll_typ.count)),
                    stride * ll_typ.count,
                    ctx,
                    ll_typ,
                )
            case ll.LiteralStructType() | ll.IdentifiedStructType():
                # A packed struct's fields sit end to end, so its layout
                # is not the one derived below - and nothing asks for it.
                asserts.assert_eq(bool(ll_typ.packed), False)
                parts: list[Part] = []
                offset = 0
                max_align = 1
                for field_typ in asserts.checked_cast(ll_typ.elements, tuple):
                    align = abi_align(field_typ, ctx)
                    max_align = max(max_align, align)
                    offset = align_up(offset, align)
                    parts.append(Part(field_typ, offset))
                    offset += abi_size(field_typ, ctx)
                return Layout(tuple(parts), align_up(offset, max_align), ctx, ll_typ)
            case _:
                raise AssertionError(f"{ll_typ} is not an aggregate with parts to lay out")

    def with_typs(self, typs: Sequence[Optional[ll.Type]]) -> Layout:
        """The same offsets and size, with each part's type replaced.

        A replacement keeps its original's offset and must fit in the
        space that original occupied, so the parts after it do not move.
        It may be smaller: a union's storage field narrows to the payload
        actually held, and ``None`` drops the part altogether, leaving
        nothing but padding where it sat. Replacing every part with the
        type it already had leaves the layout, and so any ordinary type it
        has, untouched.
        """
        replaced: list[Part] = []
        unchanged = True
        for part, typ in zip(self.parts, typs, strict=True):
            unchanged = unchanged and typ == part.typ
            if typ is None:
                continue
            asserts.assert_le(abi_size(typ, self.ctx), abi_size(part.typ, self.ctx))
            replaced.append(Part(typ, part.offset))
        return Layout(
            tuple(replaced), self.size, self.ctx, self.ordinary_typ if unchanged else None
        )

    def typ(self) -> ll.Type:
        """A type of this layout's shape: the ordinary one, or a packed stand-in."""
        if self.ordinary_typ is not None:
            return self.ordinary_typ
        return ll.LiteralStructType([typ for _index, typ in self._slots()], packed=True)

    def constant(self, consts: Sequence[ll.Value]) -> ll.Constant:
        """A constant of ``typ``'s shape, carrying one value per part."""
        asserts.assert_eq(len(consts), len(self.parts))
        if self.ordinary_typ is not None:
            return ll.Constant(self.ordinary_typ, list(consts))
        elements: list[ll.Value] = []
        typs: list[ll.Type] = []
        for index, typ in self._slots():
            typs.append(typ)
            elements.append(ll.Constant(typ, ll.Undefined) if index is None else consts[index])
        return ll.Constant(ll.LiteralStructType(typs, packed=True), elements)

    def _slots(self) -> Iterator[tuple[Optional[int], ll.Type]]:
        """Yield the packed stand-in's fields, ``None`` for each run of padding.

        Shared by the type and the constant so the two cannot disagree
        about where the padding goes.
        """
        cursor = 0
        for index, part in enumerate(self.parts):
            asserts.assert_ge(part.offset, cursor)
            if part.offset > cursor:
                yield None, ll.ArrayType(ll.IntType(8), part.offset - cursor)
            yield index, part.typ
            cursor = part.offset + abi_size(part.typ, self.ctx)
        asserts.assert_ge(self.size, cursor)
        if self.size > cursor:
            yield None, ll.ArrayType(ll.IntType(8), self.size - cursor)
