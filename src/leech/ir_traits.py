# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Trait declarations and their implementations."""

from collections.abc import Collection, Hashable
from typing import Final, Optional

from leech import ast, errors, ir_env, ir_module, src, typs

# Keep this import module-qualified because typs may still be initializing.


class TraitMethod:
    """A trait method prototype and its type-resolution context."""

    ast: Final[ast.TraitFn]
    _trait: Final[Trait]

    def __init__(self, fn_ast: ast.TraitFn, trait: Trait) -> None:
        if fn_ast.receiver is None:
            raise errors.TraitMethodMissingReceiverError(fn_ast.name.name, fn_ast.span)
        self.ast = fn_ast
        self._trait = trait

    @property
    def name(self) -> str:
        """This method's name."""
        return self.ast.name.name

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this method's prototype."""
        return self.ast.span

    def fn_typ_for_self(self, self_typ: typs.Typ) -> typs.FnTyp:
        """Return this method's signature with ``self_typ`` as its receiver pointee."""
        assert self.ast.receiver is not None
        recv_typ = typs.PtrTyp.get_or_create(
            self_typ, typs.Mutability.from_ast(self.ast.receiver.mut)
        )
        param_typs = [recv_typ] + [
            typs.Typ.from_ast(param_ast.typ, self._trait._env) for param_ast in self.ast.params
        ]
        ret_typ = (
            typs.VOID
            if self.ast.ret_typ is None
            else typs.Typ.from_ast(self.ast.ret_typ, self._trait._env)
        )
        return typs.FnTyp.get_or_create(ret_typ, tuple(param_typs))


class Trait:
    """A named collection of method prototypes and their resolution scope."""

    ast: Final[ast.TraitDefn]
    _env: Final[ir_env.Env]
    mod_name: Final[str]
    _methods: Final[dict[str, TraitMethod]]

    def __init__(self, trait_ast: ast.TraitDefn, e: ir_env.Env, mod_name: str) -> None:
        self.ast = trait_ast
        self.mod_name = mod_name
        self._env = e.new_child()
        for typ_param in typs.typ_params_from_ast(trait_ast, trait_ast.generic_params):
            self._env.add_container(typ_param.name, typ_param)
        self._methods = {}
        for method_ast in trait_ast.methods:
            method = TraitMethod(method_ast, self)
            existing = self._methods.get(method.name)
            if existing is not None:
                raise errors.DuplicateItemDefnError(
                    "trait method", method.name, method.span, existing.span
                )
            self._methods[method.name] = method

    @property
    def name(self) -> str:
        """This trait's name."""
        return self.ast.ident.name

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this trait's declaration."""
        return self.ast.span

    def get_method(self, name: str) -> Optional[TraitMethod]:
        """Return the method prototype called ``name``, if present."""
        return self._methods.get(name)

    @property
    def methods(self) -> Collection[TraitMethod]:
        """This trait's method prototypes, in declaration order."""
        return self._methods.values()


def _is_local(item: Trait | typs.Typ, mod_name: str) -> bool:
    """Return whether a nominal trait or struct belongs to ``mod_name``."""
    if isinstance(item, (Trait, typs.StructTyp)):
        return item.mod_name == mod_name
    return False


