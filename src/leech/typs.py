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
from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar, Final, Optional, Self, override

from leech import (
    asserts,
    ast,
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
    # Runtime imports are local because ir_traits imports this module.
    from leech import ir_traits


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

#: Maximum nesting depth when checking a bound's type arguments against the
#: bounds its trait declares. Legitimate bounds nest a few levels deep; a
#: bound whose type argument grows each step never repeats and would
#: otherwise recurse until the stack runs out.
MAX_BOUND_DEPTH = 32


def resolve_bound(
    bound: ast.BasicTyp,
    e: ir_env.Env,
    in_progress: frozenset[tuple[ir_traits.Trait, tuple[Typ, ...]]] = frozenset(),
) -> tuple[ir_traits.Trait, tuple[Typ, ...]]:
    """Resolve a bound to its trait and its resolved type arguments.

    Applies the same arity rules to a bound's trait reference that
    :meth:`Typ._basic_typ_from_ast` applies to a type reference, and checks the
    resolved arguments against the trait's own parameter bounds. Whether any
    particular type *satisfies* the bound is left to callers.

    :param in_progress: The trait/type-argument pairs whose own bounds are
        already being checked further up the recursion.
    """
    # This must be local because ir_traits imports this module.
    from leech import ir_traits  # noqa: PLC0415

    trait = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound.path)
    if not isinstance(trait, ir_traits.Trait):
        raise errors.BoundNotATraitError(bound.path.str(), bound.path.span)
    if not trait.typ_params:
        if bound.generic_args:
            raise errors.TypArgsOnNonGenericItemError(bound.path.str(), bound.span)
        return trait, ()
    if not bound.generic_args:
        raise errors.MissingTypArgsError(trait.name, bound.span)
    if len(bound.generic_args) != len(trait.typ_params):
        raise errors.WrongNumberOfTypArgsError(
            trait.name, len(bound.generic_args), len(trait.typ_params), bound.span
        )
    typ_args = tuple(Typ.from_ast(arg, e) for arg in bound.generic_args)

    # A trait bounding its own parameter by itself, directly or through a
    # cycle of traits, would otherwise recurse until the stack runs out.
    key = (trait, typ_args)
    if key in in_progress:
        raise NotImplementedError(f"recursive trait bound on {trait.name} isn't supported yet")
    # A bound whose type argument grows each step never repeats a key, so
    # depth is what bounds it. Each level adds exactly one key.
    if len(in_progress) >= MAX_BOUND_DEPTH:
        raise NotImplementedError(
            f"exceeded {MAX_BOUND_DEPTH} levels of bound resolution at {trait.name};"
            " recursive trait bounds aren't supported yet"
        )

    check_typ_arg_bounds(trait.typ_params, typ_args, e, bound.span, in_progress | {key})
    return trait, typ_args


def match_typ_args(declared: Typ, actual: Typ) -> Optional[dict[TypParamTyp, Typ]]:
    """Match a possibly generic ``declared`` type against a specific ``actual`` one.

    The match succeeds when some substitution for ``declared``'s own type
    parameters turns it into exactly ``actual`` - so ``Pair[A, A]``
    matches ``Pair[i32, i32]`` but not ``Pair[i32, bool]``.

    :param declared: The type as written, possibly naming type parameters.
    :param actual: The type to match it against.
    :return: The substitution that makes them equal, or ``None`` if no
        substitution does. An empty mapping means they were already equal.
    """
    bindings: dict[TypParamTyp, Typ] = {}
    declared.infer_typ_args(actual, bindings)
    return bindings if declared.substitute_typ_params(bindings) is actual else None


