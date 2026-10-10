# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Compilation-wide state for lazy requests, active semantic computations and diagnostics."""

import abc
import contextlib
import dataclasses
import enum
import functools
import operator
from collections.abc import Callable, Collection, Hashable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Final, NoReturn, Optional, Protocol, cast, override

from leech import diag, opt_util, patterns
from leech import session as session_mod

if TYPE_CHECKING:
    from leech import ir_builtins, ir_loader, ir_module, ir_traits, typs


class CycleDomain(enum.Enum):
    """A semantic operation whose active computations may form cycles.

    Each domain must always use one identity type and one detail type. This invariant
    makes it safe for ``Ctx.detect_cycle`` to restore the types erased in its stack.
    """

    MOD_VAR_INITIALIZER = enum.auto()
    TYPE_LAYOUT = enum.auto()
    TRAIT_BOUND = enum.auto()
    IMPL_SELECTION = enum.auto()


@dataclasses.dataclass(frozen=True)
class Cycle[DetailT]:
    """The ordered details for a closed active-computation cycle.

    The final detail is the recurrence that closes the cycle and therefore represents
    the same computation as the first detail.
    """

    details: tuple[DetailT, ...]
    #: The cycle's domain and participants, whichever participant it was entered from.
    key: Hashable


@dataclasses.dataclass(frozen=True)
class _CycleFrame:
    identity: object
    detail: object


class HasCtx(Protocol):
    """An object that belongs to one compilation."""

    @property
    @abc.abstractmethod
    def ctx(self) -> Ctx:
        """The compilation this object belongs to."""


class Checkable(HasCtx, Protocol):
    """A declaration that can be checked on its own."""

    def check(self) -> None:
        """Report every user error in this declaration that doesn't depend on its uses.

        Implemented as an analysis unit, so the check runs once and a failure is reported
        once, however often it is requested.
        """


def unit[OwnerT: HasCtx, T](calculate: Callable[[OwnerT], T]) -> Callable[[OwnerT], T]:
    """Make ``calculate`` an analysis unit of its object, computed by ``Ctx.unit``.

    Stack it under ``@property`` for a value, or use it on a method taking no arguments for
    a check that computes nothing. The unit is named after ``calculate``.
    """

    @functools.wraps(calculate)
    def wrapper(owner: OwnerT) -> T:
        return owner.ctx.unit(owner, calculate.__name__, lambda: calculate(owner))

    return wrapper


@dataclasses.dataclass(frozen=True)
class UnitResult[T]:
    """The outcome of a step that may fail with a user error.

    Either the step's value, or the proof that the error it failed with was reported. Make
    one with ``capture``.
    """

    _outcome: T | diag.ReportProof

    @staticmethod
    def capture[V](ctx: Ctx, step: Callable[[], V]) -> UnitResult[V]:
        """Run ``step``, recovering from a user error it raises as ``Ctx.recovering`` does."""
        with ctx.recovering() as recovery:
            value = step()
            assert not isinstance(value, diag.ReportProof), "a step's value can't be a proof"
            return UnitResult(value)
        return UnitResult(opt_util.opt_unwrap(recovery.failure))

    def get(self) -> T:
        """The step's value, or raise ``diag.ReportedError`` with the failure's proof."""
        if isinstance(self._outcome, diag.ReportProof):
            raise diag.ReportedError(self._outcome)
        return self._outcome

    @property
    def failure(self) -> Optional[diag.ReportProof]:
        """The proof of the error the step failed with, or ``None`` if it succeeded."""
        if isinstance(self._outcome, diag.ReportProof):
            return self._outcome
        return None


@dataclasses.dataclass
class Recovery:
    """What a ``Ctx.recovering`` block failed with."""

    #: The proof of the user error the block failed with, or ``None`` if it succeeded.
    failure: Optional[diag.ReportProof] = None


@dataclasses.dataclass(frozen=True, eq=False)
class UnitId:
    """Identifies one analysis unit: the object it belongs to, and its name.

    Two ids are equal only if they name the same unit of the same object, whatever equality
    the owner itself defines, so distinct but equal owners never share a unit.
    """

    owner: object
    name: str

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, UnitId) and other.owner is self.owner and other.name == self.name

    @override
    def __hash__(self) -> int:
        return hash((id(self.owner), self.name))


