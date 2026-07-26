"""l0's type system: type representations, caching, and construction from AST."""

from abc import ABC, abstractmethod
from collections.abc import Hashable
from enum import Enum
from functools import cached_property
from types import MappingProxyType
from typing import ClassVar, Final, Optional, Self, override
from weakref import WeakValueDictionary

from l0 import ir_env, ir_module
from l0 import l0ast as ast
from l0.asserts import assert_eq, assert_gt, assert_not_in, checked_cast
from l0.l0errors import (
    DuplicateFieldInStructDefnError,
    DuplicateItemDefnError,
    InfiniteSizeStructError,
)
from l0.src import SrcFile, SrcSpan


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


class Signage(Enum):
    """Whether an integer type is signed or unsigned."""

    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED


class Typ(ABC):
    """Base class for all l0 types.

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
        """
        match typ_ast:
            case ast.BasicTyp():
                typ = e.resolve_typ(typ_ast.path)
                return typ
            case ast.PtrTyp():
                return PtrTyp.get_or_create(
                    Typ.from_ast(typ_ast.pointee_typ, e), Mutability.from_ast(typ_ast.mut)
                )
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
            case _:
                assert False, f"unhandled type ast node {typ_ast}"


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
        return f"{mut_str}*{self.pointee_typ.name}"

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


class StructTyp(Typ):
    """A struct type, identified by its declaration.

    Unlike other :class:`Typ` subclasses, two ``StructTyp`` instances are
    never equal even if structurally identical: each ``struct`` definition
    in the source introduces its own distinct type (see
    :meth:`cache_key`).

    A struct's *members* - its fields and its associated functions - share
    a single namespace, so a field and an associated function can't have
    the same name. They're registered in two phases: fields by
    ``__init__``, then associated functions by :meth:`add_assoc_fn` as the
    enclosing module builds its ``impl`` blocks (see
    :meth:`~l0.ir_module.Mod.build`, which defers those until after every
    struct is declared).

    This makes a struct a named-item container much like a
    :class:`~l0.ir_module.Mod`, and the member API here deliberately
    mirrors ``Mod``'s item API (:meth:`~l0.ir_module.Mod.get_item` and
    friends) so the two can be unified if that becomes worthwhile. It
    isn't yet: ``Mod`` keys its items on *namespace and* name, since a
    module-level variable and type may share a name, whereas a struct
    keys on name alone. If structs or traits gain associated items that
    aren't functions - associated types or consts - that difference will
    need revisiting, and a shared abstraction will have more to share.

    :param ast: The parsed struct declaration.
    :param e: The enclosing scope, used to resolve field types.
    :raises DuplicateFieldInStructDefnError: If the declaration contains
        two fields with the same name.
    """

    ast: Final[ast.StructDefn]
    env: Final[ir_env.Env]
    #: This struct's members - its fields and its associated functions -
    #: keyed by name, in registration order: fields first, in declaration
    #: order, then associated functions in ``impl``-block order. Fields
    #: and associated functions deliberately share one namespace; see the
    #: class docstring.
    _members: Final[dict[str, StructField | ir_module.Fn]]

    def __init__(self, ast: ast.StructDefn, e: ir_env.Env) -> None:
        self.ast = ast
        self.env = e.new_child()
        self._members = {}
        # Registering fields here - rather than lazily, along with
        # `fields` - deliberately resolves no types: a StructField only
        # stores its index, AST node and scope, so this is safe even
        # though a field's type may name a struct declared later in the
        # module.
        for i, field_ast in enumerate(ast.fields):
            self._add_member(StructField(i, field_ast, self.env))

    @override
    @classmethod
    def cache_key(cls, *args: Hashable) -> Hashable:
        """Key struct types by their declaration's identity, not its fields.

        :param args: The arguments passed to :meth:`~Typ.create` or
            :meth:`~Typ.get_or_create`; ``args[0]`` must be the struct's
            parsed declaration.
        :return: The cache key.
        """
        assert_gt(len(args), 0)
        struct_ast = checked_cast(args[0], ast.StructDefn)
        return (cls, struct_ast)

    @property
    @override
    def name(self) -> str:
        return self.ast.ident.name

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
                raise DuplicateFieldInStructDefnError(
                    member.name, span, existing_span
                )
            raise DuplicateItemDefnError(
                "associated function", member.name, span, existing_span
            )
        self._members[member.name] = member

    def add_assoc_fn(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this struct's associated functions.

        Binds ``fn`` twice: among this struct's members, where it shares a
        namespace with the struct's fields, and in :attr:`env`, which is
        what lets associated functions of the same struct call each other
        by bare, unqualified name.

        Registered among the members first, so a rejected function is
        never left bound in :attr:`env`. This is the mirror image of
        :meth:`~l0.ir_module.Mod._add_item`, where the ``Env`` is the
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
        """
        if self in visiting:
            cycle_start = visiting.index(self)
            raise InfiniteSizeStructError(self.name, self.span, hops[cycle_start:])

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


#: The built-in numeric, boolean, string, and control-flow type singletons.
U8 = IntTyp.get_or_create(8, UNSIGNED)
I8 = IntTyp.get_or_create(8, SIGNED)
U32 = IntTyp.get_or_create(32, UNSIGNED)
I32 = IntTyp.get_or_create(32, SIGNED)
ADDR_SIZE = 64
USIZE = IntTyp.get_or_create(ADDR_SIZE, UNSIGNED)
ISIZE = IntTyp.get_or_create(ADDR_SIZE, SIGNED)
CINT = I32
BOOL = BoolTyp.get_or_create()
CSTR = PtrTyp.get_or_create(U8, CONST)
VOID = VoidTyp.get_or_create()
NEVER = NeverTyp.get_or_create()