def _contains_typ_param(typ: Typ, typ_param: TypParamTyp, resolve: Callable[[Typ], Typ]) -> bool:
    """Check occurrence after resolving each type reached by the traversal."""
    typ = resolve(typ)
    if typ is typ_param:
        return True
    if isinstance(typ, FnTyp):
        return _contains_typ_param(typ.ret_typ, typ_param, resolve) or any(
            _contains_typ_param(param_typ, typ_param, resolve) for param_typ in typ.param_typs
        )
    if isinstance(typ, PtrTyp):
        return _contains_typ_param(typ.pointee_typ, typ_param, resolve)
    if isinstance(typ, ArrayTyp):
        return _contains_typ_param(typ.element_typ, typ_param, resolve)
    if isinstance(typ, StructTyp):
        return any(_contains_typ_param(typ_arg, typ_param, resolve) for typ_arg in typ._typ_args)
    if isinstance(typ, EnumBackingTyp):
        return _contains_typ_param(typ.inner, typ_param, resolve)
    return False


def typs_overlap(a: Typ, b: Typ) -> bool:
    """Return whether substitutions can make ``a`` and ``b`` the same type.

    Type parameters in either argument are unification variables. This differs
    from :func:`match_typ_args`, whose parameters are only variables on its
    ``declared`` side. Bindings must remain finite, so an equation such as
    ``T = Box[T]`` does not establish an overlap.
    """
    bindings: dict[TypParamTyp, Typ] = {}

    def resolve(typ: Typ) -> Typ:
        while isinstance(typ, TypParamTyp) and typ in bindings:
            typ = bindings[typ]
        return typ

    def occurs(typ_param: TypParamTyp, typ: Typ) -> bool:
        return _contains_typ_param(typ, typ_param, resolve)

    def bind(typ_param: TypParamTyp, typ: Typ) -> bool:
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
        if isinstance(left, TypParamTyp):
            return bind(left, right)
        if isinstance(right, TypParamTyp):
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
            return left.length == right.length and unify(left.element_typ, right.element_typ)
        if isinstance(left, StructTyp) and isinstance(right, StructTyp):
            return (
                left.ast is right.ast
                and len(left._typ_args) == len(right._typ_args)
                and all(
                    unify(left_arg, right_arg)
                    for left_arg, right_arg in zip(left._typ_args, right._typ_args, strict=True)
                )
            )
        if isinstance(left, EnumBackingTyp) and isinstance(right, EnumBackingTyp):
            return unify(left.inner, right.inner)
        return False

    return unify(a, b)


def contains_typ_param(typ: Typ, typ_param: TypParamTyp) -> bool:
    """Return whether ``typ_param`` occurs structurally within ``typ``."""
    return _contains_typ_param(typ, typ_param, lambda candidate: candidate)


def unsatisfied_bound(
    bindings: Mapping[TypParamTyp, Typ],
    e: ir_env.Env,
    in_progress: frozenset[tuple[ir_traits.Trait, tuple[Typ, ...]]] = frozenset(),
) -> Optional[tuple[TypParamTyp, Typ, ir_traits.Trait]]:
    """Find a type argument that doesn't satisfy its parameter's bounds.

    :param bindings: Each type parameter mapped to the type argument
        standing in for it.
    :param e: The scope each bound's trait name resolves in.
    :param in_progress: Threaded through to :func:`resolve_bound`.
    :return: The first parameter, argument and trait for which the
        argument doesn't implement the trait, or ``None`` if every bound
        holds.
    """
    # Bind every parameter before checking any bound, so that a bound's type
    # arguments can name a sibling parameter declared either side of it.
    sub_env = e.new_child()
    for typ_param, typ_arg in bindings.items():
        sub_env.add_container(typ_param.name, typ_arg)

    for typ_param, typ_arg in bindings.items():
        for bound in typ_param.bounds:
            trait, bound_typ_args = resolve_bound(bound, sub_env, in_progress)
            if bound_typ_args:
                # Matching these against an impl's own trait arguments needs
                # generic traits. Nothing upstream rejects the bound, so fail
                # loudly rather than silently ignore the arguments.
                raise NotImplementedError(
                    "checking bounds with generic arguments isn't supported yet"
                )
            if not e.impl_registry.implements(trait, typ_arg):
                return typ_param, typ_arg, trait
    return None


