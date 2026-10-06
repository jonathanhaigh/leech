# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors, mono
from tests import harness


def test_import_of_module_with_syntax_error(compiler):
    # Parsing happens per-module (main.compile_to_ir recurses for each
    # `import`), so a syntax error in an imported module must be caught
    # and reported the same way as one in the top-level file.
    main_src = """
    import a;
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub fn f() i32
    """
    with pytest.raises(errors.UnexpectedTokenError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_fn(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f("abc");
    }
    """
    a_src = """
    extern fn puts(s: *u8) i32;

    pub fn f(s: *u8) i32 {
        puts(s);
        return 101;
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, stdout="abc\n", exit_status=101)


def test_imported_non_generic_fn_is_a_monomorphization_leaf(compiler):
    main_src = """
    import a;
    pub fn main() i32 { return a::f(); }
    """
    a_src = """
    fn id[T](x: T) T { x }
    pub fn f() i32 { return id[i32](7); }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    mod = compiler.build(program)

    result = mono.discover(mod)

    assert {inst.qualified_name for inst in result.imported_fn_instances} == {"a::f"}
    assert all(inst.qualified_name != "a::f" for inst in result.fn_instances)
    assert all(inst.qualified_name != "a::id[i32]" for inst in result.fn_instances)


def test_unused_imported_extern_keeps_bare_declaration(compiler):
    main_src = "import a;\npub fn main() i32 { 0 }"
    a_src = "extern fn unused(val: i32) i32;"

    compiled = compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    ir_text = compiled.mods["main"].llvm_ir

    declaration = next(line for line in ir_text.splitlines() if '"unused"' in line)
    assert declaration.startswith('declare i32 @"unused"')
    assert not declaration.startswith("define")
    assert "linkonce_odr" not in declaration
    assert "a::unused" not in declaration


def test_uncalled_private_fn_in_imported_module_is_typechecked(compiler):
    main_src = """
    import helper;
    pub fn main() i32 { return helper::ok() - 1; }
    """
    helper_src = """
    pub fn ok() i32 { return 1; }
    fn invalid() i32 { return true; }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("helper", helper_src))

    with pytest.raises(errors.InvalidRetTypError):
        compiler.compile(program)


def test_imported_unreachable_body_can_request_unused_struct_instance(compiler):
    main_src = """
    import a;
    pub fn main() i32 { return a::ok() - 1; }
    """
    a_src = """
    pub struct Widget[T] { val: T }
    pub fn ok() i32 { return 1; }
    fn private_uses_widget() i32 {
        let w = Widget[bool] { val: true };
        if (w.val) { return 2; };
        return 3;
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))

    ir_text = compiler.compile(program).mods["main"].llvm_ir

    assert '%"a::Widget[bool]" = type' in ir_text


def test_comptime_import_fn(compiler):
    main_src = """
    import a;
    let x = a::f();
    pub fn main() i32 {
        return x;
    }
    """
    a_src = """
    pub fn f() i32 {
        return 101;
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=101)


def test_import_private_fn(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f("abc");
    }
    """
    a_src = """
    extern fn puts(s: *u8) i32;

    fn f(s: *u8) i32 {
        puts(s);
        return 101;
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_private_fn_use_comptime(compiler):
    main_src = """
    import a;
    let y = a::f();
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    fn f() i32 {
        return 101;
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_var(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    pub let x = 11;
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=11)


def test_import_var_use_comptime(compiler):
    main_src = """
    import a;
    let y = a::x;
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    pub let x = 11;
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=11)


