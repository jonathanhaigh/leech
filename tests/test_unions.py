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