def check_typ_arg_bounds(
    typ_params: Sequence[TypParamTyp],
    typ_args: tuple[Typ, ...],
    e: ir_env.Env,
    span: Optional[src.SrcSpan],
    in_progress: frozenset[tuple[ir_traits.Trait, tuple[Typ, ...]]] = frozenset(),
) -> None:
    """Raise if a type argument violates its corresponding parameter's bounds.

    :param in_progress: Threaded through to :func:`resolve_bound`.
    :raises UnsatisfiedBoundError: If a bound doesn't hold.
    """
    violated = unsatisfied_bound(dict(zip(typ_params, typ_args, strict=True)), e, in_progress)
    if violated is not None:
        typ_param, typ_arg, trait = violated
        raise errors.UnsatisfiedBoundError(typ_arg.name, trait.name, typ_param.name, span)


class Typ(abc.ABC):
    """Base class for weakly interned Leech types, which compare by identity.

    :invariant: get_or_create(*a) is get_or_create(*a) [assert_not_in @ create]
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

    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        """Replace mapped type parameters while preserving canonical interning.

        The base implementation returns ``self`` because it contains no parameters.
        """
        return self

    def infer_typ_args(  # noqa: B027 - intentionally empty default, not abstract
        self, actual: Typ, bindings: dict[TypParamTyp, Typ]
    ) -> None:
        """Infer parameter bindings structurally from ``actual``.

        Shape mismatches are ignored and the first binding wins. The base implementation
        is a no-op because it contains no parameters.
        """

    def is_concrete(self) -> bool:
        """Return whether this type contains no type parameters.

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

        Unlike :attr:`name`, this must identify the type program-wide, so
        a type declared in a module carries that module's name and a type
        built from others qualifies those too. The base implementation is
        the bare name, correct for the builtin types, which are declared
        in no module and contain nothing.
        """
        return self.name

    @staticmethod
    def from_ast(typ_ast: ast.Typ, e: ir_env.Env) -> Typ:
        """Resolve a parsed type expression in ``e``."""
        match typ_ast:
            case ast.BasicTyp():
                return Typ._basic_typ_from_ast(typ_ast, e)
            case ast.PtrTyp():
                return PtrTyp.get_or_create(
                    Typ.from_ast(typ_ast.pointee_typ, e),
                    Mutability.from_ast(typ_ast.mut),
                )
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
            case _:
                raise AssertionError(f"unhandled type ast node {typ_ast}")

    @staticmethod
    def _basic_typ_from_ast(typ_ast: ast.BasicTyp, e: ir_env.Env) -> Typ:
        """Resolve a named type, applying arguments only to a bare generic template."""
        item = e.resolve_typ(typ_ast.path)
        if not isinstance(item, StructTyp) or not item.ast.generic_params or item._typ_args:
            if typ_ast.generic_args:
                raise errors.TypArgsOnNonGenericItemError(typ_ast.path.str(), typ_ast.span)
            return item

        if not typ_ast.generic_args:
            raise errors.MissingTypArgsError(item.name, typ_ast.span)
        if len(typ_ast.generic_args) != len(item.ast.generic_params):
            raise errors.WrongNumberOfTypArgsError(
                item.name, len(typ_ast.generic_args), len(item.ast.generic_params), typ_ast.span
            )
        typ_args = tuple(Typ.from_ast(arg, e) for arg in typ_ast.generic_args)
        check_typ_arg_bounds(item.typ_params, typ_args, e, typ_ast.span)
        return item.instance(typ_args)


class IntTyp(Typ):
    """A fixed-width signed or unsigned integer type, e.g. ``i32`` or ``u8``.

    :param width: The bit width.
    :param sign: Whether the type is signed or unsigned.
    """

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
        """Whether ``value`` is representable by this type.

        :param value: The value to check.
        :return: Whether ``min_value <= value <= max_value``.
        """
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

        :param name: The spelling to parse, e.g. ``"i32"`` or ``"u8"``.
        :return: The int type, or ``None`` if ``name`` doesn't spell one, or
            spells a width too large to parse.
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


class CallableTyp(Typ):
    """Base class for types of things that can be called.

    :param ret_typ: The return type.
    :param param_typs: The parameter types, in declaration order.
    """

    ret_typ: Final[Typ]
    param_typs: Final[tuple[Typ, ...]]

    def __init__(self, ret_typ: Typ, param_typs: tuple[Typ, ...]) -> None:
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
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        return FnTyp.get_or_create(
            self.ret_typ.substitute_typ_params(mapping),
            tuple(param_typ.substitute_typ_params(mapping) for param_typ in self.param_typs),
        )

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
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
    """A pointer type, e.g. ``*i32`` or ``*mut i32``.

    :param pointee_typ: The type pointed to.
    :param mut: Whether the pointee may be written through this pointer.
    """

    pointee_typ: Final[Typ]
    mut: Final[Mutability]

    def __init__(self, pointee_typ: Typ, mut: Mutability) -> None:
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
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        return PtrTyp.get_or_create(self.pointee_typ.substitute_typ_params(mapping), self.mut)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        if isinstance(actual, PtrTyp):
            self.pointee_typ.infer_typ_args(actual.pointee_typ, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.pointee_typ.is_concrete()

    def _new_with_mut(self, mut: Mutability) -> PtrTyp:
        """Return the interned pointer with this pointee and ``mut``."""
        return PtrTyp.get_or_create(self.pointee_typ, mut)


class ArrayTyp(Typ):
    """A fixed-length array type, e.g. ``[i32; 4]``."""

    element_typ: Final[Typ]
    length: Final[int]

    def __init__(self, element_typ: Typ, length: int) -> None:
        self.element_typ = element_typ
        self.length = length

    @property
    @override
    def name(self) -> str:
        return f"[{self.element_typ.name}; {self.length}]"

    @property
    @override
    def qualified_name(self) -> str:
        return f"[{self.element_typ.qualified_name}; {self.length}]"

    @override
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        return ArrayTyp.get_or_create(self.element_typ.substitute_typ_params(mapping), self.length)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        if isinstance(actual, ArrayTyp):
            self.element_typ.infer_typ_args(actual.element_typ, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.element_typ.is_concrete()


class TypParamTyp(Typ):
    """An opaque generic type parameter interned by its owner and position.

    It has no LLVM representation and stores only its display name, trait
    bounds and the scope those bounds are written in.

    :param decl_env: The scope enclosing this parameter's declaration, in
        which its bounds' trait names are spelled. Not part of the cache
        key, which the owner alone determines - a parameter is declared
        in exactly one place, so the first interning fixes it.
    """

    _owner: Final[Hashable]
    _index: Final[int]
    _name: Final[str]
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
        self._owner = owner
        self._index = index
        self._name = name
        self.bounds = bounds
        self.decl_env = decl_env

    def declares_bound(self, trait: ir_traits.Trait) -> bool:
        """Whether this parameter's own declared bounds include ``trait``.

        This is what stands in for an impl inside a generic definition:
        nothing is registered against a type parameter, so what its
        declaration assumes about it is all that can be known.

        :param trait: The trait to look for.
        :return: Whether some bound resolves to ``trait``.
        """
        return any(
            resolve_bound(bound, opt_util.opt_unwrap(self.decl_env))[0] is trait
            for bound in self.bounds
        )

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key type parameters by their declaring item and position.

        :pre: len(args) > 1 [assert_gt]
        """
        asserts.assert_gt(len(args), 1)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return self._name

    @override
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        return mapping.get(self, self)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        bindings.setdefault(self, actual)

    @override
    def is_concrete(self) -> bool:
        return False


