# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import (
    asserts,
    ast,
    comptime,
    diag,
    diag_kinds,
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
from tests import harness


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


def _build_with_imports(
    compiler: harness.CompilerHarness,
    main_src: str,
    *mods: harness.ModSrc,
):
    """Build ``main_src`` into IR alongside sibling modules it can import."""
    return compiler.build(harness.TestProgram.from_main(main_src, *mods))


def _imported_union_template(mod, mod_name: str, name: str) -> typs.UnionTypTemplate:
    imported = mod.get_item(ir_env.Env.Namespace.CONTAINERS, mod_name)
    assert imported is not None
    return _get_union_template(asserts.checked_cast(imported.value, ir_module.Mod), name)


def _path_of(compiler: harness.CompilerHarness, expr_src: str) -> ast.Path:
    """Build the path an expression spells, as if written in ``main.leech``."""
    file = leech_src.SrcFile(compiler.workspace / "main.leech")
    expr = ast.Expr.from_tree(file, parse.build_parser("expr").parse(expr_src))
    return asserts.checked_cast(expr, ast.VarExpr).path


def _resolve_variant(mod, compiler: harness.CompilerHarness, expr_src: str) -> typs.UnionVariantRef:
    target = mod.env.resolve_var(_path_of(compiler, expr_src))
    return asserts.checked_cast(target, typs.UnionVariantRef)


_UNIONS = "union Option[T] { None, Some(T) }\nunion Res[T, E] { Ok(T), Err(E) }\n"


def _check_body(compiler, body: str, unions: str = _UNIONS):
    """Type-check a main body over the shared union declarations, without lowering."""
    mod = compiler.build(f"{unions}pub fn main() i32 {{\n{body}\nreturn 0;\n}}")
    item = mod.get_item(ir_env.Env.Namespace.VARS, "main")
    assert item is not None
    return asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results


def _check_match(compiler, body: str, unions: str = _UNIONS, diags=None):
    """Type-check a function matching on an ``Option[i32]``, without lowering."""
    mod = compiler.build(f"{unions}pub fn f(o: Option[i32]) i32 {{\n{body}\n}}", diags=diags)
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


def test_generic_and_non_generic_union_module_items_have_distinct_types(compiler):
    mod = compiler.build(
        "union Option[T] { None, Some(T) }\nunion Flag { On, Off }",
    )

    template = _get_union_template(mod, "Option")
    flag = _get_union_typ(mod, "Flag")

    assert not isinstance(template, typs.Typ)
    assert isinstance(flag, typs.Typ)
    assert flag.comptime_args == ()
    assert flag.template.name == "Flag"


