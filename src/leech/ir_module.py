# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Module/program structure: functions, module-level variables, and modules."""

import abc
import dataclasses
import functools
from collections.abc import Collection, Mapping
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
    reserved,
    src,
    typcheck,
    typs,
    visibility,
)


@dataclasses.dataclass
class ModItem:
    """A named module item and its access and symbol-qualification policy."""

    mod: Final[Mod]
    name: Final[str]
    access: Final[visibility.Access]
    value: Final[ir_values.Value[typs.PtrTyp] | ir_env.Container | FnSpec]
    qualify_name: Final[bool] = True

    @property
    def _ns(self) -> ir_env.Env.Namespace:
        """Return the namespace in which this item is bound."""
        if isinstance(self.value, (ir_values.Value, FnSpec)):
            return ir_env.Env.Namespace.VARS
        return ir_env.Env.Namespace.CONTAINERS

    @property
    def qualified_name(self) -> str:
        """This item's name, prefixed with its module's name unless opted out.

        Used as the symbol name emitted into the generated LLVM IR.
        """
        if self.qualify_name:
            return f"{self.mod.name}::{self.name}"
        return self.name


class FnSpec[FnAstT_co: ast.FnSpec](abc.ABC):
    """Base class for a function declaration."""

    ast: Final[Optional[FnAstT_co]]

    def __init__(self, fn_ast: Optional[FnAstT_co]) -> None:
        self.ast = fn_ast

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The source location of this declaration, if it has one."""
        return opt_util.opt_map(self.ast, lambda node: node.span)

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
    @abc.abstractmethod
    def fn_typ(self) -> typs.FnTyp:
        """This declaration's function signature."""

    @functools.cached_property
    def ptr_typ(self) -> typs.PtrTyp:
        """A const function-pointer type for this declaration's signature."""
        return typs.PtrTyp.get_or_create(self.fn_typ, typs.CONST)

    @property
    @abc.abstractmethod
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This declaration's interned type parameters in declaration order."""

    @property
    @abc.abstractmethod
    def _qualified_name_prefix(self) -> str:
        """The instance symbol prefix, or empty for a global declaration."""

    @property
    @abc.abstractmethod
    def instances(self) -> Collection[FnInstance]:
        """Every instantiation requested so far."""

    @abc.abstractmethod
    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the cached instance for ``args``, creating it if needed.

        :post: instantiate(a) is instantiate(a) for equal a [cache]
        """


class NonBuiltinFnSpec[FnAstT_co: ast.FnSpec](FnSpec[FnAstT_co]):
    """A source-declared function resolved in its own child scope."""

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
        reserved.check_fn_params(fn_ast)
        # Eager binding lets signatures and bodies resolve parameters like named types.
        for typ_param in typs.typ_params_from_ast(fn_ast, fn_ast.generic_params, self.env):
            self.env.add_container(typ_param.name, typ_param)

    @property
    @override
    def fn_typ(self) -> typs.FnTyp:
        return self._fn_typ

    @functools.cached_property
    def _fn_typ(self) -> typs.FnTyp:
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

        return typs.FnTyp.get_or_create(ret_typ, tuple(param_typs))

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

    @property
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """Extern declarations have no type parameters."""
        return ()

    @functools.cached_property
    def _instance(self) -> FnInstance:
        """The declaration's sole bodyless instance."""
        return FnInstance(self, ())

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return this declaration's cached bodyless instance."""
        assert not args, f"{self.name}: extern declarations take no type arguments"
        return self._instance

    @property
    def instances(self) -> Collection[FnInstance]:
        """This declaration's sole bodyless instance."""
        return (self._instance,)

    @property
    def _qualified_name_prefix(self) -> str:
        """Keep the bare symbol name required by the external ABI."""
        return ""


class BodyFnSpec(abc.ABC):
    """A function declaration that owns a lowerable body."""

    env: ir_env.Env

    @property
    @abc.abstractmethod
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """Unsubstituted lowering facts shared by every instance."""

    @abc.abstractmethod
    def _build_body(self, builder: ir_builder.CfgBuilder, typ_args: tuple[typs.Typ, ...]) -> None:
        """Lower one instance's body into ``builder.cfg``."""


