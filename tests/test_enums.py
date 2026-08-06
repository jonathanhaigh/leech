# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_enum_to_int_explicit_typ_args_uncalled_reference(tmp_path):
    # `__enum_to_int[Color]` named explicitly but not called immediately -
    # a regression guard for the concrete-return-type override needing to
    # apply here too, not just at a direct call or an instance's own typ.
    src = """
    enum Color(u8) { Red, Green, Blue }
    pub fn main() i32 {
        let f = __enum_to_int[Color];
        let x = f(Color::Blue);
        return x - 2;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_variant_address_of_copies_to_a_temporary(tmp_path):
    # A variant is a value, not a place (it has no address of its own) -
    # `&` on one falls back to the general "copy into a temporary, take
    # that temporary's address" case any non-place expression gets.
    src = """
    enum Color(u8) { Red, Green, Blue }
    pub fn main() i32 {
        let p = &Color::Blue;
        return __enum_to_int(p.*) - 2;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_variant_value_with_explicit_backing_typ(tmp_path):
    src = """
    enum Color(u8) { Red, Green = 5, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return __enum_to_int(c) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_variant_value_matching_explicit_suffix_allowed(tmp_path):
    src = """
    enum Color(u8) { Red, Green = 5u8, Blue }
    pub fn main() i32 {
        return __enum_to_int(Color::Green) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_variant_value_widening_suffix_allowed(tmp_path):
    # A u8-suffixed literal coerces to a wider u16 backing type, the same
    # as it would in any other typed context (let, assignment, return).
    src = """
    enum Color(u16) { Red, Green = 5u8, Blue }
    pub fn main() i32 {
        return __enum_to_int(Color::Green) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_variant_value_mismatched_suffix_rejected(tmp_path):
    src = """
    enum Color(u8) { Red, Green = 5i32, Blue }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.EnumVariantValueTypMismatchError):
        util.compile_str(tmp_path, src)


def test_enum_auto_increment_without_explicit_backing_typ(tmp_path):
    src = """
    enum Plain { A, B, C }
    pub fn main() i32 {
        let b = Plain::B;
        return __enum_to_int(b) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_value_resumes_auto_increment_after_explicit_override(tmp_path):
    src = """
    enum Color(u8) { Red, Green = 5, Blue }
    pub fn main() i32 {
        return __enum_to_int(Color::Blue) - 6;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_negative_explicit_variant_value(tmp_path):
    src = """
    enum Temp(i8) { Freezing = -1, Boiling = 99 }
    pub fn main() i32 {
        return __enum_to_int(Temp::Freezing) + 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_negative_value_resumes_auto_increment(tmp_path):
    src = """
    enum Signed(i8) { A = -1, B, C }
    pub fn main() i32 {
        return __enum_to_int(Signed::C) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_infers_signed_backing_typ_from_negative_value(tmp_path):
    # No explicit backing type, but a negative discriminant is present -
    # the inferred type must be signed, not just wide enough.
    src = """
    enum Signed { A = -1, B, C }
    pub fn main() i32 {
        if (__size_of[Signed]() == 1usize) {
            return __enum_to_int(Signed::C) - 1;
        };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_infers_unsigned_backing_typ_without_negative_value(tmp_path):
    src = """
    enum Plain { A, B, C }
    pub fn main() i32 {
        if (__size_of[Plain]() == 1usize) {
            return 0;
        };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_first_variant_defaults_to_zero(tmp_path):
    src = """
    enum Plain { A, B, C }
    pub fn main() i32 {
        return __enum_to_int(Plain::A);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_used_as_struct_field_typ(tmp_path):
    src = """
    enum Color(u8) { Red, Green, Blue }
    struct Pixel { mut color: Color }
    pub fn main() i32 {
        let p = Pixel { color: Color::Blue };
        return __enum_to_int(p.color) - 2;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_used_as_fn_param_and_ret_typ(tmp_path):
    src = """
    enum Color(u8) { Red, Green, Blue }
    fn identity(c: Color) Color { return c; }
    pub fn main() i32 {
        return __enum_to_int(identity(Color::Green)) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_inferred_discriminant_overflows_unsigned_64_bit(tmp_path):
    src = """
    enum Huge { A = 18446744073709551616 }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.EnumDiscriminantOverflowError):
        util.compile_str(tmp_path, src)


def test_enum_inferred_discriminant_overflows_signed_64_bit(tmp_path):
    src = """
    enum Huge { A = -9223372036854775809 }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.EnumDiscriminantOverflowError):
        util.compile_str(tmp_path, src)


def test_enum_duplicate_discriminant_values_allowed(tmp_path):
    # Deliberately permissive, matching C's own enum semantics - this is a
    # C-ABI-facing feature.
    src = """
    enum Alias(u8) { A = 1, B = 1 }
    pub fn main() i32 {
        return __enum_to_int(Alias::A) - __enum_to_int(Alias::B);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_duplicate_variant_name_rejected(tmp_path):
    src = """
    enum Color { Red, Red }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateVariantInEnumDefnError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Red"' in str(exc_info.value)


def test_enum_explicit_backing_typ_not_int(tmp_path):
    src = """
    enum Bad(bool) { A, B }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.EnumBackingTypNotIntError):
        util.compile_str(tmp_path, src)


def test_enum_discriminant_overflows_explicit_backing_typ(tmp_path):
    src = """
    enum TooBig(u8) { A = 300 }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.IntLitOverflowError):
        util.compile_str(tmp_path, src)


def test_enum_negative_discriminant_overflows_unsigned_backing_typ(tmp_path):
    src = """
    enum Bad(u8) { A = -1 }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.IntLitOverflowError):
        util.compile_str(tmp_path, src)


def test_enum_to_int_on_signed_backing_typ(tmp_path):
    # An auto-incremented (non-explicit) discriminant is never negative -
    # but the backing type itself may still be explicitly signed, e.g. to
    # match a signed C enum's ABI.
    src = """
    enum Signed(i8) { Zero, One, Two }
    pub fn main() i32 {
        return __enum_to_int(Signed::Two) - 2;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_enum_cross_module(tmp_path):
    a_src = """
    pub enum Color(u8) { Red, Green, Blue }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        return __enum_to_int(a::Color::Blue) - 2;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_private_enum_inaccessible_from_other_module(tmp_path):
    a_src = """
    enum Color(u8) { Red, Green, Blue }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let c = a::Color::Red;
        return 0;
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_enum_variant_typ_args_on_non_generic_enum(tmp_path):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let x: Color[i32] = Color::Red;
        return 0;
    }
    """
    with pytest.raises(errors.TypArgsOnNonGenericItemError):
        util.compile_str(tmp_path, src)


def test_enum_unknown_variant(tmp_path):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Purple;
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)
