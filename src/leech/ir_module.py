# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Module/program structure: functions, module-level variables, and modules."""

import abc
import contextlib
import dataclasses
import functools
from collections.abc import Collection, Iterator, Mapping
from typing import Final, Optional, cast, override

from leech import (
    asserts,
    ast,
    check_results,
    compilation,
    comptime,
    diag,
    errors,
    ir_builder,
    ir_env,
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
    value: Final[ModItemValue]
    qualify_name: Final[bool] = True

    @property
    def _ns(self) -> ir_env.Env.Namespace:
        if isinstance(self.value, ir_values.Value | FnSymbol):
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

    def check(self) -> None:
        # An import has nothing left to check: its module was found when the item was
        # built, or the item would have been rejected. The module itself is checked as one
        # of the loaded modules, not through its imports.
        if not isinstance(self.value, Mod):
            declaration: compilation.Checkable = self.value
            declaration.check()


class FnSymbol[FnAstT_co: ast.FnDecl](abc.ABC):
    """Base class for a function declaration."""

    ast: Final[Optional[FnAstT_co]]
    _instances: Final[dict[tuple[typs.Typ, ...], FnInstance]]

    def __init__(self, fn_ast: Optional[FnAstT_co]) -> None:
        self.ast = fn_ast
        self._instances = {}

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The source location of this declaration, if it has one."""
        return opt_util.opt_map(self.ast, lambda node: node.span)

    @property
    @abc.abstractmethod
    def ctx(self) -> compilation.Ctx:
        """The compilation this declaration belongs to."""

    @property
    @compilation.unit
    def params(self) -> tuple[ir_values.Param, ...]:
        """This function's formal parameters, in declaration order."""
        return self.calculate_params()

    @abc.abstractmethod
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        """Compute ``params``; overridden by subclasses."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """This function's name."""

    @property
    @abc.abstractmethod
    def fn_typ(self) -> typs.FnTyp:
        """This declaration's function signature."""

    @property
    @compilation.unit
    def ptr_typ(self) -> typs.PtrTyp:
        """A const function-pointer type for this declaration's signature."""
        return typs.PtrTyp(self.fn_typ, typs.CONST)

    @property
    @abc.abstractmethod
    def comptime_params(self) -> tuple[typs.ComptimeParamTyp, ...]:
        """This declaration's comptime parameters in declaration order."""

    @property
    @abc.abstractmethod
    def _qualified_name_prefix(self) -> str:
        """The instance symbol prefix, or empty for a global declaration."""

    @abc.abstractmethod
    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the cached instance for ``args``, creating it if needed."""

    @abc.abstractmethod
    def check(self) -> None:
        pass

    def _instance(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the instance for ``args``, constructing and requesting it the first time."""
        instance = self._instances.get(args)
        if instance is None:
            instance = FnInstance(self, args)
            assert args not in self._instances, "instance creation re-entered itself"
            self._instances[args] = instance
            self.ctx.record_fn_request(instance)
        return instance


@dataclasses.dataclass(frozen=True)
class FnCandidate:
    """A path-selected function and its resolved or deferred comptime arguments.

    ``explicit_fn_args`` is empty for a non-generic function, populated for an
    explicitly applied generic function, and ``None`` when its arguments must
    be inferred.
    """

    fn: FnSymbol
    impl_args: tuple[typs.Typ, ...]
    explicit_fn_args: Optional[tuple[typs.Typ, ...]]

    def apply(self, fn_args: tuple[typs.Typ, ...]) -> AppliedFn:
        """Append the function arguments after the associated impl arguments.

        The arguments must match the function's arity and any arguments already
        recorded as explicit.
        """
        asserts.assert_eq(len(fn_args), len(self.fn.comptime_params))
        if self.explicit_fn_args is not None:
            asserts.assert_eq(fn_args, self.explicit_fn_args)
        return AppliedFn(self.fn, (*self.impl_args, *fn_args))


@dataclasses.dataclass(frozen=True)
class AppliedFn:
    """A function with all impl and function comptime arguments applied.

    ``args`` contains the impl arguments first, followed by the function's own
    arguments.
    """

    fn: FnSymbol
    args: tuple[typs.Typ, ...]

    @property
    def fn_typ(self) -> typs.FnTyp:
        """The signature after substituting every impl and function argument."""
        impl = self.fn.impl if isinstance(self.fn, SrcFnSymbol) else None
        all_params = (
            (*impl.comptime_params, *self.fn.comptime_params)
            if impl is not None
            else self.fn.comptime_params
        )
        asserts.assert_eq(len(self.args), len(all_params))
        mapping = dict(zip(all_params, self.args, strict=True))
        return asserts.checked_cast(self.fn.fn_typ.substitute_typ_params(mapping), typs.FnTyp)

    @property
    def ptr_typ(self) -> typs.PtrTyp:
        """A const function-pointer type for the substituted signature."""
        return typs.PtrTyp(self.fn_typ, typs.CONST)


class ParsedFnSymbol[FnAstT_co: ast.FnDecl](FnSymbol[FnAstT_co]):
    """A source-declared function resolved in its own child scope."""

    env: Final[ir_env.Env]
    _mod_name: Final[str]
    recv_typ: Final[Optional[typs.Typ]]
    _comptime_params: Final[tuple[typs.ComptimeParamTyp, ...]]

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
        self._comptime_params = typs.comptime_params_from_ast(
            fn_ast, fn_ast.comptime_params, self.env
        )
        # Eager binding lets signatures and bodies resolve parameters like named types.
        for comptime_param in self._comptime_params:
            self.env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, typs.ValueParamTyp):
                self.env.add_var(comptime_param.name, comptime_param)

    @property
    @override
    def ctx(self) -> compilation.Ctx:
        return self.env.ctx

    @property
    @override
    @compilation.unit
    def fn_typ(self) -> typs.FnTyp:
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
            recv_typ = typs.PtrTyp(self.recv_typ, typs.Mutability.from_ast(self.ast.receiver.mut))
            param_typs.insert(0, recv_typ)

        return typs.FnTyp(ret_typ, tuple(param_typs))

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

    @property
    @override
    def comptime_params(self) -> tuple[typs.ComptimeParamTyp, ...]:
        return self._comptime_params


