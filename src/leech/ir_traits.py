"""Trait declarations and their implementations."""

from collections.abc import Collection, Hashable
from typing import Final, Optional

from leech import ast, ir_env, ir_module, typs
from leech.errors import (
    AmbiguousMethodError,
    ConflictingImplsError,
    DuplicateItemDefnError,
    ExtraMethodInImplError,
    OrphanImplError,
    TraitMethodMissingReceiverError,
    TraitMethodNotImplementedError,
    TraitMethodSignatureMismatchError,
)
from leech.src import SrcSpan

# `typs` is imported as a module, not `from leech.typs import ...`, even
# though every other user of these names does the latter: ir_env imports
# this module, and typs imports ir_env, so by the time this module is
# first reached, typs may still be mid-initialization - a deep import of
# one of its names (rather than the module object itself) would try to
# bind a name that doesn't exist yet.


class TraitMethod:
    """One method prototype declared inside a :class:`Trait`.

    :param ast: The parsed method prototype.
    :param trait: The declaring trait, used to resolve the prototype's
        parameter and return types (which may name the trait's own type
        parameters).
    :raises TraitMethodMissingReceiverError: If ``ast`` has no ``self``
        receiver.
    """

    ast: Final[ast.TraitFn]
    trait: Final[Trait]

    def __init__(self, ast: ast.TraitFn, trait: Trait) -> None:
        if ast.receiver is None:
            raise TraitMethodMissingReceiverError(ast.name.name, ast.span)
        self.ast = ast
        self.trait = trait

    @property
    def name(self) -> str:
        """This method's name."""
        return self.ast.name.name

    @property
    def span(self) -> SrcSpan:
        """The source location of this method's prototype."""
        return self.ast.span

    def fn_typ_for_self(self, self_typ: typs.Typ) -> typs.FnTyp:
        """This method's signature with ``self_typ`` substituted for the receiver.

        The receiver's type isn't written in source - a trait method's
        prototype only says whether it takes ``self`` or ``mut self`` -
        so it's synthesized here the same way
        :meth:`~leech.ir_module.NonBuiltinFnSpec.calculate_typ` synthesizes
        an inherent method's, from whatever type is implementing the
        trait.

        :param self_typ: The type standing in for ``Self`` - a trait
            impl's own :attr:`Impl.self_typ`.
        :return: The method's signature, receiver included.
        """
        assert self.ast.receiver is not None
        recv_typ = typs.PtrTyp.get_or_create(
            self_typ, typs.Mutability.from_ast(self.ast.receiver.mut)
        )
        param_typs = [recv_typ] + [
            typs.Typ.from_ast(param_ast.typ, self.trait.env) for param_ast in self.ast.params
        ]
        ret_typ = (
            typs.VOID
            if self.ast.ret_typ is None
            else typs.Typ.from_ast(self.ast.ret_typ, self.trait.env)
        )
        return typs.FnTyp.get_or_create(ret_typ, tuple(param_typs))


