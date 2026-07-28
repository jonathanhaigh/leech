from leech import ast as ast
from util import parse_mod


def test_impl_defn_basic_typ(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        pub fn new() Foo { Foo {} }
    }
    """
    mod = parse_mod(tmp_path, src)
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
    mod = parse_mod(tmp_path, src)
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
    mod = parse_mod(tmp_path, src)
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
    mod = parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)
    assert impl.fns == []


def test_impl_defn_multiple_fns(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        pub fn new() Foo { Foo {} }
        fn helper() i32 { 1 }
    }
    """
    mod = parse_mod(tmp_path, src)
    (_struct, impl) = mod.defns
    assert isinstance(impl, ast.ImplDefn)

    names_and_access = [(fn.name.name, fn.access) for fn in impl.fns]
    assert [name for name, _access in names_and_access] == ["new", "helper"]

    new_access, helper_access = (access for _name, access in names_and_access)
    assert new_access is not None and new_access.value == "pub"
    assert helper_access is None
