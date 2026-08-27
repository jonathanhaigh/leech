# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Leech-type to LLVM-type mapping, shared by codegen and comptime evaluation.

Deliberately depends on nothing beyond ``typs`` and ``llvmlite.ir``, so it can be imported
from ``comptime.py`` (which runs during IR building, before ``codegen.py`` exists) without a
circular import (``codegen.py`` -> ``ir_module.py`` -> ``comptime.py``).
"""

import weakref

from llvmlite import ir as ll

from leech import typs

#: Struct qualified names currently having their body built, per llvmlite context. Building a
#: struct's body means recursing into its field types, one of which may (through a pointer)
#: refer back to this same struct, or to another struct that itself refers back to this one -
#: this lets that reentrant call get the already-registered, still-opaque identified type
#: back (all a pointer or array element ever needs) instead of trying to build the same body
#: again and recursing forever. Keyed by context, weakly, so an entry disappears once nothing
#: else references its context; this is purely an implementation detail of building recursive
#: struct types, not something callers need to know about or manage.
_building: weakref.WeakKeyDictionary[ll.Context, set[str]] = weakref.WeakKeyDictionary()


def ll_typ(ctx: ll.Context, typ: typs.Typ) -> ll.Type:
    """Map ``typ`` to its LLVM counterpart in ``ctx``.

    A struct instance's identified type is built at most once per ``(ctx, qualified name)``
    and reused after: safe to call repeatedly, from anywhere, for the same struct (however
    deeply nested, or however it's mutually recursive with another struct), in any order.
    """
    match typ:
        case typs.IntTyp():
            return ll.IntType(typ.width)
        case typs.BoolTyp():
            return ll.IntType(1)
        case typs.FnTyp():
            # A `never` return type has no LLVM representation of its own
            # (see the NeverTyp case below): a function declared to
            # return `never` only ever ends its body in `unreachable`,
            # so its LLVM signature can say `void` without a real `ret`
            # instruction ever needing to match it.
            if isinstance(typ.ret_typ, typs.NeverTyp):
                ll_ret_typ = ll.VoidType()
            else:
                ll_ret_typ = ll_typ(ctx, typ.ret_typ)
            return ll.FunctionType(
                ll_ret_typ, (ll_typ(ctx, param_typ) for param_typ in typ.param_typs)
            )
        case typs.PtrTyp():
            # Typed pointers are deprecated in llvmlite (and LLVM IR) but
            # llvmlite seems to rely on pointer types in a bunch of places
            # (call instruction, gep instruction) that's a pain to try to
            # work around, so just use typed pointers for now.
            return ll.PointerType(pointee=ll_typ(ctx, typ.pointee_typ))
        case typs.ArrayTyp():
            return ll.ArrayType(ll_typ(ctx, typ.element_typ), typ.length_value)
        case typs.VoidTyp():
            return ll.VoidType()
        case typs.EnumTyp():
            # No LLVM type of its own - an enum lowers directly to its
            # backing integer type's.
            return ll_typ(ctx, typ.backing_typ)
        case typs.StructTyp():
            return _struct_ll_typ(ctx, typ)
        case typs.NeverTyp():
            raise AssertionError("a never-typed value shouldn't need an LLVM type")
        case _:
            raise AssertionError(f"unhandled type {typ}")


def _struct_ll_typ(ctx: ll.Context, typ: typs.StructTyp) -> ll.Type:
    ll_struct = ctx.get_identified_type(typ.qualified_name)
    if not ll_struct.is_opaque:
        return ll_struct

    building = _building.setdefault(ctx, set())
    if typ.qualified_name in building:
        return ll_struct

    building.add(typ.qualified_name)
    try:
        ll_struct.set_body(*(ll_typ(ctx, field.typ) for field in typ.fields.values()))
    finally:
        building.discard(typ.qualified_name)
    return ll_struct
