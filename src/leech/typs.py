# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Leech's type system: type representations, caching, and construction from AST."""

import abc
import dataclasses
import enum
import functools
import inspect
import re
import types
import weakref
from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Final, Optional, cast, override

from leech import (
    asserts,
    ast,
    compilation,
    diag,
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
    if isinstance(typ, StructTyp | UnionTyp):
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
        if isinstance(left, UnionTyp) and isinstance(right, UnionTyp):
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
                        e.ctx.fail_cycle(
                            cycle,
                            errors.RecursiveTraitBoundError(
                                repeated_bound.path.str(),
                                repeated_bound.path.span,
                                entries,
                            ),
                        )
                    application = sub_env.resolve_trait(bound.path)
            else:
                application = sub_env.resolve_trait(bound.path)
            if application.args:
                # Matching these against an impl's own trait arguments needs generic
                # traits. Nothing upstream rejects the bound, so fail loudly rather
                # than silently ignore the arguments.
                raise NotImplementedError(
                    "checking bounds with generic arguments isn't supported yet"
                )
            if not e.ctx.impl_registry.implements(application.trait, typ_arg):
                return typ_param, typ_arg, application.trait
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
            lit_typ: IntTyp = IntTyp(arg_ast.explicit_width, arg_ast.explicit_signage)
        elif isinstance(param, ValueParamTyp) and isinstance(param.value_typ, IntTyp):
            lit_typ = param.value_typ
        else:
            raise errors.WrongKindOfComptimeArgError(param.name, arg_ast.diag_str(), arg_ast.span)
        if not lit_typ.fits(arg_ast.value):
            raise errors.IntLitOverflowError(arg_ast.value, lit_typ.name, arg_ast.span)
        return ComptimeValueTyp(lit_typ, arg_ast.value)
    if isinstance(arg_ast, ast.BoolLit):
        return ComptimeValueTyp(BOOL, arg_ast.value)
    if isinstance(param, ValueParamTyp) and isinstance(arg_ast, ast.BasicTyp):
        # A path argument for a value parameter forwards either another
        # value parameter or the concrete value substituted for one.
        return e.resolve_comptime_value(arg_ast.path)
    return Typ.from_ast(arg_ast, e)


def resolve_explicit_comptime_args(
    item_name: str,
    comptime_params: Sequence[ComptimeParamTyp],
    args_ast: Sequence[ast.ComptimeArg],
    e: ir_env.Env,
    span: Optional[src.SrcSpan],
) -> tuple[Typ, ...]:
    """Resolve and bounds-check an item's explicitly supplied comptime arguments."""
    if not comptime_params:
        if args_ast:
            raise errors.ComptimeArgsOnNonGenericItemError(item_name, span)
        return ()
    if not args_ast:
        raise errors.MissingComptimeArgsError(item_name, span)
    if len(args_ast) != len(comptime_params):
        raise errors.WrongNumberOfComptimeArgsError(
            item_name, len(args_ast), len(comptime_params), span
        )
    comptime_args = tuple(
        resolve_comptime_arg(param, arg_ast, e)
        for param, arg_ast in zip(comptime_params, args_ast, strict=True)
    )
    check_comptime_arg_bounds(comptime_params, comptime_args, e, span)
    return comptime_args


def apply_generic_typ(
    template: GenericTypTemplate,
    args_ast: tuple[ast.ComptimeArg, ...],
    e: ir_env.Env,
    span: src.SrcSpan,
) -> TypKind:
    """Resolve, bounds-check, and apply one generic type path segment."""
    comptime_args = resolve_explicit_comptime_args(
        template.name, template.comptime_params, args_ast, e, span
    )
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
        if isinstance(arg, ValueParamTyp | ComptimeValueTyp):
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

    Types are compared with ``is``, so each type has one canonical instance.
    How that instance is found depends on what determines the type's identity:

    - A structural type (an ``InternedTyp``) is fully determined by its
      constructor arguments and holds no environment, so constructing one
      returns the process-wide instance for those arguments. A compound one
      built over a declaration-derived type holds that type's identity in
      its key, so it cannot alias across compilations.
    - A declaration-derived type (a struct or union instance, an enum, a
      source-declared comptime parameter) is determined by one compilation's
      declaration, and is constructed once by the object that owns it: its
      template, its module, or its declaring item.
    """

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

    def diag_str(self) -> str:
        """Return ``name``, which is how a diagnostic shows the type."""
        return self.name

    def report_proof(self) -> Optional[diag.ReportProof]:
        """Return ``None``: a type records no errors reported about it."""
        return None

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
                return PtrTyp(
                    Typ.from_ast(typ_ast.pointee_typ, e),
                    Mutability.from_ast(typ_ast.mut),
                )

    @staticmethod
    def _basic_typ_from_ast(typ_ast: ast.BasicTyp, e: ir_env.Env) -> TypKind:
        """Resolve a named type, including applications on its path segments."""
        return e.resolve_typ(typ_ast.path)


class _InterningMeta(abc.ABCMeta):
    """Makes constructing a class return its interned instance for those arguments."""

    _instances: weakref.WeakValueDictionary[tuple[Hashable, ...], Any]

    def __new__(
        mcs, name: str, bases: tuple[type, ...], ns: dict[str, Any], /, **kwargs: Any
    ) -> _InterningMeta:
        # Equal calls must give equal keys, so a default argument would let
        # PtrTyp(t) and PtrTyp(t, CONST) be distinct instances.
        init = ns.get("__init__")
        if init is not None:
            for param in inspect.signature(init).parameters.values():
                assert param.default is inspect.Parameter.empty, (
                    f"{name}.__init__: an interned type takes no default arguments"
                )
        cls = super().__new__(mcs, name, bases, ns, **kwargs)
        # One cache per class, so a key needs no class component.
        cls._instances = weakref.WeakValueDictionary()
        return cls

    def __call__[T](cls: type[T], *args: Hashable) -> T:
        instances = asserts.checked_cast(cls, _InterningMeta)._instances
        obj = instances.get(args)
        if obj is None:
            obj = type.__call__(cls, *args)
            instances[args] = obj
        return cast(T, obj)


class InternedTyp(Typ, metaclass=_InterningMeta):
    """A structural type: constructing it with equal arguments gives the same instance.

    Its identity is fully determined by its positional constructor arguments,
    so it is interned process-wide and may be compared with ``is``. Instances
    are held weakly, so one lives only while something references it. Its
    ``__init__`` runs only when no instance exists for its arguments, so it
    must do nothing but store them.
    """


class IntTyp(InternedTyp):
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
        return IntTyp(width, sign)


class BoolTyp(InternedTyp):
    """The boolean type."""

    @property
    @override
    def name(self) -> str:
        return "bool"


type ComptimeLiteralTyp = IntTyp | BoolTyp
"""A type that can back a literal comptime value parameter or argument."""


class CallableTyp(InternedTyp):
    """Base class for types of things that can be called."""

    ret_typ: Final[TypKind]
    param_typs: Final[tuple[TypKind, ...]]

    def __init__(self, ret_typ: Typ, param_typs: tuple[Typ, ...]) -> None:
        self.ret_typ = as_typ_kind(ret_typ)
        self.param_typs = tuple(as_typ_kind(typ) for typ in param_typs)


class FnTyp(CallableTyp):
    """The type of a function, e.g. ``fn(i32, i32) bool``."""

    @property
    @override
    def name(self) -> str:
        param_strs = ", ".join(typ.name for typ in self.param_typs)
        return f"fn({param_strs}) {self.ret_typ.name}"

    @override
    def substitute_typ_params(self, mapping: Mapping[ComptimeParamTyp, Typ]) -> Typ:
        return FnTyp(
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


class PtrTyp(InternedTyp):
    """A pointer type, e.g. ``*i32`` or ``*mut i32``."""

    pointee_typ: Final[TypKind]
    mut: Final[Mutability]

    def __init__(self, pointee_typ: Typ, mut: Mutability) -> None:
        self.pointee_typ = as_typ_kind(pointee_typ)
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
        return PtrTyp(self.pointee_typ.substitute_typ_params(mapping), self.mut)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[ComptimeParamTyp, Typ]) -> None:
        if isinstance(actual, PtrTyp):
            self.pointee_typ.infer_typ_args(actual.pointee_typ, bindings)

    @override
    def is_concrete(self) -> bool:
        return self.pointee_typ.is_concrete()

    def _new_with_mut(self, mut: Mutability) -> PtrTyp:
        """Return the interned pointer with this pointee and ``mut``."""
        return PtrTyp(self.pointee_typ, mut)


class ArrayTyp(InternedTyp):
    """A fixed-length array type, e.g. ``array[i32, 4]``."""

    element_typ: Final[TypKind]
    length: Final[TypKind]

    def __init__(self, element_typ: Typ, length: Typ) -> None:
        self.element_typ = as_typ_kind(element_typ)
        self.length = as_typ_kind(length)

    @staticmethod
    def of_length(element_typ: TypKind, length: int) -> ArrayTyp:
        """Return the array type with a concrete literal ``length``."""
        return ArrayTyp(element_typ, ComptimeValueTyp(USIZE, length))

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
        return ArrayTyp(
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
    """Shared identity and substitution for a comptime parameter.

    Each parameter is a distinct object, constructed once by whatever
    declares it, and is compared by identity. Never instantiated directly;
    use ``TypParamTyp`` or ``ValueParamTyp``.

    :param decl_env: The scope what this parameter's declaration names is
        resolved in, holding every sibling parameter - see
        ``comptime_params_from_ast``. Absent for a parameter the compiler
        declares itself, which names nothing.
    """

    _owner: Final[object]
    _name: Final[str]
    decl_env: Final[Optional[ir_env.Env]]

    def __init__(self, owner: object, name: str, decl_env: Optional[ir_env.Env]) -> None:
        self._owner = owner
        self._name = name
        self.decl_env = decl_env

    @property
    def owner(self) -> object:
        """The item declaring this parameter."""
        return self._owner

    @property
    def ctx(self) -> compilation.Ctx:
        """The compilation of a source-declared parameter."""
        return opt_util.opt_unwrap(self.decl_env).ctx

    @abc.abstractmethod
    def check(self) -> None:
        """Resolve and validate what a source-declared parameter's declaration names.

        Deferred until the whole module graph is built, so a bound or a
        declared type may name an item declared after this parameter.
        """

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
    """An opaque generic type parameter.

    It has no LLVM representation and stores only its display name, trait
    bounds and the scope those bounds are written in.
    """

    bounds: Final[tuple[ast.BasicTyp, ...]]

    def __init__(
        self,
        owner: object,
        name: str,
        bounds: tuple[ast.BasicTyp, ...] = (),
        decl_env: Optional[ir_env.Env] = None,
    ) -> None:
        super().__init__(owner, name, decl_env)
        self.bounds = bounds

    def declares_bound(self, trait: ir_traits.Trait) -> bool:
        """Whether this parameter's own declared bounds include ``trait``.

        This is what stands in for an impl inside a generic definition:
        nothing is registered against a type parameter, so what its
        declaration assumes about it is all that can be known.
        """
        return any(
            opt_util.opt_unwrap(self.decl_env).resolve_trait(bound.path).trait is trait
            for bound in self.bounds
        )

    @override
    @compilation.unit
    def check(self) -> None:
        decl_env = opt_util.opt_unwrap(self.decl_env)
        for bound in self.bounds:
            decl_env.resolve_trait(bound.path)

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
    """An opaque comptime value parameter.

    It has no LLVM representation of its own; a concrete argument
    substitutes to a ``ComptimeValueTyp`` of the same ``value_typ``.

    :param declared_typ: The already-resolved value type, for a parameter
        the compiler declares itself, such as ``array``'s length.
    :param param_ast: The source declaration, whose written type is
        resolved on demand rather than at construction, so it may name a
        type declared later. Mutually exclusive with ``declared_typ``.
    """

    _declared_typ: Final[Optional[ComptimeLiteralTyp]]
    _param_ast: Final[Optional[ast.ValueParam]]

    def __init__(
        self,
        owner: object,
        name: str,
        declared_typ: Optional[ComptimeLiteralTyp] = None,
        param_ast: Optional[ast.ValueParam] = None,
        decl_env: Optional[ir_env.Env] = None,
    ) -> None:
        assert declared_typ is None or param_ast is None, (
            f"{name}: a resolved declared type excludes a source-written one"
        )
        super().__init__(owner, name, decl_env)
        self._declared_typ = declared_typ
        self._param_ast = param_ast

    @property
    def value_typ(self) -> ComptimeLiteralTyp:
        """This parameter's declared type - an ``IntTyp`` or ``BOOL``."""
        if self._declared_typ is not None:
            return self._declared_typ
        return self._written_value_typ

    @property
    @compilation.unit
    def _written_value_typ(self) -> ComptimeLiteralTyp:
        # Resolving it is what rejects a type no comptime value can have.
        typ_ast = opt_util.opt_unwrap(self._param_ast).typ
        typ = Typ.from_ast(typ_ast, opt_util.opt_unwrap(self.decl_env))
        if not isinstance(typ, IntTyp | BoolTyp):
            raise errors.InvalidValueParamTypError(typ.name, typ_ast.span)
        return typ

    @override
    @compilation.unit
    def check(self) -> None:
        _ = self.value_typ


class ComptimeValueTyp(InternedTyp):
    """A concrete compile-time value used as a comptime argument.

    The type-level counterpart to ``ir_values.ComptimeValue``:
    a ``Typ`` node whose entire content is one concrete compile-time value,
    interned like every other structural type - equal values are
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
    owner: object, comptime_params: Sequence[ast.ComptimeParamKind], e: ir_env.Env
) -> tuple[ComptimeParamTyp, ...]:
    """Build parsed comptime parameters declared by ``owner``, preserving declaration order.

    Each parameter's kind comes from its own syntax, so no name is looked
    up here. What its bounds or declared type name is validated later
    instead, by ``ComptimeParamTyp.check``, driven by the
    record this leaves on ``e.ctx``.

    The parameters resolve their own bounds and declared types in a
    dedicated child of ``e`` holding all of them, so a bound may name a
    sibling declared either side of it.
    """
    for param_ast in comptime_params:
        if reserved.is_reserved(param_ast.ident.name):
            raise errors.ReservedNameError(param_ast.ident.name, param_ast.ident.span)

    param_env = e.new_child()
    result: list[ComptimeParamTyp] = []
    for param_ast in comptime_params:
        param: ComptimeParamTyp
        match param_ast:
            case ast.TypParam():
                param = TypParamTyp(owner, param_ast.ident.name, param_ast.bounds, param_env)
            case ast.ValueParam():
                param = ValueParamTyp(owner, param_ast.ident.name, None, param_ast, param_env)
        result.append(param)
        e.ctx.record_comptime_param(param)
    # A second pass, so a bound may name a sibling declared after it.
    for param, param_ast in zip(result, comptime_params, strict=True):
        param_env.add_container(param.name, param, param_ast.ident.span)
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

    @property
    def ctx(self) -> compilation.Ctx:
        return self._env.ctx

    @property
    @compilation.unit
    def typ(self) -> TypKind:
        """The field's type."""
        return Typ.from_ast(self.ast.typ, self._env)

    @property
    @compilation.unit
    def access(self) -> visibility.Access:
        """Whether the field is public or private."""
        return visibility.Access.from_ast(self.ast.access)

    @property
    @compilation.unit
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

    ``apply_generic_typ`` applies comptime arguments to any instance
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


class NominalTypTemplate[InstanceT: StructTyp | UnionTyp](GenericTypTemplate):
    """A struct or union declaration, which owns its usable type instances.

    Each instance is constructed once per argument list and held here, so
    instances live exactly as long as the declaration's compilation.
    """

    _decl_env: Final[ir_env.Env]
    mod_name: Final[str]
    _instances: Final[dict[tuple[Typ, ...], InstanceT]]

    def __init__(self, e: ir_env.Env, mod_name: str) -> None:
        self._decl_env = e
        self.mod_name = mod_name
        self._instances = {}

    @override
    def instantiate(self, comptime_args: tuple[Typ, ...]) -> InstanceT:
        """Return the instance for ``comptime_args`` and record its request."""
        asserts.assert_eq(len(comptime_args), len(self.comptime_params))
        return self._instance(comptime_args, record_request=True)

    @functools.cached_property
    def validation_instance(self) -> InstanceT:
        """The instance applying the declaration's own comptime parameters, for validating it."""
        asserts.assert_gt(len(self.comptime_params), 0)
        return self._instance(self.comptime_params, record_request=False)

    @property
    def ctx(self) -> compilation.Ctx:
        return self._decl_env.ctx

    @compilation.unit
    def check(self) -> None:
        """Report any error in this declaration's layout or member types.

        Its instances are checked separately, since their member types depend on their
        arguments. The declaration's own check uses its own parameters as arguments, and
        reports a layout error with the declaration's bare name.
        """
        _check_layout_finite(self.validation_instance, None, self.name)

    @functools.cached_property
    def module_instance(self) -> InstanceT:
        """Return the zero-argument instance bound for a non-generic declaration."""
        asserts.assert_eq(len(self.comptime_params), 0)
        return self._instance((), record_request=False)

    def _instance(self, comptime_args: tuple[Typ, ...], *, record_request: bool) -> InstanceT:
        """Return the instance for ``comptime_args``, constructing it the first time.

        :param record_request: Whether a newly constructed instance is
            recorded as requested, so ``mono.discover`` finds it and code
            generation emits it. ``False`` for an instance code generation
            already reaches another way: a non-generic declaration's
            zero-argument module instance, and a generic declaration's
            opaque validation instance. Ignored when the instance exists,
            since only the request that constructs it can be recorded.
        """
        instance = self._instances.get(comptime_args)
        if instance is None:
            instance = self._new_instance(comptime_args)
            assert comptime_args not in self._instances, "instance creation re-entered itself"
            self._instances[comptime_args] = instance
            if record_request:
                self._record_request(instance)
        return instance

    @abc.abstractmethod
    def _new_instance(self, comptime_args: tuple[Typ, ...]) -> InstanceT:
        pass

    @abc.abstractmethod
    def _record_request(self, instance: InstanceT) -> None:
        pass


class StructTypTemplate(NominalTypTemplate["StructTyp"]):
    """A struct declaration that owns its usable type instances."""

    ast: Final[ast.StructDefn]

    def __init__(self, struct_ast: ast.StructDefn, e: ir_env.Env, mod_name: str) -> None:
        super().__init__(e, mod_name)
        self.ast = struct_ast
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
    def _new_instance(self, comptime_args: tuple[Typ, ...]) -> StructTyp:
        return StructTyp(self, comptime_args)

    @override
    def _record_request(self, instance: StructTyp) -> None:
        self._decl_env.ctx.record_struct_request(instance)


class StructTyp(Typ):
    """A usable nominal struct instance owned by one declaration template."""

    #: Names this kind of type in layout diagnostics.
    _LAYOUT_KIND: ClassVar[str] = "struct"

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

    @property
    def ctx(self) -> compilation.Ctx:
        return self._env.ctx

    @property
    @compilation.unit
    def fields(self) -> types.MappingProxyType[str, StructField]:
        """Return fields by declaration order after rejecting infinite-size layouts."""
        _check_layout_finite(self, None, None)
        return types.MappingProxyType(self._fields)

    @compilation.unit
    def check(self) -> None:
        for field in self.fields.values():
            _ = field.typ

    def field_at(self, index: int) -> StructField:
        """Return the field at declaration-order ``index``."""
        return tuple(self.fields.values())[index]

    def _layout_edges(
        self, container_name: str
    ) -> Iterator[tuple[errors.TypLayoutHopKind, NominalLayoutTyp]]:
        for field_ast in self.ast.fields:
            contained = _by_value_nominal(Typ.from_ast(field_ast.typ, self._env))
            if contained is None:
                continue
            yield (
                errors.StructFieldHop(
                    container_name, contained.name, field_ast.span, field_ast.ident.name
                ),
                contained,
            )

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this struct's declaration."""
        return self.template.span


class UnionVariantTemplate:
    """A union variant of a declaration, independent of any instantiation.

    A variant can be named before its union's comptime arguments are
    known, which the instance-scoped ``UnionVariant`` cannot represent.
    Its payload types therefore resolve against the declaration's own
    comptime parameters, so inferring those arguments from a payload
    never has to reach for an instance's environment.
    """

    index: Final[int]
    ast: Final[ast.UnionVariantDefn]
    _param_env: Final[ir_env.Env]

    def __init__(
        self, index: int, variant_ast: ast.UnionVariantDefn, param_env: ir_env.Env
    ) -> None:
        self.index = index
        self.ast = variant_ast
        self._param_env = param_env

    @property
    def name(self) -> str:
        return self.ast.ident.name

    @property
    def arity(self) -> int:
        """How many payload values the variant carries."""
        return len(self.ast.payload_typs)

    @property
    def ctx(self) -> compilation.Ctx:
        return self._param_env.ctx

    @property
    @compilation.unit
    def payload_typs(self) -> tuple[TypKind, ...]:
        """The payload types over the declaration's own comptime parameters."""
        return tuple(Typ.from_ast(typ_ast, self._param_env) for typ_ast in self.ast.payload_typs)


class UnionVariant:
    """One instance's view of a declared variant, with its resolution scope.

    Everything but the payload types is the declaration's, so they are
    read through ``template`` rather than copied.
    """

    template: Final[UnionVariantTemplate]
    _env: Final[ir_env.Env]

    def __init__(self, template: UnionVariantTemplate, e: ir_env.Env) -> None:
        self.template = template
        self._env = e

    @property
    def index(self) -> int:
        return self.template.index

    @property
    def ast(self) -> ast.UnionVariantDefn:
        return self.template.ast

    @property
    def name(self) -> str:
        return self.template.name

    @property
    def tag(self) -> int:
        """The discriminant stored for this variant, which is its declaration index."""
        return self.index

    @property
    def arity(self) -> int:
        """How many payload values the variant carries."""
        return self.template.arity

    @property
    def ctx(self) -> compilation.Ctx:
        return self._env.ctx

    @property
    @compilation.unit
    def payload_typs(self) -> tuple[TypKind, ...]:
        """The variant's payload types, substituted for this instance's arguments."""
        return tuple(Typ.from_ast(typ_ast, self._env) for typ_ast in self.ast.payload_typs)


class UnionTypTemplate(NominalTypTemplate["UnionTyp"]):
    """A tagged-union declaration that owns its usable type instances."""

    ast: Final[ast.UnionDefn]
    variants: Final[types.MappingProxyType[str, UnionVariantTemplate]]
    #: The declaration environment with this union's own comptime parameters
    #: bound, which is the scope a variant's payload types resolve in.
    _param_env: Final[ir_env.Env]

    def __init__(self, union_ast: ast.UnionDefn, e: ir_env.Env, mod_name: str) -> None:
        super().__init__(e, mod_name)
        self.ast = union_ast
        # Bind parameters before variants so parameter-name errors take priority.
        self._param_env = e.new_child()
        for comptime_param in self.comptime_params:
            self._param_env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, ValueParamTyp):
                self._param_env.add_var(comptime_param.name, comptime_param)
        variants: dict[str, UnionVariantTemplate] = {}
        for index, variant_ast in enumerate(union_ast.variants):
            name = variant_ast.ident.name
            if reserved.is_reserved(name):
                raise errors.ReservedNameError(name, variant_ast.ident.span)
            existing = variants.get(name)
            if existing is not None:
                raise errors.DuplicateVariantInUnionDefnError(
                    name, variant_ast.ident.span, existing.ast.ident.span
                )
            variants[name] = UnionVariantTemplate(index, variant_ast, self._param_env)
        self.variants = types.MappingProxyType(variants)

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @property
    def span(self) -> src.SrcSpan:
        """The source location of the union declaration."""
        return self.ast.span

    @override
    def calculate_comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        return comptime_params_from_ast(self.ast, self.ast.comptime_params, self._decl_env)

    @override
    def _new_instance(self, comptime_args: tuple[Typ, ...]) -> UnionTyp:
        return UnionTyp(self, comptime_args)

    @override
    def _record_request(self, instance: UnionTyp) -> None:
        self._decl_env.ctx.record_union_request(instance)


