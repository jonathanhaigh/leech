# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, errors, ir_env, ir_module, target, typs


def _get_builtin(mod, name: str) -> ir_module.GenericBuiltinFn:
    """Get the compiler-intrinsic builtin ambiently bound as ``name``."""
    var = mod.env.get(ir_env.Env.Namespace.VARS, name)
    return asserts.checked_cast(var, ir_module.GenericBuiltinFn)


def test_generic_builtin_is_bound_as_declaration(tmp_path):
    mod = util.build_ir_mod(tmp_path, "pub fn main() i32 { 0 }")

    builtin = mod.env.get(ir_env.Env.Namespace.VARS, "__size_of")

    assert isinstance(builtin, ir_module.GenericBuiltinFn)


def test_bare_generic_builtin_reference_requires_typ_args(tmp_path):
    src = "pub fn main() i32 { let size = __size_of; return 0; }"

    with pytest.raises(errors.MissingTypArgsError):
        util.compile_str(tmp_path, src)


# Widths whose byte size is unambiguous and target-independent: these are
# LLVM's own primitive integer sizes (a power of two, at least a byte
# wide), so no ABI padding ever applies. See test_size_of_runtime for
# widths where that's *not* true.
_UNAMBIGUOUS_TYPS = [
    ("bool", 1),
    ("i8", 1),
    ("u8", 1),
    ("i16", 2),
    ("i32", 4),
    ("u32", 4),
    ("i64", 8),
    ("usize", 8),
    ("isize", 8),
]