class Impl:
    """A trait implementation with methods resolved under its ``Self`` binding."""

    ast: Final[ast.ImplDefn]
    _trait: Final[Trait]
    _self_typ: Final[typs.Typ]
    env: Final[ir_env.Env]
    _mod_name: Final[str]
    _methods: Final[dict[str, ir_module.Fn]]

    def __init__(
        self,
        impl_ast: ast.ImplDefn,
        trait: Trait,
        self_typ: typs.Typ,
        e: ir_env.Env,
        mod_name: str,
    ) -> None:
        self.ast = impl_ast
        self._trait = trait
        self._self_typ = self_typ
        self._mod_name = mod_name
        self.env = e.new_child()
        self.env.add_container("Self", self_typ)
        self._methods = {}

    @property
    def name(self) -> str:
        """This impl's diagnostic name, e.g. ``<i32 as Show>``."""
        return f"<{self._self_typ.name} as {self._trait.name}>"

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this impl block."""
        return self.ast.span

    def check_orphan_rule(self) -> None:
        """Raise unless this impl's trait or self type is defined in its own module.

        Leech's orphan rule (similar to Rust's): it keeps any two modules
        from being able to write conflicting impls of the same trait for
        the same type, since the self type isn't restricted to a local
        type the way an inherent impl's target is.

        :raises OrphanImplError: If neither is local.
        """
        if not _is_local(self._trait, self._mod_name) and not _is_local(
            self._self_typ, self._mod_name
        ):
            raise errors.OrphanImplError(self._trait.name, self._self_typ.name, self.span)

    def add_method(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this impl's methods.

        :param fn: The method, already constructed with :attr:`env` as its
            enclosing scope and this impl's self type as its receiver type.
        :raises ExtraMethodInImplError: If this impl's trait declares no
            method named ``fn.name``.
        :raises DuplicateItemDefnError: If this impl already defines a
            method of that name.
        :raises TraitMethodSignatureMismatchError: If ``fn``'s signature
            doesn't match the one this impl's trait declares for it.
        """
        trait_method = self._trait.get_method(fn.name)
        if trait_method is None:
            raise errors.ExtraMethodInImplError(self._trait.name, fn.name, fn.span)
        existing = self._methods.get(fn.name)
        if existing is not None:
            raise errors.DuplicateItemDefnError("method", fn.name, fn.span, existing.span)
        expected_typ = trait_method.fn_typ_for_self(self._self_typ)
        if fn.fn_typ is not expected_typ:
            raise errors.TraitMethodSignatureMismatchError(
                self._trait.name, fn.name, fn.fn_typ.name, expected_typ.name, fn.span
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

        :raises TraitMethodNotImplementedError: If this impl's trait
            declares a method this impl never defined.
        """
        for trait_method in self._trait.methods:
            if trait_method.name not in self._methods:
                raise errors.TraitMethodNotImplementedError(
                    self._trait.name, trait_method.name, self._self_typ.name, self.span
                )


def _head_shape(typ: typs.Typ) -> Hashable:
    """A hashable key for ``typ``'s outer shape, ignoring its own parameters.

    Struct instances share their declaration's shape; other types use
    their Python class. This is only a pre-filter for structural matching.

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
    """Program-wide trait implementations keyed by receiver type.

    :invariant: for every t in _traits_by_shape[s], (t, s) in _impls [ctor]
    """

    _impls: Final[dict[tuple[Trait, Hashable], list[Impl]]]
    #: Every trait with at least one impl of a given head shape, indexing
    #: :attr:`_impls` so :meth:`_find_impls_for_typ` doesn't have to scan
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
        shape = _head_shape(impl._self_typ)
        key = (impl._trait, shape)
        impls = self._impls.get(key)
        if impls is None:
            impls = []
            self._impls[key] = impls
            self._traits_by_shape.setdefault(shape, []).append(impl._trait)
        for existing in impls:
            if _typs_overlap(impl._self_typ, existing._self_typ):
                raise errors.ConflictingImplsError(
                    impl._trait.name, impl._self_typ.name, impl.span, existing.span
                )
        impls.append(impl)

    def find_impl(self, trait: Trait, typ: typs.Typ) -> Optional[Impl]:
        """Find the impl of ``trait`` that applies to ``typ``, if any.

        :param trait: The trait to find an implementation of.
        :param typ: The (concrete) type to find an implementation for.
        :return: The applicable impl, or ``None`` if none is registered.
        """
        for impl in self._impls.get((trait, _head_shape(typ)), ()):
            bindings: dict[typs.TypParamTyp, typs.Typ] = {}
            impl._self_typ.infer_typ_args(typ, bindings)
            if impl._self_typ.substitute_typ_params(bindings) is typ:
                return impl
        return None

    def _find_impls_for_typ(self, typ: typs.Typ) -> list[Impl]:
        """Find every trait impl that applies to ``typ``.

        :param typ: The (concrete) type to find implementations for.
        :return: The applicable impls, one per trait at most.
        """
        shape = _head_shape(typ)
        result = []
        for trait in self._traits_by_shape.get(shape, ()):
            found = self.find_impl(trait, typ)
            if found is not None:
                result.append(found)
        return result


def disambiguate[T](
    matches: list[T], name: str, typ_name: str, span: Optional[src.SrcSpan]
) -> Optional[T]:
    """Return the sole candidate, rejecting ambiguous matches.

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
        raise errors.AmbiguousMethodError(name, typ_name, span)
    return matches[0] if matches else None


def lookup_member(
    typ: typs.Typ,
    name: str,
    registry: ImplRegistry,
    span: Optional[src.SrcSpan],
) -> Optional[ir_module.Fn | ir_module.FnInstance]:
    """Find ``typ``'s member (inherent or via a trait impl) called ``name``.

    Only ever meaningful for a *concrete* type - a call through an
    unsubstituted type parameter resolves differently, against the
    parameter's own declared bounds rather than the registry.

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
        for impl in registry._find_impls_for_typ(typ)
        if (method := impl.get_method(name)) is not None
    ]
    method = disambiguate(matches, name, typ.name, span)
    if method is None:
        return None

    # `_find_impls_for_typ` structurally matches a generic impl's self
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