class Fn(NonBuiltinFnSpec[ast.FnDefn], BodyFnSpec):
    """A source-defined function that may serve as a generic template.

    :param impl: The ``impl`` block this function is declared in;
        ``None`` for a free function.
    """

    impl: Final[Optional[ir_traits.Impl]]

    @override
    def __init__(
        self,
        fn_ast: ast.FnDefn,
        e: ir_env.Env,
        mod_name: str,
        recv_typ: Optional[typs.Typ] = None,
        impl: Optional[ir_traits.Impl] = None,
    ) -> None:
        super().__init__(fn_ast, e, mod_name, recv_typ)
        self.impl = impl

    @functools.cached_property
    def _instance_cache(self) -> dict[tuple[typs.Typ, ...], FnInstance]:
        return {}

    @property
    def instances(self) -> Collection[FnInstance]:
        """Every instantiation requested so far."""
        return self._instance_cache.values()

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the cached instance identified by all impl and function arguments."""
        impl_arity = len(self.impl.typ_params) if self.impl is not None else 0
        fn_arity = len(self.typ_params)
        assert len(args) == impl_arity + fn_arity, (
            f"{self.name}: expected {impl_arity} impl and {fn_arity} function type arguments; "
            f"got {len(args)} total"
        )
        inst = self._instance_cache.get(args)
        if inst is None:
            inst = FnInstance(self, args)
            self._instance_cache[args] = inst
        return inst

    @property
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This function's interned type parameters in declaration order."""
        return self._typ_params

    @functools.cached_property
    def _typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This function's interned type parameters in declaration order."""
        fn_ast = opt_util.opt_unwrap(self.ast)
        return typs.typ_params_from_ast(fn_ast, fn_ast.generic_params, self.env)

    @property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """The unsubstituted lowering facts shared by all instances."""
        return self._typ_check_results

    @functools.cached_property
    def _typ_check_results(self) -> typcheck.TypCheckResults:
        """Lazily compute the unsubstituted lowering facts shared by all instances."""
        return typcheck.TypCheck().check_fn(
            opt_util.opt_unwrap(self.ast), self.env, self.fn_typ.ret_typ, self.params
        )

    @property
    def _qualified_name_prefix(self) -> str:
        return self._mod_name

    def _build_body(self, builder: ir_builder.CfgBuilder, typ_args: tuple[typs.Typ, ...]) -> None:
        del typ_args
        builder.build_fn(opt_util.opt_unwrap(self.ast))

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
        return self.access == visibility.PUBLIC or self.ast.span.file.path == file.path

    @property
    def access(self) -> visibility.Access:
        """This declaration's source visibility."""
        assert self.ast is not None
        return visibility.Access.from_ast(self.ast.access)

    @property
    def is_generic(self) -> bool:
        """Whether this function has impl-level or function-level type parameters."""
        return (self.impl is not None and bool(self.impl.typ_params)) or bool(self.typ_params)

    @property
    def is_main(self) -> bool:
        """Whether this declaration is the executable module's ``main`` function."""
        return self.mod_name == "main" and self.name == "main"

    @property
    def mod_name(self) -> str:
        """The qualified name of the module that declares this function."""
        return self._mod_name


