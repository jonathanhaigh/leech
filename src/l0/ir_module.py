"""Module/program structure: functions, module-level variables, and modules."""

from abc import abstractmethod
from dataclasses import dataclass
import enum
from functools import cached_property
from typing import Final, Generic, Optional, TypeVar, override

from l0 import comptime, ir_builder, ir_env, main
from l0 import l0ast as ast
from l0.asserts import assert_eq, checked_cast
from l0.ir_values import Cfg, ComptimePtr, ComptimeValue, Param, Value
from l0.l0errors import (
    ImplForNonLocalStructTypError,
    ImplForNonStructTypError,
    ModDoesNotExistError,
)
from l0.opt_util import opt_unwrap
from l0.src import SrcFile
from l0.typs import (
    BOOL,
    CONST,
    ISIZE,
    USIZE,
    VOID,
    FnTyp,
    Mutability,
    PtrTyp,
    StructTyp,
    Typ,
)

FnAstT = TypeVar("FnAstT", bound=ast.FnSpec, covariant=True)


class Access(enum.Enum):
    """Whether a module item is visible outside its declaring module."""

    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(ast: Optional[ast.Access]) -> Access:
        """Determine access from an optional ``pub`` keyword in the AST.

        :param ast: The parsed ``pub`` keyword node, or ``None`` if
            absent.
        :return: :data:`PUBLIC` if ``ast`` is present, otherwise
            :data:`PRIVATE`.
        """
        if ast is None:
            return PRIVATE
        assert_eq(ast.value, "pub")
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC


@dataclass
class ModItem:
    """A single named item (function, variable, type, or import) in a module.

    :param mod: The module this item belongs to.
    :param name: The item's unqualified name.
    :param access: Whether the item is public or private.
    :param value: The item itself.
    :param qualify_name: Whether :attr:`qualified_name` should prefix
        ``name`` with the module's name.
    """

    mod: Final[Mod]
    name: Final[str]
    access: Final[Access]
    value: Final[Value[PtrTyp] | Typ]
    qualify_name: Final[bool] = True

    @property
    def qualified_name(self) -> str:
        """This item's name, prefixed with its module's name unless opted out.

        Used as the symbol name emitted into the generated LLVM IR.
        """
        if self.qualify_name:
            return f"{self.mod.name}.{self.name}"
        else:
            return self.name


class FnSpec(Generic[FnAstT], ComptimePtr[FnAstT]):
    """Base class for anything callable that can appear as a module item.

    A function pointer that can never be dereferenced to a plain value -
    it can only be called.
    """

    @override
    def load(self) -> ComptimeValue:
        assert False, "Can't dereference a function pointer"

    @override
    def store(self, value: ComptimeValue) -> None:
        assert False, "Can't dereference a function pointer"

    @override
    def is_temporary(self) -> bool:
        return False

    @cached_property
    def params(self) -> tuple[Param, ...]:
        """This function's formal parameters, in declaration order."""
        return self.calculate_params()

    @abstractmethod
    def calculate_params(self) -> tuple[Param, ...]:
        """Compute :attr:`params`; overridden by subclasses."""

    @property
    @abstractmethod
    def name(self) -> str:
        """This function's name."""

    @property
    def fn_typ(self) -> FnTyp:
        """This function's type, i.e. the pointee type of :attr:`typ`."""
        return checked_cast(self.typ.pointee_typ, FnTyp)


class NonBuiltinFnSpec(Generic[FnAstT], FnSpec[FnAstT]):
    """Base class for functions declared or defined in l0 source.

    :param ast: The parsed function declaration or definition.
    :param e: The enclosing scope, used to resolve parameter and return
        types.
    """

    env: Final[ir_env.Env]

    @override
    def __init__(self, ast: FnAstT, e: ir_env.Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()

    @override
    def calculate_typ(self) -> PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            ret_typ = Typ.from_ast(self.ast.ret_typ, self.env)

        param_typs = (
            Typ.from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        )
        return PtrTyp.get_or_create(
            FnTyp.get_or_create(ret_typ, tuple(param_typs)), CONST
        )

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        return tuple(
            Param(self, pos, param_ast) for pos, param_ast in enumerate(self.ast.params)
        )

    @property
    @override
    def name(self) -> str:
        assert self.ast is not None
        return self.ast.name.name


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    """A function declared without a body (e.g. an external/builtin function)."""


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    """A function defined with a body in l0 source."""

    @cached_property
    def cfg(self) -> Cfg:
        """This function's body, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        return builder.cfg

    def is_accessible_from(self, file: SrcFile) -> bool:
        """Whether this function can be called from code in ``file``.

        Only relevant for associated functions (defined in an ``impl``
        block): a private one can only be called from the module its
        struct is defined in, mirroring
        :meth:`~l0.typs.StructField.is_accessible_from`. Free module-level
        functions are filtered by access before reaching this point (see
        :meth:`~l0.ir_env.Env._resolve_path_segment`), so this is only
        consulted for the ``Struct::method()`` lookup path.

        :param file: The source file to check accessibility from.
        :return: Whether this function is accessible from ``file``.
        """
        assert self.ast is not None
        return (
            Access.from_ast(self.ast.access) == PUBLIC
            or self.ast.span.file.path == file.path
        )


class ModVar(ComptimePtr[ast.VarDefn]):
    """A module-level ``let`` binding.

    Its initializer must be computable at compile time (see
    :class:`l0.comptime.Interpreter`), since it becomes a global variable's
    initial value in the generated code.

    :param ast: The parsed variable declaration.
    :param e: The enclosing scope, used to build and evaluate the
        initializer expression.
    """

    env: Final[ir_env.Env]
    mut: Final[Mutability]

    @override
    def __init__(self, ast: ast.VarDefn, e: ir_env.Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()
        self.mut = Mutability.from_ast(ast.let_stmt.mut)

    @override
    def load(self) -> ComptimeValue:
        return self.initializer

    @override
    def store(self, value: ComptimeValue) -> None:
        assert False, "Cannot set mod var at comptime"

    @override
    def is_temporary(self) -> bool:
        return False

    @cached_property
    def initializer(self) -> ComptimeValue:
        """This variable's initial value, evaluated at compile time.

        Computed lazily, on first access.
        """
        # TODO: detect recursion
        assert self.ast is not None
        return comptime.Interpreter(self.cfg, (), ()).eval()

    @cached_property
    def cfg(self) -> Cfg:
        """The initializer expression, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder()
        builder.build_var_initializer(opt_unwrap(self.ast), self.env)
        return builder.cfg

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self.initializer.typ, self.mut)