class UnionTyp(Typ):
    """A usable nominal tagged-union instance owned by one declaration template."""

    #: Names this kind of type in layout diagnostics.
    _LAYOUT_KIND: ClassVar[str] = "union"

    template: Final[UnionTypTemplate]
    comptime_args: Final[tuple[Typ, ...]]
    _env: Final[ir_env.Env]
    _variants: Final[tuple[UnionVariant, ...]]

    def __init__(self, template: UnionTypTemplate, comptime_args: tuple[Typ, ...]) -> None:
        asserts.assert_eq(len(comptime_args), len(template.comptime_params))
        self.template = template
        self.comptime_args = comptime_args
        self._env = template._decl_env.new_child()
        for typ_param, typ_arg in zip(template.comptime_params, comptime_args, strict=True):
            self._env.add_container(typ_param.name, typ_arg)
        self._variants = tuple(
            UnionVariant(variant_template, self._env)
            for variant_template in template.variants.values()
        )

    @property
    def ast(self) -> ast.UnionDefn:
        """The declaration AST shared by this template's instances."""
        return self.template.ast

    @property
    def mod_name(self) -> str:
        """The name of the module that declares this union."""
        return self.template.mod_name

    @property
    @override
    def name(self) -> str:
        if not self.comptime_args:
            return self.template.name
        arg_names = ", ".join(typ_arg.name for typ_arg in self.comptime_args)
        return f"{self.template.name}[{arg_names}]"

    @property
    @override
    def qualified_name(self) -> str:
        """This instantiation's mangled symbol name, e.g. ``mod::Option[i32]``.

        The comptime arguments are qualified too, rather than rendered as
        ``name`` renders them: two same-named unions declared in different
        modules are different types, so ``Option[a::Foo]`` and
        ``Option[b::Foo]`` must not arrive at one symbol.
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
        if isinstance(actual, UnionTyp) and actual.template is self.template:
            for declared_arg, actual_arg in zip(
                self.comptime_args, actual.comptime_args, strict=True
            ):
                declared_arg.infer_typ_args(actual_arg, bindings)

    @property
    def ctx(self) -> compilation.Ctx:
        return self._env.ctx

    @property
    @compilation.unit
    def variants(self) -> tuple[UnionVariant, ...]:
        """Return variants by declaration order after rejecting infinite-size layouts."""
        _check_layout_finite(self, None, None)
        return self._variants

    @compilation.unit
    def check(self) -> None:
        for variant in self.variants:
            _ = variant.payload_typs

    def variant_at(self, index: int) -> UnionVariant:
        """Return the variant at declaration-order ``index``."""
        return self.variants[index]

    @property
    @compilation.unit
    def tag_typ(self) -> IntTyp:
        """The smallest unsigned builtin integer type holding every variant's tag.

        A union with no variants has no tag value to store, but still needs
        a type for the field, so it takes the narrowest one.
        """
        max_tag = max(len(self.template.ast.variants) - 1, 0)
        for width in (8, 16, 32, 64):
            candidate = IntTyp(width, signage.UNSIGNED)
            if candidate.fits(max_tag):
                return candidate
        raise AssertionError("a declaration cannot list more than 2**64 variants")

    def _layout_edges(
        self, container_name: str
    ) -> Iterator[tuple[errors.TypLayoutHopKind, NominalLayoutTyp]]:
        for variant_ast in self.template.ast.variants:
            for index, typ_ast in enumerate(variant_ast.payload_typs):
                contained = _by_value_nominal(Typ.from_ast(typ_ast, self._env))
                if contained is None:
                    continue
                yield (
                    errors.UnionPayloadHop(
                        container_name,
                        contained.name,
                        typ_ast.span,
                        variant_ast.ident.name,
                        index,
                    ),
                    contained,
                )

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this union's declaration."""
        return self.template.span


