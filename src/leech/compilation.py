# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compilation-wide state for lazy requests and active semantic computations."""

import contextlib
import dataclasses
import enum
import operator
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Final, Optional, cast

if TYPE_CHECKING:
    from leech import ir_module, typs


class CycleDomain(enum.Enum):
    """A semantic operation whose active computations may form cycles.

    Each domain must always use one identity type and one detail type. This invariant
    makes it safe for :meth:`Ctx.detect_cycle` to restore the types erased in its stack.
    """

    MOD_VAR_INITIALIZER = enum.auto()
    STRUCT_LAYOUT = enum.auto()
    TRAIT_BOUND = enum.auto()
    IMPL_SELECTION = enum.auto()


@dataclasses.dataclass(frozen=True)
class Cycle[DetailT]:
    """The ordered details for a closed active-computation cycle.

    The final detail is the recurrence that closes the cycle and therefore represents
    the same computation as the first detail.
    """

    details: tuple[DetailT, ...]


@dataclasses.dataclass(frozen=True)
class _CycleFrame:
    identity: object
    detail: object


type _InstanceCache[OwnerT, InstanceT] = dict[OwnerT, dict[tuple[typs.Typ, ...], InstanceT]]
"""Instances of ``InstanceT``, keyed by their owning ``OwnerT`` and then by argument tuple."""


class Ctx:
    """Own compilation-wide lazy requests and active semantic computations."""

    _fn_instances: Final[_InstanceCache[ir_module.FnSymbol, ir_module.FnInstance]]
    _requested_fn_instances: Final[list[ir_module.FnInstance]]
    _struct_instances: Final[_InstanceCache[typs.StructTypTemplate, typs.StructTyp]]
    _requested_struct_instances: Final[list[typs.StructTyp]]
    _cycle_stacks: Final[dict[CycleDomain, list[_CycleFrame]]]

    def __init__(self) -> None:
        self._fn_instances = {}
        self._requested_fn_instances = []
        self._struct_instances = {}
        self._requested_struct_instances = []
        self._cycle_stacks = {}

    @contextlib.contextmanager
    def detect_cycle[IdentityT, DetailT](
        self,
        domain: CycleDomain,
        identity: IdentityT,
        detail: DetailT,
        same_identity: Callable[[IdentityT, IdentityT], bool] = operator.eq,
    ) -> Iterator[Optional[Cycle[DetailT]]]:
        """Guard one active semantic computation and report recurrence.

        ``same_identity`` compares an earlier active identity with the new one;
        equality is used by default. A caller receiving a cycle must translate it into
        a diagnostic and raise rather than continue the guarded computation.
        """
        frames = self._cycle_stacks.setdefault(domain, [])
        for index, active in enumerate(frames):
            active_identity = cast(IdentityT, active.identity)
            if same_identity(active_identity, identity):
                details = tuple(cast(DetailT, frame.detail) for frame in frames[index:])
                yield Cycle((*details, detail))
                raise AssertionError("cycle was not translated into a diagnostic")

        frame = _CycleFrame(identity, detail)
        frames.append(frame)
        try:
            yield None
        finally:
            popped = frames.pop()
            assert popped is frame, "active computations exited out of order"

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
        template: typs.StructTypTemplate,
        args: tuple[typs.Typ, ...],
        *,
        record_request: bool,
    ) -> typs.StructTyp:
        """Return the cached struct instance for ``template`` and ``args``.

        :param record_request: Whether a newly created instance is appended to
            :meth:`requested_struct_instances`. Pass ``True`` for an instance a source
            reference requests, which :func:`~leech.mono.discover` must find and code
            generation must emit. Pass ``False`` for an instance code generation already
            reaches another way, so it must not be discovered as a separate emission
            request: a non-generic declaration's zero-argument module instance, and a
            generic template's opaque validation instance. Ignored on a cache hit, since
            only the request that first creates ``args`` can be recorded.
        """
        cached = self._cached_instance(self._struct_instances, template, args)
        if cached is not None:
            return cached

        # Local because typs imports this module while its classes are initializing.
        from leech import typs  # noqa: PLC0415

        return self._record_instance(
            self._struct_instances,
            self._requested_struct_instances if record_request else None,
            template,
            args,
            typs.StructTyp(template, args),
        )

    def requested_struct_instances(self) -> Sequence[typs.StructTyp]:
        """Return the live append-only log of requested struct instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_struct_instances

    @staticmethod
    def _cached_instance[OwnerT, InstanceT](
        cache: _InstanceCache[OwnerT, InstanceT],
        owner: OwnerT,
        args: tuple[typs.Typ, ...],
    ) -> InstanceT | None:
        """Return the instance cached for one owner and argument list, if any."""
        instances = cache.get(owner)
        return None if instances is None else instances.get(args)

    @staticmethod
    def _record_instance[OwnerT, InstanceT](
        cache: _InstanceCache[OwnerT, InstanceT],
        log: Optional[list[InstanceT]],
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
        if log is not None:
            log.append(instance)
        return instance