class FnInstance:
    """One concrete instantiation of a function declaration.

    The declaration may own a checked source or builtin body, or it may
    be a bodyless extern declaration.

    :param fn: The declaration this is an instantiation of.
    :param args: The impl's type arguments followed by the function's own.
    """

    _fn: Final[FnSpec]
    _args: Final[tuple[typs.Typ, ...]]
    _impl_arity: Final[int]
    _mapping: Final[Mapping[typs.TypParamTyp, typs.Typ]]
    ast: Final[Optional[ast.FnSpec]]

    def __init__(self, fn: FnSpec, args: tuple[typs.Typ, ...]) -> None:
        self.ast = fn.ast
        self._fn = fn
        self._args = args
        impl = fn.impl if isinstance(fn, Fn) else None
        self._impl_arity = len(impl.typ_params) if impl is not None else 0
        all_params = (*impl.typ_params, *fn.typ_params) if impl is not None else fn.typ_params
        self._mapping = dict(zip(all_params, args, strict=True))

    @property
    def args(self) -> tuple[typs.Typ, ...]:
        """Every flat instantiation argument, with impl arguments first."""
        return self._args

    @property
    def impl_args(self) -> tuple[typs.Typ, ...]:
        """The prefix corresponding to the parent impl's parameters."""
        return self._args[: self._impl_arity]

    @property
    def fn_args(self) -> tuple[typs.Typ, ...]:
        """The suffix corresponding to the function's own parameters."""
        return self._args[self._impl_arity :]

    @property
    def _receiver_typ(self) -> Optional[typs.Typ]:
        if not isinstance(self._fn, Fn) or self._fn.impl is None:
            return None
        return self._fn.impl.self_typ.substitute_typ_params(self._mapping)

    @functools.cached_property
    def fn_typ(self) -> typs.FnTyp:
        """This instance's concrete function signature."""
        return asserts.checked_cast(
            self._fn.fn_typ.substitute_typ_params(self._mapping), typs.FnTyp
        )

    @functools.cached_property
    def ptr_typ(self) -> typs.PtrTyp:
        """This instance's concrete function-pointer type."""
        return typs.PtrTyp.get_or_create(self.fn_typ, typs.CONST)

    @functools.cached_property
    def params(self) -> tuple[ir_values.Param, ...]:
        """This instance's concrete formal parameters."""
        return tuple(ir_values.Param(self, param.pos, param.ast) for param in self._fn.params)

    @property
    def name(self) -> str:
        return self._render_name(qualified=False)

    def _render_name(self, qualified: bool) -> str:
        """Render this instance's own name and type arguments.

        :param qualified: Whether to render the type arguments as they
            appear in a symbol name rather than in a diagnostic.
        """
        arg_names = ", ".join(
            typ_arg.qualified_name if qualified else typ_arg.name for typ_arg in self.fn_args
        )
        return f"{self._fn.name}[{arg_names}]" if arg_names else self._fn.name

    @property
    def qualified_name(self) -> str:
        """This instance's mangled symbol name, e.g. ``mod::id[i32]``,
        ``main::Box[i32]::get``, ``<main::Box[i32] as main::Show>::show``,
        or ``__size_of[i32]`` (no module prefix) for a builtin.

        A trait impl's method takes the trait as well as the self type,
        because two traits may declare a method of the same name and the
        same type may implement both. The shape matches the one
        :attr:`~leech.ir_traits.Impl.name` gives a non-generic impl, but
        is rebuilt here per instance from that instance's own concrete
        self type rather than from the impl's abstract one.

        The type arguments are qualified too, for the same reason
        :attr:`~leech.typs.StructTyp.qualified_name` qualifies its own:
        two same-named types from different modules are distinct and
        must not reach one symbol.
        """
        name = self._render_name(qualified=True)
        if self._receiver_typ is not None:
            prefix = self._receiver_typ.qualified_name
            impl = asserts.checked_cast(self._fn, Fn).impl
            trait = None if impl is None else impl.trait
            if trait is not None:
                prefix = f"<{prefix} as {trait.mod_name}::{trait.name}>"
            return f"{prefix}::{name}"
        # A root module can contain a function named main without itself
        # being the executable module. Only main::main is the linker entry.
        if isinstance(self._fn, Fn) and not self._fn.is_generic and self._fn.is_main:
            return name
        prefix = self._fn._qualified_name_prefix
        return f"{prefix}::{name}" if prefix else name

    def is_accessible_from(self, file: src.SrcFile) -> bool:
        return asserts.checked_cast(self._fn, Fn).is_accessible_from(file)

    @property
    def uses_generic_linkage(self) -> bool:
        """Whether this is a generic specialization that may be emitted in many modules."""
        if isinstance(self._fn, FnDecl):
            return False
        if not isinstance(self._fn, Fn):
            return True
        return self._fn.is_generic

    @property
    def has_body(self) -> bool:
        """Whether this instance owns a body that can be lowered."""
        return isinstance(self._fn, BodyFnSpec)

    @property
    def source_fn(self) -> Optional[Fn]:
        """The source declaration behind this instance, if it has one."""
        return self._fn if isinstance(self._fn, Fn) else None

    def is_concrete(self) -> bool:
        """Return whether every type this instance's signature mentions is concrete."""
        return self.fn_typ.is_concrete()

    def substitute_typ_params(self, mapping: Mapping[typs.TypParamTyp, typs.Typ]) -> FnInstance:
        """Return this instance re-derived against ``mapping``, substituting
        into its own type arguments and (for a method) its receiver type.
        A no-op when already concrete.
        """
        new_args = tuple(arg.substitute_typ_params(mapping) for arg in self._args)
        return self._fn.instantiate(new_args)

    @functools.cached_property
    def ref(self) -> FnRef:
        """The unique function-address value for this instance."""
        return FnRef(self)

    @functools.cached_property
    def _siblings(self) -> frozenset[Fn]:
        """Return functions from this instance's declaring ``impl`` block.

        A free function has no parent ``impl`` and therefore no siblings.
        """
        impl = self._fn.impl if isinstance(self._fn, Fn) else None
        return frozenset() if impl is None else frozenset(impl.fns)

    def sibling_instance(self, target: Fn) -> Optional[FnInstance]:
        """Instantiate ``target`` if it belongs to this instance's impl.

        Resolved one at a time rather than as a map of the whole block,
        so that a method nothing calls is never instantiated - an
        instance exists to be lowered and emitted, so building one
        speculatively emits a function the program never reaches.

        :param target: The function referred to from this instance's
            body.
        :return: The sibling instance, or ``None`` if ``target`` is unrelated.
        """
        if target not in self._siblings:
            return None
        return target.instantiate(self.impl_args)

    @functools.cached_property
    def cfg(self) -> ir_values.Cfg:
        """This instance's body, lowered to a control-flow graph. Built
        lazily, on first access."""
        assert isinstance(self._fn, BodyFnSpec), "extern instances have no body"
        builder = ir_builder.CfgBuilder(
            self._fn.typ_check_results,
            self._fn.env.impl_registry,
            self._fn.env.panic_fn,
            self,
            self._mapping,
        )
        self._fn._build_body(builder, self.fn_args)
        return builder.cfg


