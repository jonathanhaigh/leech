# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Leech's type system: type representations, caching, and construction from AST."""

import abc
import enum
import functools
import re
import types
import weakref
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Final, Optional, Self, override

from leech import (
    asserts,
    ast,
    compilation,
    errors,
    ir_env,
    opt_util,
    reserved,
    signage,
    src,
    target,
    visibility,
)

if TYPE_CHECKING:
    # Runtime imports are local because ir_traits/ir_values import this module.
    from leech import ir_traits, ir_values


class Mutability(enum.Enum):
    """Whether a pointer or struct field may be written through."""

    CONST = 0
    MUT = 1

    @staticmethod
    def from_ast(mut_ast: Optional[ast.Mutability]) -> Mutability:
        """Return mutable for a parsed ``mut`` keyword and const otherwise."""
        if mut_ast is None:
            return CONST
        asserts.assert_eq(mut_ast.value, "mut")
        return MUT


CONST = Mutability.CONST
MUT = Mutability.MUT


def resolve_bound(
    bound: ast.BasicTyp,
    e: ir_env.Env,
) -> tuple[ir_traits.Trait, tuple[Typ, ...]]:
    """Resolve a bound's trait and bounds-checked comptime arguments.

    Whether any particular type satisfies the bound is left to callers.
    """
    # This must be local because ir_traits imports this module.
    from leech import ir_traits  # noqa: PLC0415

    trait = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
    if not isinstance(trait, ir_traits.Trait):
        raise errors.InvalidComptimeBoundError(bound.path.str(), bound.path.span)
    return trait, resolve_trait_comptime_args(
        trait, bound.path.segs[-1].comptime_args, e, bound.span
    )


def resolve_trait_comptime_args(
    trait: ir_traits.Trait,
    args_ast: tuple[ast.ComptimeArg, ...],
    e: ir_env.Env,
    span: src.SrcSpan,
) -> tuple[Typ, ...]:
    """Resolve and bounds-check comptime arguments applied to ``trait``.

    Applies the same arity rules as ``instantiate_generic_typ`` and checks
    the resolved arguments against the trait's own parameter bounds. Shared
    by a bound's own trait reference (``T: Container[i32]``) and a trait
    impl's (``impl Sized[N] for Buf[N]``).
    """
    if not trait.comptime_params:
        if args_ast:
            raise errors.ComptimeArgsOnNonGenericItemError(trait.name, span)
        return ()
    if not args_ast:
        raise errors.MissingComptimeArgsError(trait.name, span)
    if len(args_ast) != len(trait.comptime_params):
        raise errors.WrongNumberOfComptimeArgsError(
            trait.name, len(args_ast), len(trait.comptime_params), span
        )
    comptime_args = tuple(
        resolve_comptime_arg(param, arg_ast, e)
        for param, arg_ast in zip(trait.comptime_params, args_ast, strict=True)
    )
    check_comptime_arg_bounds(trait.comptime_params, comptime_args, e, span)
    return comptime_args


def match_typ_args(declared: Typ, actual: Typ) -> Optional[dict[ComptimeParamTyp, Typ]]:
    """Match a possibly generic ``declared`` type against a specific ``actual`` one.

    The match succeeds when some substitution for ``declared``'s own comptime
    parameters turns it into exactly ``actual`` - so ``Pair[A, A]``
    matches ``Pair[i32, i32]`` but not ``Pair[i32, bool]``. Returns the
    substitution, or ``None`` if none makes them equal; an empty mapping
    means they were already equal.
    """
    bindings: dict[ComptimeParamTyp, Typ] = {}
    declared.infer_typ_args(actual, bindings)
    return bindings if declared.substitute_typ_params(bindings) is actual else None


def _contains_typ(typ: Typ, contained: Typ, resolve: Callable[[Typ], Typ]) -> bool:
    """Check occurrence after resolving each type reached by the traversal."""
    typ = resolve(typ)
    if typ is contained:
        return True
    if isinstance(typ, FnTyp):
        return _contains_typ(typ.ret_typ, contained, resolve) or any(
            _contains_typ(param_typ, contained, resolve) for param_typ in typ.param_typs
        )
    if isinstance(typ, PtrTyp):
        return _contains_typ(typ.pointee_typ, contained, resolve)
    if isinstance(typ, ArrayTyp):
        return _contains_typ(typ.element_typ, contained, resolve)
    if isinstance(typ, StructTyp):
        return any(_contains_typ(typ_arg, contained, resolve) for typ_arg in typ.comptime_args)
    if isinstance(typ, EnumBackingTyp):
        return _contains_typ(typ.inner, contained, resolve)
    return False


def typs_overlap(a: Typ, b: Typ) -> bool:
    """Return whether substitutions can make ``a`` and ``b`` the same type.

    Comptime parameters in either argument are unification variables. This differs
    from ``match_typ_args``, whose parameters are only variables on its
    ``declared`` side. Bindings must remain finite, so an equation such as
    ``T = Box[T]`` does not establish an overlap.
    """
    bindings: dict[ComptimeParamTyp, Typ] = {}

    def resolve(typ: Typ) -> Typ:
        while isinstance(typ, ComptimeParamTyp) and typ in bindings:
            typ = bindings[typ]
        return typ

    def occurs(typ_param: ComptimeParamTyp, typ: Typ) -> bool:
        return _contains_typ(typ, typ_param, resolve)

    def bind(typ_param: ComptimeParamTyp, typ: Typ) -> bool:
        typ = resolve(typ)
        if typ is typ_param:
            return True
        if occurs(typ_param, typ):
            return False
        bindings[typ_param] = typ
        return True

    def unify(left: Typ, right: Typ) -> bool:
        left = resolve(left)
        right = resolve(right)
        if left is right:
            return True
        if isinstance(left, ComptimeParamTyp):
            return bind(left, right)
        if isinstance(right, ComptimeParamTyp):
            return bind(right, left)
        if isinstance(left, FnTyp) and isinstance(right, FnTyp):
            return (
                len(left.param_typs) == len(right.param_typs)
                and unify(left.ret_typ, right.ret_typ)
                and all(
                    unify(left_param, right_param)
                    for left_param, right_param in zip(
                        left.param_typs, right.param_typs, strict=True
                    )
                )
            )
        if isinstance(left, PtrTyp) and isinstance(right, PtrTyp):
            return left.mut == right.mut and unify(left.pointee_typ, right.pointee_typ)
        if isinstance(left, ArrayTyp) and isinstance(right, ArrayTyp):
            return unify(left.length, right.length) and unify(left.element_typ, right.element_typ)
        if isinstance(left, StructTyp) and isinstance(right, StructTyp):
            return left.template is right.template and all(
                unify(left_arg, right_arg)
                for left_arg, right_arg in zip(left.comptime_args, right.comptime_args, strict=True)
            )
        if isinstance(left, EnumBackingTyp) and isinstance(right, EnumBackingTyp):
            return unify(left.inner, right.inner)
        return False

    return unify(a, b)


