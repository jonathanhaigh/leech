"""Module/program structure: functions, module-level variables, and modules."""

import enum
from abc import abstractmethod
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar, Final, override

from leech import ast, comptime, ir_builder, ir_env, ir_loader, typcheck
from leech.asserts import assert_eq, checked_cast
from leech.errors import (
    CircularVarInitializerError,
    ImplForNonLocalStructTypError,
    ImplForNonStructTypError,
    ModDoesNotExistError,
    SelfParamOutsideImplError,
)
from leech.ir_values import Cfg, ComptimePtr, ComptimeValue, Param, Value
from leech.opt_util import opt_unwrap
from leech.src import SrcFile, SrcSpan
from leech.typs import (
    BOOL,
    CONST,
    ISIZE,
    NEVER,
    USIZE,
    VOID,
    FnTyp,
    Mutability,
    PtrTyp,
    StructTyp,
    Typ,
    TypParamTyp,
)


class Access(enum.Enum):
    """Whether a module item is visible outside its declaring module."""

    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(ast: ast.Access | None) -> Access:
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
    value: Final[Value[PtrTyp] | ir_env.Container | GenericFn]
    qualify_name: Final[bool] = True

    @property
    def ns(self) -> ir_env.Env.Namespace:
        """Which namespace this item's name is bound in.

        Functions and variables are values, so they live in
        :attr:`~leech.ir_env.Env.Namespace.VARS` - a :class:`GenericFn` too,
        even though it isn't itself a :class:`~leech.ir_values.Value` (see
        its docstring), since it stands in for one; structs live in
        :attr:`~leech.ir_env.Env.Namespace.CONTAINERS`, and so do imported
        modules - a module isn't a type, but it deliberately shares that
        namespace with types, so a module and a type can't share a name.
        A module can therefore hold a value and a type of the same name,
        which is why its items are keyed on namespace as well as name
        (see :meth:`Mod.get_item`).
        """
        if isinstance(self.value, (Value, GenericFn)):
            return ir_env.Env.Namespace.VARS
        return ir_env.Env.Namespace.CONTAINERS

    @property
    def qualified_name(self) -> str:
        """This item's name, prefixed with its module's name unless opted out.

        Used as the symbol name emitted into the generated LLVM IR.
        """
        if self.qualify_name:
            return f"{self.mod.name}.{self.name}"
        return self.name


class FnSpec[FnAstT_co: ast.FnSpec](ComptimePtr[FnAstT_co]):
    """Base class for anything callable that can appear as a module item.

    A function pointer that can never be dereferenced to a plain value -
    it can only be called.
    """

    @override
    def load(self) -> ComptimeValue:
        raise AssertionError("Can't dereference a function pointer")

    @override
    def store(self, value: ComptimeValue) -> None:
        raise AssertionError("Can't dereference a function pointer")

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


