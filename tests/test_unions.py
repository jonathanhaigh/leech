# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import (
    asserts,
    ast,
    comptime,
    errors,
    ir_env,
    ir_module,
    ir_values,
    mono,
    parse,
    signage,
    typs,
)
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


def _mod_var_value(mod, name: str) -> ir_values.ComptimeValue:
    """Run a module variable's initializer through the compile-time interpreter."""
    item = mod.get_item(ir_env.Env.Namespace.VARS, name)
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.ModVar).initializer


def _as_union(value: ir_values.ComptimeValue) -> ir_values.ComptimeUnion:
    return asserts.checked_cast(value, ir_values.ComptimeUnion)


def _payload_ints(union_value: ir_values.ComptimeUnion) -> list[int]:
    return [
        asserts.checked_cast(value, ir_values.ComptimeInt).value for value in union_value.payload
    ]


def test_comptime_payload_variant_construction(tmp_path):
    mod = util.build_ir_mod(tmp_path, _UNIONS + "pub let a = Option::Some(7i32);")
    value = _as_union(_mod_var_value(mod, "a"))

    assert value.typ.name == "Option[i32]"
    assert value.variant_index == 1
    assert _payload_ints(value) == [7]


def test_comptime_unit_variant_construction(tmp_path):
    mod = util.build_ir_mod(tmp_path, _UNIONS + "pub let b: Option[i32] = Option::None;")
    value = _as_union(_mod_var_value(mod, "b"))

    assert value.typ.name == "Option[i32]"
    assert value.variant_index == 0
    assert value.payload == ()


def test_comptime_multi_payload_variant_construction(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "union Pair { Both(i32, i32) }\npub let p = Pair::Both(3i32, 4i32);",
    )
    value = _as_union(_mod_var_value(mod, "p"))

    assert value.variant_index == 0
    assert _payload_ints(value) == [3, 4]


def test_comptime_nested_variant_construction(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS + "pub let n: Option[Option[i32]] = Option::Some(Option::Some(1i32));",
    )
    outer = _as_union(_mod_var_value(mod, "n"))
    (inner_value,) = outer.payload
    inner = _as_union(inner_value)

    assert outer.typ.name == "Option[Option[i32]]"
    assert inner.typ.name == "Option[i32]"
    assert _payload_ints(inner) == [1]


def test_comptime_union_payload_cannot_hold_a_temporary_address(tmp_path):
    # ComptimeUnion is not a ComptimeAggregate, so without its own arm in
    # _check_not_temporary a temporary pointer would escape inside one.
    mod = util.build_ir_mod(tmp_path, "union Boxed { B(*i32) }\npub let g = Boxed::B(&1);")
    with pytest.raises(errors.CannotTakeAddressOfComptimeValueError):
        _mod_var_value(mod, "g")


def _union_instr_cfg(union_typ: typs.UnionTyp, payload: int):
    """A CFG that builds a union, then reads back its tag and payload."""
    cfg = ir_values.Cfg()
    bb = cfg.entry
    made = bb.union_make(union_typ, 1, (ir_values.ComptimeInt(typs.I32, payload, None),), None)
    return cfg, bb, made


