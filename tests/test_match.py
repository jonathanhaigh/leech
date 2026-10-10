# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds


def test_match_exhaustive_enum(compiler):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) {
            Color::Red | Color::Blue => 1i32,
            Color::Green => 0i32,
        };
    }
    """
    compiler.check(src)


def test_match_enum_with_wildcard(compiler):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) {
            Color::Red => 1i32,
            _ => 0i32,
        };
    }
    """
    compiler.check(src)


def test_match_bool_exhaustion(compiler):
    src = """
    pub fn main() i32 {
        let b = true;
        return match (b) {
            true => 0i32,
            false => 1i32,
        };
    }
    """
    compiler.check(src)


def test_match_int_with_required_wildcard(compiler):
    src = """
    pub fn main() i32 {
        return match (2) {
            1 => 1i32,
            _ => 0i32,
        };
    }
    """
    compiler.check(src)


def test_match_negative_int_pattern(compiler):
    src = """
    pub fn main() i32 {
        return match (-1) {
            -1 => 0i32,
            _ => 1i32,
        };
    }
    """
    compiler.check(src)


def test_match_or_pattern_routes_every_alternative(compiler):
    src = """
    enum Color { Red, Green, Blue, Cyan }
    fn classify(c: Color) i32 {
        return match (c) {
            Color::Red | Color::Green | Color::Blue => 1i32,
            Color::Cyan => 2i32,
        };
    }
    pub fn main() i32 {
        return classify(Color::Red) + classify(Color::Green) + classify(Color::Blue)
            + classify(Color::Cyan) - 5i32;
    }
    """
    compiler.check(src)


def test_match_or_pattern_of_int_literals(compiler):
    src = """
    fn classify(n: i32) i32 {
        return match (n) {
            -1 | 0 | 1 => 1i32,
            let other => other,
        };
    }
    pub fn main() i32 {
        return classify(-1i32) + classify(0i32) + classify(1i32) + classify(7i32) - 10i32;
    }
    """
    compiler.check(src)


def test_match_binding(compiler):
    src = """
    pub fn main() i32 {
        return match (7) {
            let x => x - 7,
        };
    }
    """
    compiler.check(src)


def test_match_mut_binding(compiler):
    src = """
    pub fn main() i32 {
        return match (4) {
            let mut x => {
                x = 9;
                return x - 9;
            },
        };
    }
    """
    compiler.check(src)


def test_match_block_bodied_arms(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => { 0i32 },
            false => { 1i32 },
        };
    }
    """
    compiler.check(src)


def test_match_as_tail_expression(compiler):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 0i32,
            false => 1i32,
        }
    }
    """
    compiler.check(src)


def test_match_as_statement_with_semicolon(compiler):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 1i32,
            false => 2i32,
        };
        return 0;
    }
    """
    compiler.check(src)


def test_match_as_statement_without_semicolon(compiler):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 1i32,
            false => 2i32,
        }
        return 0;
    }
    """
    compiler.check(src)


def test_match_as_call_argument(compiler):
    src = """
    fn id(x: i32) i32 { return x; }
    pub fn main() i32 {
        return id(match (true) {
            true => 1,
            false => 2,
        }) - 1;
    }
    """
    compiler.check(src)


def test_nested_match(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => match (1) {
                1 => 0i32,
                _ => 1i32,
            },
            false => 2i32,
        };
    }
    """
    compiler.check(src)


def test_match_diverging_arm(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => { return 0; },
            false => 1i32,
        };
    }
    """
    compiler.check(src)


def test_match_all_arms_diverge(compiler):
    src = """
    pub fn main() i32 {
        match (true) {
            true => { return 0; },
            false => { return 1; },
        };
    }
    """
    compiler.check(src)


def test_empty_match_on_never_scrutinee(compiler):
    src = """
    fn diverge() never { return diverge(); }
    pub fn main() i32 {
        return match (diverge()) {};
    }
    """
    compiler.compile(src)


def test_comptime_match_enum(compiler):
    src = """
    enum Color { Red, Green, Blue }
    let x = match (Color::Green) {
        Color::Red => 1,
        Color::Green => 0,
        Color::Blue => 2,
    };
    pub fn main() i32 {
        return x;
    }
    """
    compiler.check(src)


