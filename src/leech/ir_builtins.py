# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compiler intrinsics and names ambiently available in every module.

Import this module lazily because its intrinsic classes require ``ir_module`` to be loaded.
"""

from typing import TYPE_CHECKING, Final, Optional, cast, override

from leech import asserts, ir_builder, ir_env, ir_module, typs

if TYPE_CHECKING:
    from leech import compilation


class SizeOfIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__size_of[T]() usize``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__size_of", ("T",), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        return typs.FnTyp(typs.USIZE, ())

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
        return typs.FnTyp(
            typs.PtrTyp(to_typ, typs.MUT),
            (typs.PtrTyp(from_typ, typs.MUT),),
        )

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        assert builder._fn is not None
        param = builder._fn.params[0]
        target_typ = typs.PtrTyp(comptime_args[1], typs.MUT)
        casted = builder._curr_bb.ptr_cast(param, target_typ, None)
        builder._curr_bb.ret(casted, None)


class IsNullIntrinsicFn(ir_module.IntrinsicFnSymbol):
    """``__is_null[T](p: *mut T) bool``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__is_null", ("T",), e)

    @override
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        (t,) = comptime_params
        return typs.FnTyp(typs.BOOL, (typs.PtrTyp(t, typs.MUT),))

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
        return typs.FnTyp(typs.EnumBackingTyp(e_typ), (e_typ,))

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        del comptime_args
        assert builder._fn is not None
        param = builder._fn.params[0]
        result = builder._curr_bb.enum_to_int(param, None)
        builder._curr_bb.ret(result, None)


class Builtins:
    """One compilation's intrinsic functions, shared by all its modules, and its prelude's
    ``panic``."""

    _ctx: Final[compilation.Ctx]
    size_of: Final[ir_module.IntrinsicFnSymbol]
    ptr_cast_mut: Final[ir_module.IntrinsicFnSymbol]
    is_null: Final[ir_module.IntrinsicFnSymbol]
    enum_to_int: Final[ir_module.IntrinsicFnSymbol]

    def __init__(self, ctx: compilation.Ctx) -> None:
        self._ctx = ctx
        e = ir_env.Env(ctx)
        self.size_of = SizeOfIntrinsicFn(e)
        self.ptr_cast_mut = PtrCastMutIntrinsicFn(e)
        self.is_null = IsNullIntrinsicFn(e)
        self.enum_to_int = EnumToIntIntrinsicFn(e)

    @property
    def panic_ref(self) -> Optional[ir_module.FnRef]:
        """The prelude's unshadowable ``panic`` function, or ``None`` while the prelude is
        being built."""
        prelude = self._ctx.loader.prelude
        if prelude is None:
            return None
        item = prelude.get_item(ir_env.Env.Namespace.VARS, "panic")
        if item is None:
            return None
        panic_symbol = asserts.checked_cast(item.value, ir_module.SrcFnSymbol)
        return panic_symbol.instantiate(()).ref


def register(builtin_env: ir_env.Env, builtins: Builtins) -> None:
    """Bind built-in types and this program's shared intrinsic functions."""
    builtin_env.add_container("usize", typs.USIZE)
    builtin_env.add_container("isize", typs.ISIZE)
    builtin_env.add_container("bool", typs.BOOL)
    builtin_env.add_container("array", typs.ARRAY_TEMPLATE)

    builtin_env.add_var("__size_of", builtins.size_of)
    builtin_env.add_var("__ptr_cast_mut", builtins.ptr_cast_mut)
    builtin_env.add_var("__is_null", builtins.is_null)
    builtin_env.add_var("__enum_to_int", builtins.enum_to_int)
