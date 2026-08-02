"""Leech's type system: type representations, caching, and construction from AST."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Hashable, Mapping
from enum import Enum
from functools import cached_property
from types import MappingProxyType
from typing import ClassVar, Final, Optional, Self, override
from weakref import WeakValueDictionary

from leech import ast, ir_env, ir_module
from leech.asserts import assert_eq, assert_gt, assert_not_in, checked_cast
from leech.errors import (
    BoundNotATraitError,
    DuplicateFieldInStructDefnError,
    DuplicateItemDefnError,
    InfiniteSizeStructError,
    MissingTypArgsError,
    TypArgsOnNonGenericItemError,
    TypInstantiationDepthExceededError,
    UnsatisfiedBoundError,
    WrongNumberOfTypArgsError,
)
from leech.signage import SIGNED, UNSIGNED, Signage
from leech.src import SrcFile, SrcSpan
from leech.target import ADDR_SIZE


class Mutability(Enum):
    """Whether a pointer or struct field may be written through."""

    CONST = 0
    MUT = 1

    @staticmethod
    def from_ast(mut_ast: Optional[ast.Mutability]) -> Mutability:
        """Determine mutability from an optional ``mut`` keyword in the AST.

        :param mut_ast: The parsed ``mut`` keyword node, or ``None`` if
            absent.
        :return: :data:`MUT` if ``mut_ast`` is present, otherwise
            :data:`CONST`.
        """
        if mut_ast is None:
            return CONST
        assert_eq(mut_ast.value, "mut")
        return MUT


CONST = Mutability.CONST
MUT = Mutability.MUT


def check_typ_arg_bounds(
    generic_params: list[ast.GenericParam],
    typ_args: tuple[Typ, ...],
    e: ir_env.Env,
    span: Optional[SrcSpan],
) -> None:
    """Raise if any of ``typ_args`` doesn't implement a bound its parameter declares.

    Shared by every generic instantiation site - a struct's (see
    :meth:`Typ._basic_typ_from_ast`) and a generic function call's (see
    :meth:`~leech.typcheck.TypCheck._resolve_generic_call`) - so a bound
    means the same thing, and is enforced the same way, everywhere one can
    be declared.

    :param generic_params: The generic item's own declared type
        parameters, in declaration order.
    :param typ_args: The concrete type given for each of ``generic_params``,
        in the same order.
    :param e: The scope to resolve each bound name in.
    :param span: Where to point a diagnostic at.
    :raises BoundNotATraitError: If one of a parameter's declared bounds
        doesn't name a trait.
    :raises UnsatisfiedBoundError: If a type argument doesn't implement
        one of its parameter's declared bounds.
    """
    # Real circular import: `ir_traits` imports this module at its own
    # top level, so importing it back at this module's top level would
    # try to bind the name before `ir_traits` - still mid-initialization
    # at that point - exists to bind. A local import here, only ever run
    # once every module has finished loading (this runs during
    # typechecking, not import), sidesteps that entirely.
    from leech import ir_traits  # noqa: PLC0415

    for param_ast, typ_arg in zip(generic_params, typ_args, strict=True):
        for bound_path in param_ast.bounds:
            trait = e.resolve_path(ir_env.Env.Namespace.CONTAINERS, bound_path)
            if not isinstance(trait, ir_traits.Trait):
                raise BoundNotATraitError(bound_path.str(), bound_path.span)
            if e.impl_registry.find_impl(trait, typ_arg) is None:
                raise UnsatisfiedBoundError(typ_arg.name, trait.name, param_ast.ident.name, span)


class Typ(ABC):
    """Base class for all Leech types.

    Structural types (e.g. :class:`PtrTyp`, :class:`ArrayTyp`) are
    interned: two calls to :meth:`get_or_create` with equal arguments
    return the *same* object, so types can be compared and hashed by
    identity. The cache holds only weak references, so a type is
    collected once nothing else references it.
    """

    _cache: ClassVar[WeakValueDictionary[Hashable, Typ]] = WeakValueDictionary()

    @classmethod
    def create(cls, *args: Hashable) -> Self:
        """Construct and cache a new instance, keyed by ``args``.

        :param args: The arguments to construct the instance with; also
            used, together with ``cls``, as the cache key (see
            :meth:`cache_key`).
        :return: The newly-created instance.
        :raises AssertionError: If an instance with this cache key already
            exists.
        """
        key = cls.cache_key(*args)
        assert_not_in(key, Typ._cache)
        obj = cls(*args)
        Typ._cache[key] = obj
        return obj

    @classmethod
    def get(cls, *args: Hashable) -> Self:
        """Look up a previously-cached instance, keyed by ``args``.

        :param args: The arguments the instance was created with; also
            used, together with ``cls``, as the cache key (see
            :meth:`cache_key`).
        :return: The cached instance.
        :raises KeyError: If no matching instance has been cached.
        """
        return checked_cast(Typ._cache[cls.cache_key(*args)], cls)

    @classmethod
    def get_or_create(cls, *args: Hashable) -> Self:
        """Look up a cached instance keyed by ``args``, creating it if absent.

        :param args: The arguments to construct the instance with if it
            doesn't already exist; also used, together with ``cls``, as
            the cache key (see :meth:`cache_key`).
        :return: The cached or newly-created instance.
        """
        key = cls.cache_key(*args)
        obj = Typ._cache.get(key)
        if obj is None:
            obj = cls(*args)
            Typ._cache[key] = obj
        return checked_cast(obj, cls)

    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Compute the cache key for an instance constructed with ``args``.

        Subclasses with constructor arguments that don't uniquely and
        hashably identify the instance (e.g. :class:`StructTyp`) override
        this.

        :param args: The arguments the instance was, or would be,
            constructed with.
        :return: The cache key.
        """
        return (cls, *args)

    def coerces_to(self, target: Typ) -> bool:
        """Whether a value of this type may be implicitly converted to ``target``.

        Coercion is only ever applied where the target type is
        unambiguous - a call argument, an assignment, a ``return``, a
        struct literal field - never to unify the operands of an
        operator. See :meth:`~leech.ir_builder.CfgBuilder._coerce`, the sole
        caller.

        This base implementation allows only the identity coercion;
        subclasses widen it.

        :param target: The type being coerced to.
        :return: Whether the coercion is allowed.
        """
        return self == target

    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        """Replace every type parameter within this type with its mapped type.

        Rebuilds through the same interning constructors (``get_or_create``)
        that ordinary type construction uses, so the result is always the
        canonical interned instance for its shape - never a fresh,
        non-interned lookalike.

        This base implementation has no type parameter to replace and
        nothing to recurse into, so it returns ``self`` unchanged - the
        right answer for every type that can't be built from a type
        parameter (including a struct type - structs aren't generic yet).
        :class:`TypParamTyp` itself, and the types that wrap another type
        (:class:`PtrTyp`, :class:`ArrayTyp`, :class:`FnTyp`), override it.

        :param mapping: The concrete type to substitute for each type
            parameter that may appear in this type.
        :return: This type with every type parameter present in
            ``mapping`` replaced by its mapped type.
        """
        return self

    def infer_typ_args(  # noqa: B027 - intentionally empty default, not abstract
        self, actual: Typ, bindings: dict[TypParamTyp, Typ]
    ) -> None:
        """Bind each type parameter within this type from its counterpart in ``actual``.

        The structural counterpart of :meth:`substitute_typ_params`: walks
        this (declared) type and ``actual`` (an argument's already-checked
        type) in parallel, recording the concrete type standing opposite
        each type parameter found. Purely structural, with no unification
        - a shape mismatch (``actual`` isn't the matching composite shape)
        is silently skipped rather than raising, and the first binding for
        a given type parameter wins over a later, conflicting one. Neither
        omission is a problem: ordinary argument type-checking, against
        the signature these bindings are used to substitute, is what
        actually has to accept or reject the call - this only has to
        gather enough bindings to build that signature in the first
        place.

        This base implementation has no type parameter and nothing to
        recurse into, so it's a no-op - the right answer for every type
        that can't be built from one. :class:`TypParamTyp` itself, and the
        types that wrap another type (:class:`PtrTyp`, :class:`ArrayTyp`,
        :class:`FnTyp`), override it.

        :param actual: The already-checked type occupying this type's
            position.
        :param bindings: The accumulated type-parameter-to-concrete-type
            map to add to.
        """

    def is_concrete(self) -> bool:
        """Whether this type names no type parameter, anywhere within it.

        A generic struct's own fields, and a generic function's own
        signature, are checked once against their type parameters left
        opaque - so a type built while checking either of those can still
        contain a :class:`TypParamTyp`, and isn't a real, lowerable type
        until :meth:`substitute_typ_params` replaces every one. This is
        how codegen tells such a type apart from one that already has been
        (see :meth:`~leech.codegen.Compiler._discover_struct_instances`).

        This base implementation has no type parameter to contain, so it's
        unconditionally ``True`` - the right answer for every type that
        can't be built from one. :class:`TypParamTyp` itself, and the
        types that wrap another type (:class:`PtrTyp`, :class:`ArrayTyp`,
        :class:`FnTyp`, :class:`StructTyp`), override it.

        :return: Whether this type is fully concrete.
        """
        return True

    @property
    @abstractmethod
    def name(self) -> str:
        """This type's human-readable name, as used in diagnostics."""

    @staticmethod
    def from_ast(typ_ast: ast.Typ, e: ir_env.Env) -> Typ:
        """Resolve a parsed type expression to a :class:`Typ`.

        :param typ_ast: The parsed type expression.
        :param e: The scope to resolve named types in.
        :return: The resolved type.
        :raises ItemNotFoundError: If ``typ_ast`` names a type that cannot
            be resolved in ``e``.
        :raises MissingTypArgsError: If ``typ_ast`` bare-names a generic
            struct.
        :raises WrongNumberOfTypArgsError: If ``typ_ast`` applies a generic
            struct to the wrong number of type arguments.
        :raises TypArgsOnNonGenericItemError: If ``typ_ast`` applies type
            arguments to a type that isn't generic.
        """
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
        """Resolve a (possibly generic) named type, applying its type arguments if any.

        A plain named type (``i32``, ``T``, a non-generic struct) resolves
        the same way whether or not this ever runs into a generic struct;
        the interesting case is a generic struct's declaration name, which
        :meth:`~ir_env.Env.resolve_typ` alone can only resolve to the
        struct's *template* :class:`StructTyp` (see its class docstring) -
        that has to be applied to ``typ_ast``'s type arguments to become a
        real, usable type. That's distinct from ``typ_ast`` instead naming
        an already-applied instantiation indirectly - e.g. a type
        parameter ``T`` currently bound to ``Box[i32]`` - which resolves
        straight through unchanged, the same as any other plain named
        type: only the bare template itself, reached by name, still needs
        type arguments applied.

        :param typ_ast: The parsed named-type expression.
        :param e: The scope to resolve ``typ_ast``'s path, and its type
            arguments, in.
        :return: The resolved type.
        """
        item = e.resolve_typ(typ_ast.path)
        if not isinstance(item, StructTyp) or not item.ast.generic_params or item.typ_args:
            if typ_ast.generic_args:
                raise TypArgsOnNonGenericItemError(typ_ast.path.str(), typ_ast.span)
            return item

        if not typ_ast.generic_args:
            raise MissingTypArgsError(item.name, typ_ast.span)
        if len(typ_ast.generic_args) != len(item.ast.generic_params):
            raise WrongNumberOfTypArgsError(
                item.name, len(typ_ast.generic_args), len(item.ast.generic_params), typ_ast.span
            )
        typ_args = tuple(Typ.from_ast(arg, e) for arg in typ_ast.generic_args)
        check_typ_arg_bounds(item.ast.generic_params, typ_args, e, typ_ast.span)
        return item.instance(typ_args)