def test_union_variants_carry_declaration_order_and_arity(compiler):
    mod = compiler.build(
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


def test_union_variant_templates_are_instance_independent(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    assert list(template.variants) == ["None", "Some"]
    assert template.variants["None"].index == 0
    assert template.variants["None"].arity == 0
    assert template.variants["Some"].index == 1
    assert template.variants["Some"].arity == 1


def test_union_payload_typs_resolve_against_comptime_args(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    instance = template.instantiate((typs.I32,))
    assert instance.variant_at(1).payload_typs == (typs.I32,)
    assert instance.variant_at(0).payload_typs == ()


def test_union_instances_are_cached_per_argument_list(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    assert template.instantiate((typs.I32,)) is template.instantiate((typs.I32,))
    assert template.instantiate((typs.I32,)) is not template.instantiate((typs.U8,))


def test_union_names_render_comptime_args(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }\nunion Flag { On }")
    template = _get_union_template(mod, "Option")

    assert template.instantiate((typs.I32,)).name == "Option[i32]"
    assert _get_union_typ(mod, "Flag").name == "Flag"


def test_union_qualified_name_qualifies_the_union_and_its_arguments(compiler):
    # Qualifying the arguments too is what keeps same-named unions from
    # different modules apart: Option[a::Foo] and Option[b::Foo] must not
    # arrive at one symbol.
    mod = compiler.build(
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


def test_union_qualified_name_distinguishes_same_named_unions(compiler):
    mod = _build_with_imports(
        compiler,
        "import a;\nimport b;\npub fn main() i32 { return 0; }",
        harness.ModSrc("a", "pub union Payload[T] { P(T) }"),
        harness.ModSrc("b", "pub union Payload[T] { P(T) }"),
    )
    from_a = _imported_union_template(mod, "a", "Payload").instantiate((typs.I32,))
    from_b = _imported_union_template(mod, "b", "Payload").instantiate((typs.I32,))

    assert from_a.name == from_b.name == "Payload[i32]"
    assert from_a.qualified_name == "a::Payload[i32]"
    assert from_b.qualified_name == "b::Payload[i32]"
    assert from_a is not from_b


def test_union_qualified_name_qualifies_its_arguments(compiler):
    # Qualifying the arguments too is what keeps one union instantiated
    # over two same-named types from different modules apart.
    mod = _build_with_imports(
        compiler,
        "import a;\nimport b;\npub fn main() i32 { return 0; }",
        harness.ModSrc("a", "pub union Payload[T] { P(T) }\npub struct Foo {}"),
        harness.ModSrc("b", "pub struct Foo {}"),
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


def test_union_variant_template_payload_typs_use_the_declarations_own_params(compiler):
    mod = compiler.build("union U[T, value N: usize] { A, B(array[T, N]) }")
    template = _get_union_template(mod, "U")

    (payload,) = template.variants["B"].payload_typs
    array_typ = asserts.checked_cast(payload, typs.ArrayTyp)
    typ_param, value_param = template.comptime_params
    assert array_typ.element_typ is typ_param
    assert array_typ.length is value_param
    assert template.variants["A"].payload_typs == ()


def test_instantiating_a_union_records_a_request(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }\nunion Flag { On }")
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
def test_union_tag_typ_is_the_narrowest_unsigned_fit(compiler, count, width):
    mod = compiler.build(_variant_count_src(count))
    wide = _get_union_typ(mod, "Wide")

    assert wide.tag_typ == typs.IntTyp(width, signage.UNSIGNED)


def test_duplicate_variant_in_union_defn_error(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union U { A, A }")
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_UNION_VARIANT,)


def test_reserved_variant_name_error(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union U { match }")
    assert exc_info.value.kinds == (diag_kinds.RESERVED_NAME,)


def test_union_by_value_self_cycle_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union Bad { A, B(Bad) }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)


def test_union_through_pointer_is_finite(compiler):
    mod = compiler.build("union List { Nil, Cons(i32, *List) }")
    assert len(_get_union_typ(mod, "List").variants) == 2


def test_union_through_array_element_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union Bad { A, B(array[Bad, 1]) }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)


def test_union_and_struct_mutual_by_value_cycle_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union U { A, B(S) }\nstruct S { u: U }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)


def test_growing_generic_union_declaration_cycle_is_rejected(compiler):
    # Every payload adds an array layer, so exact type identity never
    # repeats; the declaration still recurs with a growing argument.
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union L[T] { Nil, Cons(L[array[T, 1]]) }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)

    err = exc_info.value.diags[0]
    assert '"L"' in str(err)
    assert isinstance(err, diag.Diag)
    (note,) = err.notes
    assert note.msg.kind is diag_kinds.PAYLOAD_CONTAINS_BY_VALUE
    assert '"Cons"' in note.msg.text()
    assert '"L[array[T, 1]]"' in note.msg.text()


def test_growing_generic_union_cycle_through_a_struct_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union U[T] { A, B(S[array[T, 1]]) }\nstruct S[T] { u: U[T] }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,) * 2


def test_cycle_growing_through_a_union_comptime_argument_is_rejected(compiler):
    # The growth is the argument becoming a union, so detecting it needs
    # contains_typ to look inside a UnionTyp's own comptime arguments.
    # Without that the walk never recognises the repeat and recurses until
    # it exhausts the stack.
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union L[T] { Nil, Cons(L[L[T]]) }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)


def test_cycle_growing_through_a_union_argument_via_a_struct_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union U[T] { A, B(S[T]) }\nstruct S[T] { u: U[U[T]] }")
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,) * 2


def test_generic_union_through_pointer_argument_is_finite(compiler):
    mod = compiler.build("union L[T] { Nil, Cons(T, *L[T]) }")
    _get_union_template(mod, "L").check()


