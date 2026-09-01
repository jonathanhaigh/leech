# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compiler intrinsics and names ambiently available in every module.

Import this module lazily because its intrinsic classes require ``ir_module`` to be loaded.
"""

from typing import cast, override

from leech import ir_builder, ir_env, ir_loader, ir_module, typs


class SizeOfIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__size_of[T]() usize``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__size_of", ("T",), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        return typs.FnTyp.get_or_create(typs.USIZE, ())

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        size = builder._curr_bb.size_of(cast(typs.TypKind, comptime_args[0]), None)
        builder._curr_bb.ret(size, None)


class PtrCastMutIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__ptr_cast_mut[From, To](p: *mut From) *mut To``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__ptr_cast_mut", ("From", "To"), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        from_typ, to_typ = comptime_params
        return typs.FnTyp.get_or_create(
            typs.PtrTyp.get_or_create(to_typ, typs.MUT),
            (typs.PtrTyp.get_or_create(from_typ, typs.MUT),),
        )

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        assert builder._fn is not None
        param = builder._fn.params[0]
        target_typ = typs.PtrTyp.get_or_create(comptime_args[1], typs.MUT)
        casted = builder._curr_bb.ptr_cast(param, target_typ, None)
        builder._curr_bb.ret(casted, None)


class IsNullIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__is_null[T](p: *mut T) bool``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__is_null", ("T",), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        (t,) = comptime_params
        return typs.FnTyp.get_or_create(typs.BOOL, (typs.PtrTyp.get_or_create(t, typs.MUT),))

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        del comptime_args
        assert builder._fn is not None
        param = builder._fn.params[0]
        result = builder._curr_bb.is_null(param, None)
        builder._curr_bb.ret(result, None)


class EnumToIntIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__enum_to_int[E](v: E) <E's backing type>``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__enum_to_int", ("E",), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        (e_typ,) = comptime_params
        return typs.FnTyp.get_or_create(typs.EnumBackingTyp.get_or_create(e_typ), (e_typ,))

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        del comptime_args
        assert builder._fn is not None
        param = builder._fn.params[0]
        result = builder._curr_bb.enum_to_int(param, None)
        builder._curr_bb.ret(result, None)


def register(builtin_env: ir_env.Env, loader: ir_loader.ModLoader) -> None:
    """Bind built-in types and this program's shared intrinsic functions."""
    builtin_env.add_container("usize", typs.USIZE)
    builtin_env.add_container("isize", typs.ISIZE)
    builtin_env.add_container("bool", typs.BOOL)
    builtin_env.add_container("array", typs.ARRAY_TEMPLATE)

    builtin_env.add_var("__size_of", loader.size_of_intrinsic)
    builtin_env.add_var("__ptr_cast_mut", loader.ptr_cast_mut_intrinsic)
    builtin_env.add_var("__is_null", loader.is_null_intrinsic)
    builtin_env.add_var("__enum_to_int", loader.enum_to_int_intrinsic)
