# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compiler-intrinsic builtins, and the other names ambiently available in
every module without needing an import (see :func:`register`).

Imported lazily, from inside :meth:`~leech.ir_module.Mod.__init__` and
:meth:`~leech.ir_loader.ModLoader.__init__`, rather than at module level by
either of those modules: every class here subclasses
:class:`~leech.ir_module.GenericBuiltinFn`, so importing this module
requires ``ir_module`` to already be fully loaded - true once the program
is running, but not yet true while ``ir_module``/``ir_loader`` are still
in the middle of importing each other (a base class, unlike a plain type
annotation, is evaluated immediately, not deferred).
"""

from typing import override

from leech import ir_builder, ir_env, ir_loader, ir_module, typs


class SizeOfBuiltinFn(ir_module.GenericBuiltinFn):
    """``__size_of[T]() usize``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__size_of", ("T",), e)

    @override
    def fn_typ_for_typ_args(self, typ_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        return typs.FnTyp.get_or_create(typs.USIZE, ())

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, e: ir_env.Env, typ_args: tuple[typs.Typ, ...]
    ) -> None:
        del e
        size = builder._curr_bb.size_of(typ_args[0], None)
        builder._curr_bb.ret(size, None)


class PtrCastMutBuiltinFn(ir_module.GenericBuiltinFn):
    """``__ptr_cast_mut[From, To](p: *mut From) *mut To``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__ptr_cast_mut", ("From", "To"), e)

    @override
    def fn_typ_for_typ_args(self, typ_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        from_typ, to_typ = typ_params
        return typs.FnTyp.get_or_create(
            typs.PtrTyp.get_or_create(to_typ, typs.MUT),
            (typs.PtrTyp.get_or_create(from_typ, typs.MUT),),
        )

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, e: ir_env.Env, typ_args: tuple[typs.Typ, ...]
    ) -> None:
        del e
        assert builder._fn is not None
        param = builder._fn.params[0]
        target_typ = typs.PtrTyp.get_or_create(typ_args[1], typs.MUT)
        casted = builder._curr_bb.ptr_cast(param, target_typ, None)
        builder._curr_bb.ret(casted, None)


class IsNullBuiltinFn(ir_module.GenericBuiltinFn):
    """``__is_null[T](p: *mut T) bool``."""

    def __init__(self, e: ir_env.Env) -> None:
        super().__init__("__is_null", ("T",), e)

    @override
    def fn_typ_for_typ_args(self, typ_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        (t,) = typ_params
        return typs.FnTyp.get_or_create(typs.BOOL, (typs.PtrTyp.get_or_create(t, typs.MUT),))

    @override
    def _build_body(
        self, builder: ir_builder.CfgBuilder, e: ir_env.Env, typ_args: tuple[typs.Typ, ...]
    ) -> None:
        del e, typ_args
        assert builder._fn is not None
        param = builder._fn.params[0]
        result = builder._curr_bb.is_null(param, None)
        builder._curr_bb.ret(result, None)


def register(builtin_env: ir_env.Env, loader: ir_loader.ModLoader) -> None:
    """Bind every ambient name - the built-in types and the compiler-
    intrinsic builtins - into ``builtin_env``.

    Called once per module, from :meth:`~leech.ir_module.Mod.__init__`.

    :param builtin_env: The module's own top-level scope to bind into.
    :param loader: The loader owning this program's shared builtin
        instances (see :attr:`~leech.ir_loader.ModLoader.builtins`).
    """
    builtin_env.add_container("usize", typs.USIZE)
    builtin_env.add_container("isize", typs.ISIZE)
    builtin_env.add_container("bool", typs.BOOL)

    builtin_env.add_var("__size_of", ir_module.GenericFn(loader.size_of_builtin))
    builtin_env.add_var("__ptr_cast_mut", ir_module.GenericFn(loader.ptr_cast_mut_builtin))
    builtin_env.add_var("__is_null", ir_module.GenericFn(loader.is_null_builtin))