class ExternFnSymbol(ParsedFnSymbol[ast.ExternFnDecl]):
    """A source-level ``extern`` function declaration without a body."""

    @property
    def comptime_params(self) -> tuple[typs.ComptimeParamTyp, ...]:
        """Extern declarations have no comptime parameters."""
        return ()

    @override
    @compilation.unit
    def check(self) -> None:
        _ = self.fn_typ

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return this declaration's cached bodyless instance."""
        assert not args, f"{self.name}: extern declarations take no comptime arguments"
        return self._instance(args)

    @property
    def _qualified_name_prefix(self) -> str:
        """Keep the bare symbol name required by the external ABI."""
        return ""


class LowerableFn(abc.ABC):
    """A function declaration that owns a lowerable body."""

    env: ir_env.Env

    @property
    @abc.abstractmethod
    def typ_check_results(self) -> check_results.TypCheckResults:
        """Unsubstituted lowering facts shared by every instance."""

    @abc.abstractmethod
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        """Lower one instance's body into ``builder.cfg``."""


class SrcFnSymbol(ParsedFnSymbol[ast.FnDefn], LowerableFn):
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

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        """Return the cached instance identified by all impl and function arguments."""
        impl_arity = len(self.impl.comptime_params) if self.impl is not None else 0
        fn_arity = len(self.comptime_params)
        assert len(args) == impl_arity + fn_arity, (
            f"{self.name}: expected {impl_arity} impl and {fn_arity} function comptime arguments; "
            f"got {len(args)} total"
        )
        return self._instance(args)

    @override
    @compilation.unit
    def check(self) -> None:
        _ = self.typ_check_results

    @property
    @compilation.unit
    def typ_check_results(self) -> check_results.TypCheckResults:
        """The unsubstituted lowering facts shared by all instances."""
        return typcheck.TypCheck().check_fn(
            opt_util.opt_unwrap(self.ast), self.env, self.fn_typ.ret_typ, self.params
        )

    @property
    def _qualified_name_prefix(self) -> str:
        return self._mod_name

    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        del comptime_args
        builder.build_fn(opt_util.opt_unwrap(self.ast))

    def is_accessible_from(self, file: src.SrcFile) -> bool:
        """Whether this function can be called from code in ``file``.

        Only relevant for associated functions (defined in an ``impl``
        block): a private one can only be called from the module its
        struct is defined in, mirroring
        ``typs.StructField.is_accessible_from``. Free module-level
        functions are filtered by access before reaching this point, during
        name resolution, so this is only consulted for the two ways of
        reaching an associated function: the ``Struct::method()`` path
        form, and the ``value.method()`` dot-call form, during lowering.
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
        """Whether this function has impl-level or function-level comptime parameters."""
        return (self.impl is not None and bool(self.impl.comptime_params)) or bool(
            self.comptime_params
        )

    @property
    def mod_name(self) -> str:
        """The qualified name of the module that declares this function."""
        return self._mod_name


class FnInstance:
    """One concrete instantiation of a function declaration.

    The declaration may own a checked source or intrinsic body, or it may
    be a bodyless extern declaration. ``args`` holds the impl's comptime
    arguments followed by the function's own.
    """

    _fn: Final[FnSymbol]
    _args: Final[tuple[typs.Typ, ...]]
    _impl_arity: Final[int]
    _mapping: Final[Mapping[typs.ComptimeParamTyp, typs.Typ]]
    ast: Final[Optional[ast.FnDecl]]

    def __init__(self, fn: FnSymbol, args: tuple[typs.Typ, ...]) -> None:
        self.ast = fn.ast
        self._fn = fn
        self._args = args
        impl = fn.impl if isinstance(fn, SrcFnSymbol) else None
        self._impl_arity = len(impl.comptime_params) if impl is not None else 0
        all_params = (
            (*impl.comptime_params, *fn.comptime_params) if impl is not None else fn.comptime_params
        )
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
        if not isinstance(self._fn, SrcFnSymbol) or self._fn.impl is None:
            return None
        return self._fn.impl.self_typ.substitute_typ_params(self._mapping)

    @property
    def ctx(self) -> compilation.Ctx:
        return self._fn.ctx

    @property
    @compilation.unit
    def fn_typ(self) -> typs.FnTyp:
        """This instance's concrete function signature."""
        return asserts.checked_cast(
            self._fn.fn_typ.substitute_typ_params(self._mapping), typs.FnTyp
        )

    @property
    @compilation.unit
    def ptr_typ(self) -> typs.PtrTyp:
        """This instance's concrete function-pointer type."""
        return typs.PtrTyp(self.fn_typ, typs.CONST)

    @property
    @compilation.unit
    def params(self) -> tuple[ir_values.Param, ...]:
        """This instance's concrete formal parameters."""
        return tuple(ir_values.Param(self, param.pos, param.ast) for param in self._fn.params)

    @property
    def name(self) -> str:
        return self._render_name(qualified=False)

    def _render_name(self, qualified: bool) -> str:
        """Render this instance's own name and comptime arguments.

        ``qualified`` selects whether the comptime arguments render as
        they appear in a symbol name rather than in a diagnostic.
        """
        arg_names = ", ".join(
            typ_arg.qualified_name if qualified else typ_arg.name for typ_arg in self.fn_args
        )
        return f"{self._fn.name}[{arg_names}]" if arg_names else self._fn.name

    @property
    def qualified_name(self) -> str:
        """This instance's mangled symbol name, e.g. ``mod::id[i32]``,
        ``main::Box[i32]::get``, ``<main::Box[i32] as main::Show>::show``,
        or ``__size_of[i32]`` (no module prefix) for an intrinsic.

        A trait impl's method takes the trait as well as the self type,
        because two traits may declare a method of the same name and the
        same type may implement both. The shape matches the one
        ``ir_traits.Impl.name`` gives a non-generic impl, but
        is rebuilt here per instance from that instance's own concrete
        self type rather than from the impl's abstract one.

        The comptime arguments are qualified too, for the same reason
        ``typs.StructTyp.qualified_name`` qualifies its own:
        two same-named types from different modules are distinct and
        must not reach one symbol.
        """
        name = self._render_name(qualified=True)
        if self._receiver_typ is not None:
            prefix = self._receiver_typ.qualified_name
            impl = asserts.checked_cast(self._fn, SrcFnSymbol).impl
            trait = None if impl is None else impl.trait
            if trait is not None:
                prefix = f"<{prefix} as {trait.mod_name}::{trait.name}>"
            return f"{prefix}::{name}"
        prefix = self._fn._qualified_name_prefix
        return f"{prefix}::{name}" if prefix else name

    def is_accessible_from(self, file: src.SrcFile) -> bool:
        return asserts.checked_cast(self._fn, SrcFnSymbol).is_accessible_from(file)

    @property
    def uses_generic_linkage(self) -> bool:
        """Whether this is a generic specialization that may be emitted in many modules."""
        if isinstance(self._fn, ExternFnSymbol):
            return False
        if not isinstance(self._fn, SrcFnSymbol):
            return True
        return self._fn.is_generic

    @property
    def has_body(self) -> bool:
        """Whether this instance owns a body that can be lowered."""
        return isinstance(self._fn, LowerableFn)

    @property
    def src_fn(self) -> Optional[SrcFnSymbol]:
        """The source declaration behind this instance, if it has one."""
        return self._fn if isinstance(self._fn, SrcFnSymbol) else None

    def is_concrete(self) -> bool:
        """Return whether every type this instance's signature mentions is concrete."""
        return self.fn_typ.is_concrete()

    def substitute_typ_params(
        self, mapping: Mapping[typs.ComptimeParamTyp, typs.Typ]
    ) -> FnInstance:
        """Return this instance re-derived against ``mapping``, substituting
        into its own comptime arguments and (for a method) its receiver type.
        A no-op when already concrete.
        """
        new_args = tuple(arg.substitute_typ_params(mapping) for arg in self._args)
        return self._fn.instantiate(new_args)

    @functools.cached_property
    def ref(self) -> FnRef:
        """The unique function-address value for this instance."""
        return FnRef(self)

    @property
    @compilation.unit
    def cfg(self) -> ir_values.Cfg:
        """This instance's body, lowered to a control-flow graph. Built
        lazily, on first access."""
        assert isinstance(self._fn, LowerableFn), "extern instances have no body"
        builder = ir_builder.CfgBuilder(
            self._fn.env.ctx,
            self._fn.typ_check_results,
            self,
            self._mapping,
        )
        self._fn._build_body(builder, self.fn_args)
        return builder.cfg