def typ_params_from_ast(
    owner: Hashable, generic_params: Sequence[ast.GenericParam], e: ir_env.Env
) -> tuple[TypParamTyp, ...]:
    """Intern parsed type parameters under ``owner``, preserving declaration order.

    :param e: The scope enclosing the declaration, which its parameters'
        bounds are resolved in. See :attr:`TypParamTyp.decl_env`.
    :raises ReservedNameError: If a parameter takes a reserved name.
    """
    for param_ast in generic_params:
        if reserved.is_reserved(param_ast.ident.name):
            raise errors.ReservedNameError(param_ast.ident.name, param_ast.ident.span)
    return tuple(
        TypParamTyp.get_or_create(owner, index, param_ast.ident.name, param_ast.bounds, e)
        for index, param_ast in enumerate(generic_params)
    )


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
    def typ(self) -> Typ:
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


#: Maximum nesting allowed while resolving generic struct fields.
_MAX_STRUCT_INSTANTIATION_DEPTH = 64


class StructTyp(Typ):
    """A nominal struct type identified by its declaration and type arguments.

    A generic declaration owns an unusable template with opaque parameters and caches one
    identity-distinct instance per argument tuple. It stores its fields only.
    """

    ast: Final[ast.StructDefn]
    _decl_env: Final[ir_env.Env]
    _typ_args: Final[tuple[Typ, ...]]
    mod_name: Final[str]
    _env: Final[ir_env.Env]
    _members: Final[dict[str, StructField]]

    def __init__(
        self,
        struct_ast: ast.StructDefn,
        e: ir_env.Env,
        typ_args: tuple[Typ, ...] = (),
        mod_name: str = "",
    ) -> None:
        self.ast = struct_ast
        self._decl_env = e
        self._typ_args = typ_args
        self.mod_name = mod_name
        self._env = e.new_child()
        # Binding parameters here lets field types resolve them like named types.
        if typ_args:
            for param_ast, typ_arg in zip(struct_ast.generic_params, typ_args, strict=True):
                self._env.add_container(param_ast.ident.name, typ_arg)
        else:
            for typ_param in self.typ_params:
                self._env.add_container(typ_param.name, typ_param)
        self._members = {}
        # Registration does not resolve field types, which may name later declarations.
        for i, field_ast in enumerate(struct_ast.fields):
            self._add_field(StructField(i, field_ast, self._env))

    @functools.cached_property
    def typ_params(self) -> tuple[TypParamTyp, ...]:
        """Return this template's interned parameters, or empty for an instance."""
        if self._typ_args:
            return ()
        return typ_params_from_ast(self.ast, self.ast.generic_params, self._decl_env)

    @functools.cached_property
    def _instance_cache(self) -> dict[tuple[Typ, ...], StructTyp]:
        return {}

    @property
    def instances(self) -> Collection[StructTyp]:
        """Return every instantiation requested from this template."""
        return self._instance_cache.values()

    def instance(self, typ_args: tuple[Typ, ...]) -> StructTyp:
        """Return the cached instantiation for ``typ_args``, creating it if needed.

        :post: instance(a) is instance(a) for equal a [cache]
        """
        inst = self._instance_cache.get(typ_args)
        if inst is None:
            inst = StructTyp.get_or_create(self.ast, self._decl_env, typ_args, self.mod_name)
            self._instance_cache[typ_args] = inst
        return inst

    @property
    @override
    def qualified_name(self) -> str:
        """This instantiation's mangled symbol name, e.g. ``mod::Pair[i32, i32]``.

        The type arguments are qualified too, rather than rendered as
        :attr:`name` renders them: two same-named structs declared in
        different modules are different types, so ``Box[a::Foo]`` and
        ``Box[b::Foo]`` must not arrive at one symbol.
        """
        qualified = f"{self.mod_name}::{self.ast.ident.name}"
        if not self._typ_args:
            return qualified
        arg_names = ", ".join(typ_arg.qualified_name for typ_arg in self._typ_args)
        return f"{qualified}[{arg_names}]"

    @override
    def is_concrete(self) -> bool:
        return all(typ_arg.is_concrete() for typ_arg in self._typ_args)

    @property
    def is_generic_template(self) -> bool:
        """Return whether this is the declaration's uninstantiated generic template."""
        return bool(self.ast.generic_params) and not self._typ_args

    @override
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        if not self._typ_args:
            return self
        substituted = tuple(typ_arg.substitute_typ_params(mapping) for typ_arg in self._typ_args)
        if substituted == self._typ_args:
            return self
        template = StructTyp.get(self.ast, self._decl_env)
        return template.instance(substituted)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        if isinstance(actual, StructTyp) and actual.ast is self.ast:
            for declared_arg, actual_arg in zip(self._typ_args, actual._typ_args, strict=True):
                declared_arg.infer_typ_args(actual_arg, bindings)

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key struct types by declaration identity and type arguments."""
        asserts.assert_gt(len(args), 0)
        struct_ast = asserts.checked_cast(args[0], ast.StructDefn)
        typ_args = asserts.checked_cast(args[2], tuple) if len(args) > 2 else ()
        return (cls, struct_ast, typ_args)

    @property
    @override
    def name(self) -> str:
        if not self._typ_args:
            return self.ast.ident.name
        arg_names = ", ".join(typ_arg.name for typ_arg in self._typ_args)
        return f"{self.ast.ident.name}[{arg_names}]"

    def _add_field(self, field: StructField) -> None:
        """Register ``field`` in this struct's field namespace.

        :raises ReservedNameError: If ``field`` takes a reserved name.
        """
        if reserved.is_reserved(field.name):
            raise errors.ReservedNameError(field.name, field.ast.ident.span)
        existing = self._members.get(field.name)
        if existing is not None:
            raise errors.DuplicateFieldInStructDefnError(
                field.name, field.ast.ident.span, existing.ast.ident.span
            )
        self._members[field.name] = field

    @functools.cached_property
    def fields(self) -> types.MappingProxyType[str, StructField]:
        """Return fields by declaration order after rejecting infinite-size layouts."""
        self._check_finite_size([], [])
        return types.MappingProxyType(self._members)

    def field_at(self, index: int) -> StructField:
        """Return the field at declaration-order ``index``."""
        return tuple(self.fields.values())[index]

    def _check_finite_size(
        self,
        visiting: list[StructTyp],
        hops: list[tuple[str, str, Optional[src.SrcSpan], str]],
    ) -> None:
        """Reject by-value cycles and runaway generic nesting while resolving fields."""
        if self in visiting:
            cycle_start = visiting.index(self)
            raise errors.InfiniteSizeStructError(self.name, self.span, hops[cycle_start:])
        if len(visiting) >= _MAX_STRUCT_INSTANTIATION_DEPTH:
            outermost = visiting[0] if visiting else self
            raise errors.TypInstantiationDepthExceededError(outermost.name, outermost.span)

        visiting = [*visiting, self]
        for field_ast in self.ast.fields:
            typ = Typ.from_ast(field_ast.typ, self._env)
            while isinstance(typ, ArrayTyp):
                typ = typ.element_typ
            if isinstance(typ, StructTyp):
                hop = (self.name, field_ast.ident.name, field_ast.span, typ.name)
                typ._check_finite_size(visiting, [*hops, hop])

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this struct's declaration."""
        return self.ast.span