def contains_typ(container: Typ, contained: Typ) -> bool:
    """Return whether ``contained`` occurs structurally within ``container``."""
    return _contains_typ(container, contained, lambda candidate: candidate)


def unsatisfied_bound(
    bindings: Mapping[ComptimeParamTyp, Typ],
    e: ir_env.Env,
) -> Optional[tuple[TypParamTyp, Typ, ir_traits.Trait]]:
    """Find a type argument that doesn't satisfy its type parameter's bounds.

    ``bindings`` maps each comptime parameter to the comptime argument
    standing in for it; a value parameter's binding is skipped, since it
    has no bounds of its own to violate. Returns the first type parameter,
    argument, and trait for which the argument doesn't implement the
    trait, or ``None`` if every bound holds.
    """
    # Bind every parameter before checking any bound, so that a bound's type
    # arguments can name a sibling parameter declared either side of it.
    sub_env = e.new_child()
    for typ_param, typ_arg in bindings.items():
        sub_env.add_container(typ_param.name, typ_arg)

    for typ_param, typ_arg in bindings.items():
        if not isinstance(typ_param, TypParamTyp):
            # A value parameter has no bounds of its own to violate; only
            # its (already-checked) declared value type constrains it.
            continue
        for bound in typ_param.bounds:
            if typ_param.is_declared_by_trait:
                with e.ctx.detect_cycle(
                    compilation.CycleDomain.TRAIT_BOUND,
                    bound,
                    bound,
                ) as cycle:
                    if cycle is not None:
                        repeated_bound = cycle.details[0]
                        entries = [
                            (entry.path.str(), entry.path.span) for entry in cycle.details[:-1]
                        ]
                        raise errors.RecursiveTraitBoundError(
                            repeated_bound.path.str(),
                            repeated_bound.path.span,
                            entries,
                        )
                    trait, bound_comptime_args = resolve_bound(bound, sub_env)
            else:
                trait, bound_comptime_args = resolve_bound(bound, sub_env)
            if bound_comptime_args:
                # Matching these against an impl's own trait arguments needs generic
                # traits. Nothing upstream rejects the bound, so fail loudly rather
                # than silently ignore the arguments.
                raise NotImplementedError(
                    "checking bounds with generic arguments isn't supported yet"
                )
            if not e.impl_registry.implements(trait, typ_arg):
                return typ_param, typ_arg, trait
    return None


def resolve_comptime_arg(param: ComptimeParamTyp, arg_ast: ast.ComptimeArg, e: ir_env.Env) -> Typ:
    """Resolve one parsed comptime argument against its declared parameter.

    An unsuffixed integer literal takes ``param``'s declared value type
    when ``param`` is a ``ValueParamTyp``; an explicitly suffixed one
    resolves to its own suffix, exactly like an ordinary integer literal
    elsewhere.
    """
    if isinstance(arg_ast, ast.IntLit):
        if arg_ast.explicit_width is not None:
            assert arg_ast.explicit_signage is not None
            lit_typ: IntTyp = IntTyp.get_or_create(arg_ast.explicit_width, arg_ast.explicit_signage)
        elif isinstance(param, ValueParamTyp) and isinstance(param.value_typ, IntTyp):
            lit_typ = param.value_typ
        else:
            raise errors.WrongKindOfComptimeArgError(param.name, arg_ast.diag_str(), arg_ast.span)
        if not lit_typ.fits(arg_ast.value):
            raise errors.IntLitOverflowError(arg_ast.value, lit_typ.name, arg_ast.span)
        return ComptimeValueTyp.get_or_create(lit_typ, arg_ast.value)
    if isinstance(arg_ast, ast.BoolLit):
        return ComptimeValueTyp.get_or_create(BOOL, arg_ast.value)
    if isinstance(arg_ast, ast.BasicTyp) and all(
        not seg.comptime_args for seg in arg_ast.path.segs
    ):
        # A bare name here may forward a value parameter, or the concrete
        # value substituted for one (e.g. `Sized[N]`, forwarding an
        # enclosing impl's own value parameter as a trait argument) -
        # resolved directly, since `Typ.from_ast`'s `resolve_typ` rejects
        # exactly that as a type.
        item = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, arg_ast.path)
        if isinstance(item, (ValueParamTyp, ComptimeValueTyp)):
            return item
    return Typ.from_ast(arg_ast, e)


def instantiate_generic_typ(
    template: GenericTypTemplate,
    args_ast: tuple[ast.ComptimeArg, ...],
    e: ir_env.Env,
    span: src.SrcSpan,
) -> TypKind:
    """Resolve, bounds-check, and apply one generic type path segment."""
    if len(args_ast) != len(template.comptime_params):
        raise errors.WrongNumberOfComptimeArgsError(
            template.name, len(args_ast), len(template.comptime_params), span
        )
    comptime_args = tuple(
        resolve_comptime_arg(param, arg_ast, e)
        for param, arg_ast in zip(template.comptime_params, args_ast, strict=True)
    )
    check_comptime_arg_bounds(template.comptime_params, comptime_args, e, span)
    return template.instantiate(comptime_args)