class FnRef(ir_values.ComptimeValue[typs.PtrTyp, ast.FnDecl]):
    """An immutable function-address value for one concrete instance."""

    instance: Final[FnInstance]

    def __init__(self, instance: FnInstance) -> None:
        super().__init__(instance.ast)
        self.instance = instance

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return self.instance.ptr_typ


class IntrinsicFnSymbol(FnSymbol[ast.FnDefn], LowerableFn):
    """A compiler intrinsic with a Python-authored body."""

    _fn_name: Final[str]
    # Intrinsics take only type parameters in this issue - see #39.
    _typ_param_names: Final[tuple[str, ...]]
    env: ir_env.Env

    def __init__(self, name: str, typ_param_names: tuple[str, ...], e: ir_env.Env) -> None:
        super().__init__(None)
        self._fn_name = name
        self._typ_param_names = typ_param_names
        self.env = e.new_child()

    @property
    @override
    def ctx(self) -> compilation.Ctx:
        return self.env.ctx

    def instantiate(self, args: tuple[typs.Typ, ...]) -> FnInstance:
        assert len(args) == len(self.comptime_params)
        return self._instance(args)

    @property
    def comptime_params(self) -> tuple[typs.ComptimeParamTyp, ...]:
        """This intrinsic's type parameters in declaration order."""
        return self._typ_params

    @override
    @compilation.unit
    def check(self) -> None:
        # An intrinsic's signature is the compiler's own, so it has nothing to check.
        pass

    @functools.cached_property
    def _typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        return tuple(typs.TypParamTyp(self, name) for name in self._typ_param_names)

    @property
    @override
    def fn_typ(self) -> typs.FnTyp:
        return self._fn_typ

    @functools.cached_property
    def _fn_typ(self) -> typs.FnTyp:
        return self.fn_typ_for_comptime_args(self.comptime_params)

    @abc.abstractmethod
    def fn_typ_for_comptime_args(self, comptime_params: tuple[typs.Typ, ...]) -> typs.FnTyp:
        """Compute this intrinsic's function type from opaque type parameters."""

    @override
    def calculate_params(self) -> tuple[ir_values.Param, ...]:
        return tuple(ir_values.Param(self, pos, None) for pos in range(len(self.fn_typ.param_typs)))

    @property
    @override
    def name(self) -> str:
        return self._fn_name

    @property
    def typ_check_results(self) -> check_results.TypCheckResults:
        """The intrinsic's empty type-checking results."""
        return self._typ_check_results

    @functools.cached_property
    def _typ_check_results(self) -> check_results.TypCheckResults:
        # Intrinsics emit directly and therefore need no type-checking facts.
        return check_results.TypCheckResults()

    @property
    def _qualified_name_prefix(self) -> str:
        # An intrinsic isn't declared in any particular module - it's shared,
        # global, and bound identically into every module's builtin_env -
        # so its mangled symbol name has no module prefix at all.
        return ""

    @abc.abstractmethod
    def _build_body(
        self, builder: ir_builder.CfgBuilder, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        """Emit this intrinsic's body directly into ``builder`` (ending in a
        ``ret``), given this instantiation's concrete type arguments."""


class ModVar(ir_values.ComptimePtr[ast.VarDefn]):
    """A module-level binding whose initializer is evaluated at compile time."""

    env: Final[ir_env.Env]
    _mut: Final[typs.Mutability]

    @override
    def __init__(self, var_ast: ast.VarDefn, e: ir_env.Env) -> None:
        super().__init__(var_ast)
        self.env = e.new_child()
        self._mut = typs.Mutability.from_ast(var_ast.let_stmt.mut)

    @property
    def ctx(self) -> compilation.Ctx:
        return self.env.ctx

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

    @compilation.unit
    def check(self) -> None:
        _ = self.initializer

    @property
    def initializer(self) -> ir_values.ComptimeValue:
        """This variable's initial value, evaluated at compile time.

        Computed lazily, on first access. Raises
        ``errors.CircularVarInitializerError`` if evaluating it
        requires (directly or transitively, possibly through other modules)
        evaluating this same variable's initializer again.
        """
        # The cycle is detected here, outside the unit, so that it is found before the
        # unit would be entered again.
        with self.env.ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER,
            self,
            self,
        ) as cycle:
            if cycle is not None:
                self.ctx.fail_cycle(
                    cycle,
                    errors.CircularVarInitializerError(
                        self.name,
                        self.span,
                        # The final detail repeats the first to close the cycle.
                        [(var.name, var.span) for var in cycle.details[:-1]],
                    ),
                )
            return self._evaluated_initializer

    @property
    @compilation.unit
    def _evaluated_initializer(self) -> ir_values.ComptimeValue:
        return comptime.Interpreter(self.cfg, (), (), self.env.ctx.builtins.panic_ref).eval()

    @property
    @compilation.unit
    def typ_check_results(self) -> check_results.TypCheckResults:
        """This variable's initializer, type-checked into a side table.

        Built lazily, on first access; forced by ``cfg`` before
        lowering begins.
        """
        return typcheck.TypCheck().check_var_initializer(opt_util.opt_unwrap(self.ast), self.env)

    @property
    @compilation.unit
    def cfg(self) -> ir_values.Cfg:
        """The initializer expression, lowered to a control-flow graph.

        Built lazily, on first access.
        """
        builder = ir_builder.CfgBuilder(
            self.env.ctx,
            self.typ_check_results,
        )
        builder.build_var_initializer(opt_util.opt_unwrap(self.ast))
        return builder.cfg

    @override
    def calculate_typ(self) -> typs.PtrTyp:
        return typs.PtrTyp(self.initializer.typ, self._mut)