def test_import_private_var(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_private_var_use_comptime(compiler):
    main_src = """
    import a;
    let y = a::x;
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_typ(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{int: 32};
        return x.int;
    }
    """
    a_src = """
    pub struct T {
        pub int: i32,
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=32)


def test_import_typ_use_comptime(compiler):
    main_src = """
    import a;
    let x = a::T{int: 32};
    pub fn main() i32 {
        return x.int;
    }
    """
    a_src = """
    pub struct T {
        pub int: i32,
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=32)


def test_import_private_typ(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{int: 32};
        return x.int;
    }
    """
    a_src = """
    struct T {
        int: i32,
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_private_typ_use_comptime(compiler):
    main_src = """
    import a;
    let x = a::T{int: 32};
    pub fn main() i32 {
        return x.int;
    }
    """
    a_src = """
    struct T {
        int: i32,
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_typ_with_var_of_same_name(compiler):
    # A module can hold a variable and a type of the same name, since they
    # live in separate namespaces. Resolving a::T in a type position must
    # find the struct, not the variable that happens to be declared first.
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{v: 32};
        return x.v;
    }
    """
    a_src = """
    pub let T = 5;

    pub struct T {
        pub v: i32,
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=32)


def test_import_var_with_typ_of_same_name(compiler):
    # The mirror image of test_import_typ_with_var_of_same_name: a::T in a
    # value position must find the variable, not the struct type.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::T;
    }
    """
    a_src = """
    pub struct T {
        pub v: i32,
    }

    pub let T = 5;
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=5)


def test_import_var_with_private_typ_of_same_name(compiler):
    # The access check has to be made against the item in the namespace
    # being resolved: a private type doesn't make a public variable of the
    # same name inaccessible.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::T;
    }
    """
    a_src = """
    struct T {
        v: i32,
    }

    pub let T = 5;
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=5)


def test_import_private_struct_field_construct(compiler):
    # T itself is public, but priv_val isn't, so constructing a T from
    # outside a's module can't set it, even though it names a real field.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::T{pub_val: 1, priv_val: 2};
        return 0;
    }
    """
    a_src = """
    pub struct T {
        pub pub_val: i32,
        priv_val: i32,
    }
    """
    with pytest.raises(errors.PrivateStructFieldAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_comptime_import_private_struct_field_construct(compiler):
    main_src = """
    import a;
    let t = a::T{priv_val: 2};
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }
    """
    with pytest.raises(errors.PrivateStructFieldAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_private_struct_field_read(compiler):
    # a::make() legitimately returns a T (constructed from within a, where
    # priv_val is accessible), but main still can't read priv_val off it.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::make();
        return t.priv_val;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }

    pub fn make() T {
        return T { priv_val: 1 };
    }
    """
    with pytest.raises(errors.PrivateStructFieldAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_private_struct_field_write(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        let mut t = a::make();
        t.priv_val = 5;
        return 0;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }

    pub fn make() T {
        return T { priv_val: 1 };
    }
    """
    with pytest.raises(errors.PrivateStructFieldAccessError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_private_struct_field_accessible_via_assoc_fn_in_defining_module(compiler):
    # main can't touch T::val directly, but can go through T's own public
    # assoc fns, which - being defined in the same module as T - can.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::T::make(42);
        return a::T::get(t);
    }
    """
    a_src = """
    pub struct T {
        val: i32,
    }

    impl T {
        pub fn make(v: i32) T {
            return T { val: v };
        }

        pub fn get(t: T) i32 {
            return t.val;
        }
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=42)


def test_mod_does_not_exist(compiler):
    main_src = """
    import nope;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.ModDoesNotExistError):
        compiler.compile(main_src)


def test_duplicate_import(compiler):
    # Importing the same module twice binds the same name twice, which is
    # a duplicate definition like any other - not a crash.
    main_src = """
    import a;
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


@pytest.mark.parametrize(
    "defn",
    (
        "fn g(p: a) i32 { return 0; }",
        "fn g() a { return 0; }",
        "struct S { x: a }",
        "fn g(p: *a) i32 { return 0; }",
        "fn g(p: array[a, 2]) i32 { return 0; }",
        "impl a { }",
        "fn g() i32 { let x = a { }; return 0; }",
    ),
)
def test_mod_used_as_typ(compiler, defn):
    # A module shares a namespace with types but isn't one, so naming it
    # where a type is required is an error rather than something that
    # slips through to crash the compiler later.
    main_src = f"""
    import a;
    {defn}
    pub fn main() i32 {{
        return 0;
    }}
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(errors.ModUsedAsTypError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_and_struct_same_name(compiler):
    # Modules and types share one namespace, so an import and a struct
    # can't have the same name.
    main_src = """
    import a;
    struct a { x: i32 }
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))


def test_import_and_var_same_name(compiler):
    # Variables are in the other namespace, though, so an import and a
    # variable - or a function - may share a name.
    main_src = """
    import a;
    let a = 11;
    fn f() i32 { return a; }
    pub fn main() i32 {
        return f() + a::f();
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=12)


def test_import_chain(compiler):
    # main -> a -> b -> c: each module is reached only through the one
    # above it, so the whole chain has to be loaded transitively.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import b;
    pub fn viaa() i32 {
        return b::viab() + 1;
    }
    """
    b_src = """
    import c;
    pub fn viab() i32 {
        return c::base() + 10;
    }
    """
    c_src = """
    pub fn base() i32 {
        return 100;
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("b", b_src), harness.ModSrc("c", c_src)
    )
    compiler.check(program, exit_status=111)


def test_diamond_import(compiler):
    # main and a both import c, so c is reached by two paths. Its Foo must
    # be one type across both, or the value a::wrap() returns wouldn't be
    # usable as the c::Foo that main's own import names.
    main_src = """
    import c;
    import a;
    pub fn main() i32 {
        let f = a::wrap(7);
        let g = c::mk(35);
        return f.v + g.v;
    }
    """
    a_src = """
    import c;
    pub fn wrap(n: i32) c::Foo {
        return c::mk(n);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("c", c_src)
    )
    compiler.check(program, exit_status=42)


def test_diamond_import_reversed_order(compiler):
    # Same as test_diamond_import but importing a before c, which is the
    # order that used to leave c::Foo undeclared when a's signature
    # referring to it was declared first.
    main_src = """
    import a;
    import c;
    pub fn main() i32 {
        let f = a::wrap(7);
        let g = c::mk(35);
        return f.v + g.v;
    }
    """
    a_src = """
    import c;
    pub fn wrap(n: i32) c::Foo {
        return c::mk(n);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("c", c_src)
    )
    compiler.check(program, exit_status=42)


def test_import_of_typ_re_exported_by_imported_mod(compiler):
    # main never imports c itself; c::Foo reaches it only through a's
    # public signature, so c has to be lowered on a's behalf.
    main_src = """
    import a;
    pub fn main() i32 {
        let f = a::viaa();
        return f.v;
    }
    """
    a_src = """
    import c;
    pub fn viaa() c::Foo {
        return c::mk(42);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("c", c_src)
    )
    compiler.check(program, exit_status=42)


def test_import_of_var_re_exported_by_imported_mod(compiler):
    # A module variable reached only transitively still needs an imported
    # declaration in main's output for the link to resolve.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import c;
    pub fn viaa() i32 {
        return c::cvar;
    }
    """
    c_src = """
    pub let cvar = 30;
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("c", c_src)
    )
    compiler.check(program, exit_status=30)


