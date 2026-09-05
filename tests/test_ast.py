# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import util

from leech import ast


def test_impl_defn_basic_typ(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        pub fn new() Foo { Foo {} }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    assert isinstance(impl.typ, ast.BasicTyp)
    assert impl.typ.path.str() == "Foo"

    assert len(impl.fn_defns) == 1
    fn = impl.fn_defns[0]
    assert fn.name.name == "new"
    assert fn.access is not None
    assert fn.access.value == "pub"


def test_impl_defn_ptr_typ(tmp_path):
    src = """
    struct Foo {}
    impl *Foo {
        fn helper() i32 { 1 }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    assert isinstance(impl.typ, ast.PtrTyp)
    assert isinstance(impl.typ.pointee_typ, ast.BasicTyp)
    assert impl.typ.pointee_typ.path.str() == "Foo"

    assert len(impl.fn_defns) == 1
    assert impl.fn_defns[0].name.name == "helper"


def test_impl_defn_array_typ(tmp_path):
    src = """
    struct Foo {}
    impl array[Foo, 3] {
        fn helper() i32 { 1 }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    assert isinstance(impl.typ, ast.BasicTyp)
    assert impl.typ.path.str() == "array[Foo, 3]"
    elt_typ, length = impl.typ.path.segs[-1].comptime_args
    assert isinstance(elt_typ, ast.BasicTyp)
    assert elt_typ.path.str() == "Foo"
    assert isinstance(length, ast.IntLit)
    assert length.value == 3


def test_impl_defn_empty(tmp_path):
    src = """
    struct Foo {}
    impl Foo {}
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)
    assert impl.fn_defns == ()


def test_fn_defn_generic_params(tmp_path):
    src = """
    fn id[T: Show](x: T) T { return x; }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    (param,) = fn.comptime_params
    assert isinstance(param, ast.TypParam)
    assert param.ident.name == "T"
    assert [b.path.str() for b in param.bounds] == ["Show"]


def test_fn_defn_value_param(tmp_path):
    src = """
    fn f[T, value N: usize]() {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    typ_param, value_param = fn.comptime_params
    assert isinstance(typ_param, ast.TypParam)
    assert isinstance(value_param, ast.ValueParam)
    assert value_param.ident.name == "N"
    assert isinstance(value_param.typ, ast.BasicTyp)
    assert value_param.typ.path.str() == "usize"


def test_fn_defn_no_generic_params(tmp_path):
    src = """
    fn f() {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    assert fn.comptime_params == ()


def test_basic_typ_generic_args(tmp_path):
    src = """
    fn f(x: Pair[i32, bool]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    arg_names = [
        t.path.str() for t in param.typ.path.segs[-1].comptime_args if isinstance(t, ast.BasicTyp)
    ]
    assert arg_names == ["i32", "bool"]


def test_basic_typ_comptime_args_accepts_int_lit(tmp_path):
    src = """
    fn f(x: Buf[i32, 4]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    _, int_arg = param.typ.path.segs[-1].comptime_args
    assert isinstance(int_arg, ast.IntLit)
    assert int_arg.value == 4


def test_array_length_path_is_populated_for_identifier(tmp_path):
    src = """
    fn f[N](x: array[i32, N]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    assert param.typ.path.str() == "array[i32, N]"
    _, length = param.typ.path.segs[-1].comptime_args
    assert isinstance(length, ast.BasicTyp)
    assert length.path.str() == "N"


def test_array_length_value_is_populated_for_literal(tmp_path):
    src = """
    fn f(x: array[i32, 4]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    assert param.typ.path.str() == "array[i32, 4]"
    _, length = param.typ.path.segs[-1].comptime_args
    assert isinstance(length, ast.IntLit)
    assert length.value == 4


def test_var_expr_generic_args(tmp_path):
    src = """
    fn f() { g[i32](); }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    stmt = fn.block.stmts[0]
    assert isinstance(stmt, ast.ExprStmt)
    call = stmt.expr
    assert isinstance(call, ast.CallExpr)
    assert isinstance(call.callee, ast.VarExpr)
    (arg,) = call.callee.path.segs[-1].comptime_args
    assert isinstance(arg, ast.BasicTyp)
    assert arg.path.str() == "i32"


def test_comptime_args_are_stored_on_path_segs(tmp_path):
    src = """
    fn f(x: pkg::Outer[i32]::Inner[bool]) {
        pkg::Outer[i32]::make[bool];
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    assert [seg.ident.name for seg in param.typ.path.segs] == [
        "pkg",
        "Outer",
        "Inner",
    ]
    assert [len(seg.comptime_args) for seg in param.typ.path.segs] == [0, 1, 1]
    assert param.typ.path.str() == "pkg::Outer[i32]::Inner[bool]"

    stmt = fn.block.stmts[0]
    assert isinstance(stmt, ast.ExprStmt)
    assert isinstance(stmt.expr, ast.VarExpr)
    assert [len(seg.comptime_args) for seg in stmt.expr.path.segs] == [0, 1, 1]
    assert stmt.expr.path.str() == "pkg::Outer[i32]::make[bool]"


def test_path_str_renders_nested_comptime_args(tmp_path):
    src = """
    fn f(x: pkg::Outer[array[*mut i32, 4], true]::Inner[false]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)

    assert param.typ.path.str() == "pkg::Outer[array[*mut i32, 4], true]::Inner[false]"


def test_import_single_seg_path(tmp_path):
    src = """
    import xyz;
    """
    mod = util.parse_mod(tmp_path, src)
    (imp,) = mod.defns
    assert isinstance(imp, ast.Import)
    assert [seg.ident.name for seg in imp.path.segs] == ["xyz"]
    assert imp.path.str() == "xyz"


def test_import_multi_seg_path(tmp_path):
    src = """
    import std::mem;
    """
    mod = util.parse_mod(tmp_path, src)
    (imp,) = mod.defns
    assert isinstance(imp, ast.Import)
    assert [seg.ident.name for seg in imp.path.segs] == ["std", "mem"]
    assert imp.path.str() == "std::mem"


def test_impl_defn_multiple_fns(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        pub fn new() Foo { Foo {} }
        fn helper() i32 { 1 }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    names_and_access = [(fn.name.name, fn.access) for fn in impl.fn_defns]
    assert [name for name, _access in names_and_access] == ["new", "helper"]

    new_access, helper_access = (access for _name, access in names_and_access)
    assert new_access is not None and new_access.value == "pub"
    assert helper_access is None


def test_block_stmt_block_like_no_semicolon(tmp_path):
    src = """
    fn f() {
        if (true) { 1 }
        while (true) { 2 };
        3;
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    if_stmt, while_stmt, int_stmt = fn.block.stmts
    assert isinstance(if_stmt, ast.ExprStmt)
    assert isinstance(if_stmt.expr, ast.IfExpr)
    assert isinstance(while_stmt, ast.ExprStmt)
    assert isinstance(while_stmt.expr, ast.WhileExpr)
    assert isinstance(int_stmt, ast.ExprStmt)
    assert isinstance(int_stmt.expr, ast.IntLit)
    assert fn.block.expr is None


def test_match_expr_patterns(tmp_path):
    src = """
    fn f(x: i32) i32 {
        match (x) {
            _ => 0,
            let mut y => y,
            let z => z,
            -2 => 3,
            2 => 7,
            true => 4,
            false => 8,
            Color::Red => 5,
            Color::Green | Color::Blue | Color::Yellow => 6,
        }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    match_expr = fn.block.expr
    assert isinstance(match_expr, ast.MatchExpr)
    assert isinstance(match_expr.scrutinee, ast.VarExpr)
    assert match_expr.scrutinee.path.str() == "x"

    (
        wildcard_arm,
        mutable_binding_arm,
        immutable_binding_arm,
        negative_int_arm,
        positive_int_arm,
        true_arm,
        false_arm,
        path_arm,
        or_arm,
    ) = match_expr.arms
    assert isinstance(wildcard_arm.pattern, ast.WildcardPattern)
    assert isinstance(mutable_binding_arm.pattern, ast.BindingPattern)
    assert mutable_binding_arm.pattern.mut is not None
    assert mutable_binding_arm.pattern.ident.name == "y"
    assert isinstance(mutable_binding_arm.body, ast.VarExpr)
    assert isinstance(immutable_binding_arm.pattern, ast.BindingPattern)
    assert immutable_binding_arm.pattern.mut is None
    assert immutable_binding_arm.pattern.ident.name == "z"

    assert isinstance(negative_int_arm.pattern, ast.IntLitPattern)
    assert negative_int_arm.pattern.negative
    assert negative_int_arm.pattern.lit.value == 2
    assert isinstance(negative_int_arm.body, ast.IntLit)
    assert negative_int_arm.body.value == 3
    assert isinstance(positive_int_arm.pattern, ast.IntLitPattern)
    assert not positive_int_arm.pattern.negative
    assert positive_int_arm.pattern.lit.value == 2

    assert isinstance(true_arm.pattern, ast.BoolLitPattern)
    assert true_arm.pattern.lit.value
    assert isinstance(false_arm.pattern, ast.BoolLitPattern)
    assert not false_arm.pattern.lit.value
    assert isinstance(path_arm.pattern, ast.PathPattern)
    assert path_arm.pattern.path.str() == "Color::Red"

    assert isinstance(or_arm.pattern, ast.OrPattern)
    assert len(or_arm.pattern.alternatives) == 3
    assert all(isinstance(pattern, ast.PathPattern) for pattern in or_arm.pattern.alternatives)
    assert [
        pattern.path.str()
        for pattern in or_arm.pattern.alternatives
        if isinstance(pattern, ast.PathPattern)
    ] == ["Color::Green", "Color::Blue", "Color::Yellow"]


def test_match_expr_block_tail_and_statement_forms(tmp_path):
    src = """
    fn f(x: i32) i32 {
        match (x) {}
        match (x) { _ => 2, };
        match (x) { _ => 3, }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    no_semicolon_stmt, semicolon_stmt = fn.block.stmts
    assert isinstance(no_semicolon_stmt, ast.ExprStmt)
    assert isinstance(no_semicolon_stmt.expr, ast.MatchExpr)
    assert no_semicolon_stmt.expr.arms == ()
    assert isinstance(semicolon_stmt, ast.ExprStmt)
    assert isinstance(semicolon_stmt.expr, ast.MatchExpr)
    assert isinstance(fn.block.expr, ast.MatchExpr)