class Mod:
    """A module's functions, variables, types, traits, and imports.

    Construction is two-phase so the loader can register it before resolving import cycles.
    """

    _name: Final[str]
    ast: Final[ast.Mod]
    #: Items in declaration order, keyed by namespace and name.
    _items: Final[dict[tuple[ir_env.Env.Namespace, str], ModItem]]
    env: Final[ir_env.Env]
    #: The proof of the error that rejected each poisoned item, by namespace and name.
    _poisoned: Final[dict[tuple[ir_env.Env.Namespace, str], diag.ReportProof]]
    _src_fn_symbols: tuple[SrcFnSymbol, ...]
    _entry_fn: Optional[SrcFnSymbol]

    def __init__(self, name: str, mod_ast: ast.Mod, ctx: compilation.Ctx) -> None:
        # Deferred because intrinsic classes subclass IntrinsicFnSymbol.
        from leech import ir_builtins  # noqa: PLC0415

        builtin_env = ir_env.Env(ctx)
        self._name = name
        self.ast = mod_ast
        self._items = {}
        self._poisoned = {}
        self.env = builtin_env.new_child()
        self._src_fn_symbols = ()
        self._entry_fn = None

        ir_builtins.register(builtin_env, ctx.builtins)

        # The prelude is None only while it is itself being built (see
        # ir_loader.ModLoader.prelude) - for every other module, it's
        # already built by the time this runs, so every one of its PUBLIC
        # items becomes ambiently available, the same way
        # `usize`/`isize`/`bool` are. An ordinary definition of the same
        # name in this module still wins: it's bound in `self.env` (a
        # child of `builtin_env`) later, by `build()`, shadowing whatever's
        # bound here.
        prelude = ctx.loader.prelude
        if prelude is not None:
            for item in prelude.items:
                if item.access == visibility.PUBLIC:
                    builtin_env.add(item._ns, item.name, item.value)

    @property
    def ctx(self) -> compilation.Ctx:
        """The compilation this module belongs to."""
        return self.env.ctx

    def build(self) -> None:
        """Build definitions, then impls.

        An item rejected with an error is reported, and building continues with the next.
        A rejected item adds nothing to the module, except that its name, unless already
        taken, is poisoned, so uses of it report nothing more.
        """
        impl_defns = []
        src_fn_symbols: list[SrcFnSymbol] = []
        for defn_ast in self.ast.defns:
            if isinstance(defn_ast, ast.ImplDefn):
                impl_defns.append(defn_ast)
            else:
                with self._rejecting_on_error(defn_ast):
                    self._build_defn(defn_ast, src_fn_symbols)

        for impl_ast in impl_defns:
            with self._rejecting_on_error(impl_ast):
                self._build_impl_defn(impl_ast, src_fn_symbols)

        self._src_fn_symbols = tuple(src_fn_symbols)

    @contextlib.contextmanager
    def _rejecting_on_error(self, defn_ast: ast.DefnKind) -> Iterator[None]:
        """Build ``defn_ast``'s item in the block, rejecting it if the block fails.

        The block must commit nothing unless it succeeds. A user error raised in it is
        recovered from, as ``Ctx.recovering`` does, and the item is rejected: its comptime
        parameters are discarded, and its name, unless already taken, is poisoned.
        """
        with self.ctx.recovering() as recovery:
            yield
        reported = recovery.failure
        if reported is None:
            return
        self.ctx.discard_comptime_params(_comptime_param_owners(defn_ast))
        binding = _defn_binding(defn_ast)
        if binding is None:
            return
        ns, ident = binding
        if not self.env.is_bound_here(ns, ident.name):
            self.env.poison(ns, ident.name, reported, ident.span)
            self._poisoned[(ns, ident.name)] = reported

    def check_declarations(self) -> None:
        """Check every declaration after the complete import graph has been built.

        This finds every user error in the module's own declarations that does not depend
        on which generic instances the program requests: every function body, module
        variable initializer (including its compile-time evaluation), struct, union and
        enum. An error in one declaration is reported, and checking continues with the next.
        """
        for item in self._items.values():
            with self.ctx.recovering():
                item.check()
        # Impl functions are not items.
        for fn in self._src_fn_symbols:
            with self.ctx.recovering():
                fn.check()

    def designate_entry(self) -> None:
        """Make this module's ``main`` the program entry point, after validating it.

        ``main`` must be a non-generic function with a body and type ``fn() i32``. Either
        access is allowed, because the entry point calls it from inside this module. The entry
        point is the C ``main`` symbol, so any ``extern fn main`` in the loaded program must
        have the same type.
        """
        item = self.get_item(ir_env.Env.Namespace.VARS, "main")
        if item is None:
            raise errors.EntryMainMissingError(self.name, self.ast.span.file.path)
        if not isinstance(item.value, SrcFnSymbol):
            raise errors.EntryMainNotDefinedFnError(ast.opt_span(item.value))
        fn = item.value
        span = opt_util.opt_unwrap(fn.ast).name.span
        if fn.is_generic:
            raise errors.EntryMainGenericError(span)
        entry_typ = typs.FnTyp(typs.I32, ())
        if fn.fn_typ is not entry_typ:
            raise errors.EntryMainSignatureError(fn.fn_typ.name, span)
        for mod in self.ctx.loader.mods:
            extern_item = mod.get_item(ir_env.Env.Namespace.VARS, "main")
            if (
                extern_item is not None
                and isinstance(extern_item.value, ExternFnSymbol)
                and extern_item.value.fn_typ is not entry_typ
            ):
                raise errors.EntryMainExternConflictError(
                    extern_item.value.fn_typ.name, ast.opt_span(extern_item.value)
                )
        self._entry_fn = fn

    @property
    def entry_fn(self) -> Optional[SrcFnSymbol]:
        """The function designated as the program entry point, if any."""
        return self._entry_fn

    @property
    def name(self) -> str:
        """This module's name, used to qualify its items' symbol names."""
        return self._name

    @property
    def items(self) -> Collection[ModItem]:
        """Every item this module declares, in declaration order."""
        return self._items.values()

    @property
    def src_fn_symbols(self) -> Collection[SrcFnSymbol]:
        """Every source function symbol, including impl functions."""
        return self._src_fn_symbols

    def get_item(self, ns: ir_env.Env.Namespace, name: str) -> Optional[ModItem]:
        """Return the item named ``name`` in ``ns``, if declared.

        Raises ``diag.ReportedError`` if the item's definition was rejected with an error.
        """
        reported = self._poisoned.get((ns, name))
        if reported is not None:
            raise diag.ReportedError(reported)
        return self._items.get((ns, name))

    def _build_defn(self, defn_ast: ast.DefnKind, src_fn_symbols: list[SrcFnSymbol]) -> None:
        ns, ident = opt_util.opt_unwrap(_defn_binding(defn_ast))
        # An import's name is checked before its module is loaded, so a rejected import
        # loads nothing.
        span = ident.span if isinstance(defn_ast, ast.Import) else defn_ast.span
        with self._binding_item(ns, ident.name, span) as item:
            match defn_ast:
                case ast.VarDefn():
                    item.bind(
                        ModVar(defn_ast, self.env), visibility.Access.from_ast(defn_ast.access)
                    )
                case ast.ExternFnDecl():
                    if defn_ast.receiver is not None:
                        raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
                    item.bind(
                        ExternFnSymbol(defn_ast, self.env, self.name),
                        visibility.PUBLIC,
                        qualify_name=False,
                    )
                case ast.FnDefn():
                    if defn_ast.receiver is not None:
                        raise errors.SelfParamOutsideImplError(defn_ast.receiver.span)
                    item.bind(
                        SrcFnSymbol(defn_ast, self.env, self.name),
                        visibility.Access.from_ast(defn_ast.access),
                    )
                case ast.StructDefn():
                    template = typs.StructTypTemplate(defn_ast, self.env, self.name)
                    value = template if template.comptime_params else template.module_instance
                    item.bind(value, visibility.Access.from_ast(defn_ast.access))
                case ast.EnumDefn():
                    item.bind(
                        typs.EnumTyp(defn_ast, self.env, self.name),
                        visibility.Access.from_ast(defn_ast.access),
                    )
                case ast.UnionDefn():
                    template = typs.UnionTypTemplate(defn_ast, self.env, self.name)
                    value = template if template.comptime_params else template.module_instance
                    item.bind(value, visibility.Access.from_ast(defn_ast.access))
                case ast.TraitDefn():
                    item.bind(
                        ir_traits.Trait(defn_ast, self.env, self.name),
                        visibility.Access.from_ast(defn_ast.access),
                    )
                case ast.Import():
                    mod = self.ctx.loader.load(self.ctx.loader.resolve_import(defn_ast.path))
                    item.bind(mod, visibility.PRIVATE)
                case ast.ImplDefn():
                    # Impl blocks are handled separately.
                    raise AssertionError("impl block reached ordinary definition building")
        if isinstance(item.value, SrcFnSymbol):
            src_fn_symbols.append(item.value)

    def _build_impl_defn(self, impl_ast: ast.ImplDefn, src_fn_symbols: list[SrcFnSymbol]) -> None:
        # The impl's own comptime parameters, if any, are bound here - before
        # either the inherent or trait branch resolves its target type(s)
        # - so a generic impl's target (e.g. `Pair[T, T]`, or a trait
        # impl's self type) can name them, and each associated function's
        # or method's own signature and body can too (see `Impl.__init__`).
        impl_env = self.env.new_child()
        impl_comptime_params = typs.comptime_params_from_ast(
            impl_ast, impl_ast.comptime_params, self.env
        )
        for comptime_param in impl_comptime_params:
            impl_env.add_container(comptime_param.name, comptime_param)
            if isinstance(comptime_param, typs.ValueParamTyp):
                impl_env.add_var(comptime_param.name, comptime_param)

        if impl_ast.for_typ is None:
            self._build_inherent_impl_defn(impl_ast, impl_comptime_params, impl_env, src_fn_symbols)
        else:
            self._build_trait_impl_defn(impl_ast, impl_comptime_params, impl_env, src_fn_symbols)

    def _build_inherent_impl_defn(
        self,
        impl_ast: ast.ImplDefn,
        impl_comptime_params: tuple[typs.ComptimeParamTyp, ...],
        impl_env: ir_env.Env,
        src_fn_symbols: list[SrcFnSymbol],
    ) -> None:
        """Build one inherent ``impl SomeStruct { ... }`` block's associated functions.

        Rejects ``impl_ast.typ`` if it doesn't name a struct or union type
        defined in this module.
        """
        impl_typ_ast = impl_ast.typ
        if not isinstance(impl_typ_ast, ast.BasicTyp):
            raise errors.ImplForNonNominalTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        typ = typs.Typ.from_ast(impl_typ_ast, impl_env)
        if not isinstance(typ, typs.StructTyp | typs.UnionTyp):
            raise errors.ImplForNonNominalTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        if len(impl_typ_ast.path.segs) > 1:
            raise errors.ImplForNonLocalTypError(impl_typ_ast.diag_str(), impl_typ_ast.span)

        if isinstance(typ, typs.UnionTyp):
            self._check_no_variant_name_clash(impl_ast, typ)

        impl = ir_traits.Impl(impl_ast, None, typ, impl_comptime_params, impl_env, self.name)
        impl.check_comptime_params_constrained()
        with self.ctx.impl_registry.registering(impl):
            fns = self._build_impl_fn_symbols(impl_ast, impl)
        src_fn_symbols.extend(fns)

    @staticmethod
    def _check_no_variant_name_clash(impl_ast: ast.ImplDefn, typ: typs.UnionTyp) -> None:
        """Reject an associated function named after one of the union's variants.

        A path into a union resolves a variant before an associated
        function, so the variant takes the `U::f` spelling. A function
        without a receiver is then unnameable outright; a method keeps
        its dot-call but loses the explicit path form, which is meant to
        be equivalent to it. Rust allows the clash and lets the variant
        win, leaving the function unreachable; rejecting it here means
        that lookup order never has a tie to break.
        """
        for fn_defn in impl_ast.fn_defns:
            variant = typ.template.variants.get(fn_defn.name.name)
            if variant is not None:
                raise errors.FnNameClashesWithUnionVariantError(
                    fn_defn.name.name,
                    typ.template.name,
                    fn_defn.name.span,
                    variant.ast.ident.span,
                )

    def _build_trait_impl_defn(
        self,
        impl_ast: ast.ImplDefn,
        impl_comptime_params: tuple[typs.ComptimeParamTyp, ...],
        impl_env: ir_env.Env,
        src_fn_symbols: list[SrcFnSymbol],
    ) -> None:
        """Build one ``impl Trait for SelfTyp { ... }`` block's methods.

        Unlike an inherent impl's target, ``SelfTyp`` isn't restricted to
        a local struct - it may be any type, since a trait impl's
        coherence comes from the orphan rule instead (see below), not
        from requiring the type to be local outright.

        Rejects ``impl_ast.typ`` if it doesn't name a trait, resolves its
        comptime arguments against the trait's declared comptime
        parameters, and validates coherence (the orphan rule, and no
        overlap with another impl of the same trait) and that this impl's
        methods exactly match the trait's declared prototypes.
        """
        trait_typ_ast = impl_ast.typ
        if not isinstance(trait_typ_ast, ast.BasicTyp):
            raise errors.ImplForNonTraitError(trait_typ_ast.diag_str(), trait_typ_ast.span)
        trait_application = impl_env.resolve_trait(trait_typ_ast.path)

        self_typ = typs.Typ.from_ast(opt_util.opt_unwrap(impl_ast.for_typ), impl_env)

        impl = ir_traits.Impl(
            impl_ast,
            trait_application.trait,
            self_typ,
            impl_comptime_params,
            impl_env,
            self.name,
            trait_args=trait_application.args,
        )
        impl.check_comptime_params_constrained()
        # Checked before building any method: two impls of the same trait
        # for the same (or overlapping) self type would otherwise collide
        # on method naming first (DuplicateItemDefnError), a less specific
        # diagnostic than the coherence violation it actually is. This is
        # also what checks the orphan rule (see
        # `ir_traits.Impl.check_orphan_rule`). The impl is registered only
        # once it is complete, so a rejected impl takes part in no lookup.
        with self.ctx.impl_registry.registering(impl):
            fns = self._build_impl_fn_symbols(impl_ast, impl)
            impl.check_complete()
        src_fn_symbols.extend(fns)

    def _build_impl_fn_symbols(
        self, impl_ast: ast.ImplDefn, impl: ir_traits.Impl
    ) -> list[SrcFnSymbol]:
        """Build every function in an ``impl`` block, returning them in order.

        Every built function is registered on ``impl`` and points back at it.
        """
        fns: list[SrcFnSymbol] = []
        for fn_ast in impl_ast.fn_defns:
            # Generic associated functions/methods aren't supported yet -
            # nothing upstream rejects the syntax, so fail loudly here
            # rather than silently mistreat the function's one literal
            # FnTyp as real.
            if fn_ast.comptime_params:
                raise NotImplementedError("generic associated functions aren't supported yet")
            fn = SrcFnSymbol(fn_ast, impl.env, self.name, recv_typ=impl.self_typ, impl=impl)
            impl.add_fn_symbol(fn)
            if impl.trait is None:
                impl.env.add_var(fn.name, fn)
            fns.append(fn)
        return fns

    @contextlib.contextmanager
    def _binding_item(
        self, ns: ir_env.Env.Namespace, name: str, span: src.SrcSpan
    ) -> Iterator[_PendingItem]:
        """Check now that an item can be named ``name``, and add it after the block.

        The block builds the item and passes it to the yielded ``_PendingItem``'s ``bind``.
        If the block raises, nothing is added.
        """
        if reserved.is_reserved(name):
            raise errors.ReservedNameError(name, span)
        with self.env.binding(ns, name, span) as binding:
            pending = _PendingItem(binding)
            yield pending
            item = ModItem(
                self,
                name,
                opt_util.opt_unwrap(pending.access),
                pending.value,
                pending.qualify_name,
            )
            assert item._ns == ns, f"{name} was checked in the wrong namespace"
        self._items[(ns, name)] = item


