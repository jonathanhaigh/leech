# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compilation-wide state for lazy requests and active semantic computations."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from leech import ir_module, typs


class Ctx:
    """Own compilation-wide lazy requests and active semantic computations."""

    #: Identity used in weak type-cache keys without retaining this context through them.
    typ_cache_token: Final[object]
    _fn_instances: Final[dict[ir_module.FnSymbol, dict[tuple[typs.Typ, ...], ir_module.FnInstance]]]
    _requested_fn_instances: Final[list[ir_module.FnInstance]]
    _struct_instances: Final[dict[typs.StructTyp, dict[tuple[typs.Typ, ...], typs.StructTyp]]]
    _requested_struct_instances: Final[list[typs.StructTyp]]

    def __init__(self) -> None:
        self.typ_cache_token = object()
        self._fn_instances = {}
        self._requested_fn_instances = []
        self._struct_instances = {}
        self._requested_struct_instances = []

    def instantiate_fn(
        self,
        symbol: ir_module.FnSymbol,
        args: tuple[typs.Typ, ...],
    ) -> ir_module.FnInstance:
        """Return the cached function instance for ``symbol`` and ``args``."""
        cached = self._cached_instance(self._fn_instances, symbol, args)
        if cached is not None:
            return cached

        # Local because ir_module imports this module while its classes are initializing.
        from leech import ir_module  # noqa: PLC0415

        return self._record_instance(
            self._fn_instances,
            self._requested_fn_instances,
            symbol,
            args,
            ir_module.FnInstance(symbol, args),
        )

    def requested_fn_instances(self) -> Sequence[ir_module.FnInstance]:
        """Return the live append-only log of requested function instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_fn_instances

    def instantiate_struct(
        self,
        template: typs.StructTyp,
        args: tuple[typs.Typ, ...],
    ) -> typs.StructTyp:
        """Return the cached struct instance for ``template`` and ``args``."""
        cached = self._cached_instance(self._struct_instances, template, args)
        if cached is not None:
            return cached

        return self._record_instance(
            self._struct_instances,
            self._requested_struct_instances,
            template,
            args,
            template._create_instance(args),
        )

    def requested_struct_instances(self) -> Sequence[typs.StructTyp]:
        """Return the live append-only log of requested struct instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_struct_instances

    @staticmethod
    def _cached_instance[OwnerT, InstanceT](
        cache: dict[OwnerT, dict[tuple[typs.Typ, ...], InstanceT]],
        owner: OwnerT,
        args: tuple[typs.Typ, ...],
    ) -> InstanceT | None:
        """Return the instance cached for one owner and argument list, if any."""
        instances = cache.get(owner)
        return None if instances is None else instances.get(args)

    @staticmethod
    def _record_instance[OwnerT, InstanceT](
        cache: dict[OwnerT, dict[tuple[typs.Typ, ...], InstanceT]],
        log: list[InstanceT],
        owner: OwnerT,
        args: tuple[typs.Typ, ...],
        instance: InstanceT,
    ) -> InstanceT:
        """Cache and record a newly constructed instance."""
        instances = cache.get(owner)
        if instances is None:
            instances = {}
            cache[owner] = instances
        assert args not in instances, "instance creation re-entered the same request"
        instances[args] = instance
        log.append(instance)
        return instance