class EnumTyp(Typ):
    """An enum type: a fixed, named set of integer discriminants backed by
    an explicit or inferred integer type.

    Unlike :class:`StructTyp`, never generic and never instantiated - one
    ``enum`` declaration is always exactly one ``EnumTyp``.

    :param enum_ast: The parsed enum declaration.
    :param e: The enclosing scope, used to resolve the backing type.
    :param mod_name: The name of the module this enum is declared in.
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

        :raises DuplicateVariantInEnumDefnError: If two variants share a name.
        :raises ReservedNameError: If a variant takes a reserved name.
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

        :raises EnumBackingTypNotIntError: If an explicit backing type is
            given and isn't an integer type.
        :raises EnumVariantValueTypMismatchError: If an explicit backing
            type is given and a discriminant literal's own explicit type
            suffix doesn't coerce to it.
        :raises IntLitOverflowError: If an explicit backing type is given
            and a discriminant doesn't fit it.
        :raises EnumDiscriminantOverflowError: If no explicit backing type
            is given and a discriminant doesn't fit any builtin integer type.
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
    signature (see ``__enum_to_int``'s :class:`~leech.ir_builtins.EnumToIntIntrinsicFn`,
    whose return type can't be spelled as any concrete type until its own
    type argument is known to be a concrete :class:`EnumTyp`) - substituting
    a concrete enum type for ``inner`` collapses this straight to that
    enum's real :attr:`EnumTyp.backing_typ`, so it never survives into a
    fully-substituted, lowerable signature.

    :param inner: The (possibly still a type parameter) type whose backing
        type this stands for; must resolve to an :class:`EnumTyp` once
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
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
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
