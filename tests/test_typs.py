# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import inspect
import pathlib
import typing

import pytest

from leech import (
    asserts,
    ast,
    compilation,
    diag,
    errors,
    ir_env,
    ir_module,
    opt_util,
    parse,
    signage,
    typs,
)
from leech import src as leech_src
from tests import harness


def test_typ_kind_covers_every_concrete_typ_subclass():
    subclasses: set[type[typs.Typ]] = set()
    pending = [typs.Typ]
    while pending:
        children = pending.pop().__subclasses__()
        subclasses.update(children)
        pending.extend(children)

    members = typing.get_args(typs.TypKind.__value__)
    for cls in subclasses:
        if not inspect.isabstract(cls):
            assert any(issubclass(cls, member) for member in members), cls.__name__


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
def test_builtin_typ_lookup(compiler, typ, value):
    src = f"""
    fn f() {typ} {{ {value} }}

    pub fn main() i32 {{
        f();
        return 0;
    }}
    """
    compiler.check(src)


def test_int_lit_at_typ_width_boundary_is_allowed(compiler):
    src = """
    pub fn main() i32 {
        let x = 255u8;
        return 0;
    }
    """
    compiler.check(src)


def test_int_lit_overflow(compiler):
    src = """
    pub fn main() i32 {
        let x = 256u8;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        compiler.compile(src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    harness.assert_span_at(span, src, "256u8")


def test_comptime_int_lit_overflow(compiler):
    src = """
    let x = 256u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        compiler.compile(src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    harness.assert_span_at(span, src, "256u8")


def test_int_lit_at_signed_typ_max_is_allowed(compiler):
    src = """
    pub fn main() i32 {
        let x = 127i8;
        return 0;
    }
    """
    compiler.check(src)


def test_int_lit_overflow_signed(compiler):
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
        compiler.compile(src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    harness.assert_span_at(span, src, "128i8")


def test_comptime_int_lit_overflow_signed(compiler):
    src = """
    let x = 128i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        compiler.compile(src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    harness.assert_span_at(span, src, "128i8")


def test_int_lit_infers_declared_let_typ(compiler):
    # 10 is a u8 here, not an i32 that coerces to one - i32 -> u8 is a
    # narrowing conversion the language rejects.
    src = """
    pub fn main() i32 {
        let x: u8 = 10;
        let y = x + 200u8;
        return if (y == 210u8) { 7 } else { 0 };
    }
    """
    compiler.check(src, exit_status=7)


@pytest.mark.parametrize(
    "prelude,expr",
    (
        # Every context with a single unambiguous target type.
        ("fn takes(v: u8) u8 { return v; }", "takes(200)"),
        ("struct T { a: u8 }", "T { a: 200 }.a"),
        ("fn first(a: array[u8, 2]) u8 { return a.[0]; }", "first(array[u8, 2]{200, 1})"),
        ("fn ret() u8 { return 200; }", "ret()"),
        ("fn tail() u8 { 200 }", "tail()"),
        ("fn cond() u8 { if (true) { 200 } else { 1 } }", "cond()"),
    ),
)
def test_int_lit_infers_at_coercion_points(compiler, prelude, expr):
    # 200 doesn't fit i8 and i32 doesn't coerce to u8, so each of these
    # only compiles if the literal is inferred as u8 in the first place.
    src = f"""
    {prelude}
    pub fn main() i32 {{
        let x: u8 = {expr};
        return if (x == 200u8) {{ 7 }} else {{ 0 }};
    }}
    """
    compiler.check(src, exit_status=7)


def test_int_lit_infers_in_assignment(compiler):
    src = """
    pub fn main() i32 {
        let mut x = 0u8;
        x = 200;
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    compiler.check(src, exit_status=7)


def test_comptime_int_lit_infers_declared_typ(compiler):
    src = """
    let x: u8 = 200;
    pub fn main() i32 {
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    compiler.check(src, exit_status=7)


def test_int_lit_inference_reaches_operands(compiler):
    # Neither operand's type is decided by the operand itself, so both
    # take the declared type of the variable they end up in.
    src = """
    pub fn main() i32 {
        let x: u8 = 200 + 55;
        return if (x == 255u8) { 7 } else { 0 };
    }
    """
    compiler.check(src, exit_status=7)


def test_int_lit_inference_does_not_reach_across_a_typed_operand(compiler):
    # An operand whose type *is* decided still has to match its peer
    # exactly: nothing coerces i32 to u8, so this stays an error.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32 + 1;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleLetTypError):
        compiler.compile(src)


def test_explicit_int_lit_suffix_beats_inference(compiler):
    # A written suffix fixes the type, so this is an i32 -> u8 narrowing.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleLetTypError):
        compiler.compile(src)


def test_int_lit_too_big_for_inferred_typ(compiler):
    src = """
    pub fn main() i32 {
        let x: u8 = 300;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        compiler.compile(src)

    msg = str(exc_info.value)
    assert "300" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    harness.assert_span_at(span, src, "300")


@pytest.mark.parametrize(
    "src,expected",
    (
        ("*i32", "*i32"),
        ("*mut i32", "*mut i32"),
        ("**mut u8", "**mut u8"),
        ("array[*mut i32, 2]", "array[*mut i32, 2]"),
    ),
)
def test_ptr_typ_name_matches_source_syntax(src, expected):
    # Type names appear in diagnostics, so they should read the way the
    # type would be written.
    file = leech_src.SrcFile(pathlib.Path("test.leech"))
    tree = parse.build_parser("typ").parse(src)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx)
    env.add_container("array", typs.ARRAY_TEMPLATE)
    typ = typs.Typ.from_ast(ast.Typ.from_tree(file, tree), env)
    assert typ.name == expected


def test_structural_typs_are_interned_by_their_arguments():
    assert typs.IntTyp(32, signage.SIGNED) is typs.I32
    assert typs.PtrTyp(typs.I32, typs.MUT) is typs.PtrTyp(typs.I32, typs.MUT)
    assert typs.PtrTyp(typs.I32, typs.MUT) is not typs.PtrTyp(typs.I32, typs.CONST)
    assert typs.FnTyp(typs.VOID, (typs.I32,)) is typs.FnTyp(typs.VOID, (typs.I32,))
    assert typs.ArrayTyp.of_length(typs.U8, 2) is typs.ArrayTyp.of_length(typs.U8, 2)


def test_interned_typ_rejects_a_default_argument():
    with pytest.raises(AssertionError, match="no default arguments"):

        class _Defaulted(typs.InternedTyp):
            def __init__(self, width: int = 0) -> None:
                self.width = width


def test_fn_comptime_params_are_built_once(compiler):
    mod = compiler.build("pub fn f[T, value N: usize](x: T) T { return x; }")
    fn = asserts.checked_cast(
        opt_util.opt_unwrap(mod.get_item(ir_env.Env.Namespace.VARS, "f")).value,
        ir_module.SrcFnSymbol,
    )

    t, n = fn.comptime_params
    assert fn.env.get(ir_env.Env.Namespace.CONTAINERS, "T") is t
    assert fn.env.get(ir_env.Env.Namespace.CONTAINERS, "N") is n
    declared = [param for param in mod.ctx.declared_comptime_params() if param.owner is fn.ast]
    assert declared == [t, n]


def test_compilations_sharing_a_bundled_ast_have_their_own_comptime_params(compiler):
    src = """
    import std::mem;
    pub fn main() i32 { mem::dealloc[i32](mem::alloc[i32]()); return 0; }
    """
    first = compiler.build(src)
    second = compiler.build(src)

    def alloc_param(mod: ir_module.Mod) -> typs.ComptimeParamTyp:
        (mem,) = [m for m in mod.ctx.loader.mods if m.name == "std::mem"]
        alloc = opt_util.opt_unwrap(mem.get_item(ir_env.Env.Namespace.VARS, "alloc")).value
        (param,) = asserts.checked_cast(alloc, ir_module.SrcFnSymbol).comptime_params
        return param

    first_param = alloc_param(first)
    second_param = alloc_param(second)
    assert first_param.owner is second_param.owner
    assert first_param is not second_param
    assert first_param.ctx is first.ctx
    assert second_param.ctx is second.ctx


def test_compilations_sharing_an_enum_ast_have_their_own_enum(compiler):
    (enum_ast,) = compiler.parse("enum E { A }").defns
    assert isinstance(enum_ast, ast.EnumDefn)
    first_ctx = compilation.Ctx()
    second_ctx = compilation.Ctx()

    first = typs.EnumTyp(enum_ast, ir_env.Env(first_ctx), "main")
    second = typs.EnumTyp(enum_ast, ir_env.Env(second_ctx), "main")

    assert first is not second
    assert first.ctx is first_ctx
    assert second.ctx is second_ctx


def test_a_failing_comptime_param_check_is_reported_once(compiler):
    (fn_ast,) = compiler.parse("fn f[value N: Missing]() {}").defns
    assert isinstance(fn_ast, ast.FnDefn)
    ctx = compilation.Ctx()
    (param,) = typs.comptime_params_from_ast(fn_ast, fn_ast.comptime_params, ir_env.Env(ctx))

    proofs = []
    for _ in range(2):
        with pytest.raises(diag.ReportedError) as exc_info:
            param.check_declaration()
        proofs.append(exc_info.value.reported)

    assert proofs[0] is proofs[1]
    assert [type(d) for d in ctx.diags.all()] == [errors.ItemNotFoundError]


def test_struct_templates_and_instances_are_isolated_by_compilation_ctx(compiler):
    parsed_mod = compiler.parse("struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    first_ctx = compilation.Ctx()
    second_ctx = compilation.Ctx()
    first_env = ir_env.Env(first_ctx)
    second_env = ir_env.Env(second_ctx)

    first = typs.StructTypTemplate(struct_ast, first_env, "main")
    second = typs.StructTypTemplate(struct_ast, second_env, "main")
    first_instance = first.instantiate((typs.I32,))
    second_instance = second.instantiate((typs.I32,))

    assert first is not second
    assert first_instance is first.instantiate((typs.I32,))
    assert first_instance is not second_instance
    assert first_instance.template is first
    assert first_instance.comptime_args == (typs.I32,)
    assert tuple(first_ctx.requested_struct_instances()) == (first_instance,)
    assert tuple(second_ctx.requested_struct_instances()) == (second_instance,)


def test_struct_validation_instance_is_cached_without_request(compiler):
    parsed_mod = compiler.parse("struct Box[T] { val: T }")
    (struct_ast,) = parsed_mod.defns
    assert isinstance(struct_ast, ast.StructDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx)
    template = typs.StructTypTemplate(struct_ast, env, "main")

    validation = template._validation_instance

    assert validation is template._validation_instance
    assert validation.template is template
    assert validation.comptime_args == template.comptime_params
    assert tuple(ctx.requested_struct_instances()) == ()


def test_substitute_typ_params_replaces_mapped_typ_param(compiler):
    mod = compiler.parse("fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)

    assert t.substitute_typ_params({t: typs.I32}) is typs.I32


def test_substitute_typ_params_leaves_unmapped_typ_param_unchanged(compiler):
    mod = compiler.parse("fn f[T, U](x: T, y: U) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)
    u = typs.TypParamTyp(fn, fn.comptime_params[1].ident.name)

    assert t.substitute_typ_params({u: typs.I32}) is t


def test_substitute_typ_params_leaves_concrete_typ_unchanged():
    assert typs.I32.substitute_typ_params({}) is typs.I32
    assert typs.BOOL.substitute_typ_params({}) is typs.BOOL


def test_substitute_typ_params_recurses_through_composite_typs(compiler):
    mod = compiler.parse("fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)
    mapping: dict[typs.ComptimeParamTyp, typs.Typ] = {t: typs.I32}

    assert typs.PtrTyp(t, typs.MUT).substitute_typ_params(mapping) is typs.PtrTyp(
        typs.I32, typs.MUT
    )
    assert typs.ArrayTyp.of_length(t, 3).substitute_typ_params(mapping) is typs.ArrayTyp.of_length(
        typs.I32, 3
    )

    fn_typ = typs.FnTyp(t, (t, typs.BOOL))
    assert fn_typ.substitute_typ_params(mapping) is typs.FnTyp(typs.I32, (typs.I32, typs.BOOL))


def _assert_typs_overlap_symmetric(left: typs.Typ, right: typs.Typ, expected: bool) -> None:
    assert typs.typs_overlap(left, right) is expected
    assert typs.typs_overlap(right, left) is expected


def test_contains_typ_finds_structural_occurrences() -> None:
    ptr = typs.PtrTyp(typs.I32, typs.CONST)
    array = typs.ArrayTyp.of_length(ptr, 2)

    assert typs.contains_typ(array, array)
    assert typs.contains_typ(array, ptr)
    assert typs.contains_typ(array, typs.I32)
    assert not typs.contains_typ(array, typs.BOOL)


def test_typs_overlap_fn_typs_symmetrically(compiler):
    mod = compiler.parse("fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)

    generic = typs.FnTyp(typs.BOOL, (t,))
    _assert_typs_overlap_symmetric(generic, typs.FnTyp(typs.BOOL, (typs.I32,)), True)
    _assert_typs_overlap_symmetric(generic, typs.FnTyp(typs.I32, (typs.I32,)), False)
    _assert_typs_overlap_symmetric(generic, typs.FnTyp(typs.BOOL, (typs.I32, typs.I32)), False)


def test_typs_overlap_ptr_typs_symmetrically_and_distinguishes_mutability(compiler):
    mod = compiler.parse("fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)

    generic = typs.PtrTyp(t, typs.CONST)
    _assert_typs_overlap_symmetric(generic, typs.PtrTyp(typs.I32, typs.CONST), True)
    _assert_typs_overlap_symmetric(generic, typs.PtrTyp(typs.I32, typs.MUT), False)


def test_typs_overlap_array_typs_symmetrically_and_distinguishes_length(compiler):
    mod = compiler.parse("fn f[T](x: T) {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)

    generic = typs.ArrayTyp.of_length(t, 3)
    _assert_typs_overlap_symmetric(generic, typs.ArrayTyp.of_length(typs.I32, 3), True)
    _assert_typs_overlap_symmetric(generic, typs.ArrayTyp.of_length(typs.I32, 4), False)


def test_typs_overlap_struct_typs_symmetrically_and_distinguishes_declaration(compiler):
    mod = compiler.build("struct Pair[A, B] {}\nstruct Other[A, B] {}")
    pair = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Pair")
    other = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Other")
    assert isinstance(pair, typs.StructTypTemplate)
    assert isinstance(other, typs.StructTypTemplate)
    t = pair.comptime_params[0]

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


def test_typs_overlap_enum_backing_typs_symmetrically(compiler):
    mod = compiler.build("enum E(u8) { A } enum F(u8) { A }")
    enum_e = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "E")
    enum_f = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "F")
    assert isinstance(enum_e, typs.EnumTyp)
    assert isinstance(enum_f, typs.EnumTyp)

    parsed = compiler.parse("fn f[T](x: T) {}")
    (fn,) = parsed.defns
    assert isinstance(fn, ast.FnDefn)
    t = typs.TypParamTyp(fn, fn.comptime_params[0].ident.name)

    generic = typs.EnumBackingTyp(t)
    _assert_typs_overlap_symmetric(generic, typs.EnumBackingTyp(enum_e), True)
    _assert_typs_overlap_symmetric(
        typs.EnumBackingTyp(enum_e),
        typs.EnumBackingTyp(enum_f),
        False,
    )


def test_int_typ_name_with_unparseable_width_is_not_a_typ(compiler):
    # The width exceeds CPython's int-from-string digit limit; resolving it
    # must diagnose an unknown type rather than crash.
    name = "i" + "9" * 5000
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(f"pub fn main() i32 {{ let x: {name} = 5; return 0; }}")


def test_comptime_value_typ_interns_equal_values():
    a = typs.ComptimeValueTyp(typs.USIZE, 4)
    b = typs.ComptimeValueTyp(typs.USIZE, 4)
    assert a is b


def test_comptime_value_typ_distinguishes_by_typ_and_value():
    four_usize = typs.ComptimeValueTyp(typs.USIZE, 4)
    five_usize = typs.ComptimeValueTyp(typs.USIZE, 5)
    four_u32 = typs.ComptimeValueTyp(typs.U32, 4)
    assert four_usize is not five_usize
    assert four_usize is not four_u32


def test_comptime_value_typ_bool_and_int_never_alias():
    true_val = typs.ComptimeValueTyp(typs.BOOL, True)
    one_val = typs.ComptimeValueTyp(typs.U32, 1)
    assert true_val is not one_val


def test_comptime_value_typ_name_is_lowercase():
    assert typs.ComptimeValueTyp(typs.USIZE, 4).name == "4"
    assert typs.ComptimeValueTyp(typs.BOOL, True).name == "true"
    assert typs.ComptimeValueTyp(typs.BOOL, False).name == "false"


def test_comptime_value_typ_is_concrete_and_self_substitutes():
    v = typs.ComptimeValueTyp(typs.USIZE, 4)
    assert v.is_concrete()
    assert v.substitute_typ_params({}) is v


def test_checked_value_returns_the_python_value():
    assert typs.ComptimeValueTyp.checked_value(typs.ComptimeValueTyp(typs.USIZE, 4)) == 4
    assert typs.ComptimeValueTyp.checked_value(typs.ComptimeValueTyp(typs.BOOL, True)) is True


def test_checked_value_rejects_a_non_comptime_value_typ():
    with pytest.raises(AssertionError):
        typs.ComptimeValueTyp.checked_value(typs.I32)


def test_value_param_typ_with_a_declared_typ(compiler):
    mod = compiler.parse("fn f() {}")
    (fn,) = mod.defns
    p = typs.ValueParamTyp(fn, "N", typs.USIZE)
    assert p.name == "N"
    assert p.value_typ is typs.USIZE
    assert not p.is_concrete()


def test_value_param_typ_substitutes_to_comptime_value_typ(compiler):
    mod = compiler.parse("fn f() {}")
    (fn,) = mod.defns
    p = typs.ValueParamTyp(fn, "N", typs.USIZE)
    four = typs.ComptimeValueTyp(typs.USIZE, 4)
    assert p.substitute_typ_params({p: four}) is four


def test_array_typ_length_value_returns_the_python_int():
    assert typs.ArrayTyp.of_length(typs.I32, 4).length_value == 4


def test_array_typ_length_value_rejects_a_non_usize_length():
    non_usize_length = typs.ComptimeValueTyp(typs.U32, 4)
    with pytest.raises(AssertionError):
        _ = typs.ArrayTyp(typs.I32, non_usize_length).length_value


def test_array_typ_of_length_wraps_comptime_value_typ():
    length = typs.ComptimeValueTyp(typs.USIZE, 3)
    assert typs.ArrayTyp.of_length(typs.I32, 3) is typs.ArrayTyp(typs.I32, length)


def test_array_typ_length_substitutes_value_param(compiler):
    mod = compiler.parse("fn f[value N: usize]() {}")
    (fn,) = mod.defns
    n = typs.ValueParamTyp(fn, "N", typs.USIZE)
    four = typs.ComptimeValueTyp(typs.USIZE, 4)
    symbolic = typs.ArrayTyp(typs.I32, n)

    assert not symbolic.is_concrete()
    assert symbolic.substitute_typ_params({n: four}) is typs.ArrayTyp.of_length(typs.I32, 4)


def test_array_typ_infers_symbolic_length_from_actual(compiler):
    mod = compiler.parse("fn f[value N: usize]() {}")
    (fn,) = mod.defns
    n = typs.ValueParamTyp(fn, "N", typs.USIZE)
    declared = typs.ArrayTyp(typs.I32, n)
    actual = typs.ArrayTyp.of_length(typs.I32, 4)

    bindings: dict[typs.ComptimeParamTyp, typs.Typ] = {}
    declared.infer_typ_args(actual, bindings)
    assert bindings[n] is typs.ComptimeValueTyp(typs.USIZE, 4)


def test_typs_overlap_treats_array_length_as_unifiable(compiler):
    mod = compiler.parse("fn f[value N: usize]() {}")
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    n = typs.ValueParamTyp(fn, "N", typs.USIZE)

    symbolic = typs.ArrayTyp(typs.I32, n)
    # The symbolic length binds to any concrete length, via the same
    # bind() path a ValueParamTyp element typ would take.
    _assert_typs_overlap_symmetric(symbolic, typs.ArrayTyp.of_length(typs.I32, 3), True)
    # Two concrete, unequal lengths correctly fail to unify.
    _assert_typs_overlap_symmetric(
        typs.ArrayTyp.of_length(typs.I32, 3), typs.ArrayTyp.of_length(typs.I32, 4), False
    )
