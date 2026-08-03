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

    assert len(impl.fns) == 1
    fn = impl.fns[0]
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

    assert len(impl.fns) == 1
    assert impl.fns[0].name.name == "helper"


def test_impl_defn_array_typ(tmp_path):
    src = """
    struct Foo {}
    impl [Foo; 3] {
        fn helper() i32 { 1 }
    }
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    assert isinstance(impl.typ, ast.ArrayTyp)
    assert isinstance(impl.typ.element_typ, ast.BasicTyp)
    assert impl.typ.element_typ.path.str() == "Foo"
    assert impl.typ.length.value == 3


def test_impl_defn_empty(tmp_path):
    src = """
    struct Foo {}
    impl Foo {}
    """
    mod = util.parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)
    assert impl.fns == ()


def test_fn_defn_generic_params(tmp_path):
    src = """
    fn id[T: Show](x: T) T { return x; }
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    (param,) = fn.generic_params
    assert param.ident.name == "T"
    assert [b.str() for b in param.bounds] == ["Show"]


def test_fn_defn_no_generic_params(tmp_path):
    src = """
    fn f() {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)
    assert fn.generic_params == ()


def test_basic_typ_generic_args(tmp_path):
    src = """
    fn f(x: Pair[i32, bool]) {}
    """
    mod = util.parse_mod(tmp_path, src)
    (fn,) = mod.defns
    assert isinstance(fn, ast.FnDefn)

    (param,) = fn.params
    assert isinstance(param.typ, ast.BasicTyp)
    arg_names = [t.path.str() for t in param.typ.generic_args if isinstance(t, ast.BasicTyp)]
    assert arg_names == ["i32", "bool"]


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
    (arg,) = call.callee.generic_args
    assert isinstance(arg, ast.BasicTyp)
    assert arg.path.str() == "i32"


def test_import_single_segment_path(tmp_path):
    src = """
    import xyz;
    """
    mod = util.parse_mod(tmp_path, src)
    (imp,) = mod.defns
    assert isinstance(imp, ast.Import)
    assert [ident.name for ident in imp.path.idents] == ["xyz"]
    assert imp.path.str() == "xyz"


def test_import_multi_segment_path(tmp_path):
    src = """
    import std::mem;
    """
    mod = util.parse_mod(tmp_path, src)
    (imp,) = mod.defns
    assert isinstance(imp, ast.Import)
    assert [ident.name for ident in imp.path.idents] == ["std", "mem"]
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

    names_and_access = [(fn.name.name, fn.access) for fn in impl.fns]
    assert [name for name, _access in names_and_access] == ["new", "helper"]

    new_access, helper_access = (access for _name, access in names_and_access)
    assert new_access is not None and new_access.value == "pub"
    assert helper_access is None
