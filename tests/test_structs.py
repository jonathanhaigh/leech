# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors
from tests import harness, util


def test_struct_access(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;

    let a = array[i32, 4]{1, 2, 3, 4};

    struct T {
      mut a: i32,
      mut b: *u8,
      mut c: array[i32, 4],
    }

    pub fn main() i32 {
        let mut t = T { a: 10, b: "abc", c: a};
        puts(t.b);
        t.a = 9;
        return t.a + t.c.[3usize];
    }
    """
    util.check_prog_output(tmp_path, src, "abc\n", 13)


def test_empty_struct(tmp_path):
    src = """
    struct T {}
    pub fn main() i32 {
        let t = T {};
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_empty_struct(tmp_path):
    src = """
    struct T {}
    let t = T {};
    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_private_field_accessible_within_defining_module(tmp_path):
    # Private (the default - no `pub`) fields are freely readable,
    # writable, and settable from within the same module as the struct.
    src = """
    struct T {
        mut val: i32,
    }

    pub fn main() i32 {
        let mut t = T { val: 1 };
        t.val = 42;
        return t.val;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_duplicate_field_in_struct_defn(compiler):
    src = """
    struct T {
      a: i32,
      b: *u8,
      a: array[i32, 4],
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateFieldInStructDefnError):
        compiler.compile(src)


def test_unknown_typ_for_struct_field(compiler):
    src = """
    struct T {
      a: *xyz,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_struct_in_struct(tmp_path):
    src = """
    struct T {
      a: i32,
    }
    struct U {
      b: T,
    }
    pub fn main() i32 {
        let x = U{b: T{a: 100}};
        return x.b.a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 100)


def test_struct_with_ptr_to_same_struct(tmp_path):
    src = """
    struct T {
      a: i32,
      t: *T,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_struct_with_array_of_ptr_to_same_struct(tmp_path):
    # An array of pointers is fine: each element is a fixed-size pointer,
    # regardless of what it points to.
    src = """
    struct T {
      a: i32,
      ts: array[*T, 3],
    }
    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_duplicate_field_in_unused_private_struct_in_imported_module(compiler):
    # Field names are registered when the struct is built, so a duplicate is
    # caught even in a struct nothing ever uses.
    a_src = """
    struct T {
      x: i32,
      x: i32,
    }
    pub fn g() i32 { return 1; }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        return a::g();
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    with pytest.raises(errors.DuplicateFieldInStructDefnError):
        compiler.compile(program)


def test_struct_contains_itself_by_value(compiler):
    src = """
    struct T {
      t: T,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InfiniteSizeTypError):
        compiler.compile(src)


def test_duplicate_field_reported_before_infinite_size(compiler):
    # A duplicate field name wins over the infinite-size error: it's a
    # local, syntactic problem that needs no type resolution to diagnose,
    # whereas the infinite-size diagnostic names fields - which is exactly
    # what's ambiguous while a name is duplicated.
    src = """
    struct T {
      t: T,
      a: i32,
      a: i32,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateFieldInStructDefnError):
        compiler.compile(src)


def test_struct_contains_itself_via_zero_length_array(compiler):
    # Unlike Rust, a zero-length array doesn't break the cycle here:
    # LLVM rejects a recursive identified struct type outright, whatever
    # the array's length.
    src = """
    struct T {
      t: array[T, 0],
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InfiniteSizeTypError):
        compiler.compile(src)


def test_struct_contains_itself_via_nonempty_array(compiler):
    src = """
    struct T {
      t: array[T, 3],
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InfiniteSizeTypError):
        compiler.compile(src)


def test_mutual_struct_recursion_by_value(compiler):
    src = """
    struct A {
      b: B,
    }
    struct B {
      a: A,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InfiniteSizeTypError):
        compiler.compile(src)


def test_mutual_struct_recursion_by_ptr(tmp_path):
    # The by-value cycle check must not follow pointer fields at all:
    # A and B each contain the other only behind a pointer, so neither
    # has unbounded size.
    src = """
    struct A {
      b: *B,
    }
    struct B {
      a: *A,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_struct_diamond_containment_is_not_a_cycle(tmp_path):
    # A contains B twice (by value), but B doesn't contain A - not a
    # cycle, just two fields sharing a type.
    src = """
    struct B {
      v: i32,
    }
    struct A {
      x: B,
      y: B,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_typ_of_brace_expr_invalid(compiler):
    src = """
    pub fn main() i32 {
        let a = i32 {a: 0};
        return 0;
    }
    """
    with pytest.raises(errors.TypeOfBraceExprInvalidError):
        compiler.compile(src)


def test_missing_field_in_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0};
        return 0;
    }
    """
    with pytest.raises(errors.MissingFieldInStructExprError):
        compiler.compile(src)


def test_invalid_field_in_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0, b: 0, c: 0};
        return 0;
    }
    """
    with pytest.raises(errors.InvalidStructFieldError):
        compiler.compile(src)


def test_duplicate_field_in_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0, b: 0, a: 0};
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateFieldInStructExprError):
        compiler.compile(src)


def test_incompatible_struct_field_typ(compiler):
    src = """
    struct T {
        a: i32,
    }
    pub fn main() i32 {
        let x = T {a: "abc"};
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleStructFieldTypError):
        compiler.compile(src)


def test_void_call_as_struct_field_initializer(compiler):
    src = """
    fn f() { }
    struct T {
        a: i32,
    }
    pub fn main() i32 {
        let x = T {a: f()};
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleStructFieldTypError):
        compiler.compile(src)


def test_field_access_into_non_struct(compiler):
    src = """
    pub fn main() i32 {
        let x = array[i32, 4]{0, 1, 2, 3};

        return x.a;
    }
    """
    with pytest.raises(errors.FieldAccessIntoInvalidTypError):
        compiler.compile(src)


def test_comptime_struct_access(tmp_path):
    src = """
    struct T {
      a: i32,
      b: u8,
      c: bool,
    }

    let mut t = T { a: 10, b: 100u8, c: false};
    let x = t.a;
    let y = t.b;
    let z = t.c;

    pub fn main() i32 {
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 10)


def test_typ_of_comptime_struct_expr_not_struct(compiler):
    src = """
    let a = i32 {a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.TypeOfBraceExprInvalidError):
        compiler.compile(src)


def test_missing_field_in_comptime_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.MissingFieldInStructExprError):
        compiler.compile(src)


def test_invalid_field_in_comptime_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0, b: 0, c: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InvalidStructFieldError):
        compiler.compile(src)


def test_duplicate_field_in_comptime_struct_expr(compiler):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0, b: 0, a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateFieldInStructExprError):
        compiler.compile(src)


def test_incompatible_comptime_struct_field_typ(compiler):
    src = """
    struct T {
        a: i32,
    }
    let x = T {a: "abc"};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleStructFieldTypError):
        compiler.compile(src)


def test_comptime_field_access_into_non_struct(compiler):
    src = """
    let x = array[i32, 4]{0, 1, 2, 3};
    let y = x.a;
    pub fn main() i32 {

        return y;
    }
    """
    with pytest.raises(errors.FieldAccessIntoInvalidTypError):
        compiler.compile(src)


def test_self_as_ret_typ_in_inherent_impl(tmp_path):
    src = """
    struct Foo { mut x: i32 }
    impl Foo {
        fn get(*self) i32 { self.*.x }
        fn twin(*self) Self { Foo { x: self.*.x } }
    }
    pub fn main() i32 {
        let f = Foo { x: 5 };
        let g = f.twin();
        return g.get() - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_self_in_generic_inherent_impl_substitutes_per_instance(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
        fn twin(*self) Self { Box[T] { val: self.*.val } }
    }
    pub fn main() i32 {
        let a = Box[i32] { val: 5 };
        let b = Box[bool] { val: true };
        let c = a.twin();
        let d = b.twin();
        if (d.get()) { return c.get() - 5; }
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