@dataclasses.dataclass
class _PendingItem:
    """A module item being built, whose name has been checked but not yet bound.

    Its value goes to ``binding``, along with the item's access and symbol qualification.
    """

    binding: ir_env.PendingBinding
    access: Optional[visibility.Access] = None
    qualify_name: bool = True

    def bind(
        self, value: ModItemValue, access: visibility.Access, qualify_name: bool = True
    ) -> None:
        self.binding.bind(value)
        self.access = access
        self.qualify_name = qualify_name

    @property
    def value(self) -> ModItemValue:
        # Only bind, which takes a ModItemValue, gives the binding its value.
        return cast(ModItemValue, self.binding.bound())


def _comptime_param_owners(defn_ast: ast.DefnKind) -> tuple[ast.Ast, ...]:
    if isinstance(defn_ast, ast.ImplDefn):
        return (defn_ast, *defn_ast.fn_defns)
    return (defn_ast,)


def _defn_binding(defn_ast: ast.DefnKind) -> Optional[tuple[ir_env.Env.Namespace, ast.Ident]]:
    match defn_ast:
        case ast.VarDefn():
            return ir_env.Env.Namespace.VARS, defn_ast.let_stmt.ident
        case ast.ExternFnDecl() | ast.FnDefn():
            return ir_env.Env.Namespace.VARS, defn_ast.name
        case ast.StructDefn() | ast.EnumDefn() | ast.UnionDefn() | ast.TraitDefn():
            return ir_env.Env.Namespace.CONTAINERS, defn_ast.ident
        case ast.Import():
            return ir_env.Env.Namespace.CONTAINERS, defn_ast.path.segs[-1].ident
        case ast.ImplDefn():
            return None


type ModItemValue = (
    ModVar
    | FnSymbol
    | typs.StructTyp
    | typs.StructTypTemplate
    | typs.UnionTyp
    | typs.UnionTypTemplate
    | typs.EnumTyp
    | Mod
    | ir_traits.Trait
)
"""Every value a ModItem can bind in a module namespace."""
