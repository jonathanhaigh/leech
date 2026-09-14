# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, errors, ir_env, ir_module, parse, signage, typs
from leech import src as leech_src


def _get_union_typ(mod, name: str) -> typs.UnionTyp:
    """Get the non-generic union type ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.UnionTyp)


def _get_union_template(mod, name: str) -> typs.UnionTypTemplate:
    """Get the generic union template ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.UnionTypTemplate)


def _build_with_imports(tmp_path, main_src: str, **modules: str):
    """Build ``main_src`` into IR alongside sibling modules it can import."""
    for name, mod_src in modules.items():
        util.write_whole_file(tmp_path / f"{name}.leech", mod_src)
    return util.build_ir_mod(tmp_path, main_src)


def _imported_union_template(mod, mod_name: str, name: str) -> typs.UnionTypTemplate:
    imported = mod.get_item(ir_env.Env.Namespace.CONTAINERS, mod_name)
    assert imported is not None
    return _get_union_template(asserts.checked_cast(imported.value, ir_module.Mod), name)


def _path_of(tmp_path, expr_src: str) -> ast.Path:
    """Build the path an expression spells, as if written in ``main.leech``."""
    file = leech_src.SrcFile(tmp_path / "main.leech")
    expr = ast.Expr.from_tree(file, parse.build_parser("expr").parse(expr_src))
    return asserts.checked_cast(expr, ast.VarExpr).path


def _resolve_variant(mod, tmp_path, expr_src: str) -> typs.UnionVariantRef:
    target = mod.env.resolve_var(_path_of(tmp_path, expr_src))
    return asserts.checked_cast(target, typs.UnionVariantRef)


_UNIONS = "union Option[T] { None, Some(T) }\nunion Res[T, E] { Ok(T), Err(E) }\n"


def _check_body(tmp_path, body: str, unions: str = _UNIONS):
    """Type-check a main body over the shared union declarations, without lowering."""
    mod = util.build_ir_mod(tmp_path, f"{unions}pub fn main() i32 {{\n{body}\nreturn 0;\n}}")
    item = mod.get_item(ir_env.Env.Namespace.VARS, "main")
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results


def _check_match(tmp_path, body: str, unions: str = _UNIONS):
    """Type-check a function matching on an ``Option[i32]``, without lowering."""
    mod = util.build_ir_mod(tmp_path, f"{unions}pub fn f(o: Option[i32]) i32 {{\n{body}\n}}")
    item = mod.get_item(ir_env.Env.Namespace.VARS, "f")
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results


def _constructions(results) -> list[tuple[str, str, int]]:
    """Every recorded construction as (path, union name, variant index)."""
    return [
        (node.path.str(), typ.name, index)
        for node, (typ, index) in results._variant_constructions.items()
    ]


def _variant_count_src(count: int) -> str:
    variants = ", ".join(f"V{i}" for i in range(count))
    return f"union Wide {{ {variants} }}"


def test_generic_and_non_generic_union_module_items_have_distinct_types(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union Option[T] { None, Some(T) }\nunion Flag { On, Off }",
    )

    template = _get_union_template(mod, "Option")
    flag = _get_union_typ(mod, "Flag")

    assert not isinstance(template, typs.Typ)
    assert isinstance(flag, typs.Typ)
    assert flag.comptime_args == ()
    assert flag.template.name == "Flag"


def test_union_variants_carry_declaration_order_and_arity(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union Shape { Point, Line(i32, i32), Circle(i32) }",
    )
    shape = _get_union_typ(mod, "Shape")

    assert [variant.name for variant in shape.variants] == ["Point", "Line", "Circle"]
    assert [variant.index for variant in shape.variants] == [0, 1, 2]
    assert [variant.tag for variant in shape.variants] == [0, 1, 2]
    assert [variant.arity for variant in shape.variants] == [0, 2, 1]
    assert shape.variant_at(1).name == "Line"
    assert shape.variant_at(1).payload_typs == (typs.I32, typs.I32)
    assert shape.variant_at(0).payload_typs == ()
    # An instance's variant is a view of the declaration's, not a copy.
    assert shape.variant_at(1).template is shape.template.variants["Line"]


