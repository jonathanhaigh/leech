# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import util


def test_alloc_write_read_dealloc_round_trips_an_int(tmp_path):
    main_src = """
    import std::mem;
    pub fn main() i32 {
        let p = mem::alloc[i32]();
        p.* = 42;
        let v = p.*;
        mem::dealloc[i32](p);
        return v;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 42, std_modules=("mem",))


def test_alloc_write_read_dealloc_round_trips_a_struct(tmp_path):
    main_src = """
    import std::mem;
    struct Pair { mut a: i32, mut b: i32 }
    pub fn main() i32 {
        let p = mem::alloc[Pair]();
        p.*.a = 3;
        p.*.b = 4;
        let sum = p.*.a + p.*.b;
        mem::dealloc[Pair](p);
        return sum;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 7, std_modules=("mem",))


def test_two_allocs_do_not_alias(tmp_path):
    main_src = """
    import std::mem;
    pub fn main() i32 {
        let p = mem::alloc[i32]();
        let q = mem::alloc[i32]();
        p.* = 1;
        q.* = 2;
        let result = p.* + q.*;
        mem::dealloc[i32](p);
        mem::dealloc[i32](q);
        return result;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 3, std_modules=("mem",))


def test_dealloc_of_null_pointer_is_a_no_op(tmp_path):
    # getenv is a portable, always-available libc function that returns a
    # genuine null pointer for an unset variable - the only way to
    # observe one, since Leech has no null-pointer literal.
    main_src = """
    import std::mem;
    extern fn getenv(name: *u8) *mut u8;
    pub fn main() i32 {
        let n = getenv("LEECH_TEST_DEFINITELY_UNSET_VAR_XYZ");
        let p = __ptr_cast_mut[u8, i32](n);
        mem::dealloc[i32](p);
        return 7;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 7, std_modules=("mem",))


def test_alloc_different_typs_use_distinct_size_of_instantiations(tmp_path):
    # A regression guard for generic type-argument substitution
    # (FnInstance's _mapping) ever being broken across two sibling
    # monomorphizations of the same generic function (here, alloc[T]'s own
    # internal __size_of[T] call) sharing state they shouldn't.
    main_src = """
    import std::mem;
    pub fn main() i32 {
        let p = mem::alloc[i32]();
        let q = mem::alloc[bool]();
        mem::dealloc[i32](p);
        mem::dealloc[bool](q);
        return 0;
    }
    """
    (llir_path,) = util.compile_modules(tmp_path, main=main_src)
    ir_text = llir_path.read_text()
    assert 'call i64 @"__size_of[i32]"' in ir_text
    assert 'call i64 @"__size_of[bool]"' in ir_text