def test_union_tag_instr_reads_the_variant_index(tmp_path):
    mod = util.build_ir_mod(tmp_path, _UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg, bb, made = _union_instr_cfg(union_typ, 7)
    tag = bb.union_tag(made, None)
    bb.ret(tag, None)

    assert tag.typ == union_typ.tag_typ
    result = asserts.checked_cast(comptime.Interpreter(cfg, (), ()).eval(), ir_values.ComptimeInt)
    assert result.value == 1
    assert result.typ == union_typ.tag_typ


def test_union_payload_instr_reads_the_active_variants_field(tmp_path):
    mod = util.build_ir_mod(tmp_path, _UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg, bb, made = _union_instr_cfg(union_typ, 7)
    payload = bb.union_payload(made, 1, 0, None)
    bb.ret(payload, None)

    assert payload.typ == typs.I32
    result = asserts.checked_cast(comptime.Interpreter(cfg, (), ()).eval(), ir_values.ComptimeInt)
    assert result.value == 7


def test_union_payload_instr_rejects_the_wrong_variant(tmp_path):
    # Lowering must compare the tag before projecting; the interpreter has
    # nothing to return for a variant the value does not hold.
    mod = util.build_ir_mod(tmp_path, _UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg = ir_values.Cfg()
    bb = cfg.entry
    made = bb.union_make(union_typ, 0, (), None)
    bb.ret(bb.union_payload(made, 1, 0, None), None)

    with pytest.raises(AssertionError):
        comptime.Interpreter(cfg, (), ()).eval()


def test_generic_body_lowers_the_substituted_union_instance(tmp_path):
    # Inside a generic body the checker records Option[T]; lowering must
    # substitute the instantiation's arguments before building.
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS + "pub fn wrap[T](x: T) Option[T] { return Option::Some(x); }",
    )
    fn = asserts.checked_cast(
        mod.get_item(ir_env.Env.Namespace.VARS, "wrap"), ir_module.ModItem
    ).value
    fn = asserts.checked_cast(fn, ir_module.SrcFnSymbol)
    recorded, _index = next(iter(fn.typ_check_results._variant_constructions.values()))
    assert recorded.name == "Option[T]"

    cfg = fn.instantiate((typs.I32,)).cfg
    makes = [
        instr
        for bb in cfg.nodes
        for instr in bb.instrs
        if isinstance(instr, ir_values.UnionMakeInstr)
    ]
    assert [instr.union_typ.name for instr in makes] == ["Option[i32]"]
    assert [instr.variant_index for instr in makes] == [1]


def _comptime_int(mod, name: str) -> int:
    return asserts.checked_cast(_mod_var_value(mod, name), ir_values.ComptimeInt).value


def _match_let(tmp_path, scrutinee: str, arms: str, unions: str = _UNIONS):
    """Evaluate a module-level `let` whose initializer matches ``scrutinee``."""
    src = f"{unions}pub let answer = match ({scrutinee}) {{ {arms} }};"
    return _comptime_int(util.build_ir_mod(tmp_path, src), "answer")


def test_comptime_match_binds_a_payload(tmp_path):
    arms = "Option::Some(let x) => x, Option::None => 0i32,"
    assert _match_let(tmp_path, "Option::Some(7i32)", arms) == 7


def test_comptime_match_takes_the_unit_variant(tmp_path):
    arms = "Option::Some(let x) => x, Option::None => 5i32,"
    assert _match_let(tmp_path, "Option[i32]::None", arms) == 5


def test_comptime_match_binds_under_two_levels_of_payload(tmp_path):
    arms = "Res::Ok(Option::Some(let x)) => x, Res::Ok(Option::None) => 1i32, Res::Err(_) => 2i32,"
    scrutinee = "Res[Option[i32], bool]::Ok(Option::Some(3i32))"
    assert _match_let(tmp_path, scrutinee, arms) == 3


def test_comptime_match_or_pattern_inside_a_payload(tmp_path):
    arms = "Option::Some(1i32 | 2i32) => 10i32, _ => 0i32,"
    assert _match_let(tmp_path, "Option::Some(2i32)", arms) == 10


def test_comptime_match_multi_payload_variant(tmp_path):
    unions = "union Pair { Both(i32, i32) }\n"
    arms = "Pair::Both(let a, let b) => a - b,"
    assert _match_let(tmp_path, "Pair::Both(9i32, 4i32)", arms, unions) == 5


def test_comptime_match_arm_reached_through_the_fall_through_chain(tmp_path):
    unions = "union Three { A(i32), B(i32), C(i32) }\n"
    arms = "Three::A(let x) => x, Three::B(let x) => x + 1i32, Three::C(let x) => x + 2i32,"
    assert _match_let(tmp_path, "Three::C(1i32)", arms, unions) == 3


def test_comptime_match_on_a_call_result(tmp_path):
    unions = _UNIONS + "fn make() Option[i32] { return Option::Some(4i32); }\n"
    arms = "Option::Some(let x) => x, Option::None => 0i32,"
    assert _match_let(tmp_path, "make()", arms, unions) == 4


@pytest.mark.parametrize(
    "scrutinee,arms",
    [
        # The payload literal must not be tested against a None value: the
        # interpreter has nothing to project from the variant it does not hold.
        ("Option[i32]::None", "Option::Some(1i32) => 1i32, _ => 0i32,"),
        (
            "Res[Option[i32], bool]::Err(true)",
            "Res::Ok(Option::Some(1i32)) => 1i32, _ => 0i32,",
        ),
        (
            "Res[Option[i32], bool]::Ok(Option::None)",
            "Res::Ok(Option::Some(1i32)) => 1i32, _ => 0i32,",
        ),
    ],
)
def test_comptime_match_payload_tests_short_circuit(tmp_path, scrutinee, arms):
    assert _match_let(tmp_path, scrutinee, arms) == 0


def test_payload_projection_is_emitted_behind_the_tag_comparison(tmp_path):
    # Structural, not just behavioural: the projection must land in a
    # different block from the tag test that guards it.
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS
        + """pub fn f(o: Option[i32]) i32 {
            return match (o) { Option::Some(1i32) => 1i32, _ => 0i32, };
        }""",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "f")
    assert item is not None
    cfg = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).instantiate(()).cfg

    tag_blocks = {
        bb for bb in cfg.nodes for i in bb.instrs if isinstance(i, ir_values.UnionTagInstr)
    }
    payload_blocks = {
        bb for bb in cfg.nodes for i in bb.instrs if isinstance(i, ir_values.UnionPayloadInstr)
    }
    assert tag_blocks
    assert payload_blocks
    assert not (tag_blocks & payload_blocks)