def test_variant_resolves_against_an_unapplied_template(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    ref = _resolve_variant(mod, compiler, "Option::Some")
    assert ref.owner is template
    assert ref.variant is template.variants["Some"]
    assert ref.name == "Some"


def test_variant_resolves_against_an_applied_instance(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    template = _get_union_template(mod, "Option")

    ref = _resolve_variant(mod, compiler, "Option[i32]::Some")
    owner = asserts.checked_cast(ref.owner, typs.UnionTyp)
    assert owner is template.instantiate((typs.I32,))
    assert owner.comptime_args == (typs.I32,)
    assert ref.variant is template.variants["Some"]


def test_variant_of_a_non_generic_union_resolves_against_its_instance(compiler):
    mod = compiler.build("union Flag { On, Off }")
    flag = _get_union_typ(mod, "Flag")

    ref = _resolve_variant(mod, compiler, "Flag::Off")
    assert ref.owner is flag
    assert ref.variant.index == 1


def test_variant_resolves_through_a_module_path(compiler):
    mod = _build_with_imports(
        compiler,
        "import a;\npub fn main() i32 { return 0; }",
        harness.ModSrc("a", "pub union Option[T] { None, Some(T) }"),
    )
    template = _imported_union_template(mod, "a", "Option")

    assert _resolve_variant(mod, compiler, "a::Option::Some").owner is template
    applied = _resolve_variant(mod, compiler, "a::Option[i32]::Some")
    assert asserts.checked_cast(applied.owner, typs.UnionTyp).comptime_args == (typs.I32,)


def test_unknown_variant_name_is_not_found(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    with pytest.raises(diag.ReportedError) as exc_info:
        _resolve_variant(mod, compiler, "Option::Nope")
    assert exc_info.value.reported.diag.kind == diag_kinds.UNKNOWN_NAME


def test_private_unions_variant_is_inaccessible(compiler):
    mod = _build_with_imports(
        compiler,
        "import a;\npub fn main() i32 { return 0; }",
        harness.ModSrc("a", "union Option[T] { None, Some(T) }"),
    )
    with pytest.raises(diag.ReportedError) as exc_info:
        _resolve_variant(mod, compiler, "a::Option::Some")
    assert exc_info.value.reported.diag.kind == diag_kinds.PRIVATE_ITEM_ACCESS


@pytest.mark.parametrize("expr", ["Option::Some[i32]", "Option[i32]::Some[i32]"])
def test_comptime_args_on_a_variant_segment_are_rejected(compiler, expr):
    # The arguments belong to the union: `Option[i32]::Some` is how to
    # say what these are trying to say.
    mod = compiler.build("union Option[T] { None, Some(T) }")
    with pytest.raises(diag.ReportedError) as exc_info:
        _resolve_variant(mod, compiler, expr)
    assert exc_info.value.reported.diag.kind == diag_kinds.UNEXPECTED_COMPTIME_ARGUMENT


def test_a_variant_cannot_qualify_a_further_path_segment(compiler):
    # A mid-path segment resolves in the container namespace, where a
    # variant is invisible, so this fails the same way `Color::Red::x`
    # does for an enum rather than reaching a variant-specific check.
    mod = compiler.build("union Option[T] { None, Some(T) }")
    with pytest.raises(diag.ReportedError) as exc_info:
        _resolve_variant(mod, compiler, "Option::Some::x")
    assert exc_info.value.reported.diag.kind == diag_kinds.UNKNOWN_NAME


def test_a_variant_is_not_reachable_in_the_container_namespace(compiler):
    mod = compiler.build("union Option[T] { None, Some(T) }")
    with pytest.raises(diag.ReportedError) as exc_info:
        mod.env.resolve_typ(_path_of(compiler, "Option::Some"))
    assert exc_info.value.reported.diag.kind == diag_kinds.UNKNOWN_NAME


def test_variant_comptime_args_come_from_the_payload_argument(compiler):
    results = _check_body(compiler, "let x = Option::Some(1i32);")
    assert _constructions(results) == [("Option::Some", "Option[i32]", 1)]


def test_variant_comptime_args_can_be_explicit(compiler):
    results = _check_body(compiler, "let x = Option[bool]::Some(true);")
    assert _constructions(results) == [("Option[bool]::Some", "Option[bool]", 1)]


def test_explicit_comptime_args_beat_the_payload_argument(compiler):
    # The payload would infer Option[i32]; the path already said otherwise,
    # so the argument is checked against the spelled-out instance instead.
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x = Option[bool]::Some(1i32);")
    assert exc_info.value.kinds == (diag_kinds.ARGUMENT_TYPE_MISMATCH,)


@pytest.mark.parametrize(
    "body",
    [
        "let x: Option[i32] = Option::None;",
        "let mut x: Option[i32] = Option[i32]::None; x = Option::None;",
        "let x = takes(Option::None);",
    ],
)
def test_unit_variant_infers_from_its_expected_type(compiler, body):
    unions = _UNIONS + "fn takes(o: Option[i32]) i32 { return 0; }\n"
    results = _check_body(compiler, body, unions)
    assert ("Option::None", "Option[i32]", 0) in _constructions(results)


def test_unit_variant_infers_from_a_return_type(compiler):
    mod = compiler.build(
        _UNIONS + "pub fn make() Option[i32] { return Option::None; }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "make")
    assert item is not None
    results = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results
    assert _constructions(results) == [("Option::None", "Option[i32]", 0)]


def test_unit_variant_infers_from_a_block_tail_expression(compiler):
    mod = compiler.build(
        _UNIONS + "pub fn make() Option[i32] { Option::None }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "make")
    assert item is not None
    results = asserts.checked_cast(item.value, ir_module.SrcFnSymbol).typ_check_results
    assert _constructions(results) == [("Option::None", "Option[i32]", 0)]


def test_unit_variant_with_nothing_to_infer_from_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x = Option::None;")
    assert exc_info.value.kinds == (diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT,)


def test_payload_variant_named_as_a_value_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x = Option::Some;")
    assert exc_info.value.kinds == (diag_kinds.MISSING_VARIANT_PAYLOAD,)


def test_payload_variant_addressed_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x = &Option::Some;")
    assert exc_info.value.kinds == (diag_kinds.MISSING_VARIANT_PAYLOAD,)


def test_unit_variant_called_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x: Option[i32] = Option::None();")
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_VARIANT_PAYLOAD,)


@pytest.mark.parametrize(
    "body",
    [
        "let x = Option::Some();",
        "let x = Option::Some(1i32, 2i32);",
        # E is unrelated to Ok's payload, so without the arity-first
        # check this would report uninferable-comptime-argument on E rather
        # than counting the extra argument.
        "let x = Res::Ok(1i32, 2i32);",
    ],
)
def test_payload_arity_is_checked_before_inference(compiler, body):
    # Neither the path nor an expected type fixes T here, so inference
    # would read the very arguments the call miscounts. Checking the count
    # first is what keeps this from becoming uninferable-comptime-argument.
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, body)
    assert exc_info.value.kinds == (diag_kinds.ARGUMENT_COUNT_MISMATCH,)


def test_wrong_payload_typ_is_rejected(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x: Option[bool] = Option::Some(1i32);")
    assert exc_info.value.kinds == (diag_kinds.ARGUMENT_TYPE_MISMATCH,)


def test_partially_inferable_variant_names_the_unbound_parameter(compiler):
    # Err(E) fixes E from its payload and leaves T with nothing to say.
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let x = Res::Err(1i32);")
    assert exc_info.value.kinds == (diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT,)

    msg = str(exc_info.value.diags[0])
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
def test_partially_inferable_variant_is_fixed_by_the_missing_context(compiler, body):
    results = _check_body(compiler, body)
    assert _constructions(results)[0][1] == "Res[bool, i32]"


def test_peer_context_across_if_branches_is_not_inferred(compiler):
    # Deferred, not accidental: the second branch is checked with no
    # expected type of its own, so it has nothing to infer T from.
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_body(compiler, "let o = if (true) { Option::Some(1i32) } else { Option::None };")
    assert exc_info.value.kinds == (diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT,)


def test_peer_context_across_if_branches_works_when_annotated(compiler):
    results = _check_body(
        compiler,
        "let o: Option[i32] = if (true) { Option::Some(1i32) } else { Option::None };",
    )
    assert sorted(_constructions(results)) == [
        ("Option::None", "Option[i32]", 0),
        ("Option::Some", "Option[i32]", 1),
    ]


def test_non_generic_union_variants_need_no_inference(compiler):
    results = _check_body(
        compiler,
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
def test_exhaustive_union_matches(compiler, arms):
    _check_match(compiler, f"return match (o) {{ {arms} }};")


def test_payload_binding_takes_the_payload_typ(compiler):
    # The binding is an i32, not an Option[i32]: arithmetic on it proves
    # the column type descended into the payload.
    arms = "Option::Some(let x) => x + 1i32, Option::None => 0i32,"
    _check_match(compiler, f"return match (o) {{ {arms} }};")


@pytest.mark.parametrize(
    "arms",
    [
        "Option::None => 0i32,",
        # A literal leaves the payload column open, so a wildcard is still
        # needed even though both variants are named.
        "Option::Some(1i32) => 1i32, Option::None => 0i32,",
    ],
)
def test_uncovered_payload_values_are_named(compiler, arms):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_match(compiler, f"return match (o) {{ {arms} }};")
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)
    assert _witnesses(exc_info.value.diags[0]) == ["Option::Some(_)"]


_NESTED = _UNIONS + "union Nested { N(Option[i32]) }\n"


def _check_nested(compiler, arms: str) -> None:
    compiler.build(_NESTED + f"pub fn f(n: Nested) i32 {{ return match (n) {{ {arms} }}; }}")


def test_nested_union_payload_is_exhausted_column_by_column(compiler):
    _check_nested(compiler, "Nested::N(Option::Some(let x)) => x, Nested::N(Option::None) => 0i32,")


def test_nested_union_payload_witness_names_both_levels(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_nested(compiler, "Nested::N(Option::None) => 0i32,")
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)
    assert _witnesses(exc_info.value.diags[0]) == ["Nested::N(Option::Some(_))"]


def _check_generic_nested(compiler, arms: str) -> None:
    body = f"return match (r) {{ {arms} }};"
    compiler.build(_UNIONS + f"pub fn g(r: Res[Option[i32], bool]) i32 {{ {body} }}")


def test_generic_nested_union_payload_is_exhausted_column_by_column(compiler):
    # The outer union's comptime argument is itself a generic union, so
    # the inner column's payload type comes from two substitutions.
    _check_generic_nested(
        compiler,
        "Res::Ok(Option::Some(let x)) => x,"
        " Res::Ok(Option::None) => 0i32,"
        " Res::Err(let b) => 1i32,",
    )


def test_generic_nested_union_witness_names_every_level(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_generic_nested(compiler, "Res::Ok(Option::None) => 0i32, Res::Err(_) => 1i32,")
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)
    assert _witnesses(exc_info.value.diags[0]) == ["Res::Ok(Option::Some(_))"]


def test_generic_nested_payload_binding_takes_the_innermost_typ(compiler):
    # `x` must be the i32 inside Option, not the Option itself.
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_generic_nested(
            compiler,
            "Res::Ok(Option::Some(let x)) => x + 1i32,"
            " Res::Ok(Option::None) => false,"
            " Res::Err(_) => 1i32,",
        )
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_MATCH_ARM_TYPES,)


def test_binding_under_an_or_pattern_outranks_an_enum_payload(compiler):
    # Bindings are rejected before an alternative is checked, so that
    # checking never registers one it is about to reject. That makes this
    # a BindingInOrPatternError rather than the payload-arity error the
    # enum variant would otherwise give.
    body = "return match (e) { E::A(let x) | E::B => 1i32, };"
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(f"enum E {{ A, B }}\npub fn f(e: E) i32 {{ {body} }}")
    assert exc_info.value.kinds == (diag_kinds.BINDING_IN_OR_PATTERN,)


def test_unreachable_payload_arm_warns(compiler):
    arms = "Option::Some(_) => 1i32, Option::Some(1i32) => 2i32, Option::None => 0i32,"
    diags = diag.Diags()
    _check_match(compiler, f"return match (o) {{ {arms} }};", diags=diags)

    assert [type(err) for err in diags.all()] == [errors.UnreachableMatchArmWarning]


@pytest.mark.parametrize(
    "arm,got,expected",
    [
        ("Option::Some => 1i32", 0, 1),
        ("Option::Some(_, _) => 1i32", 2, 1),
        ("Option::None(_) => 1i32", 1, 0),
    ],
)
def test_wrong_number_of_payload_patterns(compiler, arm, got, expected):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_match(compiler, f"return match (o) {{ {arm}, _ => 0i32, }};")
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)

    assert f"got {got}, expected {expected}" in str(exc_info.value.diags[0])


def test_variant_of_another_union_cannot_match(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_match(compiler, "return match (o) { Res::Ok(_) => 1i32, _ => 0i32, };")
    assert exc_info.value.kinds == (diag_kinds.PATTERN_TYPE_MISMATCH,)

    # The message sentence-cases its opening without touching the path.
    assert 'Path pattern "Res::Ok"' in str(exc_info.value.diags[0])


def test_pattern_comptime_args_must_name_the_column_instance(compiler):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_match(compiler, "return match (o) { Option[bool]::Some(_) => 1i32, _ => 0i32, };")
    assert exc_info.value.kinds == (diag_kinds.PATTERN_TYPE_MISMATCH,)

    assert '"Option[bool]"' in str(exc_info.value.diags[0])


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
def test_binding_anywhere_under_an_or_pattern_is_rejected(compiler, arm):
    with pytest.raises(diag.CompilationError) as exc_info:
        _check_match(compiler, f"return match (o) {{ {arm}, _ => 0i32, }};")
    assert exc_info.value.kinds == (diag_kinds.BINDING_IN_OR_PATTERN,)


def test_two_bindings_of_one_name_in_an_arm_are_rejected(compiler):
    # Confirms existing behaviour rather than adding any: an arm's
    # bindings all share one scope.
    body = "return match (p) { Pair::Both(let x, let x) => x, };"
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(f"union Pair {{ Both(i32, i32) }}\npub fn f(p: Pair) i32 {{ {body} }}")
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_DEFINITION,)


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


def test_comptime_payload_variant_construction(compiler):
    mod = compiler.build(_UNIONS + "pub let a = Option::Some(7i32);")
    value = _as_union(_mod_var_value(mod, "a"))

    assert value.typ.name == "Option[i32]"
    assert value.variant_index == 1
    assert _payload_ints(value) == [7]


def test_comptime_unit_variant_construction(compiler):
    mod = compiler.build(_UNIONS + "pub let b: Option[i32] = Option::None;")
    value = _as_union(_mod_var_value(mod, "b"))

    assert value.typ.name == "Option[i32]"
    assert value.variant_index == 0
    assert value.payload == ()


def test_comptime_multi_payload_variant_construction(compiler):
    mod = compiler.build(
        "union Pair { Both(i32, i32) }\npub let p = Pair::Both(3i32, 4i32);",
    )
    value = _as_union(_mod_var_value(mod, "p"))

    assert value.variant_index == 0
    assert _payload_ints(value) == [3, 4]


def test_comptime_nested_variant_construction(compiler):
    mod = compiler.build(
        _UNIONS + "pub let n: Option[Option[i32]] = Option::Some(Option::Some(1i32));",
    )
    outer = _as_union(_mod_var_value(mod, "n"))
    (inner_value,) = outer.payload
    inner = _as_union(inner_value)

    assert outer.typ.name == "Option[Option[i32]]"
    assert inner.typ.name == "Option[i32]"
    assert _payload_ints(inner) == [1]


def test_comptime_union_payload_cannot_hold_a_temporary_address(compiler):
    # ComptimeUnion is not a ComptimeAggregate, so without its own arm in
    # _check_not_temporary a temporary pointer would escape inside one.
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("union Boxed { B(*i32) }\npub let g = Boxed::B(&1);")
    assert exc_info.value.kinds == (diag_kinds.COMPTIME_ADDRESS_OF_TEMPORARY,)


def _union_instr_cfg(union_typ: typs.UnionTyp, payload: int):
    """A CFG that builds a union, then reads back its tag and payload."""
    cfg = ir_values.Cfg()
    bb = cfg.entry
    made = bb.union_make(union_typ, 1, (ir_values.ComptimeInt(typs.I32, payload, None),), None)
    return cfg, bb, made


def test_union_tag_instr_reads_the_variant_index(compiler):
    mod = compiler.build(_UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg, bb, made = _union_instr_cfg(union_typ, 7)
    tag = bb.union_tag(made, None)
    bb.ret(tag, None)

    assert tag.typ == union_typ.tag_typ
    result = asserts.checked_cast(comptime.Interpreter(cfg, (), ()).eval(), ir_values.ComptimeInt)
    assert result.value == 1
    assert result.typ == union_typ.tag_typ


def test_union_payload_instr_reads_the_active_variants_field(compiler):
    mod = compiler.build(_UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg, bb, made = _union_instr_cfg(union_typ, 7)
    payload = bb.union_payload(made, 1, 0, None)
    bb.ret(payload, None)

    assert payload.typ == typs.I32
    result = asserts.checked_cast(comptime.Interpreter(cfg, (), ()).eval(), ir_values.ComptimeInt)
    assert result.value == 7


def test_union_payload_instr_rejects_the_wrong_variant(compiler):
    # Lowering must compare the tag before projecting; the interpreter has
    # nothing to return for a variant the value does not hold.
    mod = compiler.build(_UNIONS)
    union_typ = _get_union_template(mod, "Option").instantiate((typs.I32,))
    cfg = ir_values.Cfg()
    bb = cfg.entry
    made = bb.union_make(union_typ, 0, (), None)
    bb.ret(bb.union_payload(made, 1, 0, None), None)

    with pytest.raises(AssertionError):
        comptime.Interpreter(cfg, (), ()).eval()


def test_generic_body_lowers_the_substituted_union_instance(compiler):
    # Inside a generic body the checker records Option[T]; lowering must
    # substitute the instantiation's arguments before building.
    mod = compiler.build(
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


def _match_let(compiler, scrutinee: str, arms: str, unions: str = _UNIONS):
    """Evaluate a module-level `let` whose initializer matches ``scrutinee``."""
    src = f"{unions}pub let answer = match ({scrutinee}) {{ {arms} }};"
    return _comptime_int(compiler.build(src), "answer")


def test_comptime_match_binds_a_payload(compiler):
    arms = "Option::Some(let x) => x, Option::None => 0i32,"
    assert _match_let(compiler, "Option::Some(7i32)", arms) == 7


def test_comptime_match_takes_the_unit_variant(compiler):
    arms = "Option::Some(let x) => x, Option::None => 5i32,"
    assert _match_let(compiler, "Option[i32]::None", arms) == 5


def test_comptime_match_binds_under_two_levels_of_payload(compiler):
    arms = "Res::Ok(Option::Some(let x)) => x, Res::Ok(Option::None) => 1i32, Res::Err(_) => 2i32,"
    scrutinee = "Res[Option[i32], bool]::Ok(Option::Some(3i32))"
    assert _match_let(compiler, scrutinee, arms) == 3


def test_comptime_match_or_pattern_inside_a_payload(compiler):
    arms = "Option::Some(1i32 | 2i32) => 10i32, _ => 0i32,"
    assert _match_let(compiler, "Option::Some(2i32)", arms) == 10


def test_comptime_match_multi_payload_variant(compiler):
    unions = "union Pair { Both(i32, i32) }\n"
    arms = "Pair::Both(let a, let b) => a - b,"
    assert _match_let(compiler, "Pair::Both(9i32, 4i32)", arms, unions) == 5


def test_comptime_match_arm_reached_through_the_fall_through_chain(compiler):
    unions = "union Three { A(i32), B(i32), C(i32) }\n"
    arms = "Three::A(let x) => x, Three::B(let x) => x + 1i32, Three::C(let x) => x + 2i32,"
    assert _match_let(compiler, "Three::C(1i32)", arms, unions) == 3


def test_comptime_match_on_a_call_result(compiler):
    unions = _UNIONS + "fn make() Option[i32] { return Option::Some(4i32); }\n"
    arms = "Option::Some(let x) => x, Option::None => 0i32,"
    assert _match_let(compiler, "make()", arms, unions) == 4


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
def test_comptime_match_payload_tests_short_circuit(compiler, scrutinee, arms):
    assert _match_let(compiler, scrutinee, arms) == 0


def test_payload_projection_is_emitted_behind_the_tag_comparison(compiler):
    # Structural, not just behavioural: the projection must land in a
    # different block from the tag test that guards it.
    mod = compiler.build(
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


def _payload_projections(compiler, arms: str) -> int:
    """How many union_payload instructions lowering a match emits."""
    mod = compiler.build(
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
def test_payload_is_projected_only_where_it_is_used(compiler, arms, projections):
    assert _payload_projections(compiler, arms) == projections


_RUNTIME_UNIONS = """
union U { I(i32), D(i64) }
union Wrap { P(*i32) }
struct Holder { a: i128, u: U, b: i32 }

fn as_i(u: U) i32 { return match (u) { U::I(let x) => x, U::D(_) => 0i32, }; }
fn as_d(u: U) i64 { return match (u) { U::D(let x) => x, U::I(_) => 0i64, }; }
"""


def test_union_round_trips_at_runtime(compiler):
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
    compiler.check(src)


def test_module_level_union_constants_read_back_at_runtime(compiler):
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
    compiler.check(src)


def test_union_constant_pointing_at_a_later_module_variable(compiler):
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
    compiler.check(src)


def test_module_level_union_global_has_its_typs_abi_size(compiler):
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
    compiler.check(src)


def test_recursive_union_walks_at_runtime(compiler):
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
    compiler.check(src)


def _discovered(compiler, src: str) -> tuple[list[str], list[str]]:
    """The struct and union instances monomorphization finds, by name."""
    mod = compiler.build(src)
    result = mono.discover(mod.ctx)
    return (
        [inst.name for inst in result.struct_instances],
        [inst.name for inst in result.union_instances],
    )


def test_discovery_finds_a_union_requested_by_a_struct_field(compiler):
    structs, unions = _discovered(
        compiler,
        """
        union Option[T] { None, Some(T) }
        struct Box[T] { o: Option[T] }
        pub fn main() i32 { let b = Box[i32] { o: Option::None }; return 0; }
        """,
    )
    assert "Box[i32]" in structs
    assert "Option[i32]" in unions


def test_discovery_finds_a_struct_requested_by_a_union_payload(compiler):
    structs, unions = _discovered(
        compiler,
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


def test_discovery_alternates_along_a_struct_union_chain(compiler):
    # struct -> union -> struct -> union, with only the head named in
    # source and every link behind a pointer. The layout walk is itself
    # transitive, so a by-value chain would be requested in one go; a
    # pointer stops it, leaving each link to be requested while draining
    # the other log after that log has already been finished with. A
    # single pass over each finds only the first two.
    structs, unions = _discovered(
        compiler,
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
def test_module_level_constant_of_a_padded_payload_reads_back(compiler, payload, args, reads):
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
    compiler.check(src)


def test_imported_union_constant_pointing_at_a_private_global(compiler):
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
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program)


# --- impl blocks on unions ---


def test_inherent_method_on_generic_union_called_on_a_value(compiler):
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
    compiler.check(src)


def test_inherent_method_on_generic_union_called_through_a_pointer(compiler):
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
    compiler.check(src)


def test_assoc_fn_on_a_union_reached_through_explicit_comptime_args(compiler):
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
    compiler.check(src)


def test_trait_impl_for_a_union(compiler):
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
    compiler.check(src)


def test_overlapping_trait_impls_for_one_union_rejected(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_IMPLS,)


def test_orphan_trait_impl_for_a_non_local_union_rejected(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.ORPHAN_IMPL,)


def test_foreign_trait_impl_for_a_local_union_is_not_an_orphan(compiler):
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
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program)


def test_inherent_impl_for_a_non_local_union_rejected(compiler):
    a_src = "pub union Option[T] { None, Some(T) }"
    main_src = """
    import a;
    impl a::Option[i32] {
        fn f() i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.IMPL_OUTSIDE_TYPE_MODULE,)


@pytest.mark.parametrize(
    "order",
    (
        "union U { A, B }\nimpl U { fn A() i32 { 1 } }",
        "impl U { fn A() i32 { 1 } }\nunion U { A, B }",
    ),
    ids=("union-first", "impl-first"),
)
def test_assoc_fn_named_after_a_variant_rejected(compiler, order):
    # A path into a union resolves a variant before an associated
    # function, so either declaration order must be rejected rather than
    # leaving the function unnameable.
    src = f"""
    {order}
    pub fn main() i32 {{ return 0; }}
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_VARIANT_AND_FUNCTION_NAMES,)


def test_assoc_fn_named_after_a_variant_of_a_generic_union_rejected(compiler):
    src = """
    union Option[T] { None, Some(T) }
    impl[T] Option[T] {
        fn Some(v: T) Option[T] { return Option::Some(v); }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_VARIANT_AND_FUNCTION_NAMES,)


def test_assoc_fn_not_named_after_a_variant_is_accepted(compiler):
    # The clash check must not reject a name merely near a variant's.
    src = """
    union U { A, B }
    impl U { fn c() i32 { return 3i32; } }
    pub fn main() i32 { return U::c() - 3i32; }
    """
    compiler.check(src)


def test_trait_method_may_share_a_variant_name(compiler):
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
    compiler.check(src)


def test_inherent_method_named_after_a_variant_rejected(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_VARIANT_AND_FUNCTION_NAMES,)


# --- end-to-end runtime and comptime behaviour ---


def test_unwrap_or_over_an_option(compiler):
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
    compiler.check(src)


def test_result_carrying_a_struct_payload(compiler):
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
    compiler.check(src)


def test_multi_payload_variant_read_back(compiler):
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
    compiler.check(src)


def test_union_stored_in_a_struct_field(compiler):
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
    compiler.check(src)


def test_union_in_an_array(compiler):
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
    compiler.check(src)


def test_recursive_list_union_walked_to_a_length(compiler):
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
    compiler.check(src)


def test_zero_variant_union_is_lowered_but_uninhabited(compiler):
    # Compile-only: Empty has no constructor, so there is no value to call
    # absurd with. It must be `pub`, or mono would never force its body and
    # the test would pass without lowering anything.
    src = """
    union Empty {}
    pub fn absurd(e: Empty) i32 { return match (e) {}; }
    pub fn main() i32 { return 0; }
    """
    ir_text = compiler.compile(src).llvm_ir
    # A definition, not the declaration that carries the same spelling:
    # the point is that the empty match was lowered, not that the symbol
    # was named. Its one block falls straight through to `unreachable`,
    # there being no arm to branch to.
    assert 'define i32 @"main::absurd"' in ir_text
    assert "unreachable" in ir_text


def test_comptime_union_constant_read_at_runtime(compiler):
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
    compiler.check(src)


def test_comptime_match_over_a_union_in_a_module_initializer(compiler):
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
    compiler.check(src)