class Ctx:
    """The root of one compilation's state.

    Owns the module loader, the trait implementations and the builtins, as well as lazy
    requests and active semantic computations. Reports diagnostics to its session's.
    Constructing one loads nothing.
    """

    session: Final[session_mod.Session]
    impl_registry: Final[ir_traits.ImplRegistry]
    loader: Final[ir_loader.ModLoader]

    _requested_fn_instances: Final[list[ir_module.FnInstance]]
    _requested_struct_instances: Final[list[typs.StructTyp]]
    _requested_union_instances: Final[list[typs.UnionTyp]]
    _union_variant_constructors: Final[dict[typs.UnionTyp, tuple[patterns.VariantConstructor, ...]]]
    _cycle_stacks: Final[dict[CycleDomain, list[_CycleFrame]]]
    #: The analysis units being computed, outermost first.
    unit_stack: Final[list[UnitId]]
    _units: Final[dict[UnitId, UnitResult[Any]]]
    #: The proof of each cycle reported so far, by ``Cycle.key``.
    _reported_cycles: Final[dict[Hashable, diag.ReportProof]]
    #: Source-declared comptime parameters, in declaration order.
    _declared_comptime_params: Final[list[typs.ComptimeParamTyp]]

    def __init__(self, session: Optional[session_mod.Session] = None) -> None:
        # Local because these modules import this one while their classes are initializing.
        from leech import ir_loader, ir_traits  # noqa: PLC0415

        self.session = opt_util.opt_or_default(session, session_mod.Session())
        self.impl_registry = ir_traits.ImplRegistry(self)
        self.loader = ir_loader.ModLoader(self)
        self._requested_fn_instances = []
        self._requested_struct_instances = []
        self._requested_union_instances = []
        self._union_variant_constructors = {}
        self._cycle_stacks = {}
        self.unit_stack = []
        self._units = {}
        self._reported_cycles = {}
        self._declared_comptime_params = []

    @property
    def diags(self) -> diag.Diags:
        return self.session.diags

    @functools.cached_property
    def builtins(self) -> ir_builtins.Builtins:
        """The intrinsic functions and the prelude's ``panic``, created on first use."""
        # Local because intrinsic classes subclass ir_module.IntrinsicFnSymbol.
        from leech import ir_builtins  # noqa: PLC0415

        return ir_builtins.Builtins(self)

    def unit[T](self, owner: object, name: str, compute: Callable[[], T]) -> T:
        """Return ``owner``'s analysis unit ``name``, calling ``compute`` the first time.

        An analysis unit is a lazily computed property whose computation may report user
        errors. A failure is cached too: a user error ``compute`` reports and raises as
        ``diag.ReportedError`` is not reported again, and every request, including the
        first, raises ``diag.ReportedError`` carrying its proof. Any other exception
        propagates and is not cached.

        Results are kept per compilation, so an object shared by several compilations has a
        separate result in each.
        """
        unit_id = UnitId(owner, name)
        result = self._units.get(unit_id)
        if result is None:
            with self._computing(unit_id):
                result = UnitResult.capture(self, compute)
            self._units[unit_id] = result
        return cast(T, result.get())

    @contextlib.contextmanager
    def _computing(self, unit_id: UnitId) -> Iterator[None]:
        assert unit_id not in self.unit_stack, f"analysis unit {unit_id.name} re-entered itself"
        self.unit_stack.append(unit_id)
        try:
            yield
        finally:
            popped = self.unit_stack.pop()
            assert popped is unit_id, "analysis units exited out of order"

    @contextlib.contextmanager
    def recovering(self) -> Iterator[Recovery]:
        """Recover from a user error raised in the block, which ends the block.

        The error, already reported and raised as ``diag.ReportedError``, is not propagated.
        The yielded ``Recovery``'s ``failure`` is its proof once the block has ended. Any
        other exception propagates.
        """
        recovery = Recovery()
        try:
            yield recovery
        except diag.ReportedError as err:
            recovery.failure = err.reported

    def discard_comptime_params(self, owners: Collection[Hashable]) -> None:
        """Forget the recorded comptime parameters declared by any of ``owners``.

        Used when the item declaring them is rejected, so they are never validated.
        Owners are compared by identity, since equal declarations may be distinct items.
        """
        owner_ids = {id(owner) for owner in owners}
        self._declared_comptime_params[:] = [
            param for param in self._declared_comptime_params if id(param.owner) not in owner_ids
        ]

    def record_comptime_param(self, param: typs.ComptimeParamTyp) -> None:
        """Record a source-declared comptime parameter for later validation.

        What a parameter's bounds or declared type name cannot be resolved
        while declarations are still being collected, so every parameter
        built from source is collected here and checked once the whole
        module graph is loaded.
        """
        self._declared_comptime_params.append(param)

    def declared_comptime_params(self) -> Collection[typs.ComptimeParamTyp]:
        """Every recorded comptime parameter, in declaration order."""
        return self._declared_comptime_params

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
        a diagnostic and pass it to ``fail_cycle`` rather than continue the guarded
        computation.
        """
        frames = self._cycle_stacks.setdefault(domain, [])
        for index, active in enumerate(frames):
            active_identity = cast(IdentityT, active.identity)
            if same_identity(active_identity, identity):
                details = tuple(cast(DetailT, frame.detail) for frame in frames[index:])
                key = (domain, frozenset(frame.identity for frame in frames[index:]))
                yield Cycle((*details, detail), key)
                raise AssertionError("cycle was not translated into a diagnostic")

        frame = _CycleFrame(identity, detail)
        frames.append(frame)
        try:
            yield None
        finally:
            popped = frames.pop()
            assert popped is frame, "active computations exited out of order"

    def fail_cycle[DetailT](self, cycle: Cycle[DetailT], err: diag.Diag) -> NoReturn:
        """Report ``err`` for ``cycle``, unless the same cycle was reported already, and raise.

        A cycle is found once from each participant it is entered from; it is the same cycle
        if it has the same domain and participants, and only the first ``err`` is reported.
        """
        reported = self._reported_cycles.get(cycle.key)
        if reported is None:
            reported = self.diags.error(err)
            self._reported_cycles[cycle.key] = reported
        raise diag.ReportedError(reported)

    def record_fn_request(self, instance: ir_module.FnInstance) -> None:
        """Record a newly constructed function instance, for code generation to emit."""
        self._requested_fn_instances.append(instance)

    def requested_fn_instances(self) -> Sequence[ir_module.FnInstance]:
        """Return the live append-only log of requested function instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_fn_instances

    def record_struct_request(self, instance: typs.StructTyp) -> None:
        """Record a newly constructed struct instance that a source reference requests."""
        self._requested_struct_instances.append(instance)

    def requested_struct_instances(self) -> Sequence[typs.StructTyp]:
        """Return the live append-only log of requested struct instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_struct_instances

    def record_union_request(self, instance: typs.UnionTyp) -> None:
        """Record a newly constructed union instance that a source reference requests."""
        self._requested_union_instances.append(instance)

    def requested_union_instances(self) -> Sequence[typs.UnionTyp]:
        """Return the live append-only log of requested union instances.

        Callers may drain it by index while lowering appends further requests; this method
        deliberately does not return a snapshot.
        """
        return self._requested_union_instances

    def union_variant_constructors(
        self,
        union_typ: typs.UnionTyp,
        build: Callable[[], tuple[patterns.VariantConstructor, ...]],
    ) -> tuple[patterns.VariantConstructor, ...]:
        """Return ``union_typ``'s pattern constructors, calling ``build`` at most once.

        Held here rather than by whichever body is being checked because
        a constructor's sub-column spaces are built eagerly, so every
        function matching one union would otherwise rebuild every space
        its payloads can reach. The instances keying this belong to this
        compilation, so the memo lasts as long as they do.
        """
        cached = self._union_variant_constructors.get(union_typ)
        if cached is None:
            cached = build()
            self._union_variant_constructors[union_typ] = cached
        return cached