def _payload_projections(tmp_path, arms: str) -> int:
    """How many union_payload instructions lowering a match emits."""
    mod = util.build_ir_mod(
        tmp_path,
        _UNIONS + f"pub fn f(o: Option[i32]) i32 {{ return match (o) {{ {arms} }}; }}",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "f")
    assert item is not None
    cfg = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).instantiate(()).cfg
    return sum(
        1
        for bb in cfg.nodes
        for instr in bb.instrs
        if isinstance(instr, ir_values.UnionPayloadInstr)
    )


@pytest.mark.parametrize(
    "arms,projections",
    [
        # A payload nothing tests and nothing binds is never projected.
        ("Option::Some(_) => 1i32, Option::None => 0i32,", 0),
        ("Option::Some(let x) => x, Option::None => 0i32,", 1),
        ("Option::Some(1i32) => 1i32, _ => 0i32,", 1),
    ],
)
def test_payload_is_projected_only_where_it_is_used(tmp_path, arms, projections):
    assert _payload_projections(tmp_path, arms) == projections


_RUNTIME_UNIONS = """
union U { I(i32), D(i64) }
union Wrap { P(*i32) }
struct Holder { a: i128, u: U, b: i32 }

fn as_i(u: U) i32 { return match (u) { U::I(let x) => x, U::D(_) => 0i32, }; }
fn as_d(u: U) i64 { return match (u) { U::D(let x) => x, U::I(_) => 0i64, }; }
"""


def test_union_round_trips_at_runtime(tmp_path):
    src = (
        _RUNTIME_UNIONS
        + """
    pub fn main() i32 {
        let small = U::I(5i32);
        let large = U::D(9i64);
        if (as_i(small) != 5i32) { return 1i32; }
        if (as_d(large) != 9i64) { return 2i32; }
        return 0;
    }
    """
    )
    util.check_prog_output(tmp_path, src, "", 0)