@dataclasses.dataclass(frozen=True)
class UnionVariantRef:
    """A named union variant, with whatever its path established about the union.

    ``owner`` is the declaration while the union's comptime arguments are
    still to be inferred, and an instance once a path spells them out. The
    variant is always the declaration's either way; once inference settles
    the arguments, the concrete ``UnionVariant`` is looked up by index on
    the resulting instance.
    """

    owner: UnionTypTemplate | UnionTyp
    variant: UnionVariantTemplate

    @property
    def name(self) -> str:
        """The variant's name, unqualified by its union."""
        return self.variant.name

    @property
    def template(self) -> UnionTypTemplate:
        """The union declaration, whichever kind of owner the path established."""
        if isinstance(self.owner, UnionTyp):
            return self.owner.template
        return self.owner


class EnumTyp(Typ):
    """An enum type: a fixed, named set of integer discriminants backed by
    an explicit or inferred integer type.

    Unlike ``StructTyp``, never generic and never instantiated - one
    ``enum`` declaration is exactly one ``EnumTyp`` in each compilation,
    constructed when its module is built.
    """

    ast: Final[ast.EnumDefn]
    mod_name: Final[str]
    _env: Final[ir_env.Env]

    def __init__(self, enum_ast: ast.EnumDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = enum_ast
        self.mod_name = mod_name
        self._env = e

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @property
    @override
    def qualified_name(self) -> str:
        return f"{self.mod_name}::{self.name}"

    @property
    def ctx(self) -> compilation.Ctx:
        return self._env.ctx

    @property
    @compilation.unit
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

    @compilation.unit
    def check(self) -> None:
        _ = self.backing_typ

    @property
    @compilation.unit
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
                    literal_typ = IntTyp(literal.explicit_width, literal.explicit_signage)
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
            candidate = IntTyp(width, inferred_signage)
            if candidate.fits(min_value) and candidate.fits(max_value):
                return candidate

        widest = IntTyp(64, inferred_signage)
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


class EnumBackingTyp(InternedTyp):
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
        return EnumBackingTyp(substituted_inner)

    @override
    def is_concrete(self) -> bool:
        return self.inner.is_concrete()


class VoidTyp(InternedTyp):
    """The type of an expression that produces no value."""

    @property
    @override
    def name(self) -> str:
        return "void"


class NeverTyp(InternedTyp):
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


type NominalLayoutTyp = StructTyp | UnionTyp
"""A nominal type whose declaration can hold another type by value."""


def _by_value_nominal(typ: Typ) -> Optional[NominalLayoutTyp]:
    """Return the nominal type held by value at ``typ``, unwrapping arrays.

    A pointer stops the walk, since its size does not depend on its
    pointee; an array of any length does not, since LLVM rejects a
    recursive identified struct even through a zero-length array.
    """
    while isinstance(typ, ArrayTyp):
        typ = typ.element_typ
    if isinstance(typ, StructTyp | UnionTyp):
        return typ
    return None


def _layout_display_name(
    typ: NominalLayoutTyp, hop: Optional[errors.TypLayoutHopKind], root_name: Optional[str]
) -> str:
    return root_name if hop is None and root_name is not None else typ.name


def _layout_repeats(earlier: NominalLayoutTyp, current: NominalLayoutTyp) -> bool:
    """Return whether ``current`` repeats or grows ``earlier``'s layout."""
    if earlier is current:
        return True
    if earlier.template is not current.template:
        return False

    for earlier_arg, current_arg in zip(earlier.comptime_args, current.comptime_args, strict=True):
        if not contains_typ(current_arg, earlier_arg):
            return False
    return True


def _check_layout_finite(
    typ: NominalLayoutTyp,
    incoming_hop: Optional[errors.TypLayoutHopKind],
    root_name: Optional[str],
) -> None:
    """Reject exact or structurally growing by-value layout cycles.

    ``incoming_hop`` is the edge this type was reached through, absent at
    the root. ``root_name`` overrides the root's own name, so validating a
    generic declaration through its opaque instance still reports the bare
    declared name.
    """
    detail: tuple[NominalLayoutTyp, Optional[errors.TypLayoutHopKind]] = (typ, incoming_hop)
    with typ._env.ctx.detect_cycle(
        compilation.CycleDomain.TYPE_LAYOUT,
        typ,
        detail,
        same_identity=_layout_repeats,
    ) as cycle:
        if cycle is not None:
            repeated, repeated_hop = cycle.details[0]
            hops = []
            for _, hop in cycle.details[1:]:
                assert hop is not None, "only the root layout frame may omit its incoming hop"
                hops.append(hop)
            typ._env.ctx.fail_cycle(
                cycle,
                errors.InfiniteSizeTypError(
                    repeated._LAYOUT_KIND,
                    _layout_display_name(repeated, repeated_hop, root_name),
                    repeated.span,
                    hops,
                ),
            )

        container_name = _layout_display_name(typ, incoming_hop, root_name)
        for hop, contained in typ._layout_edges(container_name):
            _check_layout_finite(contained, hop, root_name)


type TypKind = (
    IntTyp
    | BoolTyp
    | FnTyp
    | PtrTyp
    | ArrayTyp
    | ComptimeParamTyp
    | ComptimeValueTyp
    | StructTyp
    | UnionTyp
    | EnumTyp
    | EnumBackingTyp
    | VoidTyp
    | NeverTyp
)
"""Every instantiable Typ implementation used by the compiler."""


def as_typ_kind(typ: Typ) -> TypKind:
    """Narrow ``typ`` to ``TypKind``, which every ``Typ`` instance is."""
    return cast(TypKind, typ)


#: The built-in numeric, boolean, string, and control-flow type singletons.
U8 = IntTyp(8, signage.UNSIGNED)
I8 = IntTyp(8, signage.SIGNED)
U32 = IntTyp(32, signage.UNSIGNED)
I32 = IntTyp(32, signage.SIGNED)
USIZE = IntTyp(target.ADDR_SIZE, signage.UNSIGNED)
ISIZE = IntTyp(target.ADDR_SIZE, signage.SIGNED)
CINT = I32
BOOL = BoolTyp()
CSTR = PtrTyp(U8, CONST)
VOID = VoidTyp()
NEVER = NeverTyp()


class ArrayTypTemplate(GenericTypTemplate):
    """The singleton generic template underlying ``array[T, N]``."""

    @property
    @override
    def name(self) -> str:
        return "array"

    @override
    def calculate_comptime_params(self) -> tuple[ComptimeParamTyp, ...]:
        return (
            TypParamTyp(self, "T"),
            ValueParamTyp(self, "N", USIZE),
        )

    @override
    def instantiate(self, comptime_args: tuple[Typ, ...]) -> ArrayTyp:
        elt_typ, length = comptime_args
        return ArrayTyp(elt_typ, length)


#: The singleton backing the built-in ``array[T, N]`` type.
ARRAY_TEMPLATE: Final[ArrayTypTemplate] = ArrayTypTemplate()