def test_comptime_match_bool(compiler):
    src = """
    let answer = match (false) {
        true => 1,
        false => 0,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_int_with_wildcard(compiler):
    src = """
    let answer = match (2) {
        1 => 1,
        _ => 0,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_negative_int_pattern(compiler):
    src = """
    let answer = match (-1) {
        -1 => 0,
        _ => 1,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_or_pattern(compiler):
    src = """
    enum Color { Red, Green, Blue }
    let answer = match (Color::Blue) {
        Color::Red | Color::Blue => 0,
        Color::Green => 1,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_binding(compiler):
    src = """
    let answer = match (7) {
        let x => x - 7,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_mut_binding(compiler):
    src = """
    let answer = match (4) {
        let mut x => {
            x = 9;
            x - 9
        },
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_match_block_bodied_arms(compiler):
    src = """
    let answer = match (true) {
        true => { 0 },
        false => { 1 },
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_comptime_nested_match(compiler):
    src = """
    let answer = match (true) {
        true => match (1) {
            1 => 0,
            _ => 1,
        },
        false => 2,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    compiler.check(src)


def test_match_in_place_context_copies_temporary(compiler):
    src = """
    pub fn main() i32 {
        let mut a = 1;
        let p = &(match (true) {
            _ => a,
        });
        a = 2;
        return p.*;
    }
    """
    compiler.check(src, exit_status=1)


def test_match_in_place_context_merges_value_arms(compiler):
    src = """
    pub fn main() i32 {
        let mut a = 1;
        let b = 2;
        let p = &(match (true) {
            true => a,
            false => b,
        });
        a = 3;
        return p.*;
    }
    """
    compiler.check(src, exit_status=1)


def test_match_aliased_discriminants(compiler):
    src = """
    enum Alias(u8) { A = 1, B = 1 }
    pub fn main() i32 {
        let a = Alias::A;
        return match (a) {
            Alias::A => 0i32,
        };
    }
    """
    compiler.check(src)


def test_match_aliased_discriminant_warns(compiler):
    src = """
    enum Alias(u8) { A = 1, B = 1 }
    pub fn main() i32 {
        let a = Alias::A;
        return match (a) {
            Alias::A => 0i32,
            Alias::B => 1i32,
        };
    }
    """
    compiled = compiler.compile(src)

    assert [err.kind for err in compiled.diags.all()] == [diag_kinds.UNREACHABLE_MATCH_ARM]


def test_match_arm_typs_peer_across_multiple_arms(compiler):
    src = """
    fn takes_u8(x: u8) u8 { return x; }
    pub fn main() i32 {
        let x = match (2) {
            0 => 1u8,
            1 => 2,
            _ => 3,
        };
        return takes_u8(x);
    }
    """
    compiler.check(src, exit_status=3)


def test_match_expected_typ_still_wins(compiler):
    src = """
    pub fn main() i32 {
        let x: u8 = match (2) {
            0 => 1,
            1 => 2,
            _ => 3,
        };
        return x;
    }
    """
    compiler.check(src, exit_status=3)


def test_match_non_exhaustive_error(compiler):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) { Color::Red => 0i32, };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)


def test_match_non_exhaustive_with_redundant_arm_warns(compiler):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Red;
        return match (c) {
            Color::Red => 0i32,
            Color::Red => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (
        diag_kinds.NON_EXHAUSTIVE_MATCH,
        diag_kinds.UNREACHABLE_MATCH_ARM,
    )


def test_match_arm_typ_mismatch_error(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => 0i32,
            false => "no",
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_MATCH_ARM_TYPES,)


def test_pattern_typ_mismatch_error(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            1 => 0i32,
            false => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PATTERN_TYPE_MISMATCH,)


def test_binding_in_or_pattern_error(compiler):
    src = """
    enum Color { Red, Green }
    pub fn main() i32 {
        let c = Color::Red;
        return match (c) {
            Color::Red | let x => 0i32,
            Color::Green => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.BINDING_IN_OR_PATTERN,)


def test_payload_pattern_on_enum_variant_error(compiler):
    src = """
    enum E { A, B }
    pub fn main() i32 {
        let e = E::B;
        return match (e) {
            E::A(5) => 0i32,
            _ => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)


def test_payload_binding_on_enum_variant_error(compiler):
    src = """
    enum E { A, B }
    pub fn main() i32 {
        let e = E::B;
        return match (e) {
            E::A(let x) => x,
            _ => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)


def test_payload_pattern_nested_under_or_pattern_error(compiler):
    src = """
    enum E { A, B }
    pub fn main() i32 {
        let e = E::B;
        return match (e) {
            E::A(5) | E::B => 0i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)


def test_not_a_pattern_error(compiler):
    src = """
    pub fn main() i32 {
        let x = 1;
        return match (1) {
            x => 0i32,
            _ => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.NON_PATTERN_PATH,)
