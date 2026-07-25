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
from l0.l0errors import DuplicateFieldInStructDefnError
from l0.src import SrcSpan


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
                return PtrTyp.get_or_create(Typ.from_ast(typ_ast.pointee_typ, e), CONST)
            case ast.ArrayTyp():
                return ArrayTyp.get_or_create(
                    Typ.from_ast(typ_ast.element_typ, e), typ_ast.length.value
                )
            case _:
                raise NotImplementedError(ast)


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


class StructTyp(Typ):
    """A struct type, identified by its declaration.

    Unlike other :class:`Typ` subclasses, two ``StructTyp`` instances are
    never equal even if structurally identical: each ``struct`` definition
    in the source introduces its own distinct type (see
    :meth:`cache_key`).

    :param ast: The parsed struct declaration.
    :param e: The enclosing scope, used to resolve field types.
    """

    ast: Final[ast.StructDefn]
    env: Final[ir_env.Env]

    def __init__(self, ast: ast.StructDefn, e: ir_env.Env) -> None:
        self.ast = ast
        self.env = e.new_child()

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

    @cached_property
    def fields(self) -> MappingProxyType[str, StructField]:
        """This struct's fields, keyed by name, in declaration order.

        :raises DuplicateFieldInStructDefnError: If the declaration
            contains two fields with the same name.
        """
        fields = {}
        for i, field_ast in enumerate(self.ast.fields):
            name = field_ast.ident.name
            if name in fields:
                raise DuplicateFieldInStructDefnError(
                    name, field_ast.ident.span, fields[name].ast.ident.span
                )

            fields[name] = StructField(i, field_ast, self.env)

        return fields.items().mapping

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
