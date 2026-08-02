# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Module/program structure: functions, module-level variables, and modules."""

import abc
import dataclasses
import enum
import functools
from collections.abc import Callable, Collection, Mapping
from typing import ClassVar, Final, Optional, override

from leech import (
    asserts,
    ast,
    comptime,
    errors,
    ir_builder,
    ir_env,
    ir_loader,
    ir_traits,
    ir_values,
    opt_util,
    src,
    typcheck,
    typs,
)


class Access(enum.Enum):
    """Whether a module item is visible outside its declaring module."""

    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(pub_ast: Optional[ast.Access]) -> Access:
        """Determine access from an optional ``pub`` keyword in the AST.

        :param pub_ast: The parsed ``pub`` keyword node, or ``None`` if
            absent.
        :return: :data:`PUBLIC` if ``pub_ast`` is present, otherwise
            :data:`PRIVATE`.
        """
        if pub_ast is None:
            return PRIVATE
        asserts.assert_eq(pub_ast.value, "pub")
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC


@dataclasses.dataclass
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
    value: Final[ir_values.Value[typs.PtrTyp] | ir_env.Container | GenericFn]
    qualify_name: Final[bool] = True

    @property
    def _ns(self) -> ir_env.Env.Namespace:
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
        if isinstance(self.value, (ir_values.Value, GenericFn)):
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


class FnSpec[FnAstT_co: ast.FnSpec](ir_values.ComptimePtr[FnAstT_co]):
    """Base class for anything callable that can appear as a module item.

    A function pointer that can never be dereferenced to a plain value -
    it can only be called.
    """

    @override
    def load(self) -> ir_values.ComptimeValue:
        raise AssertionError("Can't dereference a function pointer")

    @override
    def store(self, value: ir_values.ComptimeValue) -> None:
        raise AssertionError("Can't dereference a function pointer")

    @override
    def is_temporary(self) -> bool:
        return False

    @functools.cached_property
    def params(self) -> tuple[ir_values.Param, ...]:
        """This function's formal parameters, in declaration order."""
        return self.calculate_params()

    @abc.abstractmethod
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        """Compute :attr:`params`; overridden by subclasses."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """This function's name."""

    @property
    def fn_typ(self) -> typs.FnTyp:
        """This function's type, i.e. the pointee type of :attr:`typ`."""
        return asserts.checked_cast(self.typ.pointee_typ, typs.FnTyp)

    def body_cfg(self) -> Optional[ir_values.Cfg]:
        """This function's lowered body, if it has one.

        Overridden by :class:`Fn` and :class:`FnInstance` to return their
        (lazily built) :attr:`~Fn.cfg`; ``None`` here covers
        :class:`FnDecl`, an extern declaration with no body to lower.
        """
        return None