def check_comptime_arg_bounds(
    comptime_params: Sequence[ComptimeParamTyp],
    comptime_args: tuple[Typ, ...],
    e: ir_env.Env,
    span: Optional[src.SrcSpan],
) -> None:
    """Raise if a comptime argument violates its corresponding parameter's bounds."""
    typ_param_bindings: dict[ComptimeParamTyp, Typ] = {}
    for param, arg in zip(comptime_params, comptime_args, strict=True):
        arg_value_typ = None
        if isinstance(arg, (ValueParamTyp, ComptimeValueTyp)):
            arg_value_typ = arg.value_typ
        if isinstance(param, ValueParamTyp):
            if arg_value_typ is None:
                raise errors.WrongKindOfComptimeArgError(param.name, arg.name, span)
            if arg_value_typ is not param.value_typ:
                raise errors.WrongComptimeValueTypError(arg.name, param.value_typ.name, span)
        else:
            if arg_value_typ is not None:
                raise errors.WrongKindOfComptimeArgError(param.name, arg.name, span)
            typ_param_bindings[asserts.checked_cast(param, TypParamTyp)] = arg

    violated = unsatisfied_bound(typ_param_bindings, e)
    if violated is not None:
        typ_param, typ_arg, trait = violated
        raise errors.UnsatisfiedBoundError(typ_arg.name, trait.name, typ_param.name, span)


class Typ(abc.ABC):
    """Base class for identity-compared Leech types.

    Most types are weakly interned here. Struct instances are instead owned by
    their compilation context.
    """

    _cache: ClassVar[weakref.WeakValueDictionary[Hashable, Typ]] = weakref.WeakValueDictionary()

    @classmethod
    def create(cls, *args: Hashable) -> Self:
        """Construct and cache a new instance, asserting that its key is unused."""
        key = cls.cache_key(*args)
        asserts.assert_not_in(key, Typ._cache)
        obj = cls(*args)
        Typ._cache[key] = obj
        return obj

    @classmethod
    def get(cls, *args: Hashable) -> Self:
        """Return the instance cached under ``args``."""
        return asserts.checked_cast(Typ._cache[cls.cache_key(*args)], cls)

    @classmethod
    def get_or_create(cls, *args: Hashable) -> Self:
        """Return the instance cached under ``args``, creating it when absent."""
        key = cls.cache_key(*args)
        obj = Typ._cache.get(key)
        if obj is None:
            obj = cls(*args)
            Typ._cache[key] = obj
        return asserts.checked_cast(obj, cls)

    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Return the cache key for an instance constructed with ``args``."""
        return (cls, *args)

    def coerces_to(self, target_typ: Typ) -> bool:
        """Return whether this type implicitly converts to ``target_typ``.

        The base implementation permits only identity; subclasses may widen it.
        """
        return self == target_typ

    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        """Replace mapped comptime parameters while preserving canonical interning.

        The base implementation returns ``self`` because it contains no parameters.
        """
        return self

    def infer_typ_args(  # noqa: B027 - intentionally empty default, not abstract
        self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]
    ) -> None:
        """Infer parameter bindings structurally from ``actual``.

        Shape mismatches are ignored and the first binding wins. The base implementation
        is a no-op because it contains no parameters.
        """

    def is_concrete(self) -> bool:
        """Return whether this type contains no comptime parameters.

        The base implementation is unconditionally true.
        """
        return True

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """This type's human-readable name, as used in diagnostics."""

    @property
    def qualified_name(self) -> str:
        """This type's name as used to build LLVM symbol names.

        Unlike ``name``, this must identify the type program-wide, so
        a type declared in a module carries that module's name and a type
        built from others qualifies those too. The base implementation is
        the bare name, correct for the builtin types, which are declared
        in no module and contain nothing.
        """
        return self.name

    @staticmethod
    def from_ast(typ_ast: ast.TypKind, e: ir_env.Env) -> TypKind:
        """Resolve a parsed type expression in ``e``."""
        match typ_ast:
            case ast.BasicTyp():
                return Typ._basic_typ_from_ast(typ_ast, e)
            case ast.PtrTyp():
                return PtrTyp.get_or_create(
                    Typ.from_ast(typ_ast.pointee_typ, e),
                    Mutability.from_ast(typ_ast.mut),
                )

    @staticmethod
    def _basic_typ_from_ast(typ_ast: ast.BasicTyp, e: ir_env.Env) -> TypKind:
        """Resolve a named type, including applications on its path segments."""
        return e.resolve_typ(typ_ast.path)


class IntTyp(Typ):
    """A fixed-width signed or unsigned integer type, e.g. ``i32`` or ``u8``."""

    width: Final[int]
    signage: Final[signage.Signage]

    def __init__(self, width: int, sign: signage.Signage) -> None:
        self.width = width
        self.signage = sign

    @property
    @override
    def name(self) -> str:
        sign_char = "i" if self.signage == signage.SIGNED else "u"
        return f"{sign_char}{self.width}"

    @property
    def min_value(self) -> int:
        """The smallest value representable by this type."""
        if self.signage == signage.SIGNED:
            return -(2 ** (self.width - 1))
        return 0

    @property
    def max_value(self) -> int:
        """The largest value representable by this type."""
        if self.signage == signage.SIGNED:
            return 2 ** (self.width - 1) - 1
        return 2**self.width - 1

    def fits(self, value: int) -> bool:
        """Whether ``value`` is representable by this type."""
        return self.min_value <= value <= self.max_value

    @override
    def coerces_to(self, target_typ: Typ) -> bool:
        """Allow widening to an integer type that can represent every value.

        Comparing ranges is the whole rule, so the awkward cases fall out
        without being special-cased: ``u8`` coerces to ``i16``, and even
        to ``i9``, but not to ``i8``, which can't hold 255; and no signed
        type coerces to an unsigned one, which could never hold its
        negative values.
        """
        return isinstance(target_typ, IntTyp) and (
            target_typ.min_value <= self.min_value and self.max_value <= target_typ.max_value
        )

    _NAME_RE: ClassVar[re.Pattern[str]] = re.compile("([iu])([1-9][0-9]*)")

    @staticmethod
    def is_name(name: str) -> bool:
        """Return whether ``name`` is spelled like a builtin int type.

        Spelling alone: true even for a width too large to build a type from.
        """
        return IntTyp._NAME_RE.fullmatch(name) is not None

    @staticmethod
    def from_name(name: str) -> Optional[IntTyp]:
        """Recognize and build the interned builtin int type spelled ``name``.

        Returns ``None`` if ``name`` doesn't spell one, or spells a width
        too large to parse.
        """
        m = IntTyp._NAME_RE.fullmatch(name)
        if m is None:
            return None
        try:
            width = int(m[2])
        except ValueError:
            # More digits than CPython will convert to an int.
            return None
        sign = signage.SIGNED if m[1] == "i" else signage.UNSIGNED
        return IntTyp.get_or_create(width, sign)