def test_union_variant_templates_are_instance_independent(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    assert list(template.variants) == ["None", "Some"]
    assert template.variants["None"].index == 0
    assert template.variants["None"].arity == 0
    assert template.variants["Some"].index == 1
    assert template.variants["Some"].arity == 1


def test_union_payload_typs_resolve_against_comptime_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    instance = template.instantiate((typs.I32,))
    assert instance.variant_at(1).payload_typs == (typs.I32,)
    assert instance.variant_at(0).payload_typs == ()


def test_union_instances_are_cached_per_argument_list(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    assert template.instantiate((typs.I32,)) is template.instantiate((typs.I32,))
    assert template.instantiate((typs.I32,)) is not template.instantiate((typs.U8,))


def test_union_names_render_comptime_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }\nunion Flag { On }")
    template = _get_union_template(mod, "Option")

    assert template.instantiate((typs.I32,)).name == "Option[i32]"
    assert _get_union_typ(mod, "Flag").name == "Flag"


def test_union_qualified_name_qualifies_the_union_and_its_arguments(tmp_path):
    # Qualifying the arguments too is what keeps same-named unions from
    # different modules apart: Option[a::Foo] and Option[b::Foo] must not
    # arrive at one symbol.
    mod = util.build_ir_mod(
        tmp_path,
        "union Option[T] { None, Some(T) }\nstruct Foo {}\nunion Flag { On }",
    )
    foo = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Foo")
    assert foo is not None
    instance = _get_union_template(mod, "Option").instantiate(
        (asserts.checked_cast(foo.value, typs.StructTyp),)
    )

    assert instance.name == "Option[Foo]"
    assert instance.qualified_name == "main::Option[main::Foo]"
    assert _get_union_typ(mod, "Flag").qualified_name == "main::Flag"


def test_union_qualified_name_distinguishes_same_named_unions(tmp_path):
    mod = _build_with_imports(
        tmp_path,
        "import a;\nimport b;\npub fn main() i32 { return 0; }",
        a="pub union Payload[T] { P(T) }",
        b="pub union Payload[T] { P(T) }",
    )
    from_a = _imported_union_template(mod, "a", "Payload").instantiate((typs.I32,))
    from_b = _imported_union_template(mod, "b", "Payload").instantiate((typs.I32,))

    assert from_a.name == from_b.name == "Payload[i32]"
    assert from_a.qualified_name == "a::Payload[i32]"
    assert from_b.qualified_name == "b::Payload[i32]"
    assert from_a is not from_b


def test_union_qualified_name_qualifies_its_arguments(tmp_path):
    # Qualifying the arguments too is what keeps one union instantiated
    # over two same-named types from different modules apart.
    mod = _build_with_imports(
        tmp_path,
        "import a;\nimport b;\npub fn main() i32 { return 0; }",
        a="pub union Payload[T] { P(T) }\npub struct Foo {}",
        b="pub struct Foo {}",
    )
    template = _imported_union_template(mod, "a", "Payload")
    foos = []
    for mod_name in ("a", "b"):
        imported = mod.get_item(ir_env.Env.Namespace.CONTAINERS, mod_name)
        assert imported is not None
        item = asserts.checked_cast(imported.value, ir_module.Mod).get_item(
            ir_env.Env.Namespace.CONTAINERS, "Foo"
        )
        assert item is not None
        foos.append(asserts.checked_cast(item.value, typs.StructTyp))

    over_a, over_b = (template.instantiate((foo,)) for foo in foos)
    assert over_a.name == over_b.name == "Payload[Foo]"
    assert over_a.qualified_name == "a::Payload[a::Foo]"
    assert over_b.qualified_name == "a::Payload[b::Foo]"


def test_union_variant_template_payload_typs_use_the_declarations_own_params(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union U[T, value N: usize] { A, B(array[T, N]) }")
    template = _get_union_template(mod, "U")

    (payload,) = template.variants["B"].payload_typs
    array_typ = asserts.checked_cast(payload, typs.ArrayTyp)
    typ_param, value_param = template.comptime_params
    assert array_typ.element_typ is typ_param
    assert array_typ.length is value_param
    assert template.variants["A"].payload_typs == ()


def test_instantiating_a_union_records_a_request(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }\nunion Flag { On }")
    template = _get_union_template(mod, "Option")
    ctx = mod.env.ctx

    instance = template.instantiate((typs.I32,))
    assert instance in ctx.requested_union_instances()
    # The module instance is reached another way, so it is never a request.
    assert _get_union_typ(mod, "Flag") not in ctx.requested_union_instances()


@pytest.mark.parametrize(
    "count,width",
    [(0, 8), (1, 8), (2, 8), (256, 8), (257, 16)],
)
def test_union_tag_typ_is_the_narrowest_unsigned_fit(tmp_path, count, width):
    mod = util.build_ir_mod(tmp_path, _variant_count_src(count))
    wide = _get_union_typ(mod, "Wide")

    assert wide.tag_typ == typs.IntTyp.get_or_create(width, signage.UNSIGNED)


def test_duplicate_variant_in_union_defn_error(tmp_path):
    with pytest.raises(errors.DuplicateVariantInUnionDefnError):
        util.build_ir_mod(tmp_path, "union U { A, A }")


def test_reserved_variant_name_error(tmp_path):
    with pytest.raises(errors.ReservedNameError):
        util.build_ir_mod(tmp_path, "union U { match }")


def test_union_by_value_self_cycle_is_rejected(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Bad { A, B(Bad) }")
    with pytest.raises(errors.InfiniteSizeTypError):
        _ = _get_union_typ(mod, "Bad").variants


def test_union_through_pointer_is_finite(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union List { Nil, Cons(i32, *List) }")
    assert len(_get_union_typ(mod, "List").variants) == 2


def test_union_through_array_element_is_rejected(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Bad { A, B(array[Bad, 1]) }")
    with pytest.raises(errors.InfiniteSizeTypError):
        _ = _get_union_typ(mod, "Bad").variants


def test_union_and_struct_mutual_by_value_cycle_is_rejected(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union U { A, B(S) }\nstruct S { u: U }",
    )
    with pytest.raises(errors.InfiniteSizeTypError):
        _ = _get_union_typ(mod, "U").variants


def test_growing_generic_union_declaration_cycle_is_rejected(tmp_path):
    # Every payload adds an array layer, so exact type identity never
    # repeats; the declaration still recurs with a growing argument.
    mod = util.build_ir_mod(tmp_path, "union L[T] { Nil, Cons(L[array[T, 1]]) }")
    with pytest.raises(errors.InfiniteSizeTypError) as exc_info:
        _get_union_template(mod, "L").validate_declaration()

    assert exc_info.value.message.message == 'Union "L" has infinite size'
    assert len(exc_info.value.extra) == 1
    assert exc_info.value.extra[0].message == (
        'Payload 0 of variant "Cons" of union "L" contains "L[array[T, 1]]" by value'
    )


def test_growing_generic_union_cycle_through_a_struct_is_rejected(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union U[T] { A, B(S[array[T, 1]]) }\nstruct S[T] { u: U[T] }",
    )
    with pytest.raises(errors.InfiniteSizeTypError):
        _get_union_template(mod, "U").validate_declaration()


def test_cycle_growing_through_a_union_comptime_argument_is_rejected(tmp_path):
    # The growth is the argument becoming a union, so detecting it needs
    # contains_typ to look inside a UnionTyp's own comptime arguments.
    # Without that the walk never recognises the repeat and recurses until
    # it exhausts the stack.
    mod = util.build_ir_mod(tmp_path, "union L[T] { Nil, Cons(L[L[T]]) }")
    with pytest.raises(errors.InfiniteSizeTypError):
        _get_union_template(mod, "L").validate_declaration()


def test_cycle_growing_through_a_union_argument_via_a_struct_is_rejected(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union U[T] { A, B(S[T]) }\nstruct S[T] { u: U[U[T]] }",
    )
    with pytest.raises(errors.InfiniteSizeTypError):
        _get_union_template(mod, "U").validate_declaration()


def test_generic_union_through_pointer_argument_is_finite(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union L[T] { Nil, Cons(T, *L[T]) }")
    _get_union_template(mod, "L").validate_declaration()


def test_variant_resolves_against_an_unapplied_template(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    ref = _resolve_variant(mod, tmp_path, "Option::Some")
    assert ref.owner is template
    assert ref.variant is template.variants["Some"]
    assert ref.name == "Some"


def test_variant_resolves_against_an_applied_instance(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    ref = _resolve_variant(mod, tmp_path, "Option[i32]::Some")
    owner = asserts.checked_cast(ref.owner, typs.UnionTyp)
    assert owner is template.instantiate((typs.I32,))
    assert owner.comptime_args == (typs.I32,)
    assert ref.variant is template.variants["Some"]


def test_variant_of_a_non_generic_union_resolves_against_its_instance(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Flag { On, Off }")
    flag = _get_union_typ(mod, "Flag")

    ref = _resolve_variant(mod, tmp_path, "Flag::Off")
    assert ref.owner is flag
    assert ref.variant.index == 1


def test_variant_resolves_through_a_module_path(tmp_path):
    mod = _build_with_imports(
        tmp_path,
        "import a;\npub fn main() i32 { return 0; }",
        a="pub union Option[T] { None, Some(T) }",
    )
    template = _imported_union_template(mod, "a", "Option")

    assert _resolve_variant(mod, tmp_path, "a::Option::Some").owner is template
    applied = _resolve_variant(mod, tmp_path, "a::Option[i32]::Some")
    assert asserts.checked_cast(applied.owner, typs.UnionTyp).comptime_args == (typs.I32,)


def test_unknown_variant_name_is_not_found(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    with pytest.raises(errors.ItemNotFoundError):
        _resolve_variant(mod, tmp_path, "Option::Nope")


def test_private_unions_variant_is_inaccessible(tmp_path):
    mod = _build_with_imports(
        tmp_path,
        "import a;\npub fn main() i32 { return 0; }",
        a="union Option[T] { None, Some(T) }",
    )
    with pytest.raises(errors.PrivateItemAccessError):
        _resolve_variant(mod, tmp_path, "a::Option::Some")


@pytest.mark.parametrize("expr", ["Option::Some[i32]", "Option[i32]::Some[i32]"])
def test_comptime_args_on_a_variant_segment_are_rejected(tmp_path, expr):
    # The arguments belong to the union: `Option[i32]::Some` is how to
    # say what these are trying to say.
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError):
        _resolve_variant(mod, tmp_path, expr)


def test_a_variant_cannot_qualify_a_further_path_segment(tmp_path):
    # A mid-path segment resolves in the container namespace, where a
    # variant is invisible, so this fails the same way `Color::Red::x`
    # does for an enum rather than reaching a variant-specific check.
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    with pytest.raises(errors.ItemNotFoundError):
        _resolve_variant(mod, tmp_path, "Option::Some::x")


def test_a_variant_is_not_reachable_in_the_container_namespace(tmp_path):
    mod = util.build_ir_mod(tmp_path, "union Option[T] { None, Some(T) }")
    with pytest.raises(errors.ItemNotFoundError):
        mod.env.resolve_typ(_path_of(tmp_path, "Option::Some"))


def test_variant_comptime_args_come_from_the_payload_argument(tmp_path):
    results = _check_body(tmp_path, "let x = Option::Some(1i32);")
    assert _constructions(results) == [("Option::Some", "Option[i32]", 1)]


def test_variant_comptime_args_can_be_explicit(tmp_path):
    results = _check_body(tmp_path, "let x = Option[bool]::Some(true);")
    assert _constructions(results) == [("Option[bool]::Some", "Option[bool]", 1)]


def test_explicit_comptime_args_beat_the_payload_argument(tmp_path):
    # The payload would infer Option[i32]; the path already said otherwise,
    # so the argument is checked against the spelled-out instance instead.
    with pytest.raises(errors.InvalidArgTypError):
        _check_body(tmp_path, "let x = Option[bool]::Some(1i32);")


@pytest.mark.parametrize(
    "body",
    [
        "let x: Option[i32] = Option::None;",
        "let mut x: Option[i32] = Option[i32]::None; x = Option::None;",
        "let x = takes(Option::None);",
    ],
)
def test_unit_variant_infers_from_its_expected_type(tmp_path, body):
    unions = _UNIONS + "fn takes(o: Option[i32]) i32 { return 0; }\n"
    results = _check_body(tmp_path, body, unions)
    assert ("Option::None", "Option[i32]", 0) in _constructions(results)


def test_unit_variant_infers_from_a_return_type(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS + "pub fn make() Option[i32] { return Option::None; }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "make")
    assert item is not None
    results = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results
    assert _constructions(results) == [("Option::None", "Option[i32]", 0)]


def test_unit_variant_infers_from_a_block_tail_expression(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS + "pub fn make() Option[i32] { Option::None }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "make")
    assert item is not None
    results = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results
    assert _constructions(results) == [("Option::None", "Option[i32]", 0)]


def test_unit_variant_with_nothing_to_infer_from_is_rejected(tmp_path):
    with pytest.raises(errors.CannotInferComptimeArgError):
        _check_body(tmp_path, "let x = Option::None;")


def test_payload_variant_named_as_a_value_is_rejected(tmp_path):
    with pytest.raises(errors.VariantConstructorNotAValueError):
        _check_body(tmp_path, "let x = Option::Some;")


def test_payload_variant_addressed_is_rejected(tmp_path):
    with pytest.raises(errors.VariantConstructorNotAValueError):
        _check_body(tmp_path, "let x = &Option::Some;")


def test_unit_variant_called_is_rejected(tmp_path):
    with pytest.raises(errors.UnitVariantCalledError):
        _check_body(tmp_path, "let x: Option[i32] = Option::None();")


@pytest.mark.parametrize(
    "body,error",
    [
        ("let x = Option::Some();", errors.NotEnoughArgsError),
        ("let x = Option::Some(1i32, 2i32);", errors.TooManyArgsError),
        # E is unrelated to Ok's payload, so without the arity-first
        # check this would report CannotInferComptimeArgError on E rather
        # than counting the extra argument.
        ("let x = Res::Ok(1i32, 2i32);", errors.TooManyArgsError),
    ],
)
def test_payload_arity_is_checked_before_inference(tmp_path, body, error):
    # Neither the path nor an expected type fixes T here, so inference
    # would read the very arguments the call miscounts. Checking the count
    # first is what keeps this from becoming CannotInferComptimeArgError.
    with pytest.raises(error):
        _check_body(tmp_path, body)


def test_wrong_payload_typ_is_rejected(tmp_path):
    with pytest.raises(errors.InvalidArgTypError):
        _check_body(tmp_path, "let x: Option[bool] = Option::Some(1i32);")


def test_partially_inferable_variant_names_the_unbound_parameter(tmp_path):
    # Err(E) fixes E from its payload and leaves T with nothing to say.
    with pytest.raises(errors.CannotInferComptimeArgError) as exc_info:
        _check_body(tmp_path, "let x = Res::Err(1i32);")

    msg = str(exc_info.value)
    assert '"T"' in msg
    assert '"Res"' in msg
    assert "generic union" in msg


@pytest.mark.parametrize(
    "body",
    [
        "let x = Res[bool, i32]::Err(1i32);",
        "let x: Res[bool, i32] = Res::Err(1i32);",
    ],
)
def test_partially_inferable_variant_is_fixed_by_the_missing_context(tmp_path, body):
    results = _check_body(tmp_path, body)
    assert _constructions(results)[0][1] == "Res[bool, i32]"


def test_peer_context_across_if_branches_is_not_inferred(tmp_path):
    # Deferred, not accidental: the second branch is checked with no
    # expected type of its own, so it has nothing to infer T from.
    with pytest.raises(errors.CannotInferComptimeArgError):
        _check_body(tmp_path, "let o = if (true) { Option::Some(1i32) } else { Option::None };")


def test_peer_context_across_if_branches_works_when_annotated(tmp_path):
    results = _check_body(
        tmp_path,
        "let o: Option[i32] = if (true) { Option::Some(1i32) } else { Option::None };",
    )
    assert sorted(_constructions(results)) == [
        ("Option::None", "Option[i32]", 0),
        ("Option::Some", "Option[i32]", 1),
    ]


def test_non_generic_union_variants_need_no_inference(tmp_path):
    results = _check_body(
        tmp_path,
        "let a = Flag::On; let b = Pair::Both(1i32, true);",
        "union Flag { On, Off }\nunion Pair { Both(i32, bool) }\n",
    )
    assert sorted(_constructions(results)) == [
        ("Flag::On", "Flag", 0),
        ("Pair::Both", "Pair", 0),
    ]


def _witnesses(error) -> list[str]:
    """The uncovered patterns a non-exhaustive match reports, in order."""
    return [
        extra.message.removeprefix('Uncovered pattern "').removesuffix('"') for extra in error.extra
    ]


@pytest.mark.parametrize(
    "arms",
    [
        "Option::None => 0i32, Option::Some(let x) => x,",
        "Option::Some(_) => 1i32, Option::None => 0i32,",
        "Option::Some(1i32) => 1i32, Option::Some(_) => 2i32, Option::None => 0i32,",
        "Option::Some(1i32 | 2i32) => 1i32, Option::Some(_) => 2i32, Option::None => 0i32,",
        "Option[i32]::Some(_) => 1i32, Option::None => 0i32,",
    ],
)
def test_exhaustive_union_matches(tmp_path, arms):
    _check_match(tmp_path, f"return match (o) {{ {arms} }};")


def test_payload_binding_takes_the_payload_typ(tmp_path):
    # The binding is an i32, not an Option[i32]: arithmetic on it proves
    # the column type descended into the payload.
    arms = "Option::Some(let x) => x + 1i32, Option::None => 0i32,"
    _check_match(tmp_path, f"return match (o) {{ {arms} }};")


@pytest.mark.parametrize(
    "arms",
    [
        "Option::None => 0i32,",
        # A literal leaves the payload column open, so a wildcard is still
        # needed even though both variants are named.
        "Option::Some(1i32) => 1i32, Option::None => 0i32,",
    ],
)
def test_uncovered_payload_values_are_named(tmp_path, arms):
    with pytest.raises(errors.NonExhaustiveMatchError) as exc_info:
        _check_match(tmp_path, f"return match (o) {{ {arms} }};")
    assert _witnesses(exc_info.value) == ["Option::Some(_)"]


_NESTED = _UNIONS + "union Nested { N(Option[i32]) }\n"


def _check_nested(tmp_path, arms: str) -> None:
    util.build_ir_mod(
        tmp_path, _NESTED + f"pub fn f(n: Nested) i32 {{ return match (n) {{ {arms} }}; }}"
    )


def test_nested_union_payload_is_exhausted_column_by_column(tmp_path):
    _check_nested(tmp_path, "Nested::N(Option::Some(let x)) => x, Nested::N(Option::None) => 0i32,")


def test_nested_union_payload_witness_names_both_levels(tmp_path):
    with pytest.raises(errors.NonExhaustiveMatchError) as exc_info:
        _check_nested(tmp_path, "Nested::N(Option::None) => 0i32,")
    assert _witnesses(exc_info.value) == ["Nested::N(Option::Some(_))"]


def _check_generic_nested(tmp_path, arms: str) -> None:
    body = f"return match (r) {{ {arms} }};"
    util.build_ir_mod(tmp_path, _UNIONS + f"pub fn g(r: Res[Option[i32], bool]) i32 {{ {body} }}")


def test_generic_nested_union_payload_is_exhausted_column_by_column(tmp_path):
    # The outer union's comptime argument is itself a generic union, so
    # the inner column's payload type comes from two substitutions.
    _check_generic_nested(
        tmp_path,
        "Res::Ok(Option::Some(let x)) => x,"
        " Res::Ok(Option::None) => 0i32,"
        " Res::Err(let b) => 1i32,",
    )


def test_generic_nested_union_witness_names_every_level(tmp_path):
    with pytest.raises(errors.NonExhaustiveMatchError) as exc_info:
        _check_generic_nested(tmp_path, "Res::Ok(Option::None) => 0i32, Res::Err(_) => 1i32,")
    assert _witnesses(exc_info.value) == ["Res::Ok(Option::Some(_))"]


def test_generic_nested_payload_binding_takes_the_innermost_typ(tmp_path):
    # `x` must be the i32 inside Option, not the Option itself.
    with pytest.raises(errors.MatchArmTypMismatchError):
        _check_generic_nested(
            tmp_path,
            "Res::Ok(Option::Some(let x)) => x + 1i32,"
            " Res::Ok(Option::None) => false,"
            " Res::Err(_) => 1i32,",
        )


def test_binding_under_an_or_pattern_outranks_an_enum_payload(tmp_path):
    # Bindings are rejected before an alternative is checked, so that
    # checking never registers one it is about to reject. That makes this
    # a BindingInOrPatternError rather than the payload-arity error the
    # enum variant would otherwise give.
    body = "return match (e) { E::A(let x) | E::B => 1i32, };"
    with pytest.raises(errors.BindingInOrPatternError):
        util.build_ir_mod(tmp_path, f"enum E {{ A, B }}\npub fn f(e: E) i32 {{ {body} }}")


def test_unreachable_payload_arm_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(errors, "_errors", [])
    monkeypatch.setattr(errors, "_error_level", errors.NOTE)
    arms = "Option::Some(_) => 1i32, Option::Some(1i32) => 2i32, Option::None => 0i32,"
    _check_match(tmp_path, f"return match (o) {{ {arms} }};")

    assert [type(err) for err in errors.all_errors()] == [errors.UnreachableMatchArmWarning]


@pytest.mark.parametrize(
    "arm,got,expected",
    [
        ("Option::Some => 1i32", 0, 1),
        ("Option::Some(_, _) => 1i32", 2, 1),
        ("Option::None(_) => 1i32", 1, 0),
    ],
)
def test_wrong_number_of_payload_patterns(tmp_path, arm, got, expected):
    with pytest.raises(errors.WrongNumberOfPayloadPatternsError) as exc_info:
        _check_match(tmp_path, f"return match (o) {{ {arm}, _ => 0i32, }};")

    assert f"got {got}, expected {expected}" in str(exc_info.value)


def test_variant_of_another_union_cannot_match(tmp_path):
    with pytest.raises(errors.PatternTypMismatchError) as exc_info:
        _check_match(tmp_path, "return match (o) { Res::Ok(_) => 1i32, _ => 0i32, };")

    # The message sentence-cases its opening without touching the path.
    assert 'Path pattern "Res::Ok"' in str(exc_info.value)


def test_pattern_comptime_args_must_name_the_column_instance(tmp_path):
    with pytest.raises(errors.PatternTypMismatchError) as exc_info:
        _check_match(tmp_path, "return match (o) { Option[bool]::Some(_) => 1i32, _ => 0i32, };")

    assert '"Option[bool]"' in str(exc_info.value)


@pytest.mark.parametrize(
    "arm",
    [
        # Under a payload, so the or-pattern's own alternatives are paths
        # and only a walk to any depth finds this binding.
        "Option::Some(let x) | Option::None => 1i32",
        # An immediate alternative of the inner or-pattern.
        "Option::Some(let x | 1i32) => 1i32",
    ],
)
def test_binding_anywhere_under_an_or_pattern_is_rejected(tmp_path, arm):
    with pytest.raises(errors.BindingInOrPatternError):
        _check_match(tmp_path, f"return match (o) {{ {arm}, _ => 0i32, }};")


def test_two_bindings_of_one_name_in_an_arm_are_rejected(tmp_path):
    # Confirms existing behaviour rather than adding any: an arm's
    # bindings all share one scope.
    body = "return match (p) { Pair::Both(let x, let x) => x, };"
    with pytest.raises(errors.DuplicateItemDefnError):
        util.build_ir_mod(
            tmp_path, f"union Pair {{ Both(i32, i32) }}\npub fn f(p: Pair) i32 {{ {body} }}"
        )
