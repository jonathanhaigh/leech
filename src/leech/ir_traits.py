# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Trait declarations and their implementations."""

import functools
from collections.abc import Collection, Hashable, Mapping
from typing import Final, Optional

from leech import ast, errors, ir_env, ir_module, reserved, src, typs

# Keep this import module-qualified because typs may still be initializing.


class TraitMethod:
    """A trait method prototype and its type-resolution context."""

    ast: Final[ast.TraitFn]
    _trait: Final[Trait]

    def __init__(self, fn_ast: ast.TraitFn, trait: Trait) -> None:
        if fn_ast.receiver is None:
            raise errors.TraitMethodMissingReceiverError(fn_ast.name.name, fn_ast.span)
        reserved.check_fn_params(fn_ast)
        self.ast = fn_ast
        self._trait = trait

    @property
    def name(self) -> str:
        """This method's name."""
        return self.ast.name.name

    @property
    def trait(self) -> Trait:
        """The trait that declares this method."""
        return self._trait

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this method's prototype."""
        return self.ast.span

    def fn_typ_for_self(self, self_typ: typs.Typ) -> typs.FnTyp:
        """Return this method's signature with ``self_typ`` as its receiver pointee.

        ``self_typ`` is also what ``Self`` names in the declared parameter and
        return types.
        """
        assert self.ast.receiver is not None
        recv_typ = typs.PtrTyp.get_or_create(
            self_typ, typs.Mutability.from_ast(self.ast.receiver.mut)
        )
        env = self._trait._env.new_child()
        env.add_container(reserved.SELF_TYP_NAME, self_typ)
        param_typs = [recv_typ] + [
            typs.Typ.from_ast(param_ast.typ, env) for param_ast in self.ast.params
        ]
        ret_typ = (
            typs.VOID if self.ast.ret_typ is None else typs.Typ.from_ast(self.ast.ret_typ, env)
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
        for typ_param in self.typ_params:
            self._env.add_container(typ_param.name, typ_param)
        self._methods = {}
        for method_ast in trait_ast.methods:
            method = TraitMethod(method_ast, self)
            if reserved.is_reserved(method.name):
                raise errors.ReservedNameError(method.name, method_ast.name.span)
            existing = self._methods.get(method.name)
            if existing is not None:
                raise errors.DuplicateItemDefnError(
                    "trait method", method.name, method.span, existing.span
                )
            self._methods[method.name] = method

    @functools.cached_property
    def typ_params(self) -> tuple[typs.TypParamTyp, ...]:
        """This trait's own interned generic parameters, in declaration order."""
        return typs.typ_params_from_ast(self.ast, self.ast.generic_params, self._env)

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
    """An ``impl`` block's functions, resolved under its ``Self`` binding.

    :param trait: The trait implemented, or ``None`` for an inherent
        block.
    """

    ast: Final[ast.ImplDefn]
    trait: Final[Optional[Trait]]
    self_typ: Final[typs.Typ]
    env: Final[ir_env.Env]
    _mod_name: Final[str]
    _trait_methods: Final[dict[TraitMethod, ir_module.Fn]]
    _fns: Final[dict[str, ir_module.Fn]]

    def __init__(
        self,
        impl_ast: ast.ImplDefn,
        trait: Optional[Trait],
        self_typ: typs.Typ,
        e: ir_env.Env,
        mod_name: str,
    ) -> None:
        self.ast = impl_ast
        self.trait = trait
        self.self_typ = self_typ
        self._mod_name = mod_name
        self.env = e.new_child()
        self.env.add_container(reserved.SELF_TYP_NAME, self_typ)
        self._trait_methods = {}
        self._fns = {}

    @property
    def name(self) -> str:
        """This impl's diagnostic name, e.g. ``<i32 as Show>`` or ``Foo``."""
        if self.trait is None:
            return self.self_typ.name
        return f"<{self.self_typ.name} as {self.trait.name}>"

    @property
    def span(self) -> src.SrcSpan:
        """The source location of this impl block."""
        return self.ast.span

    @property
    def fns(self) -> Collection[ir_module.Fn]:
        """Every function declared in this block, in declaration order.

        Read live, so a function registered after a caller takes this
        still counts.
        """
        return self._fns.values()

    def get_fn(self, name: str) -> Optional[ir_module.Fn]:
        """Return the function called ``name``, if this block declares one."""
        return self._fns.get(name)

    def check_orphan_rule(self) -> None:
        """Raise unless this impl's trait or self type is defined in its own module.

        Leech's orphan rule (similar to Rust's): it keeps any two modules
        from being able to write conflicting impls of the same trait for
        the same type, since the self type isn't restricted to a local
        type the way an inherent impl's target is. An inherent block is
        exempt - its own target is required to be local.

        :raises OrphanImplError: If neither is local.
        """
        if self.trait is None:
            return
        if not _is_local(self.trait, self._mod_name) and not _is_local(
            self.self_typ, self._mod_name
        ):
            raise errors.OrphanImplError(self.trait.name, self.self_typ.name, self.span)

    def add_fn(self, fn: ir_module.Fn) -> None:
        """Register ``fn`` as one of this block's functions.

        For a trait impl this also checks ``fn`` against the prototype
        the trait declares for it.

        :raises ExtraMethodInImplError: If this impl's trait declares no
            method named ``fn.name``.
        :raises DuplicateItemDefnError: If this impl already defines a
            function of that name.
        :raises TraitMethodSignatureMismatchError: If ``fn``'s signature
            doesn't match the one this impl's trait declares for it.
        """
        trait = self.trait
        trait_method = None
        if trait is not None:
            trait_method = trait.get_method(fn.name)
            if trait_method is None:
                raise errors.ExtraMethodInImplError(trait.name, fn.name, fn.span)
        existing = self._fns.get(fn.name)
        if existing is not None:
            kind = "associated function" if trait is None else "method"
            raise errors.DuplicateItemDefnError(kind, fn.name, fn.span, existing.span)
        if trait_method is not None:
            assert trait is not None
            expected_typ = trait_method.fn_typ_for_self(self.self_typ)
            if fn.fn_typ is not expected_typ:
                raise errors.TraitMethodSignatureMismatchError(
                    trait.name, fn.name, fn.fn_typ.name, expected_typ.name, fn.span
                )
        self._fns[fn.name] = fn
        if trait_method is not None:
            self._trait_methods[trait_method] = fn

    def get_trait_method(self, trait_method: TraitMethod) -> Optional[ir_module.Fn]:
        """Find this impl's implementation of ``trait_method``."""
        return self._trait_methods.get(trait_method)

    def check_complete(self) -> None:
        """Raise if this impl is missing any of its trait's methods.

        An inherent block has no obligations to check.

        :raises TraitMethodNotImplementedError: If this impl's trait
            declares a method this impl never defined.
        """
        if self.trait is None:
            return
        for trait_method in self.trait.methods:
            if trait_method not in self._trait_methods:
                raise errors.TraitMethodNotImplementedError(
                    self.trait.name, trait_method.name, self.self_typ.name, self.span
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
    return typs.match_typ_args(a, b) is not None or typs.match_typ_args(b, a) is not None


class ImplRegistry:
    """Program-wide implementations, with trait implementations indexed by receiver type.

    :invariant: for every t in _traits_by_shape[s], (t, s) in _impls [ctor]
    """

    _impls: Final[dict[tuple[Optional[Trait], Hashable], list[Impl]]]
    #: Every trait with at least one impl of a given head shape, indexing
    #: :attr:`_impls` so :meth:`_find_trait_impls_for_typ` doesn't have to scan
    #: every trait/shape pair ever registered in the program.
    _traits_by_shape: Final[dict[Hashable, list[Trait]]]
    #: How many impl selections are checking their own bounds further up
    #: the stack, bounding the mutual recursion between selection and
    #: bound checking. See :meth:`_impl_bounds_hold`.
    _selection_depth: int

    def __init__(self) -> None:
        self._impls = {}
        self._traits_by_shape = {}
        self._selection_depth = 0

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
        shape = _head_shape(impl.self_typ)
        key = (impl.trait, shape)
        impls = self._impls.get(key)
        if impls is None:
            impls = []
            self._impls[key] = impls
            if impl.trait is not None:
                self._traits_by_shape.setdefault(shape, []).append(impl.trait)
        if impl.trait is not None:
            for existing_trait_impl in impls:
                if _typs_overlap(impl.self_typ, existing_trait_impl.self_typ):
                    raise errors.ConflictingImplsError(
                        impl.trait.name,
                        impl.self_typ.name,
                        impl.span,
                        existing_trait_impl.span,
                    )
        impls.append(impl)

    def find_inherent_impls(self, typ: typs.Typ) -> list[Impl]:
        """Find every inherent ``impl`` block that applies to ``typ``.

        Unlike :meth:`find_trait_impl`, more than one can apply: inherent
        blocks are distinguished by the members they declare, not by being
        mutually exclusive.

        :param typ: The type to find inherent implementations for.
        :return: The applicable inherent impl blocks.
        """
        inherent_impls = []
        for inherent_impl in self._impls.get((None, _head_shape(typ)), ()):
            bindings = typs.match_typ_args(inherent_impl.self_typ, typ)
            if bindings is not None and self._impl_bounds_hold(inherent_impl, bindings):
                inherent_impls.append(inherent_impl)
        return inherent_impls

    def find_trait_impl(self, trait: Trait, typ: typs.Typ) -> Optional[Impl]:
        """Find the trait impl of ``trait`` that applies to ``typ``, if any.

        :param trait: The trait to find an implementation of.
        :param typ: The type to find an implementation for.
        :return: The applicable trait impl, or ``None`` if none is registered.
        """
        for trait_impl in self._impls.get((trait, _head_shape(typ)), ()):
            bindings = typs.match_typ_args(trait_impl.self_typ, typ)
            if bindings is not None and self._impl_bounds_hold(trait_impl, bindings):
                return trait_impl
        return None

    def implements(self, trait: Trait, typ: typs.Typ) -> bool:
        """Whether ``typ`` is known to implement ``trait``.

        Two things can establish that. A registered trait impl proves it for a
        concrete type. Inside a generic definition there is no concrete
        type and nothing is registered against a type parameter, so what
        stands in is an assumption in scope: the parameter's own declared
        bounds. ``fn f[U: Show]`` may call ``Show``'s methods on ``U``,
        and ``fn f[U]`` may not - the obligation moves to wherever ``f``
        is instantiated, which is checked against a concrete type there.

        :param trait: The trait to prove an implementation of.
        :param typ: The type to prove it for.
        :return: Whether an impl or a bound in scope establishes it.
        """
        if isinstance(typ, typs.TypParamTyp) and typ.declares_bound(trait):
            return True
        return self.find_trait_impl(trait, typ) is not None

    def _impl_bounds_hold(self, impl: Impl, bindings: Mapping[typs.TypParamTyp, typs.Typ]) -> bool:
        """Whether ``impl``'s own bounds hold for the arguments matching it to ``typ``.

        An impl whose bounds don't hold doesn't apply, rather than making
        the program ill-formed: ``Box[bool]`` simply doesn't implement
        ``Show`` when only ``impl[T: Show] Show for Box[T]`` provides it.
        Two impls distinguished only by their bounds still conflict,
        though, so this gates selection and not :meth:`add_impl`'s
        coherence checks.

        This holds for an abstract type as much as a concrete one, by way
        of :meth:`implements`: matching the impl above against its own
        ``Box[T]`` binds ``T`` to itself, and the impl's ``T: Show`` is
        exactly the assumption that discharges it.

        :raises NotImplementedError: If checking a bound needs more than
            :data:`~leech.typs.MAX_BOUND_DEPTH` nested selections.
        """
        if self._selection_depth >= typs.MAX_BOUND_DEPTH:
            raise NotImplementedError(
                f"exceeded {typs.MAX_BOUND_DEPTH} levels of impl selection at {impl.name};"
                " recursive trait bounds aren't supported yet"
            )
        self._selection_depth += 1
        try:
            return typs.unsatisfied_bound(bindings, impl.env) is None
        finally:
            self._selection_depth -= 1

    def _find_trait_impls_for_typ(self, typ: typs.Typ) -> list[Impl]:
        """Find every trait impl that applies to ``typ``.

        :param typ: The (concrete) type to find implementations for.
        :return: The applicable trait impls, one per trait at most.
        """
        shape = _head_shape(typ)
        trait_impls = []
        for trait in self._traits_by_shape.get(shape, ()):
            trait_impl = self.find_trait_impl(trait, typ)
            if trait_impl is not None:
                trait_impls.append(trait_impl)
        return trait_impls


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
        inherent_fn = typ.get_assoc_fn(name)
        if inherent_fn is not None:
            return inherent_fn

    matches = []
    for trait_impl in registry._find_trait_impls_for_typ(typ):
        trait = trait_impl.trait
        assert trait is not None
        trait_method = trait.get_method(name)
        if trait_method is None:
            continue
        method = trait_impl.get_trait_method(trait_method)
        if method is not None:
            matches.append(method)
    method = disambiguate(matches, name, typ.name, span)
    if method is None:
        return None

    # `_find_trait_impls_for_typ` structurally matches a generic impl's self
    # type against `typ` (e.g. `Pair[A, A]` against a concrete
    # `Pair[i32, i32]`), but the method it returns is still the one built
    # against the impl's own *abstract* self type - identical to `typ`
    # only when the matched impl is a non-generic one. Otherwise it needs
    # the impl's own type parameters substituted throughout its body,
    # which is what instantiating it against `typ` does.
    return method.for_recv_typ(typ)