class Trait:
    """A trait definition: a named set of method prototypes an ``impl
    Trait for SomeTyp { ... }`` block promises to provide.

    Lives in :attr:`~leech.ir_env.Env.Namespace.CONTAINERS`, alongside types
    and modules, so a trait can't share a name with either - but isn't
    itself a type: naming one where a type is expected is
    :class:`~leech.errors.TraitUsedAsTypError`.

    :param ast: The parsed trait declaration.
    :param e: The enclosing scope, used to resolve method signatures.
    :param mod_name: The name of the module this trait is declared in,
        used by the orphan rule (see :meth:`Impl.check_orphan_rule`).
    :raises DuplicateItemDefnError: If two of the trait's methods share a
        name.
    """

    ast: Final[ast.TraitDefn]
    env: Final[ir_env.Env]
    mod_name: Final[str]
    _methods: Final[dict[str, TraitMethod]]

    def __init__(self, ast: ast.TraitDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = ast
        self.mod_name = mod_name
        self.env = e.new_child()
        for index, param_ast in enumerate(ast.generic_params):
            self.env.add_container(
                param_ast.ident.name, typs.TypParamTyp.get_or_create(ast, index, param_ast)
            )
        self._methods = {}
        for method_ast in ast.methods:
            method = TraitMethod(method_ast, self)
            existing = self._methods.get(method.name)
            if existing is not None:
                raise DuplicateItemDefnError(
                    "trait method", method.name, method.span, existing.span
                )
            self._methods[method.name] = method

    @property
    def name(self) -> str:
        """This trait's name."""
        return self.ast.ident.name

    @property
    def span(self) -> SrcSpan:
        """The source location of this trait's declaration."""
        return self.ast.span

    def get_method(self, name: str) -> Optional[TraitMethod]:
        """Find this trait's method prototype called ``name``.

        :param name: The name to look up.
        :return: The method, or ``None`` if this trait declares no method
            of that name.
        """
        return self._methods.get(name)

    @property
    def methods(self) -> Collection[TraitMethod]:
        """This trait's method prototypes, in declaration order."""
        return self._methods.values()


def _is_local(item: Trait | typs.Typ, mod_name: str) -> bool:
    """Whether a trait or type is defined in the module named ``mod_name``.

    Used by the orphan rule (see :meth:`Impl.check_orphan_rule`): a struct
    knows the module that declared it (see
    :attr:`~leech.typs.StructTyp.mod_name`, threaded through even a generic
    instantiation's own type arguments), and a trait does too (see
    :attr:`Trait.mod_name`); every other type - ``i32``, ``bool``, a
    pointer, an array - belongs to no module and so is never local.

    :param item: The trait or type to check.
    :param mod_name: The module to check locality against.
    :return: Whether ``item`` is defined in that module.
    """
    if isinstance(item, (Trait, typs.StructTyp)):
        return item.mod_name == mod_name
    return False


class Impl:
    """One ``impl Trait for SelfTyp { ... }`` block.

    Unlike an inherent ``impl`` block's associated functions - registered
    directly among their struct's own members (see
    :meth:`~leech.typs.StructTyp.add_assoc_fn`) - a trait impl's methods live
    only here: :attr:`self_typ` may not even be a struct, and, for a
    generic impl, may not be a real, lowerable type at all (its type
    parameters are left opaque - the same way a generic struct's own
    fields are, see :meth:`~leech.typs.StructTyp.is_concrete`). Finding one
    from a receiver's type goes through :class:`ImplRegistry` instead.

    :param ast: The parsed ``impl`` block.
    :param trait: The trait this implements.
    :param self_typ: The type this implements ``trait`` for - the impl's
        own type parameters, if any, appear within it as opaque
        :class:`~leech.typs.TypParamTyp`\\ s (e.g. ``Pair[A, A]`` for
        ``impl[A: Show] Show for Pair[A, A]``).
    :param e: The enclosing scope, used to resolve method signatures and
        bodies. Recorded (extended with ``Self`` bound to ``self_typ``) as
        :attr:`env`.
    :param mod_name: The name of the module this impl is declared in.
    """

    ast: Final[ast.ImplDefn]
    trait: Final[Trait]
    self_typ: Final[typs.Typ]
    env: Final[ir_env.Env]
    mod_name: Final[str]
    _methods: Final[dict[str, ir_module.Fn]]

    def __init__(
        self,
        ast: ast.ImplDefn,
        trait: Trait,
        self_typ: typs.Typ,
        e: ir_env.Env,
        mod_name: str,
    ) -> None:
        self.ast = ast
        self.trait = trait
        self.self_typ = self_typ
        self.mod_name = mod_name
        self.env = e.new_child()
        self.env.add_container("Self", self_typ)
        self._methods = {}

    @property
    def name(self) -> str:
        """This impl's diagnostic name, e.g. ``<i32 as Show>``."""
        return f"<{self.self_typ.name} as {self.trait.name}>"

    @property
    def span(self) -> SrcSpan:
        """The source location of this impl block."""
        return self.ast.span

    def check_orphan_rule(self) -> None:
        """Raise unless :attr:`trait` or :attr:`self_typ` is defined in :attr:`mod_name`.

        Leech's orphan rule (similar to Rust's): it keeps any two modules
        from being able to write conflicting impls of the same trait for
        the same type, since :attr:`self_typ` isn't restricted to a local
        type the way an inherent impl's target is.

        :raises OrphanImplError: If neither is local.
        """
        if not _is_local(self.trait, self.mod_name) and not _is_local(self.self_typ, self.mod_name):
            raise OrphanImplError(self.trait.name, self.self_typ.name, self.span)

    def add_method(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this impl's methods.

        :param fn: The method, already constructed with :attr:`env` as its
            enclosing scope and :attr:`self_typ` as its receiver type.
        :raises ExtraMethodInImplError: If :attr:`trait` declares no
            method named ``fn.name``.
        :raises DuplicateItemDefnError: If this impl already defines a
            method of that name.
        :raises TraitMethodSignatureMismatchError: If ``fn``'s signature
            doesn't match the one :attr:`trait` declares for it.
        """
        trait_method = self.trait.get_method(fn.name)
        if trait_method is None:
            raise ExtraMethodInImplError(self.trait.name, fn.name, fn.span)
        existing = self._methods.get(fn.name)
        if existing is not None:
            raise DuplicateItemDefnError("method", fn.name, fn.span, existing.span)
        expected_typ = trait_method.fn_typ_for_self(self.self_typ)
        if fn.fn_typ is not expected_typ:
            raise TraitMethodSignatureMismatchError(
                self.trait.name, fn.name, fn.fn_typ.name, expected_typ.name, fn.span
            )
        self._methods[fn.name] = fn

    def get_method(self, name: str) -> Optional[ir_module.Fn]:
        """Find this impl's method called ``name``.

        :param name: The name to look up.
        :return: The method, or ``None`` if this impl defines no method of
            that name.
        """
        return self._methods.get(name)

    def check_complete(self) -> None:
        """Raise if this impl is missing any of its trait's methods.

        :raises TraitMethodNotImplementedError: If :attr:`trait` declares
            a method this impl never defined.
        """
        for trait_method in self.trait.methods:
            if trait_method.name not in self._methods:
                raise TraitMethodNotImplementedError(
                    self.trait.name, trait_method.name, self.self_typ.name, self.span
                )


def head_shape(typ: typs.Typ) -> Hashable:
    """A hashable key for ``typ``'s outer shape, ignoring its own parameters.

    Used to index :class:`ImplRegistry` by something cheaper than a full
    structural match: every struct declaration has its own shape,
    regardless of type arguments (``Pair[i32, i32]`` and ``Pair[i32,
    bool]`` share one), and every other type is grouped by its Python
    class (every pointer type shares one, regardless of pointee or
    mutability). The registry still verifies a real structural match (see
    :meth:`ImplRegistry.find_impl`) before treating a same-shape impl as
    applicable - this is a fast pre-filter, not the source of truth.

    :param typ: The type to compute a head shape for.
    :return: The head shape.
    """
    if isinstance(typ, typs.StructTyp):
        return typ.ast
    return type(typ)


def _typs_overlap(a: typs.Typ, b: typs.Typ) -> bool:
    """Whether two impl self types could both apply to some concrete type.

    Attempts a structural match in both directions (``a`` as the
    "declared" shape and ``b`` as the "actual" one, then vice versa),
    using the same machinery generic call resolution infers type
    arguments with (see :meth:`~leech.typs.Typ.infer_typ_args`). Neither
    direction alone suffices: if ``a`` is ``Pair[A, A]`` (from one impl's
    own type parameter) and ``b`` is a plain ``Pair[i32, i32]``, only
    ``a``-as-declared matches; if the roles were reversed the same pair of
    impls should still be reported as conflicting.

    :param a: One impl's self type.
    :param b: The other impl's self type.
    :return: Whether the two could overlap.
    """
    bindings: dict[typs.TypParamTyp, typs.Typ] = {}
    a.infer_typ_args(b, bindings)
    if a.substitute_typ_params(bindings) is b:
        return True
    bindings = {}
    b.infer_typ_args(a, bindings)
    return b.substitute_typ_params(bindings) is a


class ImplRegistry:
    """Every trait impl in the program, keyed for lookup by receiver type.

    Owned by :class:`~leech.ir_loader.ModLoader` - the only object that
    sees every module in a compilation - so a trait impl in one module is
    visible while checking another's, regardless of import direction.
    """

    _impls: Final[dict[tuple[Trait, Hashable], list[Impl]]]
    #: Every trait with at least one impl of a given head shape, indexing
    #: :attr:`_impls` so :meth:`find_impls_for_typ` doesn't have to scan
    #: every trait/shape pair ever registered in the program.
    _traits_by_shape: Final[dict[Hashable, list[Trait]]]

    def __init__(self) -> None:
        self._impls = {}
        self._traits_by_shape = {}

    def add_impl(self, impl: Impl) -> None:
        """Register ``impl``, rejecting it if it's incoherent or conflicts with one
        already registered.

        :param impl: The impl to register.
        :raises OrphanImplError: If neither ``impl``'s trait nor its self
            type is defined in ``impl``'s own module.
        :raises ConflictingImplsError: If ``impl``'s self type could
            overlap with an already-registered impl of the same trait.
        """
        impl.check_orphan_rule()
        shape = head_shape(impl.self_typ)
        key = (impl.trait, shape)
        impls = self._impls.get(key)
        if impls is None:
            impls = []
            self._impls[key] = impls
            self._traits_by_shape.setdefault(shape, []).append(impl.trait)
        for existing in impls:
            if _typs_overlap(impl.self_typ, existing.self_typ):
                raise ConflictingImplsError(
                    impl.trait.name, impl.self_typ.name, impl.span, existing.span
                )
        impls.append(impl)

    def find_impl(self, trait: Trait, typ: typs.Typ) -> Optional[Impl]:
        """Find the impl of ``trait`` that applies to ``typ``, if any.

        :param trait: The trait to find an implementation of.
        :param typ: The (concrete) type to find an implementation for.
        :return: The applicable impl, or ``None`` if none is registered.
        """
        for impl in self._impls.get((trait, head_shape(typ)), ()):
            bindings: dict[typs.TypParamTyp, typs.Typ] = {}
            impl.self_typ.infer_typ_args(typ, bindings)
            if impl.self_typ.substitute_typ_params(bindings) is typ:
                return impl
        return None

    def find_impls_for_typ(self, typ: typs.Typ) -> list[Impl]:
        """Find every trait impl (for any trait) that applies to ``typ``.

        Used to detect an ambiguous method call - the same method name
        provided by more than one applicable trait.

        :param typ: The (concrete) type to find implementations for.
        :return: The applicable impls, one per trait at most.
        """
        shape = head_shape(typ)
        result = []
        for trait in self._traits_by_shape.get(shape, ()):
            found = self.find_impl(trait, typ)
            if found is not None:
                result.append(found)
        return result


def disambiguate[T](
    matches: list[T], name: str, typ_name: str, span: Optional[SrcSpan]
) -> Optional[T]:
    """Resolve a list of same-named candidate methods to the one to use.

    Shared by every method-lookup site with more than one place a name
    could come from - :func:`lookup_member` (a concrete type's inherent
    member vs. its applicable trait impls) and
    :meth:`~leech.typcheck.TypCheck._resolve_bound_method` (an
    unsubstituted type parameter's declared bounds) - so "more than one
    candidate provides this name" is reported the same way everywhere.

    :param matches: Every candidate found, however that search was done.
    :param name: The member name being looked up.
    :param typ_name: The name of the type the lookup was against, for the
        diagnostic.
    :param span: Where to point an :class:`~leech.errors.AmbiguousMethodError`
        at, if one applies.
    :return: The sole match, or ``None`` if ``matches`` is empty.
    :raises AmbiguousMethodError: If ``matches`` has more than one entry.
    """
    if len(matches) > 1:
        raise AmbiguousMethodError(name, typ_name, span)
    return matches[0] if matches else None


def lookup_member(
    typ: typs.Typ,
    name: str,
    registry: ImplRegistry,
    span: Optional[SrcSpan],
) -> Optional[ir_module.Fn]:
    """Find ``typ``'s member (inherent or via a trait impl) called ``name``.

    The generalisation :attr:`~leech.typs.StructTyp.get_assoc_fn` used to be
    the only way to look up a callable member - now every type can have
    one, through a trait impl, not just a struct through its own
    inherent members. A free function rather than a method on
    :class:`~leech.typs.Typ` (as the type system's own docstrings once
    anticipated) specifically to avoid ``typs.py`` needing to import this
    module: it's deep in the dependency graph, and every caller here
    already has both a type and a registry (see
    :attr:`~leech.ir_env.Env.impl_registry`) close at hand.

    Only ever meaningful for a *concrete* type - a call through an
    unsubstituted type parameter resolves differently, against the
    parameter's own declared bounds rather than the registry (see
    :meth:`~leech.typcheck.TypCheck._resolve_callee`).

    :param typ: The type to look up a member on.
    :param name: The member's name.
    :param registry: The program-wide trait impl registry.
    :param span: Where to point an :class:`~leech.errors.AmbiguousMethodError`
        at, if one applies.
    :return: The member, or ``None`` if neither an inherent member nor any
        applicable trait impl provides one of that name.
    :raises AmbiguousMethodError: If more than one applicable trait impl
        provides a member of that name.
    """
    if isinstance(typ, typs.StructTyp):
        inherent = typ.get_assoc_fn(name)
        if inherent is not None:
            return inherent

    matches = [
        method
        for impl in registry.find_impls_for_typ(typ)
        if (method := impl.get_method(name)) is not None
    ]
    method = disambiguate(matches, name, typ.name, span)
    if method is None:
        return None

    # `find_impls_for_typ` structurally matches a generic impl's self
    # type against `typ` (e.g. `Pair[A, A]` against a concrete
    # `Pair[i32, i32]`), but the method it returns is still the one built
    # against the impl's own *abstract* self type - identical to `typ`
    # only when the matched impl is a non-generic one. Calling it as-is
    # would need the impl's own type parameters substituted throughout
    # its body first, which nothing here does yet - unlike a generic
    # *function* call, which monomorphizes through `Fn.instance`.
    assert method.recv_typ is typ, (
        "calling a method through a generic trait impl isn't supported yet"
    )
    return method