class IntTyp(Typ):
    """A fixed-width signed or unsigned integer type, e.g. ``i32`` or ``u8``.

    :param width: The bit width.
    :param signage: Whether the type is signed or unsigned.
    """

    width: Final[int]
    signage: Final[Signage]

    def __init__(self, width: int, signage: Signage) -> None:
        self.width = width
        self.signage = signage

    @property
    @override
    def name(self) -> str:
        sign_char = "i" if self.signage == SIGNED else "u"
        return f"{sign_char}{self.width}"

    @property
    def min_value(self) -> int:
        """The smallest value representable by this type."""
        if self.signage == SIGNED:
            return -(2 ** (self.width - 1))
        return 0

    @property
    def max_value(self) -> int:
        """The largest value representable by this type."""
        if self.signage == SIGNED:
            return 2 ** (self.width - 1) - 1
        return 2**self.width - 1

    def fits(self, value: int) -> bool:
        """Whether ``value`` is representable by this type.

        :param value: The value to check.
        :return: Whether ``min_value <= value <= max_value``.
        """
        return self.min_value <= value <= self.max_value

    @override
    def coerces_to(self, target: Typ) -> bool:
        """Allow widening to an integer type that can represent every value.

        Comparing ranges is the whole rule, so the awkward cases fall out
        without being special-cased: ``u8`` coerces to ``i16``, and even
        to ``i9``, but not to ``i8``, which can't hold 255; and no signed
        type coerces to an unsigned one, which could never hold its
        negative values.
        """
        return isinstance(target, IntTyp) and (
            target.min_value <= self.min_value and self.max_value <= target.max_value
        )


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

    @override
    def coerces_to(self, target: Typ) -> bool:
        """Allow a mutable pointer where a const one is wanted.

        Giving up the ability to write through a pointer is always safe,
        so ``*mut T`` coerces to ``*T``. The reverse doesn't: a ``*T``
        can't be used where writing is required. Nothing is emitted for
        this coercion - the two have the same representation.
        """
        return super().coerces_to(target) or (
            self.mut == MUT and self.new_with_mut(CONST) == target
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

    def new_with_mut(self, mut: Mutability) -> PtrTyp:
        """Return the pointer type with the same pointee but different mutability.

        :param mut: The mutability of the returned pointer type.
        :return: A (possibly cached, possibly newly-created) pointer type
            with this type's :attr:`pointee_typ` and the given ``mut``.
        """
        return PtrTyp.get_or_create(self.pointee_typ, mut)


class ArrayTyp(Typ):
    """A fixed-length array type, e.g. ``[i32; 4]``.

    :param element_typ: The type of each element.
    :param length: The number of elements.
    """

    element_typ: Final[Typ]
    length: Final[int]

    def __init__(self, element_typ: Typ, length: int) -> None:
        self.element_typ = element_typ
        self.length = length

    @property
    @override
    def name(self) -> str:
        return f"[{self.element_typ.name}; {self.length}]"

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
    """A generic item's type parameter, e.g. the ``T`` in ``fn id[T](x: T) T``.

    Interned by the declaring item's AST node together with the
    parameter's position in its parameter list (:meth:`cache_key`), not by
    name: two parameters called ``T`` on two different generic items - or
    two parameters of the same item - are never the same type, preserving
    the identity-equality invariant every other :class:`Typ` relies on.

    Stands for an as-yet-unknown type: it has no LLVM representation of
    its own, since a generic body is never lowered directly (see
    :meth:`substitute_typ_params` and :meth:`~leech.ir_module.Fn.instance`)
    - :meth:`~leech.codegen.Compiler._ll_typ` asserts it never has to
    translate one.

    :param owner_ast: The AST node of the generic item that declares this
        parameter.
    :param index: The parameter's position in ``owner_ast``'s
        ``generic_params``.
    :param param_ast: The parameter's own declaration, i.e.
        ``owner_ast.generic_params[index]`` - passed separately rather
        than looked up, since only ``owner_ast`` and ``index`` identify
        the type (see :meth:`cache_key`).
    """

    owner_ast: Final[ast.Ast]
    index: Final[int]
    ast: Final[ast.GenericParam]

    def __init__(self, owner_ast: ast.Ast, index: int, param_ast: ast.GenericParam) -> None:
        self.owner_ast = owner_ast
        self.index = index
        self.ast = param_ast

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key type parameters by their declaring item and position alone.

        :param args: The arguments passed to :meth:`~Typ.create` or
            :meth:`~Typ.get_or_create`; ``args[0]`` must be the owning
            item's AST node and ``args[1]`` the parameter's index.
        :return: The cache key.
        """
        assert_gt(len(args), 1)
        return (cls, args[0], args[1])

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

    @override
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        return mapping.get(self, self)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        bindings.setdefault(self, actual)

    @override
    def is_concrete(self) -> bool:
        return False


class StructField:
    """A single field of a :class:`StructTyp`.

    :param index: The field's position among its struct's fields.
    :param ast: The parsed field declaration.
    :param e: The struct's scope, used to resolve the field's type.
    """

    index: Final[int]
    ast: Final[ast.StructFieldDefn]
    env: Final[ir_env.Env]

    def __init__(self, index: int, ast: ast.StructFieldDefn, e: ir_env.Env) -> None:
        self.index = index
        self.ast = ast
        self.env = e

    @property
    def name(self) -> str:
        """The field's name."""
        return self.ast.ident.name

    @cached_property
    def typ(self) -> Typ:
        """The field's type."""
        return Typ.from_ast(self.ast.typ, self.env)

    @cached_property
    def access(self) -> ir_module.Access:
        """Whether the field is public or private."""
        return ir_module.Access.from_ast(self.ast.access)

    @cached_property
    def mut(self) -> Mutability:
        """Whether the field may be written through a mut pointer to the struct.

        A field can never be written through a const struct pointer,
        regardless of this value.
        """
        return Mutability.from_ast(self.ast.mut)

    def is_accessible_from(self, file: SrcFile) -> bool:
        """Whether this field can be read, written, or initialized from
        code in ``file``.

        Public fields are accessible from any module; private fields are
        only accessible from the module the struct is defined in.

        :param file: The source file to check accessibility from.
        :return: Whether the field is accessible from ``file``.
        """
        return self.access == ir_module.PUBLIC or self.ast.span.file.path == file.path


#: The deepest a chain of nested generic struct instantiations reached
#: while resolving a struct's fields (see
#: :meth:`StructTyp._check_finite_size`) may go before it's treated as a
#: runaway expansion rather than a legitimately deep type.
_MAX_STRUCT_INSTANTIATION_DEPTH = 64


class StructTyp(Typ):
    """A struct type, identified by its declaration and, for a generic
    struct, its type arguments.

    Unlike other :class:`Typ` subclasses, two ``StructTyp`` instances are
    never equal even if structurally identical: each ``struct`` definition
    in the source introduces its own distinct type, or - for a generic
    struct - family of types, one per distinct :attr:`typ_args` (see
    :meth:`cache_key`). A non-generic struct's declaration has exactly one
    ``StructTyp``, with :attr:`typ_args` always empty; a generic struct's
    declaration is itself represented by the "template" ``StructTyp``
    that :attr:`typ_args` is empty for too - it's what a bare use of the
    struct's name resolves to (see :meth:`~Typ._basic_typ_from_ast`)
    before type arguments are applied, and its fields, resolved through
    :attr:`env`, name each of its own type parameters opaquely, the same
    way a generic function's own :attr:`~leech.ir_values.Value.typ` does
    (see :class:`~leech.ir_module.GenericFn`) - it's never itself a usable
    type. Applying type arguments looks up or creates the instantiated
    ``StructTyp`` for those arguments instead.

    A struct's *members* - its fields and its associated functions - share
    a single namespace, so a field and an associated function can't have
    the same name. They're registered in two phases: fields by
    ``__init__``, then associated functions by :meth:`add_assoc_fn` as the
    enclosing module builds its ``impl`` blocks (see
    :meth:`~leech.ir_module.Mod.build`, which defers those until after every
    struct is declared). Only the template struct's members - never an
    instantiation's - are populated this way; see :meth:`add_assoc_fn`.

    This makes a struct a named-item container much like a
    :class:`~leech.ir_module.Mod`, and the member API here deliberately
    mirrors ``Mod``'s item API (:meth:`~leech.ir_module.Mod.get_item` and
    friends) so the two can be unified if that becomes worthwhile. It
    isn't yet: ``Mod`` keys its items on *namespace and* name, since a
    module-level variable and type may share a name, whereas a struct
    keys on name alone. If structs or traits gain associated items that
    aren't functions - associated types or consts - that difference will
    need revisiting, and a shared abstraction will have more to share.

    :param ast: The parsed struct declaration.
    :param e: The enclosing scope, used to resolve field types and (for a
        generic struct) type parameters. Recorded as :attr:`decl_env`, so
        a later instantiation can be built against the same scope without
        needing a use site's own (irrelevant) scope.
    :param typ_args: The concrete type to substitute for each of this
        struct's own type parameters, in declaration order - empty for a
        non-generic struct, and for the template of a generic one.
    :param mod_name: The name of the module this struct is declared in,
        used to qualify a generic instantiation's mangled symbol name
        (see :attr:`qualified_name`) - a non-generic struct is qualified
        by its own :class:`~leech.ir_module.ModItem` instead, same as any
        other module item.
    :raises DuplicateFieldInStructDefnError: If the declaration contains
        two fields with the same name.
    """

    ast: Final[ast.StructDefn]
    decl_env: Final[ir_env.Env]
    typ_args: Final[tuple[Typ, ...]]
    mod_name: Final[str]
    env: Final[ir_env.Env]
    #: This struct's members - its fields and its associated functions -
    #: keyed by name, in registration order: fields first, in declaration
    #: order, then associated functions in ``impl``-block order. Fields
    #: and associated functions deliberately share one namespace; see the
    #: class docstring.
    _members: Final[dict[str, StructField | ir_module.Fn]]

    def __init__(
        self,
        ast: ast.StructDefn,
        e: ir_env.Env,
        typ_args: tuple[Typ, ...] = (),
        mod_name: str = "",
    ) -> None:
        self.ast = ast
        self.decl_env = e
        self.typ_args = typ_args
        self.mod_name = mod_name
        self.env = e.new_child()
        # Bound here rather than looked up on demand, so a type parameter
        # resolves like any other named type wherever a field type names
        # it - mirrors NonBuiltinFnSpec.__init__. Concrete arguments for a
        # real instantiation; opaque TypParamTyps for the template, whose
        # fields are only ever checked against the parameters themselves,
        # never lowered.
        if typ_args:
            for param_ast, typ_arg in zip(ast.generic_params, typ_args, strict=True):
                self.env.add_container(param_ast.ident.name, typ_arg)
        else:
            for index, param_ast in enumerate(ast.generic_params):
                self.env.add_container(
                    param_ast.ident.name, TypParamTyp.get_or_create(ast, index, param_ast)
                )
        self._members = {}
        # Registering fields here - rather than lazily, along with
        # `fields` - deliberately resolves no types: a StructField only
        # stores its index, AST node and scope, so this is safe even
        # though a field's type may name a struct declared later in the
        # module.
        for i, field_ast in enumerate(ast.fields):
            self._add_member(StructField(i, field_ast, self.env))

    @cached_property
    def _instance_cache(self) -> dict[tuple[Typ, ...], StructTyp]:
        return {}

    @property
    def instances(self) -> Collection[StructTyp]:
        """Every instantiation of this generic struct requested so far.

        Only meaningful on the template (see the class docstring) - an
        instantiation's own cache is simply never added to, since nothing
        ever calls :meth:`instance` on one. Used by codegen to discover
        instantiations to declare and compile - see
        :meth:`~leech.codegen.Compiler._discover_struct_instances`, which
        further filters to :meth:`is_concrete` ones: this struct's own
        (im)mediate typechecking can request an instantiation that still
        names one of *its own* type parameters (e.g. resolving a generic
        function body's use of ``Box[T]``), and those aren't real,
        lowerable types.
        """
        return self._instance_cache.values()

    def instance(self, typ_args: tuple[Typ, ...]) -> StructTyp:
        """Get this generic struct's instantiation for ``typ_args``, building it if needed.

        Cached, so requesting the same ``typ_args`` twice returns the same
        ``StructTyp`` - preserving the identity-equality invariant every
        other :class:`Typ` relies on - and tracked in :attr:`instances` so
        codegen can find it even though, unlike a module-declared struct,
        it's never bound under a name in any :class:`~leech.ir_env.Env`.

        :param typ_args: The concrete type to substitute for each of this
            struct's own type parameters, in declaration order.
        :return: The (possibly newly-built, possibly cached) instantiation.
        """
        inst = self._instance_cache.get(typ_args)
        if inst is None:
            inst = StructTyp.get_or_create(self.ast, self.decl_env, typ_args, self.mod_name)
            self._instance_cache[typ_args] = inst
        return inst

    @property
    def qualified_name(self) -> str:
        """This instantiation's mangled symbol name, e.g. ``mod.Pair[i32, i32]``.

        Extends :attr:`~leech.ir_module.ModItem.qualified_name`'s
        module-prefixed scheme - an instantiation is never itself a
        ``ModItem`` (nothing declares one directly; it's built on demand
        by :meth:`instance`), so it computes the same form independently.
        """
        return f"{self.mod_name}.{self.name}"

    @override
    def is_concrete(self) -> bool:
        return all(typ_arg.is_concrete() for typ_arg in self.typ_args)

    @override
    def substitute_typ_params(self, mapping: Mapping[TypParamTyp, Typ]) -> Typ:
        if not self.typ_args:
            return self
        substituted = tuple(typ_arg.substitute_typ_params(mapping) for typ_arg in self.typ_args)
        if substituted == self.typ_args:
            return self
        template = StructTyp.get(self.ast, self.decl_env)
        return template.instance(substituted)

    @override
    def infer_typ_args(self, actual: Typ, bindings: dict[TypParamTyp, Typ]) -> None:
        if isinstance(actual, StructTyp) and actual.ast is self.ast:
            for declared_arg, actual_arg in zip(self.typ_args, actual.typ_args, strict=True):
                declared_arg.infer_typ_args(actual_arg, bindings)

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key struct types by their declaration's identity and type arguments.

        :param args: The arguments passed to :meth:`~Typ.create` or
            :meth:`~Typ.get_or_create`; ``args[0]`` must be the struct's
            parsed declaration, and ``args[2]``, if given, its type
            arguments (defaulting to ``()``, matching :meth:`__init__`).
        :return: The cache key.
        """
        assert_gt(len(args), 0)
        struct_ast = checked_cast(args[0], ast.StructDefn)
        typ_args = checked_cast(args[2], tuple) if len(args) > 2 else ()
        return (cls, struct_ast, typ_args)

    @property
    @override
    def name(self) -> str:
        if not self.typ_args:
            return self.ast.ident.name
        arg_names = ", ".join(typ_arg.name for typ_arg in self.typ_args)
        return f"{self.ast.ident.name}[{arg_names}]"

    @staticmethod
    def _member_ident_span(member: StructField | ir_module.Fn) -> Optional[SrcSpan]:
        """The span of ``member``'s name, for duplicate-member diagnostics.

        Points at the identifier rather than the whole declaration, since
        a duplicate member is a problem with its name specifically.

        :param member: The field or associated function to locate.
        :return: The span of the member's name.
        """
        if isinstance(member, StructField):
            return member.ast.ident.span
        assert member.ast is not None
        return member.ast.name.span

    def _add_member(self, member: StructField | ir_module.Fn) -> None:
        """Register ``member`` under its own name among this struct's members.

        A struct's fields and associated functions share a single
        namespace, so this rejects an associated function whose name is
        already taken by a field just as readily as it rejects two fields
        or two associated functions of the same name.

        :param member: The field or associated function to register.
        :raises DuplicateFieldInStructDefnError: If ``member`` is a field
            whose name is already taken.
        :raises DuplicateItemDefnError: If ``member`` is an associated
            function whose name is already taken - by a field or by
            another associated function.
        """
        existing = self._members.get(member.name)
        if existing is not None:
            span = StructTyp._member_ident_span(member)
            existing_span = StructTyp._member_ident_span(existing)
            if isinstance(member, StructField):
                # Every field is registered during construction, before
                # the enclosing module builds any impl block, so the only
                # thing a field can collide with is another field.
                assert isinstance(existing, StructField)
                raise DuplicateFieldInStructDefnError(member.name, span, existing_span)
            raise DuplicateItemDefnError("associated function", member.name, span, existing_span)
        self._members[member.name] = member

    def add_assoc_fn(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this struct's associated functions.

        Binds ``fn`` twice: among this struct's members, where it shares a
        namespace with the struct's fields, and in :attr:`env`, which is
        what lets associated functions of the same struct call each other
        by bare, unqualified name.

        Registered among the members first, so a rejected function is
        never left bound in :attr:`env`. This is the mirror image of
        :meth:`~leech.ir_module.Mod._add_item`, where the ``Env`` is the
        duplicate-definition authority; here the member table is, since
        fields never enter :attr:`env`.

        :param fn: The associated function, already constructed with
            :attr:`env` as its enclosing scope.
        :raises DuplicateItemDefnError: If this struct already has a field
            or an associated function named ``fn.name``.
        """
        self._add_member(fn)
        self.env.add_var(fn.name, fn)

    def get_assoc_fn(self, name: str) -> Optional[ir_module.Fn]:
        """Find this struct's associated function called ``name``.

        A field of that name is deliberately *not* a match: fields are
        reached only through :attr:`fields`, never as
        ``SomeStruct::name`` nor as the callee of a method call.

        :param name: The name to look up.
        :return: The associated function, or ``None`` if this struct has
            no member of that name, or its member of that name is a field.
        """
        member = self._members.get(name)
        return member if isinstance(member, ir_module.Fn) else None

    @cached_property
    def fields(self) -> MappingProxyType[str, StructField]:
        """This struct's fields, keyed by name, in declaration order.

        A view of the field members among this struct's members;
        duplicate names were already rejected as each member was
        registered (see :meth:`add_assoc_fn` and the class docstring).

        :raises InfiniteSizeStructError: If this struct contains itself by
            value, directly or through other structs.
        :raises TypInstantiationDepthExceededError: If resolving this
            struct's fields requires instantiating a generic struct
            nested beyond :data:`_MAX_STRUCT_INSTANTIATION_DEPTH`.
        """
        self._check_finite_size([], [])

        fields = {
            name: member
            for name, member in self._members.items()
            if isinstance(member, StructField)
        }
        return fields.items().mapping

    def _check_finite_size(
        self,
        visiting: list[StructTyp],
        hops: list[tuple[str, str, Optional[SrcSpan], str]],
    ) -> None:
        """Raise if this struct is reachable from itself by value.

        Walks field types directly from :attr:`ast` rather than through
        :attr:`fields`, so this can run as the first step of resolving
        :attr:`fields` itself without recursing into it.

        :param visiting: The structs on the path from the struct whose
            size is being checked down to this one, in traversal order.
        :param hops: The ``(containing, field_name, field_span,
            contained)`` edge for each step in ``visiting``; one shorter
            than ``visiting``, since it records the edges *between*
            consecutive entries.
        :raises InfiniteSizeStructError: If this struct is already being
            visited, i.e. reachable from itself.
        :raises TypInstantiationDepthExceededError: If ``visiting`` is
            already at :data:`_MAX_STRUCT_INSTANTIATION_DEPTH`. Unlike the
            cycle above, a runaway generic instantiation (e.g.
            ``struct L[T] { x: L[[T; 1]] }``) never repeats the same
            ``StructTyp`` - each nesting level is a genuinely distinct
            instantiation - so a depth cap is the only way to catch it.
        """
        if self in visiting:
            cycle_start = visiting.index(self)
            raise InfiniteSizeStructError(self.name, self.span, hops[cycle_start:])
        if len(visiting) >= _MAX_STRUCT_INSTANTIATION_DEPTH:
            outermost = visiting[0] if visiting else self
            raise TypInstantiationDepthExceededError(outermost.name, outermost.span)

        visiting = [*visiting, self]
        for field_ast in self.ast.fields:
            typ = Typ.from_ast(field_ast.typ, self.env)
            while isinstance(typ, ArrayTyp):
                typ = typ.element_typ
            if isinstance(typ, StructTyp):
                hop = (self.name, field_ast.ident.name, field_ast.span, typ.name)
                typ._check_finite_size(visiting, [*hops, hop])

    @property
    def span(self) -> SrcSpan:
        """The source location of this struct's declaration."""
        return self.ast.span


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
    def coerces_to(self, target: Typ) -> bool:
        return True


#: The built-in numeric, boolean, string, and control-flow type singletons.
U8 = IntTyp.get_or_create(8, UNSIGNED)
I8 = IntTyp.get_or_create(8, SIGNED)
U32 = IntTyp.get_or_create(32, UNSIGNED)
I32 = IntTyp.get_or_create(32, SIGNED)
USIZE = IntTyp.get_or_create(ADDR_SIZE, UNSIGNED)
ISIZE = IntTyp.get_or_create(ADDR_SIZE, SIGNED)
CINT = I32
BOOL = BoolTyp.get_or_create()
CSTR = PtrTyp.get_or_create(U8, CONST)
VOID = VoidTyp.get_or_create()
NEVER = NeverTyp.get_or_create()
