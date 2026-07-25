import pytest

from l0.l0errors import (
    AssignToConstError,
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
    # when it ran. If the place is evaluated before the value (which l0
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