class Mod(Typ):
    """A compiled l0 module: the functions, variables, types, and imports it declares.

    Also usable as a :class:`~l0.typs.Typ`, so a module name can appear as
    a path segment when resolving a qualified name (e.g. ``some_mod::foo``).

    :param name: The module's name, used to qualify its items' symbol
        names in the generated code.
    :param mod_ast: The parsed module.
    """

    _name: Final[str]
    ast: Final[ast.Mod]
    items: Final[list[ModItem]]
    env: Final[ir_env.Env]

    @override
    def __init__(self, name: str, mod_ast: ast.Mod) -> None:
        builtin_env = ir_env.Env()
        self._name = name
        self.ast = mod_ast
        self.items = []
        self.env = builtin_env.new_child()

        builtin_env.add_typ("usize", USIZE)
        builtin_env.add_typ("isize", ISIZE)
        builtin_env.add_typ("bool", BOOL)

        impl_defns = []
        for defn_ast in mod_ast.defns:
            if isinstance(defn_ast, ast.ImplDefn):
                impl_defns.append(defn_ast)
            else:
                self._build_defn(defn_ast)

        for impl_ast in impl_defns:
            self._build_impl_defn(impl_ast)

    @property
    @override
    def name(self) -> str:
        return self._name

    def _build_defn(self, defn_ast: ast.Defn) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(
                    defn_ast.let_stmt.ident.name,
                    Access.from_ast(defn_ast.access),
                    ModVar(defn_ast, self.env),
                )
            case ast.FnDecl():
                self._add_item(
                    defn_ast.name.name,
                    Access.PUBLIC,
                    FnDecl(defn_ast, self.env),
                    False,
                )
            case ast.FnDefn():
                qualify_name = self.name != "main" or defn_ast.name.name != "main"
                self._add_item(
                    defn_ast.name.name,
                    Access.from_ast(defn_ast.access),
                    Fn(defn_ast, self.env),
                    qualify_name,
                )
            case ast.StructDefn():
                self._add_item(
                    defn_ast.ident.name,
                    Access.from_ast(defn_ast.access),
                    StructTyp.create(defn_ast, self.env),
                )
            case ast.Import():
                mod_name = defn_ast.ident.name
                mod_path = defn_ast.span.file.path.with_stem(mod_name)
                if not mod_path.exists():
                    raise ModDoesNotExistError(mod_name, defn_ast.ident.span)
                mod = main.compile_to_ir(SrcFile(mod_path))
                self._add_item(mod_name, PRIVATE, mod)
            case _:
                raise NotImplementedError

    def _build_impl_defn(self, impl_ast: ast.ImplDefn) -> None:
        impl_typ_ast = impl_ast.typ
        if not isinstance(impl_typ_ast, ast.BasicTyp):
            raise ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        typ = Typ.from_ast(impl_typ_ast, self.env)
        if not isinstance(typ, StructTyp):
            raise ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        if len(impl_typ_ast.path.idents) > 1:
            raise ImplForNonLocalStructTypError(
                impl_typ_ast.diag_str(), impl_typ_ast.span
            )

        for fn_ast in impl_ast.fns:
            fn = Fn(fn_ast, typ.env)
            typ.env.add_var(fn_ast.name.name, fn)
            self._add_item(
                f"{typ.name}::{fn_ast.name.name}", Access.from_ast(fn_ast.access), fn
            )

    def _add_item(
        self,
        name: str,
        access: Access,
        value: Value[PtrTyp] | Typ,
        qualify_name: bool = True,
    ) -> None:
        v = ModItem(self, name, access, value, qualify_name)
        self.items.append(v)
        if isinstance(value, Value):
            self.env.add_var(name, value)
        else:
            value = checked_cast(value, Typ)
            self.env.add_typ(name, value)