class NonBuiltinFnSpec[FnAstT_co: ast.FnSpec](FnSpec[FnAstT_co]):
    """Base class for functions declared or defined in Leech source.

    :param ast: The parsed function declaration or definition.
    :param e: The enclosing scope, used to resolve parameter and return
        types.
    :param mod_name: The name of the module this function is declared in,
        used to qualify a generic instantiation's mangled symbol name
        (see :attr:`FnInstance.qualified_name`) - an ordinary
        (non-generic) function is qualified by its own :class:`ModItem`
        instead, same as any other module item.
    :param recv_struct_typ: The struct this function is an associated
        function of, if any - only consulted when ``ast.receiver`` is
        present, to synthesize the receiver's pointer-to-struct type
        (which, unlike an ordinary parameter's, isn't written in source).
    """

    env: Final[ir_env.Env]
    mod_name: Final[str]
    recv_struct_typ: Final[StructTyp | None]

    @override
    def __init__(
        self,
        ast: FnAstT_co,
        e: ir_env.Env,
        mod_name: str,
        recv_struct_typ: StructTyp | None = None,
    ) -> None:
        super().__init__(ast)
        self.env = e.new_child()
        self.mod_name = mod_name
        self.recv_struct_typ = recv_struct_typ
        # Bound here rather than looked up on demand, so a type parameter
        # resolves like any other named type wherever the body names it -
        # in a param or return type (below), or in an expression. A
        # no-op loop for a non-generic function or an extern declaration,
        # neither of which can have any.
        for index, param_ast in enumerate(ast.generic_params):
            self.env.add_container(
                param_ast.ident.name, TypParamTyp.get_or_create(ast, index, param_ast)
            )

    @override
    def calculate_typ(self) -> PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            # "never" names a type only here, in return-type position: a
            # function that never returns normally is the one place a
            # bottom type is useful to write down. Elsewhere (params,
            # struct fields, ...) it stays unnameable, the same as "void".
            ret_typ_env = self.env.new_child()
            ret_typ_env.add_container("never", NEVER)
            ret_typ = Typ.from_ast(self.ast.ret_typ, ret_typ_env)

        param_typs: list[Typ] = [
            Typ.from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        ]
        if self.ast.receiver is not None:
            assert self.recv_struct_typ is not None
            recv_typ = PtrTyp.get_or_create(
                self.recv_struct_typ, Mutability.from_ast(self.ast.receiver.mut)
            )
            param_typs.insert(0, recv_typ)

        return PtrTyp.get_or_create(FnTyp.get_or_create(ret_typ, tuple(param_typs)), CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        offset = 1 if self.ast.receiver is not None else 0
        params = [
            Param(self, pos + offset, param_ast) for pos, param_ast in enumerate(self.ast.params)
        ]
        if self.ast.receiver is not None:
            params.insert(0, Param(self, 0, self.ast.receiver))
        return tuple(params)

    @property
    @override
    def name(self) -> str:
        assert self.ast is not None
        return self.ast.name.name


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    """A function declared without a body (e.g. an external/builtin function)."""


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    """A function defined with a body in Leech source."""

    @cached_property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """This function's body, type-checked into a side table.

        Built lazily, on first access; forced by :attr:`cfg` before
        lowering begins.
        """
        return typcheck.TypCheck().check_fn(
            opt_unwrap(self.ast), self.env, self.fn_typ.ret_typ, self.params
        )

    @cached_property
    def cfg(self) -> Cfg:
        """This function's body, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(self.typ_check_results, self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        return builder.cfg

    def is_accessible_from(self, file: SrcFile) -> bool:
        """Whether this function can be called from code in ``file``.

        Only relevant for associated functions (defined in an ``impl``
        block): a private one can only be called from the module its
        struct is defined in, mirroring
        :meth:`~leech.typs.StructField.is_accessible_from`. Free module-level
        functions are filtered by access before reaching this point (see
        :meth:`~leech.ir_env.Env._resolve_path_segment`), so this is only
        consulted for the two ways of reaching an associated function: the
        ``Struct::method()`` path form, and the ``value.method()``
        dot-call form (see
        :meth:`~leech.ir_builder.CfgBuilder.build_call_expr`).

        :param file: The source file to check accessibility from.
        :return: Whether this function is accessible from ``file``.
        """
        assert self.ast is not None
        return Access.from_ast(self.ast.access) == PUBLIC or self.ast.span.file.path == file.path

    @cached_property
    def _instances(self) -> dict[tuple[Typ, ...], FnInstance]:
        return {}

    def instance(self, typ_args: tuple[Typ, ...]) -> FnInstance:
        """Get this function's instantiation for ``typ_args``, building it if needed.

        Cached, so requesting the same ``typ_args`` twice - from two
        different call sites, or the same one lowered more than once -
        returns the same :class:`FnInstance`, and its body is only ever
        lowered once.

        :param typ_args: The concrete type to substitute for each of this
            function's own type parameters, in declaration order - empty
            for a non-generic function.
        :return: The (possibly newly-built, possibly cached) instantiation.
        """
        inst = self._instances.get(typ_args)
        if inst is None:
            inst = FnInstance(self, typ_args)
            self._instances[typ_args] = inst
        return inst


class FnInstance(FnSpec[ast.FnDefn]):
    """One concrete instantiation of a generic function's body, for one
    fixed sequence of type arguments.

    Built (and cached) by :meth:`Fn.instance`. A generic ``Fn``'s own
    :attr:`~leech.ir_values.Value.typ` and :attr:`FnSpec.params` are only
    ever used to type-check its body against its own type parameters,
    treated opaquely (see :meth:`NonBuiltinFnSpec.__init__`); they aren't
    real types anything should be lowered or called against. This is:
    its :attr:`~leech.ir_values.Value.typ` and :attr:`FnSpec.params` are
    ``fn``'s own, with every type parameter replaced by its entry in
    ``typ_args``, and it has its own :attr:`cfg`, lowered once per
    distinct instantiation rather than once for the generic declaration.

    :param fn: The generic function this is an instantiation of.
    :param typ_args: The concrete type to substitute for each of ``fn``'s
        own type parameters, in declaration order.
    """

    fn: Final[Fn]
    typ_args: Final[tuple[Typ, ...]]
    _mapping: Final[Mapping[TypParamTyp, Typ]]

    def __init__(self, fn: Fn, typ_args: tuple[Typ, ...]) -> None:
        super().__init__(fn.ast)
        self.fn = fn
        self.typ_args = typ_args
        fn_ast = opt_unwrap(fn.ast)
        self._mapping = {
            TypParamTyp.get_or_create(fn_ast, i, param_ast): typ_arg
            for i, (param_ast, typ_arg) in enumerate(
                zip(fn_ast.generic_params, typ_args, strict=True)
            )
        }

    @override
    def calculate_typ(self) -> PtrTyp:
        substituted = checked_cast(self.fn.fn_typ.substitute_typ_params(self._mapping), FnTyp)
        return PtrTyp.get_or_create(substituted, CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        return tuple(Param(self, param.pos, param.ast) for param in self.fn.params)

    @property
    @override
    def name(self) -> str:
        arg_names = ", ".join(typ_arg.name for typ_arg in self.typ_args)
        return f"{self.fn.name}[{arg_names}]"

    @property
    def qualified_name(self) -> str:
        """This instance's mangled symbol name, e.g. ``mod.id[i32]``.

        Extends :attr:`ModItem.qualified_name`'s module-prefixed scheme -
        an instance is never itself a :class:`ModItem` (nothing declares
        one directly; it's built on demand by :meth:`Fn.instance`), so it
        computes the same form independently, from :attr:`fn`'s own
        module.
        """
        return f"{self.fn.mod_name}.{self.name}"

    @cached_property
    def env(self) -> ir_env.Env:
        """The scope to lower this instance's body in.

        :attr:`fn`'s own scope, with each type parameter shadowed here by
        its concrete type argument - so anything resolved fresh from
        source while lowering (a ``let``'s declared type, a struct
        literal's, ...) picks up the concrete type automatically, without
        the lowering code needing to know it's inside an instantiation.
        """
        e = self.fn.env.new_child()
        fn_ast = opt_unwrap(self.fn.ast)
        for param_ast, typ_arg in zip(fn_ast.generic_params, self.typ_args, strict=True):
            e.add_container(param_ast.ident.name, typ_arg)
        return e

    @cached_property
    def cfg(self) -> Cfg:
        """This instance's body, lowered to a control-flow graph.

        Built lazily, on first access, from :attr:`fn`'s own
        (unsubstituted) type-check results - a generic body is checked
        once, not once per instantiation (see :meth:`Mod.build`) - read
        back here against this instance's concrete type arguments.
        """
        builder = ir_builder.CfgBuilder(self.fn.typ_check_results, self, self._mapping)
        builder.build_fn(opt_unwrap(self.fn.ast), self.env)
        return builder.cfg


class GenericFn:
    """A free function with type parameters, not yet applied to any.

    Bound into :attr:`~leech.ir_env.Env.Namespace.VARS` in place of
    :attr:`fn` itself, under its name. A generic function's type isn't
    well-defined until it's applied to concrete type arguments (see
    :meth:`Fn.instance`) - :attr:`fn`'s own
    :attr:`~leech.ir_values.Value.typ` is one arbitrary, meaningless
    ``FnTyp`` built from its type parameters taken literally (needed only
    to type-check :attr:`fn`'s body against itself, with each parameter
    opaque - see :meth:`~leech.ir_module.NonBuiltinFnSpec.__init__`), not
    a type any caller should see. Binding :attr:`fn` directly would let
    it be resolved and called as though that were a real type; naming a
    ``GenericFn`` the way an ordinary value is named is instead a
    :class:`~leech.errors.MissingTypArgsError` (see
    :meth:`leech.typcheck.TypCheck.check_var_expr`).

    :param fn: The wrapped generic function.
    """

    fn: Final[Fn]

    def __init__(self, fn: Fn) -> None:
        self.fn = fn

    @property
    def name(self) -> str:
        """This function's name."""
        return self.fn.name

    @property
    def span(self) -> SrcSpan | None:
        """The source location of this function's declaration."""
        return self.fn.span


class ModVar(ComptimePtr[ast.VarDefn]):
    """A module-level ``let`` binding.

    Its initializer must be computable at compile time (see
    :class:`leech.comptime.Interpreter`), since it becomes a global variable's
    initial value in the generated code.

    :param ast: The parsed variable declaration.
    :param e: The enclosing scope, used to build and evaluate the
        initializer expression.
    """

    env: Final[ir_env.Env]
    mut: Final[Mutability]

    #: Module variables whose initializer is currently being evaluated,
    #: innermost last. Shared across every ``ModVar`` (and, since imports
    #: can now be circular, every module) in this compilation, so a cycle
    #: is caught regardless of which variable it's first reached from or
    #: how many modules it passes through.
    _resolving: ClassVar[list[ModVar]] = []

    @override
    def __init__(self, ast: ast.VarDefn, e: ir_env.Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()
        self.mut = Mutability.from_ast(ast.let_stmt.mut)

    @property
    def name(self) -> str:
        """This variable's name."""
        assert self.ast is not None
        return self.ast.let_stmt.ident.name

    @override
    def load(self) -> ComptimeValue:
        return self.initializer

    @override
    def store(self, value: ComptimeValue) -> None:
        raise AssertionError("Cannot set mod var at comptime")

    @override
    def is_temporary(self) -> bool:
        return False

    @cached_property
    def initializer(self) -> ComptimeValue:
        """This variable's initial value, evaluated at compile time.

        Computed lazily, on first access.

        :raises CircularVarInitializerError: If evaluating this
            variable's initializer requires (directly or transitively,
            possibly through other modules) evaluating this same
            variable's initializer again.
        """
        assert self.ast is not None
        if self in ModVar._resolving:
            cycle = ModVar._resolving[ModVar._resolving.index(self) :]
            raise CircularVarInitializerError(
                self.name, self.span, [(v.name, v.span) for v in cycle]
            )

        ModVar._resolving.append(self)
        try:
            return comptime.Interpreter(self.cfg, (), ()).eval()
        finally:
            ModVar._resolving.pop()

    @cached_property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """This variable's initializer, type-checked into a side table.

        Built lazily, on first access; forced by :attr:`cfg` before
        lowering begins.
        """
        return typcheck.TypCheck().check_var_initializer(opt_unwrap(self.ast), self.env)

    @cached_property
    def cfg(self) -> Cfg:
        """The initializer expression, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(self.typ_check_results)
        builder.build_var_initializer(opt_unwrap(self.ast), self.env)
        return builder.cfg

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp.get_or_create(self.initializer.typ, self.mut)


class Mod:
    """A compiled Leech module: the functions, variables, types, and imports it
    declares.

    A module is *not* a type, and can't be used as one. It can head a
    qualified path (``some_mod::foo``) because
    :meth:`~leech.ir_env.Env._resolve_path_segment` accepts a module as a
    scope to look a name up in, and it shares the
    :attr:`~leech.ir_env.Env.Namespace.CONTAINERS` namespace with types so
    that a module and a type can't have the same name (see
    :attr:`ModItem.ns`).

    Construction is two-phase: ``__init__`` only sets up empty state, and
    :meth:`build` populates :attr:`items`. This lets
    :meth:`~leech.ir_loader.ModLoader.load` register a module before building
    it, so an import cycle resolves to the module already under
    construction rather than recursing forever.

    :param name: The module's name, used to qualify its items' symbol
        names in the generated code.
    :param mod_ast: The parsed module.
    :param loader: The loader to resolve this module's imports through.
    """

    _name: Final[str]
    ast: Final[ast.Mod]
    #: This module's items, keyed on the namespace and name they're
    #: declared under, in declaration order. See :meth:`get_item` and
    #: :attr:`items`.
    _items: Final[dict[tuple[ir_env.Env.Namespace, str], ModItem]]
    env: Final[ir_env.Env]
    loader: Final[ir_loader.ModLoader]

    def __init__(self, name: str, mod_ast: ast.Mod, loader: ir_loader.ModLoader) -> None:
        builtin_env = ir_env.Env()
        self._name = name
        self.ast = mod_ast
        self._items = {}
        self.env = builtin_env.new_child()
        self.loader = loader

        builtin_env.add_container("usize", USIZE)
        builtin_env.add_container("isize", ISIZE)
        builtin_env.add_container("bool", BOOL)

    def build(self) -> None:
        """Populate :attr:`items` from this module's parsed definitions.

        Called once, by the loader, immediately after construction.
        ``impl`` blocks are built last so the structs they attach to are
        already bound in :attr:`env` - and so every struct's fields are
        registered before any associated function, which is what makes an
        associated function always the newcomer in a name clash with a
        field (see :meth:`~leech.typs.StructTyp.add_assoc_fn`).

        A generic function's body is type-checked here too, eagerly and
        unconditionally - unlike an ordinary function, whose body is only
        checked lazily, on first use of :attr:`Fn.typ_check_results` (see
        :meth:`~leech.ir_module.Fn.cfg`). A generic body is checked once,
        against its type parameters, independently of whether or how it's
        ever applied (that happens later, per call site - see
        :meth:`Fn.instance`), so nothing else is guaranteed to ever force
        it.

        :raises ModDoesNotExistError: If an ``import`` names a module file
            that doesn't exist.
        :raises DuplicateItemDefnError: If two definitions in this module
            share a name, or if a struct's associated function has the
            same name as one of that struct's fields or another of its
            associated functions.
        :raises DuplicateFieldInStructDefnError: If a struct in this
            module declares two fields with the same name.
        """
        impl_defns = []
        generic_fns: list[Fn] = []
        for defn_ast in self.ast.defns:
            if isinstance(defn_ast, ast.ImplDefn):
                impl_defns.append(defn_ast)
            else:
                self._build_defn(defn_ast, generic_fns)

        for impl_ast in impl_defns:
            self._build_impl_defn(impl_ast)

        for fn in generic_fns:
            _ = fn.typ_check_results

    @property
    def name(self) -> str:
        """This module's name, used to qualify its items' symbol names."""
        return self._name

    @property
    def items(self) -> Collection[ModItem]:
        """Every item this module declares, in declaration order."""
        return self._items.values()

    def get_item(self, ns: ir_env.Env.Namespace, name: str) -> ModItem | None:
        """Find the item this module declares as ``name`` in ``ns``.

        :param ns: The namespace to look in. A value and a type of the
            same name are different items (see :attr:`ModItem.ns`), so the
            namespace is part of the lookup.
        :param name: The item's unqualified name.
        :return: The item, or ``None`` if this module declares nothing of
            that name in ``ns``.
        """
        return self._items.get((ns, name))

    def _build_defn(self, defn_ast: ast.Defn, generic_fns: list[Fn]) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(
                    defn_ast.let_stmt.ident.name,
                    Access.from_ast(defn_ast.access),
                    ModVar(defn_ast, self.env),
                )
            case ast.FnDecl():
                if defn_ast.receiver is not None:
                    raise SelfParamOutsideImplError(defn_ast.receiver.span)
                self._add_item(
                    defn_ast.name.name,
                    Access.PUBLIC,
                    FnDecl(defn_ast, self.env, self.name),
                    False,
                )
            case ast.FnDefn():
                if defn_ast.receiver is not None:
                    raise SelfParamOutsideImplError(defn_ast.receiver.span)
                qualify_name = self.name != "main" or defn_ast.name.name != "main"
                fn = Fn(defn_ast, self.env, self.name)
                value: Fn | GenericFn = fn
                if defn_ast.generic_params:
                    value = GenericFn(fn)
                    generic_fns.append(fn)
                self._add_item(
                    defn_ast.name.name, Access.from_ast(defn_ast.access), value, qualify_name
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
                mod = self.loader.load(mod_path)
                self._add_item(mod_name, PRIVATE, mod, span=defn_ast.ident.span)
            case _:
                # ImplDefn is the only other Defn subclass; the caller
                # (Mod.build) filters those out before calling here.
                raise AssertionError(f"unhandled definition {defn_ast}")

    def _build_impl_defn(self, impl_ast: ast.ImplDefn) -> None:
        impl_typ_ast = impl_ast.typ
        if not isinstance(impl_typ_ast, ast.BasicTyp):
            raise ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        typ = Typ.from_ast(impl_typ_ast, self.env)
        if not isinstance(typ, StructTyp):
            raise ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        if len(impl_typ_ast.path.idents) > 1:
            raise ImplForNonLocalStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        for fn_ast in impl_ast.fns:
            # Generic associated functions aren't supported yet - nothing
            # upstream rejects the syntax, so fail loudly here rather than
            # silently mistreat the method's one literal FnTyp as real.
            assert not fn_ast.generic_params, "generic associated functions aren't supported yet"
            fn = Fn(fn_ast, typ.env, self.name, recv_struct_typ=typ)
            typ.add_assoc_fn(fn)
            self._add_item(f"{typ.name}::{fn_ast.name.name}", Access.from_ast(fn_ast.access), fn)

    def _add_item(
        self,
        name: str,
        access: Access,
        value: Value[PtrTyp] | ir_env.Container | GenericFn,
        qualify_name: bool = True,
        span: SrcSpan | None = None,
    ) -> None:
        item = ModItem(self, name, access, value, qualify_name)
        # Bind in env before recording the item: Env.add is what rejects a
        # duplicate definition, and it has to raise before _items is
        # written to, so that a name can never be silently rebound to a
        # different item.
        self.env.add(item.ns, name, value, span)
        self._items[(item.ns, name)] = item
