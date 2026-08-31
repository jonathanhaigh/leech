# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_match_exhaustive_enum(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_enum_with_wildcard(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_bool_exhaustion(tmp_path):
    src = """
    pub fn main() i32 {
        let b = true;
        return match (b) {
            true => 0i32,
            false => 1i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_int_with_required_wildcard(tmp_path):
    src = """
    pub fn main() i32 {
        return match (2) {
            1 => 1i32,
            _ => 0i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_negative_int_pattern(tmp_path):
    src = """
    pub fn main() i32 {
        return match (-1) {
            -1 => 0i32,
            _ => 1i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_binding(tmp_path):
    src = """
    pub fn main() i32 {
        return match (7) {
            let x => x - 7,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_mut_binding(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_block_bodied_arms(tmp_path):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => { 0i32 },
            false => { 1i32 },
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_as_tail_expression(tmp_path):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 0i32,
            false => 1i32,
        }
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_as_statement_with_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 1i32,
            false => 2i32,
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_as_statement_without_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        match (true) {
            true => 1i32,
            false => 2i32,
        }
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_as_call_argument(tmp_path):
    src = """
    fn id(x: i32) i32 { return x; }
    pub fn main() i32 {
        return id(match (true) {
            true => 1,
            false => 2,
        }) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_nested_match(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_diverging_arm(tmp_path):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => { return 0; },
            false => 1i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_all_arms_diverge(tmp_path):
    src = """
    pub fn main() i32 {
        match (true) {
            true => { return 0; },
            false => { return 1; },
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_empty_match_on_never_scrutinee(tmp_path):
    src = """
    fn diverge() never { return diverge(); }
    pub fn main() i32 {
        return match (diverge()) {};
    }
    """
    util.compile_str(tmp_path, src)


def test_comptime_match_enum(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_bool(tmp_path):
    src = """
    let answer = match (false) {
        true => 1,
        false => 0,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_int_with_wildcard(tmp_path):
    src = """
    let answer = match (2) {
        1 => 1,
        _ => 0,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_negative_int_pattern(tmp_path):
    src = """
    let answer = match (-1) {
        -1 => 0,
        _ => 1,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_or_pattern(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_binding(tmp_path):
    src = """
    let answer = match (7) {
        let x => x - 7,
    };
    pub fn main() i32 {
        return answer;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_mut_binding(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_match_block_bodied_arms(tmp_path):
    src = """
    let answer = match (true) {
        true => { 0 },
        false => { 1 },
    };
    pub fn main() i32 {
        return answer;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_comptime_nested_match(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_in_place_context_copies_temporary(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 1)


def test_match_in_place_context_merges_value_arms(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 1)


def test_match_aliased_discriminants(tmp_path):
    src = """
    enum Alias(u8) { A = 1, B = 1 }
    pub fn main() i32 {
        let a = Alias::A;
        return match (a) {
            Alias::A => 0i32,
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_match_aliased_discriminant_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(errors, "_errors", [])
    monkeypatch.setattr(errors, "_error_level", errors.NOTE)
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
    util.compile_str(tmp_path, src)

    assert [type(err) for err in errors.all_errors()] == [errors.UnreachableMatchArmWarning]
    assert errors.error_level() == errors.WARNING


def test_match_arm_typs_peer_across_multiple_arms(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 3)


def test_match_expected_typ_still_wins(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 3)


def test_match_non_exhaustive_error(tmp_path):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) { Color::Red => 0i32, };
    }
    """
    with pytest.raises(errors.NonExhaustiveMatchError):
        util.compile_str(tmp_path, src)


def test_match_non_exhaustive_with_redundant_arm_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(errors, "_errors", [])
    monkeypatch.setattr(errors, "_error_level", errors.NOTE)
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
    with pytest.raises(errors.NonExhaustiveMatchError):
        util.compile_str(tmp_path, src)
    assert [type(err) for err in errors.all_errors()] == [errors.UnreachableMatchArmWarning]


def test_match_arm_typ_mismatch_error(tmp_path):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => 0i32,
            false => "no",
        };
    }
    """
    with pytest.raises(errors.MatchArmTypMismatchError):
        util.compile_str(tmp_path, src)


def test_pattern_typ_mismatch_error(tmp_path):
    src = """
    pub fn main() i32 {
        return match (true) {
            1 => 0i32,
            false => 1i32,
        };
    }
    """
    with pytest.raises(errors.PatternTypMismatchError):
        util.compile_str(tmp_path, src)


def test_binding_in_or_pattern_error(tmp_path):
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
    with pytest.raises(errors.BindingInOrPatternError):
        util.compile_str(tmp_path, src)


def test_not_a_pattern_error(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 1;
        return match (1) {
            x => 0i32,
            _ => 1i32,
        };
    }
    """
    with pytest.raises(errors.NotAPatternError):
        util.compile_str(tmp_path, src)
