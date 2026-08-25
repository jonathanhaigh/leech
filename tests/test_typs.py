# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest
import util

from leech import ast, compilation, errors, ir_env, ir_traits, parse, typs
from leech import src as leech_src


@pytest.mark.parametrize(
    "typ,value",
    (
        ("bool", "true"),
        ("i32", "1"),
        ("i32", "1i32"),
        ("u17", "10u17"),
        ("usize", "99usize"),
        ("isize", "54isize"),
    ),
)
def test_builtin_typ_lookup(tmp_path, typ, value):
    src = f"""
    fn f() {typ} {{ {value} }}

    pub fn main() i32 {{
        f();
        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_int_lit_at_typ_width_boundary_is_allowed(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 255u8;
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_int_lit_overflow(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 256u8;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "256u8")


def test_comptime_int_lit_overflow(tmp_path):
    src = """
    let x = 256u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "256u8")


def test_int_lit_at_signed_typ_max_is_allowed(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 127i8;
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_int_lit_overflow_signed(tmp_path):
    # 128 doesn't fit in i8 even though it fits in 8 bits, since i8's
    # range is -128..127, not 0..255 - a signed literal one past its
    # type's max positive value must still be rejected.
    src = """
    pub fn main() i32 {
        let x = 128i8;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "128i8")


def test_comptime_int_lit_overflow_signed(tmp_path):
    src = """
    let x = 128i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "128i8")