class FnRef(ir_values.ComptimeValue[typs.PtrTyp, ast.FnSpec]):
    """An immutable function-address value for one concrete instance."""

    instance: Final[FnInstance]

    def __init__(self, instance: FnInstance) -> None:
        super().__init__(instance.ast)
        self.instance = instance

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return self.instance.ptr_typ


class GenericBuiltinFn(FnSpec[ast.FnDefn], BodyFnSpec):
    """A compiler-intrinsic generic function with a Python-authored body."""

    _fn_name: Final[str]
    _typ_param_names: Final[tuple[str, ...]]
    env: ir_env.Env

    def __init__(self, name: str, typ_param_names: tuple[str, ...], e: ir_env.Env) -> None:
        super().__init__(None)
        self._fn_name = name
        self._typ_param_names = typ_param_names
        self.env = e.new_child()

    @functools.cached_property
    def _instance_cache(self) -> dict[tuple[typs.Typ, ...], FnInstance]:
        return {}

    @property
    def instances(self) -> Collection[FnInstance]:
        """Every instantiation requested so far."""
        return self._instance_cache.values()

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the cached instance for ``args``, creating it if needed."""
        assert len(args) == len(self.typ_params)
        inst = self._instance_cache.get(args)
        if inst is None:
            inst = FnInstance(self, args)
            self._instance_cache[args] = inst
        return inst

    @property
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This builtin's interned type parameters in declaration order."""
        return self._typ_params

    @functools.cached_property
    def _typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This builtin's interned type parameters in declaration order."""
        return tuple(
            typs.TypParamTyp.get_or_create(self, i, name)
            for i, name in enumerate(self._typ_param_names)
        )

    @property
    @override
    def fn_typ(self) -> typs.FnTyp:
        return self._fn_typ

    @functools.cached_property
    def _fn_typ(self) -> typs.FnTyp:
        return self.fn_typ_for_typ_args(self.typ_params)

    @abc.abstractmethod
    def fn_typ_for_typ_args(self, typ_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        """Compute this builtin's function type from opaque type parameters."""

    @override
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        return tuple(ir_values.Param(self, pos, None) for pos in range(len(self.fn_typ.param_typs)))

    @property
    @override
    def name(self) -> str:
        return self._fn_name

    @property
    def typ_check_results(self) -> typcheck.TypCheckResults:
        """The builtin's empty type-checking results."""
        return self._typ_check_results

    @functools.cached_property
    def _typ_check_results(self) -> typcheck.TypCheckResults:
        # Builtins emit directly and therefore need no type-checking facts.
        return typcheck.TypCheckResults()

    @property
    def _qualified_name_prefix(self) -> str:
        # A builtin isn't declared in any particular module - it's shared,
        # global, and bound identically into every module's builtin_env -
        # so its mangled symbol name has no module prefix at all.
        return ""

    @abc.abstractmethod
    def _build_body(self, builder: ir_builder.CfgBuilder, typ_args: tuple[typs.Typ, ...]) -> None:
        """Emit this builtin's body directly into ``builder`` (ending in a
        ``ret``), given this instantiation's concrete type arguments."""


class ModVar(ir_values.ComptimePtr[ast.VarDefn]):
    """A module-level binding whose initializer is evaluated at compile time."""

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
            return comptime.Interpreter(self.cfg, (), (), self.env.panic_fn).eval()
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
        builder = ir_builder.CfgBuilder(
            self.typ_check_results,
            self.env.impl_registry,
            self.env.panic_fn,
        )
        builder.build_var_initializer(opt_util.opt_unwrap(self.ast))
        return builder.cfg

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp.get_or_create(self.initializer.typ, self._mut)


class Mod:
    """A module's functions, variables, types, traits, and imports.

    Construction is two-phase so the loader can register it before resolving import cycles.
    """

    _name: Final[str]
    ast: Final[ast.Mod]
    #: Items in declaration order, keyed by namespace and name.
    _items: Final[dict[tuple[ir_env.Env.Namespace, str], ModItem]]
    env: Final[ir_env.Env]
    loader: Final[ir_loader.ModLoader]
    _fns: tuple[Fn, ...]

    def __init__(self, name: str, mod_ast: ast.Mod, loader: ir_loader.ModLoader) -> None:
        # Deferred because builtin classes subclass GenericBuiltinFn.
        from leech import ir_builtins  # noqa: PLC0415

        builtin_env = ir_env.Env(
            impl_registry=loader.impl_registry, panic_fn=loader.prelude_panic_fn
        )
        self._name = name
        self.ast = mod_ast
        self._items = {}
        self.env = builtin_env.new_child()
        self.loader = loader
        self._fns = ()

        ir_builtins.register(builtin_env, loader)

        # The prelude module's own construction (see
        # ir_loader.ModLoader.__init__) is what makes `loader.prelude` be
        # None here - for every other module, it's already built by the
        # time this runs, so every one of its PUBLIC items becomes
        # ambiently available, the same way `usize`/`isize`/`bool` are.
        # An ordinary definition of the same name in this module still
        # wins: it's bound in `self.env` (a child of `builtin_env`) later,
        # by `build()`, shadowing whatever's bound here.
        if loader.prelude is not None:
            for item in loader.prelude.items:
                if item.access == visibility.PUBLIC:
                    builtin_env.add(item._ns, item.name, item.value)

    def build(self) -> None:
        """Build definitions and impls, then eagerly check every declared body."""
        impl_defns = []
        fns: list[Fn] = []
        for defn_ast in self.ast.defns:
            if isinstance(defn_ast, ast.ImplDefn):
                impl_defns.append(defn_ast)
            else:
                self._build_defn(defn_ast, fns)

        for impl_ast in impl_defns:
            self._build_impl_defn(impl_ast, fns)

        self._fns = tuple(fns)

    def check_declarations(self) -> None:
        """Type-check every body after the complete import graph has been built."""
        for fn in self._fns:
            _ = fn.typ_check_results

    @property
    def name(self) -> str:
        """This module's name, used to qualify its items' symbol names."""
        return self._name

    @property
    def items(self) -> Collection[ModItem]:
        """Every item this module declares, in declaration order."""
        return self._items.values()

    @property
    def fns(self) -> Collection[Fn]:
        """Every source function declaration, including impl functions."""
        return self._fns

    def get_item(self, ns: ir_env.Env.Namespace, name: str) -> Optional[ModItem]:
        """Return the item named ``name`` in ``ns``, if declared."""
        return self._items.get((ns, name))

    def _build_defn(self, defn_ast: ast.Defn, fns: list[Fn]) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(
                    defn_ast.let_stmt.ident.name,
                    visibility.Access.from_ast(defn_ast.access),
                    ModVar(defn_ast, self.env),
                )
            case ast.FnDecl():
                if defn_ast.receiver is not None:
                    raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
                self._add_item(
                    defn_ast.name.name,
                    visibility.PUBLIC,
                    FnDecl(defn_ast, self.env, self.name),
                    False,
                )
            case ast.FnDefn():
                if defn_ast.receiver is not None:
                    raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
                qualify_name = self.name != "main" or defn_ast.name.name != "main"
                fn = Fn(defn_ast, self.env, self.name)
                fns.append(fn)
                self._add_item(
                    defn_ast.name.name,
                    visibility.Access.from_ast(defn_ast.access),
                    fn,
                    qualify_name,
                    defn_ast.span,
                )
            case ast.StructDefn():
                self._add_item(
                    defn_ast.ident.name,
                    visibility.Access.from_ast(defn_ast.access),
                    typs.StructTyp.create(defn_ast, self.env, (), self.name),
                )
            case ast.EnumDefn():
                self._add_item(
                    defn_ast.ident.name,
                    visibility.Access.from_ast(defn_ast.access),
                    typs.EnumTyp.create(defn_ast, self.env, self.name),
                )
            case ast.TraitDefn():
                self._add_item(
                    defn_ast.ident.name,
                    visibility.Access.from_ast(defn_ast.access),
                    ir_traits.Trait(defn_ast, self.env, self.name),
                )
            case ast.Import():
                last_ident = defn_ast.path.idents[-1]
                mod_path, qualified_name = self.loader.resolve_import(
                    defn_ast.span.file, defn_ast.path
                )
                mod = self.loader.load(mod_path, qualified_name)
                self._add_item(last_ident.name, visibility.PRIVATE, mod, span=last_ident.span)
            case _:
                # Impl blocks are handled separately.
                raise AssertionError(f"unhandled definition {defn_ast}")

    def _build_impl_defn(self, impl_ast: ast.ImplDefn, fns: list[Fn]) -> None:
        """Build one inherent or trait ``impl`` block.

        :param impl_ast: The parsed ``impl`` block.
        :param fns: Accumulates every source function declaration.
        """
        # The impl's own type parameters, if any, are bound here - before
        # either the inherent or trait branch resolves its target type(s)
        # - so a generic impl's target (e.g. `Pair[T, T]`, or a trait
        # impl's self type) can name them, and each associated function's
        # or method's own signature and body can too (see `Impl.__init__`).
        impl_env = self.env.new_child()
        impl_typ_params = typs.typ_params_from_ast(impl_ast, impl_ast.generic_params, self.env)
        for typ_param in impl_typ_params:
            impl_env.add_container(typ_param.name, typ_param)

        if impl_ast.for_typ is None:
            self._build_inherent_impl_defn(impl_ast, impl_typ_params, impl_env, fns)
        else:
            self._build_trait_impl_defn(impl_ast, impl_typ_params, impl_env, fns)

    def _build_inherent_impl_defn(
        self,
        impl_ast: ast.ImplDefn,
        impl_typ_params: tuple[typs.TypParamTyp, ...],
        impl_env: ir_env.Env,
        fns: list[Fn],
    ) -> None:
        """Build one inherent ``impl SomeStruct { ... }`` block's associated functions.

        :param impl_ast: The parsed ``impl`` block (``impl_ast.for_typ is
            None``).
        :param impl_env: The impl's own scope, with its type parameters
            (if any) already bound.
        :param fns: Accumulates every source function declaration.
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

        impl = ir_traits.Impl(impl_ast, None, typ, impl_typ_params, impl_env, self.name)
        impl.check_typ_params_constrained()
        self.loader.impl_registry.add_impl(impl)
        self._build_impl_fns(impl_ast, impl, fns)

    def _build_trait_impl_defn(
        self,
        impl_ast: ast.ImplDefn,
        impl_typ_params: tuple[typs.TypParamTyp, ...],
        impl_env: ir_env.Env,
        fns: list[Fn],
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
        :param fns: Accumulates every source function declaration.
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
        if trait_typ_ast.generic_args:
            raise NotImplementedError("generic traits aren't supported yet")

        self_typ = typs.Typ.from_ast(opt_util.opt_unwrap(impl_ast.for_typ), impl_env)

        impl = ir_traits.Impl(impl_ast, trait, self_typ, impl_typ_params, impl_env, self.name)
        impl.check_typ_params_constrained()
        # Registered before building any method: two impls of the same
        # trait for the same (or overlapping) self type would otherwise
        # collide on method naming first (DuplicateItemDefnError), a less
        # specific diagnostic than the coherence violation it actually is.
        # This is also what checks the orphan rule (see
        # `ir_traits.Impl.check_orphan_rule`).
        self.loader.impl_registry.add_impl(impl)

        self._build_impl_fns(impl_ast, impl, fns)
        impl.check_complete()

    def _build_impl_fns(
        self,
        impl_ast: ast.ImplDefn,
        impl: ir_traits.Impl,
        fns: list[Fn],
    ) -> None:
        """Build and register every function in an ``impl`` block.

        :param impl_ast: The parsed ``impl`` block.
        :param impl: The block's reified form, which every built function
            is registered on and points back at.
        :param fns: Accumulates every source function declaration.
        """
        for fn_ast in impl_ast.fns:
            # Generic associated functions/methods aren't supported yet -
            # nothing upstream rejects the syntax, so fail loudly here
            # rather than silently mistreat the function's one literal
            # FnTyp as real.
            if fn_ast.generic_params:
                raise NotImplementedError("generic associated functions aren't supported yet")
            fn = Fn(fn_ast, impl.env, self.name, recv_typ=impl.self_typ, impl=impl)
            fns.append(fn)
            impl.add_fn(fn)
            if impl.trait is None:
                impl.env.add_var(fn.name, fn)

    def _add_item(
        self,
        name: str,
        access: visibility.Access,
        value: ir_values.Value[typs.PtrTyp] | ir_env.Container | FnSpec,
        qualify_name: bool = True,
        span: Optional[src.SrcSpan] = None,
    ) -> None:
        item = ModItem(self, name, access, value, qualify_name)
        if reserved.is_reserved(name):
            raise errors.ReservedNameError(name, span if span is not None else ast.opt_span(value))
        # Bind in env before recording the item: Env.add is what rejects a
        # duplicate definition, and it has to raise before _items is
        # written to, so that a name can never be silently rebound to a
        # different item.
        self.env.add(item._ns, name, value, span)
        self._items[(item._ns, name)] = item