def test_circular_import(compiler):
    # a and b import each other. Loading registers each module before
    # building it, so the cycle resolves to the module already under
    # construction instead of recursing forever.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    import b;
    pub fn f() i32 {
        return b::g() + 1;
    }

    pub fn only_from_b() i32 {
        return 5;
    }
    """
    b_src = """
    import a;
    pub fn g() i32 {
        return a::only_from_b() * 2;
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("b", b_src)
    )
    compiler.check(program, exit_status=11)


def test_circular_import_of_typ(compiler):
    # The cycle carries a struct type as well as functions, so the
    # partially-built module has to be usable for type resolution too.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f().v;
    }
    """
    a_src = """
    import b;
    pub struct Foo {
        pub v: i32,
    }

    pub fn f() Foo {
        return b::g();
    }
    """
    b_src = """
    import a;
    pub fn g() a::Foo {
        return a::Foo { v: 42 };
    }
    """
    program = harness.TestProgram.from_main(
        main_src, harness.ModSrc("a", a_src), harness.ModSrc("b", b_src)
    )
    compiler.check(program, exit_status=42)


def test_self_import(compiler):
    # Degenerate cycle: a module importing itself resolves to itself.
    main_src = """
    import main;
    pub fn main() i32 {
        return main::helper();
    }

    pub fn helper() i32 {
        return 7;
    }
    """
    compiler.check(main_src, exit_status=7)