def test_module_level_union_constants_read_back_at_runtime(tmp_path):
    # A size check alone cannot see a wrong field offset or array stride,
    # so the values are read back out of each constant.
    src = (
        _RUNTIME_UNIONS
        + """
    pub let plain = U::I(5i32);
    pub let held = Holder { a: 1i128, u: U::D(9i64), b: 3i32 };
    pub let arr = array[U, 2]{ U::I(1i32), U::D(2i64) };
    pub let later = 42i32;
    pub let ptr_bearing = Wrap::P(&later);

    pub fn main() i32 {
        if (as_i(plain) != 5i32) { return 1i32; }
        if (held.a != 1i128) { return 2i32; }
        if (as_d(held.u) != 9i64) { return 3i32; }
        if (held.b != 3i32) { return 4i32; }
        if (as_i(arr.[0usize]) != 1i32) { return 5i32; }
        if (as_d(arr.[1usize]) != 2i64) { return 6i32; }
        let p = match (ptr_bearing) { Wrap::P(let q) => q, };
        if (p.* != 42i32) { return 7i32; }
        return 0;
    }
    """
    )
    util.check_prog_output(tmp_path, src, "", 0)


def test_union_constant_pointing_at_a_later_module_variable(tmp_path):
    # The global has to be declared out of source order, because lowering
    # this constant needs the one it points at.
    src = """
    union Wrap { P(*i32) }
    pub let ptr_first = Wrap::P(&later);
    pub let later = 42i32;
    pub fn main() i32 {
        let p = match (ptr_first) { Wrap::P(let q) => q, };
        return p.* - 42i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_module_level_union_global_has_its_typs_abi_size(tmp_path):
    # The one place two independently built LLVM types must agree: the
    # ad-hoc constant type and the union's own.
    src = """
    union U { I(i32), D(i64) }
    pub let g = U::I(5i32);
    pub fn main() i32 {
        if (__size_of[U]() != 16usize) { return 1i32; }
        return match (g) { U::I(let x) => x - 5i32, U::D(_) => 2i32, };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_recursive_union_walks_at_runtime(tmp_path):
    src = """
    union List[T] { Nil, Cons(T, *List[T]) }
    fn length(l: *List[i32]) i32 {
        let mut count = 0i32;
        let mut cur = l;
        while (true) {
            let next = match (cur.*) { List::Nil => cur, List::Cons(_, let tail) => tail, };
            let done = match (cur.*) { List::Nil => true, List::Cons(_, _) => false, };
            if (done) { return count; }
            count = count + 1i32;
            cur = next;
        }
        return count;
    }
    pub fn main() i32 {
        let tail: List[i32] = List::Nil;
        let mid = List::Cons(2i32, &tail);
        let head = List::Cons(1i32, &mid);
        return length(&head) - 2i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def _discovered(tmp_path, src: str) -> tuple[list[str], list[str]]:
    """The struct and union instances monomorphization finds, by name."""
    mod = util.build_ir_mod(tmp_path, src)
    result = mono.discover(mod)
    return (
        [inst.name for inst in result.struct_instances],
        [inst.name for inst in result.union_instances],
    )


def test_discovery_finds_a_union_requested_by_a_struct_field(tmp_path):
    structs, unions = _discovered(
        tmp_path,
        """
        union Option[T] { None, Some(T) }
        struct Box[T] { o: Option[T] }
        pub fn main() i32 { let b = Box[i32] { o: Option::None }; return 0; }
        """,
    )
    assert "Box[i32]" in structs
    assert "Option[i32]" in unions


def test_discovery_finds_a_struct_requested_by_a_union_payload(tmp_path):
    structs, unions = _discovered(
        tmp_path,
        """
        struct Pair[T] { a: T, b: T }
        union Holder[T] { H(Pair[T]) }
        pub fn main() i32 {
            let h = Holder::H(Pair[i32] { a: 1i32, b: 2i32 });
            return 0;
        }
        """,
    )
    assert "Holder[i32]" in unions
    assert "Pair[i32]" in structs


def test_discovery_alternates_along_a_struct_union_chain(tmp_path):
    # struct -> union -> struct -> union, with only the head named in
    # source and every link behind a pointer. The layout walk is itself
    # transitive, so a by-value chain would be requested in one go; a
    # pointer stops it, leaving each link to be requested while draining
    # the other log after that log has already been finished with. A
    # single pass over each finds only the first two.
    structs, unions = _discovered(
        tmp_path,
        """
        union Inner[T] { I(T) }
        struct Middle[T] { i: *Inner[T] }
        union Outer[T] { O(*Middle[T]) }
        struct Top[T] { o: *Outer[T] }
        pub fn take(t: Top[i32]) i32 { return 0; }
        pub fn main() i32 { return 0; }
        """,
    )
    assert structs == ["Top[i32]", "Middle[i32]"]
    assert unions == ["Outer[i32]", "Inner[i32]"]


@pytest.mark.parametrize(
    "payload,args,reads",
    [
        # Fields needing padding between them: a packed payload would put
        # the second field where the read path does not look for it.
        ("i8, i32", "1i8, 22i32", [("a", "i8", "1i8"), ("b", "i32", "22i32")]),
        ("i32, i64", "3i32, 44i64", [("a", "i32", "3i32"), ("b", "i64", "44i64")]),
        ("bool, i64", "true, 55i64", [("b", "i64", "55i64")]),
    ],
)
def test_module_level_constant_of_a_padded_payload_reads_back(tmp_path, payload, args, reads):
    accessors = "".join(
        f"fn get_{name}(v: V) {typ} {{ return match (v) {{ V::P(let a, let b) => {name}, }}; }}\n"
        for name, typ, _expected in reads
    )
    checks = "".join(
        f"    if (get_{name}(g) != {expected}) {{ return {i + 1}i32; }}\n"
        for i, (name, _typ, expected) in enumerate(reads)
    )
    src = f"""
    union V {{ P({payload}) }}
    pub let g = V::P({args});
    {accessors}
    pub fn main() i32 {{
{checks}        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_imported_union_constant_pointing_at_a_private_global(tmp_path):
    # The importing module needs the exported global's ad-hoc type but must
    # not build its constant: doing so would reach a global that module
    # keeps to itself.
    a_src = """
    pub union Wrap { P(*i32) }
    let hidden = 42i32;
    pub let exported = Wrap::P(&hidden);
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let p = match (a::exported) { a::Wrap::P(let q) => q, };
        return p.* - 42i32;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


# --- impl blocks on unions ---


def test_inherent_method_on_generic_union_called_on_a_value(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    impl[T] Option[T] {
        fn unwrap_or(*self, fallback: T) T {
            return match (self.*) {
                Option::Some(let x) => x,
                Option::None => fallback,
            };
        }
    }
    pub fn main() i32 {
        let some = Option::Some(7i32);
        let none: Option[i32] = Option::None;
        return some.unwrap_or(0i32) + none.unwrap_or(35i32) - 42i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inherent_method_on_generic_union_called_through_a_pointer(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    impl[T] Option[T] {
        fn is_some(*self) bool {
            return match (self.*) {
                Option::Some(_) => true,
                Option::None => false,
            };
        }
    }
    pub fn main() i32 {
        let some = Option::Some(1i32);
        let none: Option[i32] = Option::None;
        // The explicit path form passes the pointer receiver itself,
        // where the dot form takes the address of a place for you.
        let held = Option[i32]::is_some(&some);
        let empty = Option[i32]::is_some(&none);
        if (held and not empty and some.is_some()) { return 0; };
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_assoc_fn_on_a_union_reached_through_explicit_comptime_args(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    impl[T] Option[T] {
        fn make(v: T) Option[T] { return Option::Some(v); }
    }
    pub fn main() i32 {
        return match (Option[i32]::make(9i32)) {
            Option::Some(let x) => x - 9i32,
            Option::None => 1i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_impl_for_a_union(tmp_path):
    src = """
    trait Code { fn code(*self) i32; }
    union Option[T] { None, Some(T) }
    impl[T] Code for Option[T] {
        fn code(*self) i32 {
            return match (self.*) {
                Option::Some(_) => 0i32,
                Option::None => 1i32,
            };
        }
    }
    pub fn main() i32 {
        let some = Option::Some(1i32);
        return some.code();
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_overlapping_trait_impls_for_one_union_rejected(tmp_path):
    src = """
    trait Code { fn code(*self) i32; }
    union Option[T] { None, Some(T) }
    impl[T] Code for Option[T] {
        fn code(*self) i32 { 0 }
    }
    impl Code for Option[i32] {
        fn code(*self) i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ConflictingImplsError):
        util.compile_str(tmp_path, src)


def test_orphan_trait_impl_for_a_non_local_union_rejected(tmp_path):
    a_src = """
    pub trait Code { fn code(*self) i32; }
    pub union Option[T] { None, Some(T) }
    """
    main_src = """
    import a;
    impl a::Code for a::Option[i32] {
        fn code(*self) i32 { 0 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.OrphanImplError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_foreign_trait_impl_for_a_local_union_is_not_an_orphan(tmp_path):
    # The union is this module's own, which is what makes the impl
    # allowed - the orphan rule needs either side to be local.
    a_src = "pub trait Code { fn code(*self) i32; }"
    main_src = """
    import a;
    union Option[T] { None, Some(T) }
    impl[T] a::Code for Option[T] {
        fn code(*self) i32 {
            return match (self.*) {
                Option::Some(_) => 0i32,
                Option::None => 1i32,
            };
        }
    }
    pub fn main() i32 {
        let some = Option::Some(1i32);
        return some.code();
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_inherent_impl_for_a_non_local_union_rejected(tmp_path):
    a_src = "pub union Option[T] { None, Some(T) }"
    main_src = """
    import a;
    impl a::Option[i32] {
        fn f() i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ImplForNonLocalTypError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


@pytest.mark.parametrize(
    "order",
    (
        "union U { A, B }\nimpl U { fn A() i32 { 1 } }",
        "impl U { fn A() i32 { 1 } }\nunion U { A, B }",
    ),
    ids=("union-first", "impl-first"),
)
def test_assoc_fn_named_after_a_variant_rejected(order, tmp_path):
    # A path into a union resolves a variant before an associated
    # function, so either declaration order must be rejected rather than
    # leaving the function unnameable.
    src = f"""
    {order}
    pub fn main() i32 {{ return 0; }}
    """
    with pytest.raises(errors.FnNameClashesWithUnionVariantError):
        util.compile_str(tmp_path, src)


def test_assoc_fn_named_after_a_variant_of_a_generic_union_rejected(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    impl[T] Option[T] {
        fn Some(v: T) Option[T] { return Option::Some(v); }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.FnNameClashesWithUnionVariantError):
        util.compile_str(tmp_path, src)


def test_assoc_fn_not_named_after_a_variant_is_accepted(tmp_path):
    # The clash check must not reject a name merely near a variant's.
    src = """
    union U { A, B }
    impl U { fn c() i32 { return 3i32; } }
    pub fn main() i32 { return U::c() - 3i32; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_method_may_share_a_variant_name(tmp_path):
    # The clash rule is about inherent functions only. A trait fixes its
    # methods' names, so rejecting the overlap would bar the union from
    # implementing the trait at all; the method is reached through the
    # trait rather than by a path into the union, so nothing is shadowed.
    src = """
    trait Maker { fn Some(*self) i32; }
    union U { Some(i32), None }
    impl Maker for U {
        fn Some(*self) i32 { return 0i32; }
    }
    pub fn main() i32 {
        let u = U::Some(1i32);
        return u.Some();
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inherent_method_named_after_a_variant_rejected(tmp_path):
    # A method keeps its dot-call when the variant takes its name, but
    # loses the explicit path form that is meant to be equivalent to it:
    # `U::Some(&u)` would build a variant instead of calling the method.
    src = """
    union U { Some(i32), None }
    impl U {
        fn Some(*self) i32 { return 0i32; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.FnNameClashesWithUnionVariantError):
        util.compile_str(tmp_path, src)


# --- end-to-end runtime and comptime behaviour ---


def test_unwrap_or_over_an_option(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    fn unwrap_or[T](o: Option[T], fallback: T) T {
        return match (o) {
            Option::Some(let x) => x,
            Option::None => fallback,
        };
    }
    pub fn main() i32 {
        let some = Option::Some(40i32);
        let none: Option[i32] = Option::None;
        return unwrap_or(some, 0i32) + unwrap_or(none, 2i32) - 42i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_result_carrying_a_struct_payload(tmp_path):
    src = """
    struct Point { x: i32, y: i32 }
    union Result[T, E] { Ok(T), Err(E) }
    pub fn main() i32 {
        let r: Result[Point, i32] = Result::Ok(Point { x: 3, y: 4 });
        return match (r) {
            Result::Ok(let p) => p.x * p.y - 12i32,
            Result::Err(let e) => e,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_multi_payload_variant_read_back(tmp_path):
    # Differently sized and aligned payload fields, each checked against
    # the value put in - a wrong field offset would show up here.
    src = """
    union V { P(i8, i32, i64), Q }
    pub fn main() i32 {
        let v = V::P(1i8, 2i32, 3i64);
        return match (v) {
            V::P(let a, let b, let c) => {
                if (a == 1i8 and b == 2i32 and c == 3i64) { 0i32 } else { 1i32 }
            },
            V::Q => 2i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_union_stored_in_a_struct_field(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    struct Holder { tag: i32, opt: Option[i64] }
    pub fn main() i32 {
        let h = Holder { tag: 7, opt: Option::Some(35i64) };
        return match (h.opt) {
            Option::Some(let x) => if (x == 35i64 and h.tag == 7i32) { 0i32 } else { 1i32 },
            Option::None => 2i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_union_in_an_array(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    pub fn main() i32 {
        let arr = array[Option[i32], 3]{
            Option::Some(10i32),
            Option::None,
            Option::Some(32i32),
        };
        let mut total = 0i32;
        let mut i = 0usize;
        while (i < 3usize) {
            let here = match (arr.[i]) {
                Option::Some(let x) => x,
                Option::None => 0i32,
            };
            total = total + here;
            i = i + 1usize;
        };
        return total - 42i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_recursive_list_union_walked_to_a_length(tmp_path):
    src = """
    union List[T] { Nil, Cons(T, *List[T]) }
    fn length[T](list: *List[T]) i32 {
        let mut n = 0i32;
        let mut cur = list;
        while (true) {
            cur = match (cur.*) {
                List::Nil => { break; },
                List::Cons(_, let rest) => rest,
            };
            n = n + 1i32;
        };
        return n;
    }
    pub fn main() i32 {
        let nil: List[i32] = List::Nil;
        let third = List::Cons(3i32, &nil);
        let second = List::Cons(2i32, &third);
        let first = List::Cons(1i32, &second);
        return length(&first) - 3i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_zero_variant_union_is_lowered_but_uninhabited(tmp_path):
    # Compile-only: Empty has no constructor, so there is no value to call
    # absurd with. It must be `pub`, or mono would never force its body and
    # the test would pass without lowering anything.
    src = """
    union Empty {}
    pub fn absurd(e: Empty) i32 { return match (e) {}; }
    pub fn main() i32 { return 0; }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    # A definition, not the declaration that carries the same spelling:
    # the point is that the empty match was lowered, not that the symbol
    # was named. Its one block falls straight through to `unreachable`,
    # there being no arm to branch to.
    assert 'define i32 @"main::absurd"' in ir_text
    assert "unreachable" in ir_text


def test_comptime_union_constant_read_at_runtime(tmp_path):
    src = """
    union Option[T] { None, Some(T) }
    pub let G = Option::Some(1i32);
    pub fn main() i32 {
        return match (G) {
            Option::Some(let x) => x - 1i32,
            Option::None => 1i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_over_a_union_in_a_module_initializer(tmp_path):
    # The initializer is folded by the interpreter, so the match itself
    # never reaches codegen - only the integer it produces.
    src = """
    union Option[T] { None, Some(T) }
    let x = match (Option::Some(42i32)) {
        Option::Some(let v) => v,
        Option::None => 0i32,
    };
    pub fn main() i32 {
        return x - 42i32;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