@pytest.mark.parametrize(("typ", "expected_size"), _UNAMBIGUOUS_TYPS)
def test_size_of_runtime_unambiguous_typs(tmp_path, typ, expected_size):
    src = f"""
    pub fn main() i32 {{
        if (__size_of[{typ}]() == {expected_size}usize) {{
            return 1;
        }};
        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 1)


@pytest.mark.parametrize(
    ("typ", "expected_size"),
    [
        # A width that isn't even a whole number of bytes - LLVM rounds up
        # to the smallest byte count that can hold it, then further up to
        # its ABI alignment.
        ("i3", 1),
        ("i17", 4),
        # A width that *is* a whole number of bytes (24 bits = 3 bytes)
        # but not a power of two - still gets padded up to 4, by the same
        # ABI-alignment rule. This is the case naive `width // 8` silently
        # gets wrong for both reasons above.
        ("i24", 4),
    ],
)
def test_size_of_runtime_padded_typs(tmp_path, typ, expected_size):
    # Confirmed against the real compiled-and-run output by hand, not
    # derived from any formula in comptime.py - this is the ground truth
    # comptime.py's own (deliberately more conservative) handling is
    # checked against below.
    src = f"""
    pub fn main() i32 {{
        if (__size_of[{typ}]() == {expected_size}usize) {{
            return 1;
        }};
        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_size_of_struct_is_at_least_sum_of_field_sizes(tmp_path):
    # A loose bound, not exact equality - padding is legitimately
    # platform-dependent, which is exactly why the GEP idiom is used
    # rather than a hand-computed size.
    src = """
    struct Point { x: i32, y: i32 }
    pub fn main() i32 {
        if (__size_of[Point]() >= 8usize) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_size_of_wrong_number_of_typ_args(tmp_path):
    src = """
    pub fn main() i32 {
        return __size_of[i32, bool]();
    }
    """
    with pytest.raises(errors.WrongNumberOfTypArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"__size_of"' in str(exc_info.value)


@pytest.mark.parametrize(("typ", "expected_size"), _UNAMBIGUOUS_TYPS)
def test_size_of_at_comptime_matches_runtime(tmp_path, typ, expected_size):
    # Regression guard for the Python-side primitive-size computation
    # (comptime.py's SizeOfInstr handling) ever drifting from the
    # GEP-idiom's runtime answer.
    src = f"""
    let comptime_size = __size_of[{typ}]();
    pub fn main() i32 {{
        if (comptime_size == {expected_size}usize) {{
            if (comptime_size == __size_of[{typ}]()) {{
                return 1;
            }};
        }};
        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_is_null_false_at_comptime(tmp_path):
    # No Comptime* pointer value the interpreter can ever produce is null
    # (see comptime.py's IsNullInstr handling) - unlike a real runtime
    # pointer, comptime pointers always refer to something real.
    src = """
    let mut x = 1;
    let p = &x;
    let comptime_is_null = __is_null(p);
    pub fn main() i32 {
        if (comptime_is_null) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_size_of_ptr_typ_at_comptime_matches_runtime(tmp_path):
    src = f"""
    let comptime_size = __size_of[*i32]();
    pub fn main() i32 {{
        if (comptime_size == {target.ADDR_SIZE // 8}usize) {{
            if (comptime_size == __size_of[*i32]()) {{
                return 1;
            }};
        }};
        return 0;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 1)


@pytest.mark.parametrize("typ", ["i3", "i17", "i24"])
def test_size_of_padded_typ_at_comptime_raises(tmp_path, typ):
    # comptime.py deliberately doesn't try to replicate LLVM's ABI-padding
    # rules in Python (see test_size_of_runtime_padded_typs) - it raises
    # rather than risk silently disagreeing with them.
    src = f"""
    let s = __size_of[{typ}]();
    pub fn main() i32 {{
        return 0;
    }}
    """
    with pytest.raises(errors.SizeOfNotComptimeEvaluableError):
        util.compile_str(tmp_path, src)


def test_size_of_struct_at_comptime_raises(tmp_path):
    src = """
    struct Point { x: i32, y: i32 }
    let s = __size_of[Point]();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.SizeOfNotComptimeEvaluableError):
        util.compile_str(tmp_path, src)


def test_ptr_cast_mut_round_trips_pointer_value(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let p = &x;
        let q = __ptr_cast_mut[i32, u8](p);
        let r = __ptr_cast_mut[u8, i32](q);
        return r.*;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_ptr_cast_mut_at_comptime_raises_even_for_same_typ(tmp_path):
    # The Comptime* value model is value-oriented, not byte-oriented (a
    # ComptimeAlloc holds a typed ComptimeValue, not a byte buffer) and
    # never records a pointer's mutability separately from its pointee -
    # so no ptr_cast_mut is comptime-evaluable, not even a same-pointee-
    # type one that would be a pure no-op at runtime.
    src = """
    let mut x = 5;
    let p = &x;
    let q = __ptr_cast_mut[i32, i32](p);
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PtrCastNotComptimeEvaluableError):
        util.compile_str(tmp_path, src)


def test_ptr_cast_mut_across_different_pointee_typs_at_comptime_raises(tmp_path):
    src = """
    let mut x = 5;
    let p = &x;
    let q = __ptr_cast_mut[i32, bool](p);
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PtrCastNotComptimeEvaluableError):
        util.compile_str(tmp_path, src)


def test_ptr_cast_mut_wrong_number_of_typ_args(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let p = __ptr_cast_mut[i32](&x);
        return 0;
    }
    """
    with pytest.raises(errors.WrongNumberOfTypArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"__ptr_cast_mut"' in str(exc_info.value)


def test_is_null_false_for_real_pointer(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let p = &x;
        if (__is_null(p)) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_is_null_true_for_genuine_null_pointer(tmp_path):
    # getenv is a portable, always-available libc function that returns a
    # genuine null pointer for an unset variable - the only way to
    # observe one, since Leech has no null-pointer literal.
    src = """
    extern fn getenv(name: *u8) *mut u8;
    pub fn main() i32 {
        let p = getenv("LEECH_TEST_DEFINITELY_UNSET_VAR_XYZ");
        if (__is_null(p)) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_size_of_builtin_instance_caches_by_typ_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "pub fn main() i32 { return 0; }")
    size_of = _get_builtin(mod, "__size_of")

    assert size_of.instantiate((typs.I32,)) is size_of.instantiate((typs.I32,))
    assert size_of.instantiate((typs.I32,)) is not size_of.instantiate((typs.BOOL,))


def test_size_of_builtin_compiled_once_across_multiple_calls(tmp_path):
    src = """
    pub fn main() i32 {
        let a = __size_of[i32]();
        let b = __size_of[i32]();
        return 0;
    }
    """
    ll_path = util.compile_str(tmp_path, src)
    ir_text = ll_path.read_text()
    assert ir_text.count('define linkonce_odr i64 @"__size_of[i32]"') == 1
