import pytest

from leech.errors import (
    AssignToConstError,
    IncompatibleAssignmentTypError,
    SetNonLocalVarAtComptimeError,
)
from util import check_prog_output, compile_str


def test_assign_to_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let mut a = 1;
        a = 2;
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_assign_to_mod_var(tmp_path):
    src = """
    let mut a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_comptime_assign_to_mod_var(tmp_path):
    src = """
    let mut a = 1;
    fn f() i32 {
        a = 2;
        return 0;
    }
    let x = f();
    pub fn main() i32 {
        return a;
    }
    """
    with pytest.raises(SetNonLocalVarAtComptimeError):
        compile_str(tmp_path, src)


def test_assign_to_const_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let a = 1;
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_const_mod_var(tmp_path):
    src = """
    let a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_local_arr_element(tmp_path):
    src = """
    pub fn main() i32 {
        let mut a = [1, 2, 3, 4];
        a[2usize] = 10;
        return a[2usize] + a[0usize];
    }
    """
    check_prog_output(tmp_path, src, "", 11)


def test_assign_to_local_nested_arr_element(tmp_path):
    src = """
    pub fn main() i32 {
        let mut a = [[1, 2], [3, 4]];
        a[1usize][0usize] = 10;
        return a[1usize][0usize];
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_assign_to_const_local_arr_element(tmp_path):
    src = """
    pub fn main() i32 {
        let a = [1, 2, 3, 4];
        a[2usize] = 10;
        return a[2usize] + a[0usize];
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_const_local_nested_arr_element(tmp_path):
    src = """
    pub fn main() i32 {
        let a = [[1, 2], [3, 4]];
        a[1usize][0usize] = 10;
        return a[1usize][0usize];
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_local_struct_field(tmp_path):
    src = """
    struct T {mut a: i32, mut b: i32}

    pub fn main() i32 {
        let mut x = T {a: 10, b: 11};
        x.a = 100;
        x.b = 110;
        return x.a + x.b;
    }
    """
    check_prog_output(tmp_path, src, "", 210)


def test_assign_to_local_nested_struct_field(tmp_path):
    src = """
    struct T {mut a: i32}
    struct U {mut b: T}
    struct V {mut c: U}

    pub fn main() i32 {
        let mut x = V{c: U{b: T{a: 99}}};
        x.c.b.a = 101;
        return x.c.b.a;
    }
    """
    check_prog_output(tmp_path, src, "", 101)


def test_assign_to_const_local_struct_field(tmp_path):
    src = """
    struct T {mut a: i32}

    pub fn main() i32 {
        let x = T {a: 10};
        x.a = 11;
        return x.a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_local_struct_const_field(tmp_path):
    src = """
    struct T {a: i32}

    pub fn main() i32 {
        let mut x = T {a: 10};
        x.a = 11;
        return x.a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_const_local_nested_struct_field(tmp_path):
    src = """
    struct T {mut a: i32}
    struct U {mut b: T}
    pub fn main() i32 {
        let x = U{b: T{a: 10}};
        x.b.a = 11;
        return x.b.a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_local_nested_struct_const_field(tmp_path):
    src = """
    struct T {a: i32}
    struct U {mut b: T}
    pub fn main() i32 {
        let mut x = U{b: T{a: 10}};
        x.b.a = 11;
        return x.b.a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_through_ptr_deref(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 1;
        let p = &x;
        p.* = 2;
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_assign_through_const_ptr_deref(tmp_path):
    # &x on a const local produces a pointer whose pointee is const too,
    # so assigning through it is rejected the same way any other const
    # place is.
    src = """
    pub fn main() i32 {
        let x = 1;
        let p = &x;
        p.* = 2;
        return x;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_through_explicit_mut_ptr_param(tmp_path):
    # `*mut T`, written explicitly as a parameter's type (as opposed to
    # arising implicitly from `&` on a `let mut` local, which
    # test_assign_through_ptr_deref already covers), is writable through
    # the same way.
    src = """
    fn set(p: *mut i32) {
        p.* = 2;
    }
    pub fn main() i32 {
        let mut x = 1;
        set(&x);
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_assign_wrong_typ_to_local(tmp_path):
    # Assignment used not to type-check at all, so a mismatch tripped an
    # internal assertion in StoreInstr instead of being diagnosed. i32
    # into a u8 place narrows, so it doesn't coerce either.
    src = """
    pub fn main() i32 {
        let mut x = 1u8;
        x = 2i32;
        return 0;
    }
    """
    with pytest.raises(IncompatibleAssignmentTypError):
        compile_str(tmp_path, src)


def test_assign_wrong_typ_through_ptr_deref(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 1u8;
        let mut p = &x;
        p.* = 2i32;
        return 0;
    }
    """
    with pytest.raises(IncompatibleAssignmentTypError):
        compile_str(tmp_path, src)


def test_assign_wrong_typ_to_struct_field(tmp_path):
    src = """
    struct T { mut a: i32 }
    pub fn main() i32 {
        let mut t = T { a: 1 };
        t.a = "abc";
        return 0;
    }
    """
    with pytest.raises(IncompatibleAssignmentTypError):
        compile_str(tmp_path, src)


def test_assign_to_const_reported_before_typ_mismatch(tmp_path):
    # Both wrong at once: the place being const is reported first, since
    # it's a problem with the assignment itself rather than the value.
    src = """
    pub fn main() i32 {
        let x = 1u8;
        x = 2i32;
        return 0;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_through_explicit_const_ptr_param(tmp_path):
    # `*T` (no `mut`) is still const by default when written explicitly.
    src = """
    fn set(p: *i32) {
        p.* = 2;
    }
    pub fn main() i32 {
        let x = 1;
        set(&x);
        return x;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_temporary(tmp_path):
    src = """
    fn f() [i32; 4] {
        return [1, 2, 3, 4];
    }

    pub fn main() i32 {
        f()[0usize] = 10;
        return 0;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_evaluates_place_before_value(tmp_path):
    # `mark_place` has the side effect of setting `order` to 1 before
    # yielding the (const, always-0) index of the assignment's place.
    # `read_order` has no side effect; it just reports what `order` was
    # when it ran. If the place is evaluated before the value (which Leech
    # guarantees; see CfgBuilder.build_assignment_stmt's docstring),
    # `read_order` runs after `mark_place` has already set `order` to 1,
    # so `arr[0]` ends up holding 1. If it were the other way around,
    # `read_order` would still see `order`'s initial value of 0.
    src = """
    let mut order = 0;

    fn mark_place() usize {
        order = 1;
        return 0usize;
    }

    fn read_order() i32 {
        return order;
    }

    pub fn main() i32 {
        let mut arr = [0, 0];
        arr[mark_place()] = read_order();
        return arr[0usize];
    }
    """
    check_prog_output(tmp_path, src, "", 1)