class BoolTyp(Typ):
    """The boolean type."""

    @property
    @override
    def name(self) -> str:
        return "bool"


type ComptimeLiteralTyp = IntTyp | BoolTyp
"""A type that can back a literal comptime value parameter or argument."""


class CallableTyp(Typ):
    """Base class for types of things that can be called."""

    ret_typ: Final[TypKind]
    param_typs: Final[tuple[TypKind, ...]]

    def __init__(self, ret_typ: TypKind, param_typs: tuple[TypKind, ...]) -> None:
        self.ret_typ = ret_typ
        self.param_typs = param_typs


class FnTyp(CallableTyp):
    """The type of a function, e.g. ``fn(i32, i32) bool``."""

    @property
    @override
    def name(self) -> str:
        param_strs = ", ".join(typ.name for typ in self.param_typs)
        return f"fn({param_strs}) {self.ret_typ.name}"

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return FnTyp.get_or_create(
            self.ret_typ.substitute_typ_params(mapping),
            tuple(param_typ.substitute_typ_params(mapping) for param_typ in self.param_typs),
        )

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if not isinstance(actual, FnTyp):
            return
        self.ret_typ.infer_typ_args(actual.ret_typ, bindings)
        for declared_param, actual_param in zip(self.param_typs, actual.param_typs, strict=False):
            declared_param.infer_typ_args(actual_param, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.ret_typ.is_concrete() and all(
            param_typ.is_concrete() for param_typ in self.param_typs
        )


class PtrTyp(Typ):
    """A pointer type, e.g. ``*i32`` or ``*mut i32``."""

    pointee_typ: Final[TypKind]
    mut: Final[Mutability]

    def __init__(self, pointee_typ: TypKind, mut: Mutability) -> None:
        self.pointee_typ = pointee_typ
        self.mut = mut

    @property
    @override
    def name(self) -> str:
        mut_str = "mut " if self.mut == MUT else ""
        return f"*{mut_str}{self.pointee_typ.name}"

    @property
    @override
    def qualified_name(self) -> str:
        mut_str = "mut " if self.mut == MUT else ""
        return f"*{mut_str}{self.pointee_typ.qualified_name}"

    @override
    def coerces_to(self, target_typ: Typ) -> bool:
        """Allow a mutable pointer where a const one is wanted.

        Giving up the ability to write through a pointer is always safe,
        so ``*mut T`` coerces to ``*T``. The reverse doesn't: a ``*T``
        can't be used where writing is required. Nothing is emitted for
        this coercion - a pointer's own type and its mut/const variant
        share the same representation.
        """
        return super().coerces_to(target_typ) or (
            self.mut == MUT and self._new_with_mut(CONST) == target_typ
        )

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return PtrTyp.get_or_create(self.pointee_typ.substitute_typ_params(mapping), self.mut)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if isinstance(actual, PtrTyp):
            self.pointee_typ.infer_typ_args(actual.pointee_typ, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.pointee_typ.is_concrete()

    def _new_with_mut(self, mut: Mutability) -> PtrTyp:
        """Return the interned pointer with this pointee and ``mut``."""
        return PtrTyp.get_or_create(self.pointee_typ, mut)


class ArrayTyp(Typ):
    """A fixed-length array type, e.g. ``array[i32, 4]``."""

    element_typ: Final[TypKind]
    length: Final[TypKind]

    def __init__(self, element_typ: TypKind, length: TypKind) -> None:
        self.element_typ = element_typ
        self.length = length

    @staticmethod
    def of_length(element_typ: TypKind, length: int) -> ArrayTyp:
        """Return the cached instance for a concrete literal ``length``."""
        return ArrayTyp.get_or_create(element_typ, ComptimeValueTyp.get_or_create(USIZE, length))

    @property
    def length_value(self) -> int:
        """This array's concrete length as a Python ``int``."""
        concrete = asserts.checked_cast(self.length, ComptimeValueTyp)
        assert concrete.value_typ is USIZE, (
            f"array length must be usize, not {concrete.value_typ.name}"
        )
        return asserts.checked_cast(concrete.value, int)

    @property
    @override
    def name(self) -> str:
        return f"array[{self.element_typ.name}, {self.length.name}]"

    @property
    @override
    def qualified_name(self) -> str:
        return f"array[{self.element_typ.qualified_name}, {self.length.qualified_name}]"

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return ArrayTyp.get_or_create(
            self.element_typ.substitute_typ_params(mapping),
            self.length.substitute_typ_params(mapping),
        )

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if isinstance(actual, ArrayTyp):
            self.element_typ.infer_typ_args(actual.element_typ, bindings)
            self.length.infer_typ_args(actual.length, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.element_typ.is_concrete() and self.length.is_concrete()


class ComptimeParamTyp(Typ):
    """Shared identity, caching, and substitution for a comptime parameter.

    Interned by its owner and position - a parameter is declared in exactly
    one place, so the first interning fixes it. Never instantiated
    directly; use ``TypParamTyp`` or ``ValueParamTyp``.
    """

    _owner: Final[Hashable]
    _index: Final[int]
    _name: Final[str]

    def __init__(self, owner: Hashable, index: int, name: str) -> None:
        self._owner = owner
        self._index = index
        self._name = name

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key a comptime parameter by its declaring item and position."""
        asserts.assert_gt(len(args), 1)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return self._name

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return mapping.get(self, self)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        bindings.setdefault(self, actual)

    @override
    def is_concrete(self) -> bool:
        return False


class TypParamTyp(ComptimeParamTyp):
    """An opaque generic type parameter interned by its owner and position.

    It has no LLVM representation and stores only its display name, trait
    bounds and the scope those bounds are written in.

    :param decl_env: The scope enclosing this parameter's declaration, in
        which its bounds' trait names are spelled. Not part of the cache
        key, which the owner alone determines - a parameter is declared
        in exactly one place, so the first interning fixes it.
    """

    bounds: Final[tuple[ast.BasicTyp, ...]]
    decl_env: Final[Optional[ir_env.Env]]

    def __init__(
        self,
        owner: Hashable,
        index: int,
        name: str,
        bounds: tuple[ast.BasicTyp, ...] = (),
        decl_env: Optional[ir_env.Env] = None,
    ) -> None:
        super().__init__(owner, index, name)
        self.bounds = bounds
        self.decl_env = decl_env

    def declares_bound(self, trait: ir_traits.Trait) -> bool:
        """Whether this parameter's own declared bounds include ``trait``.

        This is what stands in for an impl inside a generic definition:
        nothing is registered against a type parameter, so what its
        declaration assumes about it is all that can be known.
        """
        return any(
            resolve_bound(bound, opt_util.opt_unwrap(self.decl_env))[0] is trait
            for bound in self.bounds
        )

    @property
    def is_declared_by_trait(self) -> bool:
        """Whether this parameter belongs to a trait declaration.

        Only trait-owned bounds can recursively validate other declaration
        bounds. Impl bounds may recur legally while selection descends through
        a smaller type, and impl-obligation recursion is guarded separately.
        Function, struct, and intrinsic bounds are one-shot entry points whose
        recursive paths must pass through a trait-owned bound to close a cycle.
        """
        return isinstance(self._owner, ast.TraitDefn)


class ValueParamTyp(ComptimeParamTyp):
    """An opaque comptime value parameter interned by its owner and position.

    It has no LLVM representation of its own; a concrete argument
    substitutes to a ``ComptimeValueTyp`` of the same ``value_typ``.

    :param value_typ: This parameter's declared type - an ``IntTyp``
        or ``BOOL``.
    """

    value_typ: Final[ComptimeLiteralTyp]

    def __init__(
        self, owner: Hashable, index: int, name: str, value_typ: ComptimeLiteralTyp
    ) -> None:
        super().__init__(owner, index, name)
        self.value_typ = value_typ


class ComptimeValueTyp(Typ):
    """A concrete compile-time value used as a comptime argument.

    The type-level counterpart to ``ir_values.ComptimeValue``:
    a ``Typ`` node whose entire content is one concrete compile-time value,
    interned the same way every other ``Typ`` is - equal values are
    therefore always the same instance.

    :param value_typ: This value's type - an ``IntTyp`` or ``BOOL``.
    """

    value_typ: Final[ComptimeLiteralTyp]
    value: Final[int | bool]

    def __init__(self, value_typ: ComptimeLiteralTyp, value: int | bool) -> None:
        assert (value_typ is BOOL) == isinstance(value, bool), (
            f"{value!r} does not match declared type {value_typ.name}"
        )
        self.value_typ = value_typ
        self.value = value

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key a comptime value by its type and its own value."""
        asserts.assert_eq(len(args), 2)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return str(self.value).lower()

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return self

    @override
    def is_concrete(self) -> bool:
        return True

    @staticmethod
    def checked_value(typ: Typ) -> int | bool:
        """Assert ``typ`` is a concrete comptime value and return its Python value."""
        return asserts.checked_cast(typ, ComptimeValueTyp).value

    def to_comptime_value(self, ast_node: Optional[ast.Ast] = None) -> ir_values.ComptimeValue:
        """Build the ``ir_values.ComptimeValue`` this value denotes."""
        # Local to avoid the ir_values import cycle (ir_values imports this module).
        from leech import ir_values  # noqa: PLC0415

        match self.value_typ:
            case BoolTyp():
                return ir_values.ComptimeBool(asserts.checked_cast(self.value, bool), ast_node)
            case IntTyp():
                return ir_values.ComptimeInt(
                    self.value_typ, asserts.checked_cast(self.value, int), ast_node
                )


def comptime_params_from_ast(
    owner: Hashable, comptime_params: Sequence[ast.ComptimeParam], e: ir_env.Env
) -> tuple[ComptimeParamTyp, ...]:
    """Intern parsed comptime parameters under ``owner``, preserving declaration order.

    A single-entry bound list is resolved against ``e``: a name that
    denotes a ``Trait`` declares a type parameter with that bound (as a
    multi-entry bound list always does); a name that denotes an
    ``IntTyp`` or ``BOOL`` declares a value parameter of that
    type instead. This declaration is called eagerly (e.g. from a
    function's or trait's own ``__init__``), so a single bound that
    doesn't yet resolve - e.g. a trait declared later in the same module,
    as in mutually recursive trait bounds - is left unresolved here and
    always yields a type parameter; the deferred ``resolve_bound``
    call that checks it later (after the whole module is loaded) raises
    if it still doesn't name a trait.

    :param e: The scope enclosing the declaration, in which parameters'
        bounds are resolved. See ``TypParamTyp.decl_env``.
    """
    # Local to avoid the ir_traits import cycle.
    from leech import ir_traits  # noqa: PLC0415

    for param_ast in comptime_params:
        if reserved.is_reserved(param_ast.ident.name):
            raise errors.ReservedNameError(param_ast.ident.name, param_ast.ident.span)

    result: list[ComptimeParamTyp] = []
    for index, param_ast in enumerate(comptime_params):
        value_typ: Optional[ComptimeLiteralTyp] = None
        if len(param_ast.bounds) == 1:
            (bound,) = param_ast.bounds
            try:
                item: Optional[Any] = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
            except errors.ItemNotFoundError:
                # Not yet declared in this module - defer to a type parameter;
                # resolve_bound raises later if it never resolves to a trait.
                item = None
            if isinstance(item, (IntTyp, BoolTyp)):
                value_typ = item
            elif item is not None and not isinstance(item, ir_traits.Trait):
                raise errors.InvalidComptimeBoundError(bound.path.str(), bound.path.span)
        if value_typ is not None:
            result.append(
                ValueParamTyp.get_or_create(owner, index, param_ast.ident.name, value_typ)
            )
        else:
            result.append(
                TypParamTyp.get_or_create(owner, index, param_ast.ident.name, param_ast.bounds, e)
            )
    return tuple(result)


class StructField:
    """A struct field with its declaration index and type-resolution scope."""

    index: Final[int]
    ast: Final[ast.StructFieldDefn]
    _env: Final[ir_env.Env]

    def __init__(self, index: int, field_ast: ast.StructFieldDefn, e: ir_env.Env) -> None:
        self.index = index
        self.ast = field_ast
        self._env = e

    @property
    def name(self) -> str:
        """The field's name."""
        return self.ast.ident.name

    @functools.cached_property
    def typ(self) -> TypKind:
        """The field's type."""
        return Typ.from_ast(self.ast.typ, self._env)

    @functools.cached_property
    def access(self) -> visibility.Access:
        """Whether the field is public or private."""
        return visibility.Access.from_ast(self.ast.access)

    @functools.cached_property
    def mut(self) -> Mutability:
        """Whether the field may be written through a mut pointer to the struct.

        A field can never be written through a const struct pointer,
        regardless of this value.
        """
        return Mutability.from_ast(self.ast.mut)

    def is_accessible_from(self, file: src.SrcFile) -> bool:
        """Return whether this field is accessible from ``file``."""
        return self.access == visibility.PUBLIC or self.ast.span.file.path == file.path


class GenericTypTemplate(abc.ABC):
    """Shared surface for an unapplied generic type: a struct declaration
    or the built-in ``array`` template.

    ``instantiate_generic_typ`` applies comptime arguments to any instance
    uniformly, regardless of which kind of template it is.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The declaration's bare source name."""

    @functools.cached_property
    def comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        """The template's formal comptime parameters, in declaration order."""
        return self.calculate_comptime_params()

    @abc.abstractmethod
    def calculate_comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        """Compute ``comptime_params``; overridden by subclasses."""

    @abc.abstractmethod
    def instantiate(self, comptime_args: tuple[Typ, ...]) -> TypKind:
        """Return the usable type for applying ``comptime_args``."""


class StructTypTemplate(GenericTypTemplate):
    """A struct declaration that owns its usable type instances."""

    ast: Final[ast.StructDefn]
    _decl_env: Final[ir_env.Env]
    mod_name: Final[str]

    def __init__(self, struct_ast: ast.StructDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = struct_ast
        self._decl_env = e
        self.mod_name = mod_name
        # Bind parameters before fields so parameter-name errors take priority.
        param_env = e.new_child()
        for comptime_param in self.comptime_params:
            param_env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, ValueParamTyp):
                param_env.add_var(comptime_param.name, comptime_param)
        field_asts: dict[str, ast.StructFieldDefn] = {}
        for field_ast in struct_ast.fields:
            name = field_ast.ident.name
            if reserved.is_reserved(name):
                raise errors.ReservedNameError(name, field_ast.ident.span)
            existing = field_asts.get(name)
            if existing is not None:
                raise errors.DuplicateFieldInStructDefnError(
                    name, field_ast.ident.span, existing.ident.span
                )
            field_asts[name] = field_ast

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @property
    def span(self) -> src.SrcSpan:
        """The source location of the struct declaration."""
        return self.ast.span

    @override
    def calculate_comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        return comptime_params_from_ast(self.ast, self.ast.comptime_params, self._decl_env)

    @override
    def instantiate(self, comptime_args: tuple[Typ, ...]) -> StructTyp:
        """Return the cached instance for ``comptime_args`` and record its request."""
        asserts.assert_eq(len(comptime_args), len(self.comptime_params))
        return self._decl_env.ctx.instantiate_struct(self, comptime_args, record_request=True)

    @functools.cached_property
    def _validation_instance(self) -> StructTyp:
        """Return the cached opaque instance used only for declaration validation."""
        asserts.assert_gt(len(self.comptime_params), 0)
        return self._decl_env.ctx.instantiate_struct(
            self, self.comptime_params, record_request=False
        )

    def validate_declaration(self) -> None:
        """Validate this declaration's layout with its bare root name."""
        self._validation_instance._check_finite_size(None, self.name)

    @functools.cached_property
    def module_instance(self) -> StructTyp:
        """Return the zero-argument instance bound for a non-generic declaration."""
        asserts.assert_eq(len(self.comptime_params), 0)
        return self._decl_env.ctx.instantiate_struct(self, (), record_request=False)


class StructTyp(Typ):
    """A usable nominal struct instance owned by one declaration template."""

    template: Final[StructTypTemplate]
    comptime_args: Final[tuple[Typ, ...]]
    _env: Final[ir_env.Env]
    _fields: Final[dict[str, StructField]]

    def __init__(self, template: StructTypTemplate, comptime_args: tuple[Typ, ...]) -> None:
        asserts.assert_eq(len(comptime_args), len(template.comptime_params))
        self.template = template
        self.comptime_args = comptime_args
        self._env = template._decl_env.new_child()
        for typ_param, typ_arg in zip(template.comptime_params, comptime_args, strict=True):
            self._env.add_container(typ_param.name, typ_arg)
        self._fields = {
            field_ast.ident.name: StructField(index, field_ast, self._env)
            for index, field_ast in enumerate(template.ast.fields)
        }

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Reject the global cache because compilation contexts own instances."""
        raise AssertionError("struct instances are owned by the compilation context")

    @property
    def ast(self) -> ast.StructDefn:
        """The declaration AST shared by this template's instances."""
        return self.template.ast

    @property
    def mod_name(self) -> str:
        """The name of the module that declares this struct."""
        return self.template.mod_name

    @property
    @override
    def qualified_name(self) -> str:
        """This instantiation's mangled symbol name, e.g. ``mod::Pair[i32, i32]``.

        The comptime arguments are qualified too, rather than rendered as
        ``name`` renders them: two same-named structs declared in
        different modules are different types, so ``Box[a::Foo]`` and
        ``Box[b::Foo]`` must not arrive at one symbol.
        """
        qualified = f"{self.mod_name}::{self.template.name}"
        if not self.comptime_args:
            return qualified
        arg_names = ", ".join(typ_arg.qualified_name for typ_arg in self.comptime_args)
        return f"{qualified}[{arg_names}]"

    @override
    def is_concrete(self) -> bool:
        return all(typ_arg.is_concrete() for typ_arg in self.comptime_args)

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        substituted = tuple(
            typ_arg.substitute_typ_params(mapping) for typ_arg in self.comptime_args
        )
        if substituted == self.comptime_args:
            return self
        return self.template.instantiate(substituted)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if isinstance(actual, StructTyp) and actual.template is self.template:
            for declared_arg, actual_arg in zip(
                self.comptime_args, actual.comptime_args, strict=True
            ):
                declared_arg.infer_typ_args(actual_arg, bindings)

    @property
    @override
    def name(self) -> str:
        if not self.comptime_args:
            return self.template.name
        arg_names = ", ".join(typ_arg.name for typ_arg in self.comptime_args)
        return f"{self.template.name}[{arg_names}]"

    @functools.cached_property
    def fields(self) -> types.MappingProxyType[str, StructField]:
        """Return fields by declaration order after rejecting infinite-size layouts."""
        self._check_finite_size(None, None)
        return types.MappingProxyType(self._fields)

    def field_at(self, index: int) -> StructField:
        """Return the field at declaration-order ``index``."""
        return tuple(self.fields.values())[index]

    def _check_finite_size(
        self,
        incoming_hop: Optional[errors.StructLayoutHop],
        root_name: Optional[str],
    ) -> None:
        """Reject exact or structurally growing by-value layout cycles."""
        detail: tuple[
            StructTyp,
            Optional[errors.StructLayoutHop],
        ] = (self, incoming_hop)
        with self._env.ctx.detect_cycle(
            compilation.CycleDomain.STRUCT_LAYOUT,
            self,
            detail,
            same_identity=StructTyp._layout_repeats,
        ) as cycle:
            if cycle is not None:
                repeated, repeated_hop = cycle.details[0]
                hops = []
                for _, hop in cycle.details[1:]:
                    assert hop is not None, "only the root layout frame may omit its field hop"
                    hops.append(hop)
                repeated_name = StructTyp._display_name(repeated, repeated_hop, root_name)
                raise errors.InfiniteSizeStructError(repeated_name, repeated.span, hops)

            for field_ast in self.ast.fields:
                typ = Typ.from_ast(field_ast.typ, self._env)
                while isinstance(typ, ArrayTyp):
                    typ = typ.element_typ
                if isinstance(typ, StructTyp):
                    hop = errors.StructLayoutHop(
                        StructTyp._display_name(self, incoming_hop, root_name),
                        field_ast.ident.name,
                        field_ast.span,
                        typ.name,
                    )
                    typ._check_finite_size(hop, root_name)

    @staticmethod
    def _display_name(
        typ: StructTyp, hop: Optional[errors.StructLayoutHop], root_name: Optional[str]
    ) -> str:
        return root_name if hop is None and root_name is not None else typ.name

    @staticmethod
    def _layout_repeats(earlier: StructTyp, current: StructTyp) -> bool:
        """Return whether ``current`` repeats or grows ``earlier``'s layout."""
        if earlier is current:
            return True
        if earlier.template is not current.template:
            return False

        for earlier_arg, current_arg in zip(
            earlier.comptime_args, current.comptime_args, strict=True
        ):
            if not contains_typ(current_arg, earlier_arg):
                return False
        return True

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this struct's declaration."""
        return self.template.span


class EnumTyp(Typ):
    """An enum type: a fixed, named set of integer discriminants backed by
    an explicit or inferred integer type.

    Unlike ``StructTyp``, never generic and never instantiated - one
    ``enum`` declaration is always exactly one ``EnumTyp``.
    """

    ast: Final[ast.EnumDefn]
    mod_name: Final[str]
    _env: Final[ir_env.Env]

    def __init__(self, enum_ast: ast.EnumDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = enum_ast
        self.mod_name = mod_name
        self._env = e

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        asserts.assert_gt(len(args), 0)
        return (cls, args[0])

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @property
    @override
    def qualified_name(self) -> str:
        return f"{self.mod_name}::{self.name}"

    @functools.cached_property
    def variants(self) -> types.MappingProxyType[str, int]:
        """This enum's variants, keyed by name, in declaration order, each
        mapped to its discriminant value - the previous variant's value
        plus one, or an explicit ``= N`` override.
        """
        result: dict[str, int] = {}
        next_value = 0
        for variant_ast in self.ast.variants:
            if variant_ast.value is None:
                value = next_value
            elif variant_ast.negative:
                value = -variant_ast.value.value
            else:
                value = variant_ast.value.value
            name = variant_ast.ident.name
            if reserved.is_reserved(name):
                raise errors.ReservedNameError(name, variant_ast.ident.span)
            if name in result:
                previous_span = next(v.span for v in self.ast.variants if v.ident.name == name)
                raise errors.DuplicateVariantInEnumDefnError(name, variant_ast.span, previous_span)
            result[name] = value
            next_value = value + 1
        return result.items().mapping

    @functools.cached_property
    def backing_typ(self) -> IntTyp:
        """This enum's backing integer type: explicit, or the smallest
        unsigned builtin integer type fitting every discriminant.
        """
        if self.ast.backing_typ is not None:
            typ = Typ.from_ast(self.ast.backing_typ, self._env)
            if not isinstance(typ, IntTyp):
                raise errors.EnumBackingTypNotIntError(typ.name, self.ast.backing_typ.span)
            for variant_ast, value in zip(self.ast.variants, self.variants.values(), strict=True):
                literal = variant_ast.value
                if literal is not None and literal.explicit_width is not None:
                    assert literal.explicit_signage is not None
                    literal_typ = IntTyp.get_or_create(
                        literal.explicit_width, literal.explicit_signage
                    )
                    if not literal_typ.coerces_to(typ):
                        raise errors.EnumVariantValueTypMismatchError(
                            literal_typ.name, typ.name, variant_ast.span
                        )
                if not typ.fits(value):
                    raise errors.IntLitOverflowError(value, typ.name, variant_ast.span)
            return typ

        values = self.variants.values()
        min_value = min(values, default=0)
        max_value = max(values, default=0)
        inferred_signage = signage.SIGNED if min_value < 0 else signage.UNSIGNED
        for width in (8, 16, 32, 64):
            candidate = IntTyp.get_or_create(width, inferred_signage)
            if candidate.fits(min_value) and candidate.fits(max_value):
                return candidate

        widest = IntTyp.get_or_create(64, inferred_signage)
        overflowing_value = min_value if not widest.fits(min_value) else max_value
        overflowing_span = next(
            variant_ast.span
            for variant_ast, value in zip(self.ast.variants, values, strict=True)
            if value == overflowing_value
        )
        raise errors.EnumDiscriminantOverflowError(overflowing_value, overflowing_span)

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this enum's declaration."""
        return self.ast.span


class EnumBackingTyp(Typ):
    """The backing integer type of whatever (possibly still opaque) type
    ``inner`` becomes once substituted.

    Exists only inside a generic builtin's own opaque self-describing
    signature (see ``__enum_to_int``'s ``ir_builtins.EnumToIntIntrinsicFn``,
    whose return type can't be spelled as any concrete type until its own
    type argument is known to be a concrete ``EnumTyp``) - substituting
    a concrete enum type for ``inner`` collapses this straight to that
    enum's real ``EnumTyp.backing_typ``, so it never survives into a
    fully-substituted, lowerable signature.

    :param inner: The (possibly still a type parameter) type whose backing
        type this stands for; must resolve to an ``EnumTyp`` once
        every type parameter within it is substituted away.
    """

    inner: Final[Typ]

    def __init__(self, inner: Typ) -> None:
        self.inner = inner

    @property
    @override
    def name(self) -> str:
        return f"<backing type of {self.inner.name}>"

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        substituted_inner = self.inner.substitute_typ_params(mapping)
        if isinstance(substituted_inner, EnumTyp):
            return substituted_inner.backing_typ
        if substituted_inner.is_concrete():
            raise AssertionError(
                f"EnumBackingTyp's inner type must resolve to an enum, got {substituted_inner.name}"
            )
        return EnumBackingTyp.get_or_create(substituted_inner)

    @override
    def is_concrete(self) -> bool:
        return self.inner.is_concrete()


class VoidTyp(Typ):
    """The type of an expression that produces no value."""

    @property
    @override
    def name(self) -> str:
        return "void"


class NeverTyp(Typ):
    """The type of an expression that never completes normally.

    Used for expressions such as ``return`` that unconditionally divert
    control flow, so their static type can unify with any other type.
    """

    @property
    @override
    def name(self) -> str:
        return "never"

    @override
    def coerces_to(self, target_typ: Typ) -> bool:
        return True


type TypKind = (
    IntTyp
    | BoolTyp
    | FnTyp
    | PtrTyp
    | ArrayTyp
    | ComptimeParamTyp
    | ComptimeValueTyp
    | StructTyp
    | EnumTyp
    | EnumBackingTyp
    | VoidTyp
    | NeverTyp
)
"""Every instantiable Typ implementation used by the compiler."""


#: The built-in numeric, boolean, string, and control-flow type singletons.
U8 = IntTyp.get_or_create(8, signage.UNSIGNED)
I8 = IntTyp.get_or_create(8, signage.SIGNED)
U32 = IntTyp.get_or_create(32, signage.UNSIGNED)
I32 = IntTyp.get_or_create(32, signage.SIGNED)
USIZE = IntTyp.get_or_create(target.ADDR_SIZE, signage.UNSIGNED)
ISIZE = IntTyp.get_or_create(target.ADDR_SIZE, signage.SIGNED)
CINT = I32
BOOL = BoolTyp.get_or_create()
CSTR = PtrTyp.get_or_create(U8, CONST)
VOID = VoidTyp.get_or_create()
NEVER = NeverTyp.get_or_create()


class ArrayTypTemplate(GenericTypTemplate):
    """The singleton generic template underlying ``array[T, N]``."""

    @property
    @override
    def name(self) -> str:
        return "array"

    @override
    def calculate_comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        return (
            TypParamTyp.get_or_create(self, 0, "T"),
            ValueParamTyp.get_or_create(self, 1, "N", USIZE),
        )

    @override
    def instantiate(self, comptime_args: tuple[Typ, ...]) -> ArrayTyp:
        elt_typ, length = comptime_args
        return ArrayTyp.get_or_create(elt_typ, length)


#: The singleton backing the built-in ``array[T, N]`` type.
ARRAY_TEMPLATE: Final[ArrayTypTemplate] = ArrayTypTemplate()