class NonBuiltinFnSpec[FnAstT_co: ast.FnSpec](FnSpec[FnAstT_co]):
    """Base class for functions declared or defined in Leech source.

    :param fn_ast: The parsed function declaration or definition.
    :param e: The enclosing scope, used to resolve parameter and return
        types.
    :param mod_name: The name of the module this function is declared in,
        used to qualify a generic instantiation's mangled symbol name
        (see :attr:`FnInstance.qualified_name`) - an ordinary
        (non-generic) function is qualified by its own :class:`ModItem`
        instead, same as any other module item.
    :param recv_typ: The type this function is an associated function or
        method of, if any - only consulted when ``ast.receiver`` is
        present, to synthesize the receiver's pointer-to-``recv_typ`` type
        (which, unlike an ordinary parameter's, isn't written in source).
        A struct for an inherent method; any type - struct, int, bool,
        pointer, array - for a trait impl's.
    """

    env: Final[ir_env.Env]
    _mod_name: Final[str]
    recv_typ: Final[Optional[typs.Typ]]

    @override
    def __init__(
        self,
        fn_ast: FnAstT_co,
        e: ir_env.Env,
        mod_name: str,
        recv_typ: Optional[typs.Typ] = None,
    ) -> None:
        super().__init__(fn_ast)
        self.env = e.new_child()
        self._mod_name = mod_name
        self.recv_typ = recv_typ
        # Bound here rather than looked up on demand, so a type parameter
        # resolves like any other named type wherever the body names it -
        # in a param or return type (below), or in an expression. A
        # no-op loop for a non-generic function or an extern declaration,
        # neither of which can have any.
        for index, param_ast in enumerate(fn_ast.generic_params):
            self.env.add_container(
                param_ast.ident.name, typs.TypParamTyp.get_or_create(fn_ast, index, param_ast)
            )

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = typs.VOID
        else:
            # "never" names a type only here, in return-type position: a
            # function that never returns normally is the one place a
            # bottom type is useful to write down. Elsewhere (params,
            # struct fields, ...) it stays unnameable, the same as "void".
            ret_typ_env = self.env.new_child()
            ret_typ_env.add_container("never", typs.NEVER)
            ret_typ = typs.Typ.from_ast(self.ast.ret_typ, ret_typ_env)

        param_typs: list[typs.Typ] = [
            typs.Typ.from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        ]
        if self.ast.receiver is not None:
            assert self.recv_typ is not None
            recv_typ = typs.PtrTyp.get_or_create(
                self.recv_typ, typs.Mutability.from_ast(self.ast.receiver.mut)
            )
            param_typs.insert(0, recv_typ)

        return typs.PtrTyp.get_or_create(
            typs.FnTyp.get_or_create(ret_typ, tuple(param_typs)), typs.CONST
        )

    @override
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        assert self.ast is not None
        offset = 1 if self.ast.receiver is not None else 0
        params = [
            ir_values.Param(self, pos + offset, param_ast)
            for pos, param_ast in enumerate(self.ast.params)
        ]
        if self.ast.receiver is not None:
            params.insert(0, ir_values.Param(self, 0, self.ast.receiver))
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

    @functools.cached_property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """This function's body, type-checked into a side table.

        Built lazily, on first access; forced by :attr:`cfg` before
        lowering begins.
        """
        return typcheck.TypCheck().check_fn(
            opt_util.opt_unwrap(self.ast), self.env, self.fn_typ.ret_typ, self.params
        )

    @functools.cached_property
    def cfg(self) -> ir_values.Cfg:
        """This function's body, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(self.typ_check_results, self)
        builder.build_fn(opt_util.opt_unwrap(self.ast), self.env)
        return builder.cfg

    @override
    def body_cfg(self) -> Optional[ir_values.Cfg]:
        return self.cfg

    def is_accessible_from(self, file: src.SrcFile) -> bool:
        """Whether this function can be called from code in ``file``.

        Only relevant for associated functions (defined in an ``impl``
        block): a private one can only be called from the module its
        struct is defined in, mirroring
        :meth:`~leech.typs.StructField.is_accessible_from`. Free module-level
        functions are filtered by access before reaching this point, during
        name resolution, so this is only consulted for the two ways of
        reaching an associated function: the ``Struct::method()`` path
        form, and the ``value.method()`` dot-call form, during lowering.

        :param file: The source file to check accessibility from.
        :return: Whether this function is accessible from ``file``.
        """
        assert self.ast is not None
        return Access.from_ast(self.ast.access) == PUBLIC or self.ast.span.file.path == file.path

    @functools.cached_property
    def _instance_cache(self) -> dict[tuple[typs.Typ, ...], FnInstance]:
        return {}

    @property
    def instances(self) -> Collection[FnInstance]:
        """Every instantiation of this function requested so far.

        Used by codegen to discover instances to emit - see
        :meth:`~leech.codegen.Compiler.compile`.
        """
        return self._instance_cache.values()

    def instance(self, typ_args: tuple[typs.Typ, ...]) -> FnInstance:
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
        inst = self._instance_cache.get(typ_args)
        if inst is None:
            inst = FnInstance(self, typ_args)
            self._instance_cache[typ_args] = inst
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

    _fn: Final[Fn]
    _typ_args: Final[tuple[typs.Typ, ...]]
    _mapping: Final[Mapping[typs.TypParamTyp, typs.Typ]]

    def __init__(self, fn: Fn, typ_args: tuple[typs.Typ, ...]) -> None:
        super().__init__(fn.ast)
        self._fn = fn
        self._typ_args = typ_args
        fn_ast = opt_util.opt_unwrap(fn.ast)
        self._mapping = {
            typs.TypParamTyp.get_or_create(fn_ast, i, param_ast): typ_arg
            for i, (param_ast, typ_arg) in enumerate(
                zip(fn_ast.generic_params, typ_args, strict=True)
            )
        }

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        substituted = asserts.checked_cast(
            self._fn.fn_typ.substitute_typ_params(self._mapping), typs.FnTyp
        )
        return typs.PtrTyp.get_or_create(substituted, typs.CONST)

    @override
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        return tuple(ir_values.Param(self, param.pos, param.ast) for param in self._fn.params)

    @property
    @override
    def name(self) -> str:
        arg_names = ", ".join(typ_arg.name for typ_arg in self._typ_args)
        return f"{self._fn.name}[{arg_names}]"

    @property
    def qualified_name(self) -> str:
        """This instance's mangled symbol name, e.g. ``mod.id[i32]``.

        Extends :attr:`ModItem.qualified_name`'s module-prefixed scheme -
        an instance is never itself a :class:`ModItem` (nothing declares
        one directly; it's built on demand by :meth:`Fn.instance`), so it
        computes the same form independently, from the module of the
        generic function this instantiates.
        """
        return f"{self._fn._mod_name}.{self.name}"

    @functools.cached_property
    def env(self) -> ir_env.Env:
        """The scope to lower this instance's body in.

        The own scope of the generic function this instantiates, with
        each type parameter shadowed here by its concrete type argument -
        so anything resolved fresh from source while lowering (a
        ``let``'s declared type, a struct literal's, ...) picks up the
        concrete type automatically, without the lowering code needing to
        know it's inside an instantiation.
        """
        e = self._fn.env.new_child()
        fn_ast = opt_util.opt_unwrap(self._fn.ast)
        for param_ast, typ_arg in zip(fn_ast.generic_params, self._typ_args, strict=True):
            e.add_container(param_ast.ident.name, typ_arg)
        return e

    @functools.cached_property
    def cfg(self) -> ir_values.Cfg:
        """This instance's body, lowered to a control-flow graph.

        Built lazily, on first access, from the unsubstituted type-check
        results of the generic function this instantiates - a generic
        body is checked once, not once per instantiation (see
        :meth:`Mod.build`) - read back here against this instance's
        concrete type arguments.
        """
        builder = ir_builder.CfgBuilder(self._fn.typ_check_results, self, self._mapping)
        builder.build_fn(opt_util.opt_unwrap(self._fn.ast), self.env)
        return builder.cfg

    @override
    def body_cfg(self) -> Optional[ir_values.Cfg]:
        return self.cfg


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
    :class:`~leech.errors.MissingTypArgsError`.

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
    def span(self) -> Optional[src.SrcSpan]:
        """The source location of this function's declaration."""
        return self.fn.span


class ModVar(ir_values.ComptimePtr[ast.VarDefn]):
    """A module-level ``let`` binding.

    Its initializer must be computable at compile time (see
    :class:`leech.comptime.Interpreter`), since it becomes a global variable's
    initial value in the generated code.

    :param var_ast: The parsed variable declaration.
    :param e: The enclosing scope, used to build and evaluate the
        initializer expression.
    """

    env: Final[ir_env.Env]
    _mut: Final[typs.Mutability]

    #: Module variables whose initializer is currently being evaluated,
    #: innermost last. Shared across every ``ModVar`` (and, since imports
    #: can now be circular, every module) in this compilation, so a cycle
    #: is caught regardless of which variable it's first reached from or
    #: how many modules it passes through.
    _resolving: ClassVar[list[ModVar]] = []

    @override
    def __init__(self, var_ast: ast.VarDefn, e: ir_env.Env) -> None:
        super().__init__(var_ast)
        self.env = e.new_child()
        self._mut = typs.Mutability.from_ast(var_ast.let_stmt.mut)

    @property
    def name(self) -> str:
        """This variable's name."""
        assert self.ast is not None
        return self.ast.let_stmt.ident.name

    @override
    def load(self) -> ir_values.ComptimeValue:
        return self.initializer

    @override
    def store(self, value: ir_values.ComptimeValue) -> None:
        raise AssertionError("Cannot set mod var at comptime")

    @override
    def is_temporary(self) -> bool:
        return False

    @functools.cached_property
    def initializer(self) -> ir_values.ComptimeValue:
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
            raise errors.CircularVarInitializerError(
                self.name, self.span, [(v.name, v.span) for v in cycle]
            )

        ModVar._resolving.append(self)
        try:
            return comptime.Interpreter(self.cfg, (), ()).eval()
        finally:
            ModVar._resolving.pop()

    @functools.cached_property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """This variable's initializer, type-checked into a side table.

        Built lazily, on first access; forced by :attr:`cfg` before
        lowering begins.
        """
        return typcheck.TypCheck().check_var_initializer(opt_util.opt_unwrap(self.ast), self.env)

    @functools.cached_property
    def cfg(self) -> ir_values.Cfg:
        """The initializer expression, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(self.typ_check_results)
        builder.build_var_initializer(opt_util.opt_unwrap(self.ast), self.env)
        return builder.cfg

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp.get_or_create(self.initializer.typ, self._mut)


class Mod:
    """A compiled Leech module: the functions, variables, types, and imports it
    declares.

    A module is *not* a type, and can't be used as one. It can head a
    qualified path (``some_mod::foo``) because path resolution accepts a
    module as a scope to look a name up in, and it shares the
    :attr:`~leech.ir_env.Env.Namespace.CONTAINERS` namespace with types so
    that a module and a type can't have the same name.

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
        builtin_env = ir_env.Env(impl_registry=loader.impl_registry)
        self._name = name
        self.ast = mod_ast
        self._items = {}
        self.env = builtin_env.new_child()
        self.loader = loader

        builtin_env.add_container("usize", typs.USIZE)
        builtin_env.add_container("isize", typs.ISIZE)
        builtin_env.add_container("bool", typs.BOOL)

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
        it. A generic ``impl`` block's associated functions are checked
        the same eager way.

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
            self._build_impl_defn(impl_ast, generic_fns)

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

    def get_item(self, ns: ir_env.Env.Namespace, name: str) -> Optional[ModItem]:
        """Find the item this module declares as ``name`` in ``ns``.

        :param ns: The namespace to look in. A value and a type of the
            same name are different items, so the namespace is part of
            the lookup.
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
                    raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
                self._add_item(
                    defn_ast.name.name,
                    Access.PUBLIC,
                    FnDecl(defn_ast, self.env, self.name),
                    False,
                )
            case ast.FnDefn():
                if defn_ast.receiver is not None:
                    raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
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
                    typs.StructTyp.create(defn_ast, self.env, (), self.name),
                )
            case ast.TraitDefn():
                self._add_item(
                    defn_ast.ident.name,
                    Access.from_ast(defn_ast.access),
                    ir_traits.Trait(defn_ast, self.env, self.name),
                )
            case ast.Import():
                mod_name = defn_ast.ident.name
                mod_path = defn_ast.span.file.path.with_stem(mod_name)
                if not mod_path.exists():
                    raise errors.ModDoesNotExistError(mod_name, defn_ast.ident.span)
                mod = self.loader.load(mod_path)
                self._add_item(mod_name, PRIVATE, mod, span=defn_ast.ident.span)
            case _:
                # ImplDefn is the only other Defn subclass; the caller
                # (Mod.build) filters those out before calling here.
                raise AssertionError(f"unhandled definition {defn_ast}")

    def _build_impl_defn(self, impl_ast: ast.ImplDefn, generic_fns: list[Fn]) -> None:
        """Build one ``impl`` block: dispatches to :meth:`_build_inherent_impl_defn`
        or :meth:`_build_trait_impl_defn` depending on whether it has a
        ``for`` clause.

        :param impl_ast: The parsed ``impl`` block.
        :param generic_fns: Accumulates every associated function or
            method defined in a *generic* ``impl`` block
            (``impl_ast.generic_params`` non-empty), so :meth:`build` can
            force it to type-check eagerly, the same as a free generic
            function - see its docstring. Such a function is otherwise
            unreachable: it's registered as one of its (generic) receiver
            type's members, never as a :class:`ModItem`, since the
            receiver - built from the impl's own, still-opaque type
            parameters - names no real, lowerable type for codegen to
            make sense of (see :meth:`~leech.typs.StructTyp.is_concrete`).
            Calling such a method isn't supported yet: that needs a
            monomorphization mechanism for impl-level (as opposed to
            function-level) type parameters, which
            :meth:`~leech.ir_module.Fn.instance` doesn't provide.
        """
        # The impl's own type parameters, if any, are bound here - before
        # either the inherent or trait branch resolves its target type(s)
        # - so a generic impl's target (e.g. `Pair[T, T]`, or a trait
        # impl's self type) can name them, and each associated function's
        # or method's own signature and body can too (see
        # `Impl.__init__`/the inherent branch's `fn_env`).
        impl_env = self.env.new_child()
        for index, param_ast in enumerate(impl_ast.generic_params):
            impl_env.add_container(
                param_ast.ident.name, typs.TypParamTyp.get_or_create(impl_ast, index, param_ast)
            )

        if impl_ast.for_typ is None:
            self._build_inherent_impl_defn(impl_ast, impl_env, generic_fns)
        else:
            self._build_trait_impl_defn(impl_ast, impl_env, generic_fns)

    def _build_inherent_impl_defn(
        self, impl_ast: ast.ImplDefn, impl_env: ir_env.Env, generic_fns: list[Fn]
    ) -> None:
        """Build one inherent ``impl SomeStruct { ... }`` block's associated functions.

        :param impl_ast: The parsed ``impl`` block (``impl_ast.for_typ is
            None``).
        :param impl_env: The impl's own scope, with its type parameters
            (if any) already bound.
        :param generic_fns: See :meth:`_build_impl_defn`.
        :raises ImplForNonStructTypError: If ``impl_ast.typ`` doesn't name
            a struct type.
        :raises ImplForNonLocalStructTypError: If ``impl_ast.typ`` names a
            struct defined outside this module.
        """
        impl_typ_ast = impl_ast.typ
        if not isinstance(impl_typ_ast, ast.BasicTyp):
            raise errors.ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        typ = typs.Typ.from_ast(impl_typ_ast, impl_env)
        if not isinstance(typ, typs.StructTyp):
            raise errors.ImplForNonStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        if len(impl_typ_ast.path.idents) > 1:
            raise errors.ImplForNonLocalStructTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        fn_env = typ.env.new_child()
        for index, param_ast in enumerate(impl_ast.generic_params):
            fn_env.add_container(
                param_ast.ident.name, typs.TypParamTyp.get_or_create(impl_ast, index, param_ast)
            )

        self._build_impl_fns(impl_ast, fn_env, typ, generic_fns, typ.add_assoc_fn, typ.name)

    def _build_trait_impl_defn(
        self, impl_ast: ast.ImplDefn, impl_env: ir_env.Env, generic_fns: list[Fn]
    ) -> None:
        """Build one ``impl Trait for SelfTyp { ... }`` block's methods.

        Unlike an inherent impl's target, ``SelfTyp`` isn't restricted to
        a local struct - it may be any type, since a trait impl's
        coherence comes from the orphan rule instead (see below), not
        from requiring the type to be local outright.

        :param impl_ast: The parsed ``impl`` block (``impl_ast.for_typ is
            not None``).
        :param impl_env: The impl's own scope, with its type parameters
            (if any) already bound.
        :param generic_fns: See :meth:`_build_impl_defn`.
        :raises ImplForNonTraitError: If ``impl_ast.typ`` doesn't name a
            trait.
        :raises OrphanImplError: If neither the trait nor the self type is
            defined in this module.
        :raises ExtraMethodInImplError: If a method here isn't declared by
            the trait.
        :raises TraitMethodSignatureMismatchError: If a method's signature
            doesn't match the trait's declared prototype for it.
        :raises TraitMethodNotImplementedError: If the trait declares a
            method this impl doesn't define.
        :raises ConflictingImplsError: If this impl's self type could
            overlap with another already-registered impl of the same
            trait.
        """
        trait_typ_ast = impl_ast.typ
        if not isinstance(trait_typ_ast, ast.BasicTyp):
            raise errors.ImplForNonTraitError(trait_typ_ast.diag_str(), trait_typ_ast.span)
        trait = impl_env.resolve_path(ir_env.Env.Namespace.CONTAINERS, trait_typ_ast.path)
        if not isinstance(trait, ir_traits.Trait):
            raise errors.ImplForNonTraitError(trait_typ_ast.diag_str(), trait_typ_ast.span)
        # A generic trait's own type parameters aren't supported yet -
        # nothing upstream rejects `impl Eq[i32] for i32` syntactically,
        # so fail loudly here rather than silently ignore the arguments.
        assert not trait_typ_ast.generic_args, "generic traits aren't supported yet"

        self_typ = typs.Typ.from_ast(opt_util.opt_unwrap(impl_ast.for_typ), impl_env)

        impl = ir_traits.Impl(impl_ast, trait, self_typ, impl_env, self.name)
        # Registered before building any method: two impls of the same
        # trait for the same (or overlapping) self type would otherwise
        # collide on method naming first (DuplicateItemDefnError), a less
        # specific diagnostic than the coherence violation it actually is.
        # This is also what checks the orphan rule (see
        # `ir_traits.Impl.check_orphan_rule`).
        self.loader.impl_registry.add_impl(impl)

        self._build_impl_fns(impl_ast, impl.env, self_typ, generic_fns, impl.add_method, impl.name)
        impl.check_complete()

    def _build_impl_fns(
        self,
        impl_ast: ast.ImplDefn,
        fn_env: ir_env.Env,
        recv_typ: typs.Typ,
        generic_fns: list[Fn],
        register: Callable[[Fn], None],
        qualified_prefix: str,
    ) -> None:
        """Build every function in an ``impl`` block, shared by the inherent and
        trait branches of :meth:`_build_impl_defn`.

        :param impl_ast: The parsed ``impl`` block.
        :param fn_env: Each function's enclosing scope.
        :param recv_typ: The receiver type to build each function with.
        :param generic_fns: See :meth:`_build_impl_defn`.
        :param register: Called with each built function to record it as a
            member of ``recv_typ`` (:meth:`~leech.typs.StructTyp.add_assoc_fn`)
            or of the trait impl (:meth:`~leech.ir_traits.Impl.add_method`).
        :param qualified_prefix: The prefix (a struct's or an impl's own
            name) a non-generic function is registered as a
            :class:`ModItem` under, as ``f"{qualified_prefix}::{name}"``.
        """
        for fn_ast in impl_ast.fns:
            # Generic associated functions/methods aren't supported yet -
            # nothing upstream rejects the syntax, so fail loudly here
            # rather than silently mistreat the function's one literal
            # FnTyp as real.
            assert not fn_ast.generic_params, "generic associated functions aren't supported yet"
            fn = Fn(fn_ast, fn_env, self.name, recv_typ=recv_typ)
            register(fn)
            if impl_ast.generic_params:
                generic_fns.append(fn)
            else:
                self._add_item(
                    f"{qualified_prefix}::{fn_ast.name.name}", Access.from_ast(fn_ast.access), fn
                )

    def _add_item(
        self,
        name: str,
        access: Access,
        value: ir_values.Value[typs.PtrTyp] | ir_env.Container | GenericFn,
        qualify_name: bool = True,
        span: Optional[src.SrcSpan] = None,
    ) -> None:
        item = ModItem(self, name, access, value, qualify_name)
        # Bind in env before recording the item: Env.add is what rejects a
        # duplicate definition, and it has to raise before _items is
        # written to, so that a name can never be silently rebound to a
        # different item.
        self.env.add(item._ns, name, value, span)
        self._items[(item._ns, name)] = item