def test_int_lit_infers_declared_let_typ(tmp_path):
    # 10 is a u8 here, not an i32 that coerces to one - i32 -> u8 is a
    # narrowing conversion the language rejects.
    src = """
    pub fn main() i32 {
        let x: u8 = 10;
        let y = x + 200u8;
        return if (y == 210u8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize(
    "prelude,expr",
    (
        # Every context with a single unambiguous target type.
        ("fn takes(v: u8) u8 { return v; }", "takes(200)"),
        ("struct T { a: u8 }", "T { a: 200 }.a"),
        ("fn first(a: [u8; 2]) u8 { return a.[0]; }", "first([200, 1])"),
        ("fn ret() u8 { return 200; }", "ret()"),
        ("fn tail() u8 { 200 }", "tail()"),
        ("fn cond() u8 { if (true) { 200 } else { 1 } }", "cond()"),
    ),
)
def test_int_lit_infers_at_coercion_points(tmp_path, prelude, expr):
    # 200 doesn't fit i8 and i32 doesn't coerce to u8, so each of these
    # only compiles if the literal is inferred as u8 in the first place.
    src = f"""
    {prelude}
    pub fn main() i32 {{
        let x: u8 = {expr};
        return if (x == 200u8) {{ 7 }} else {{ 0 }};
    }}
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_int_lit_infers_in_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 0u8;
        x = 200;
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_comptime_int_lit_infers_declared_typ(tmp_path):
    src = """
    let x: u8 = 200;
    pub fn main() i32 {
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_int_lit_inference_reaches_operands(tmp_path):
    # Neither operand's type is decided by the operand itself, so both
    # take the declared type of the variable they end up in.
    src = """
    pub fn main() i32 {
        let x: u8 = 200 + 55;
        return if (x == 255u8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_int_lit_inference_does_not_reach_across_a_typed_operand(tmp_path):
    # An operand whose type *is* decided still has to match its peer
    # exactly: nothing coerces i32 to u8, so this stays an error.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32 + 1;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleLetTypError):
        util.compile_str(tmp_path, src)


def test_explicit_int_lit_suffix_beats_inference(tmp_path):
    # A written suffix fixes the type, so this is an i32 -> u8 narrowing.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleLetTypError):
        util.compile_str(tmp_path, src)


def test_int_lit_too_big_for_inferred_typ(tmp_path):
    src = """
    pub fn main() i32 {
        let x: u8 = 300;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "300" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "300")


@pytest.mark.parametrize(
    "src,expected",
    (
        ("*i32", "*i32"),
        ("*mut i32", "*mut i32"),
        ("**mut u8", "**mut u8"),
        ("[*mut i32; 2]", "[*mut i32; 2]"),
    ),
)
def test_ptr_typ_name_matches_source_syntax(src, expected):
    # Type names appear in diagnostics, so they should read the way the
    # type would be written.
    file = leech_src.SrcFile(pathlib.Path("test.leech"))
    tree = parse.build_parser("typ").parse(src)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    typ = typs.Typ.from_ast(ast.Typ.from_tree(file, tree), env)
    assert typ.name == expected


def test_typ_param_typ_interns_by_owner_and_index(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T, U](x: T, y: U) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    t0 = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)
    t0_again = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)
    t1 = typs.TypParamTyp.get_or_create(fn, 1, fn.generic_params[1].ident.name)

    assert t0 is t0_again
    assert t0 is not t1
    assert t0.name == "T"
    assert t1.name == "U"


def test_typ_param_typ_distinct_across_owners(tmp_path):
    mod = util.parse_mod(
        tmp_path,
        """
        fn f[T](x: T) {}
        fn g[T](x: T) {}
        """,
    )
    f_defn, g_defn = mod.defns
    assert isinstance(f_defn, ast.FnDefn)
    assert isinstance(g_defn, ast.FnDefn)

    t_f = typs.TypParamTyp.get_or_create(f_defn, 0, f_defn.generic_params[0].ident.name)
    t_g = typs.TypParamTyp.get_or_create(g_defn, 0, g_defn.generic_params[0].ident.name)
    assert t_f is not t_g
    assert t_f.name == t_g.name == "T"


def test_struct_templates_and_instances_are_isolated_by_compilation_ctx(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    first_ctx = compilation.Ctx()
    second_ctx = compilation.Ctx()
    first_env = ir_env.Env(first_ctx, ir_traits.ImplRegistry(first_ctx), None)
    second_env = ir_env.Env(second_ctx, ir_traits.ImplRegistry(second_ctx), None)

    first = typs.StructTypTemplate(struct_ast, first_env, "main")
    second = typs.StructTypTemplate(struct_ast, second_env, "main")
    first_instance = first.instantiate((typs.I32,))
    second_instance = second.instantiate((typs.I32,))

    assert first is not second
    assert first_instance is first.instantiate((typs.I32,))
    assert first_instance is not second_instance
    assert first_instance.template is first
    assert first_instance.typ_args == (typs.I32,)
    assert tuple(first_ctx.requested_struct_instances()) == (first_instance,)
    assert tuple(second_ctx.requested_struct_instances()) == (second_instance,)


def test_struct_validation_instance_is_cached_without_request(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    template = typs.StructTypTemplate(struct_ast, env, "main")

    validation = template._validation_instance

    assert validation is template._validation_instance
    assert validation.template is template
    assert validation.typ_args == template.typ_params
    assert tuple(ctx.requested_struct_instances()) == ()


def test_struct_typ_rejects_global_typ_cache_construction(tmp_path):
    parsed_mod = util.parse_mod(tmp_path, "struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    template = typs.StructTypTemplate(struct_ast, env, "main")

    with pytest.raises(AssertionError, match="owned by the compilation context"):
        typs.StructTyp.get_or_create(template, (typs.I32,))


def test_substitute_typ_params_replaces_mapped_typ_param(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)

    assert t.substitute_typ_params({t: typs.I32}) is typs.I32


def test_substitute_typ_params_leaves_unmapped_typ_param_unchanged(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T, U](x: T, y: U) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)
    u = typs.TypParamTyp.get_or_create(fn, 1, fn.generic_params[1].ident.name)

    assert t.substitute_typ_params({u: typs.I32}) is t


def test_substitute_typ_params_leaves_concrete_typ_unchanged():
    assert typs.I32.substitute_typ_params({}) is typs.I32
    assert typs.BOOL.substitute_typ_params({}) is typs.BOOL


def test_substitute_typ_params_recurses_through_composite_typs(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)
    mapping = {t: typs.I32}

    assert typs.PtrTyp.get_or_create(t, typs.MUT).substitute_typ_params(
        mapping
    ) is typs.PtrTyp.get_or_create(typs.I32, typs.MUT)
    assert typs.ArrayTyp.get_or_create(t, 3).substitute_typ_params(
        mapping
    ) is typs.ArrayTyp.get_or_create(typs.I32, 3)

    fn_typ = typs.FnTyp.get_or_create(t, (t, typs.BOOL))
    assert fn_typ.substitute_typ_params(mapping) is typs.FnTyp.get_or_create(
        typs.I32, (typs.I32, typs.BOOL)
    )


def _assert_typs_overlap_symmetric(left: typs.Typ, right: typs.Typ, expected: bool) -> None:
    assert typs.typs_overlap(left, right) is expected
    assert typs.typs_overlap(right, left) is expected


def test_contains_typ_finds_structural_occurrences() -> None:
    ptr = typs.PtrTyp.get_or_create(typs.I32, typs.CONST)
    array = typs.ArrayTyp.get_or_create(ptr, 2)

    assert typs.contains_typ(array, array)
    assert typs.contains_typ(array, ptr)
    assert typs.contains_typ(array, typs.I32)
    assert not typs.contains_typ(array, typs.BOOL)


def test_typs_overlap_fn_typs_symmetrically(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)

    generic = typs.FnTyp.get_or_create(typs.BOOL, (t,))
    _assert_typs_overlap_symmetric(generic, typs.FnTyp.get_or_create(typs.BOOL, (typs.I32,)), True)
    _assert_typs_overlap_symmetric(generic, typs.FnTyp.get_or_create(typs.I32, (typs.I32,)), False)
    _assert_typs_overlap_symmetric(
        generic, typs.FnTyp.get_or_create(typs.BOOL, (typs.I32, typs.I32)), False
    )


def test_typs_overlap_ptr_typs_symmetrically_and_distinguishes_mutability(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)

    generic = typs.PtrTyp.get_or_create(t, typs.CONST)
    _assert_typs_overlap_symmetric(generic, typs.PtrTyp.get_or_create(typs.I32, typs.CONST), True)
    _assert_typs_overlap_symmetric(generic, typs.PtrTyp.get_or_create(typs.I32, typs.MUT), False)


def test_typs_overlap_array_typs_symmetrically_and_distinguishes_length(tmp_path):
    mod = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)

    generic = typs.ArrayTyp.get_or_create(t, 3)
    _assert_typs_overlap_symmetric(generic, typs.ArrayTyp.get_or_create(typs.I32, 3), True)
    _assert_typs_overlap_symmetric(generic, typs.ArrayTyp.get_or_create(typs.I32, 4), False)


def test_typs_overlap_struct_typs_symmetrically_and_distinguishes_declaration(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Pair[A, B] {}\nstruct Other[A, B] {}")
    pair = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Pair")
    other = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Other")
    assert isinstance(pair, typs.StructTypTemplate)
    assert isinstance(other, typs.StructTypTemplate)
    t = pair.typ_params[0]

    _assert_typs_overlap_symmetric(
        pair.instantiate((t, typs.I32)),
        pair.instantiate((typs.BOOL, typs.I32)),
        True,
    )
    _assert_typs_overlap_symmetric(
        pair.instantiate((typs.I32, typs.I32)),
        other.instantiate((typs.I32, typs.I32)),
        False,
    )


def test_typs_overlap_enum_backing_typs_symmetrically(tmp_path):
    mod = util.build_ir_mod(tmp_path, "enum E(u8) { A } enum F(u8) { A }")
    enum_e = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "E")
    enum_f = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "F")
    assert isinstance(enum_e, typs.EnumTyp)
    assert isinstance(enum_f, typs.EnumTyp)

    parsed = util.parse_mod(tmp_path, "fn f[T](x: T) {}")
    (fn,) = parsed.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp.get_or_create(fn, 0, fn.generic_params[0].ident.name)

    generic = typs.EnumBackingTyp.get_or_create(t)
    _assert_typs_overlap_symmetric(generic, typs.EnumBackingTyp.get_or_create(enum_e), True)
    _assert_typs_overlap_symmetric(
        typs.EnumBackingTyp.get_or_create(enum_e),
        typs.EnumBackingTyp.get_or_create(enum_f),
        False,
    )


def test_int_typ_name_with_unparseable_width_is_not_a_typ(tmp_path):
    # The width exceeds CPython's int-from-string digit limit; resolving it
    # must diagnose an unknown type rather than crash.
    name = "i" + "9" * 5000
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, f"pub fn main() i32 {{ let x: {name} = 5; return 0; }}")
