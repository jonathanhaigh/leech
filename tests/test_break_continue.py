# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds


def test_break(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (true) {
            i = i + 1;
            if (i == 5) { break; };
        };
        return i;
    }
    """
    compiler.check(src, exit_status=5)


def test_continue(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        let mut sum = 0;
        while (i < 10) {
            i = i + 1;
            if (i == 3) { continue; };
            sum = sum + i;
        };
        return sum;
    }
    """
    # 1+2+4+5+6+7+8+9+10, skipping 3
    compiler.check(src, exit_status=52)


def test_labeled_break_outer_from_inner(compiler):
    src = """
    pub fn main() i32 {
        let mut count = 0;
        outer: while (true) {
            let mut j = 0;
            while (true) {
                j = j + 1;
                count = count + 1;
                if (j == 3) { break outer; };
            };
        };
        return count;
    }
    """
    # If `break outer;` only broke the inner loop, this would loop forever
    # (or count well past 3).
    compiler.check(src, exit_status=3)


def test_labeled_continue_outer_from_inner(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        let mut inner_runs = 0;
        outer: while (i < 3) {
            i = i + 1;
            let mut j = 0;
            while (j < 10) {
                j = j + 1;
                inner_runs = inner_runs + 1;
                if (j == 2) { continue outer; };
            };
        };
        return inner_runs;
    }
    """
    # Each outer iteration's inner loop runs exactly twice (j=1, j=2) before
    # `continue outer;` skips the rest of the inner loop and the rest of
    # the outer body, for 3 outer iterations total.
    compiler.check(src, exit_status=6)


def test_shadowed_label_targets_inner_loop(compiler):
    src = """
    pub fn main() i32 {
        let mut outer_iters = 0;
        let mut inner_total = 0;
        lbl: while (outer_iters < 3) {
            outer_iters = outer_iters + 1;
            let mut j = 0;
            lbl: while (j < 10) {
                j = j + 1;
                inner_total = inner_total + 1;
                if (j == 4) { break lbl; };
            };
        };
        return outer_iters * 100 + inner_total;
    }
    """
    # If `break lbl;` had escaped to the outer loop instead of the inner
    # one shadowing it, outer_iters would be 1, not 3.
    compiler.check(src, exit_status=(3 * 100 + 4 * 3) % 256)


def test_sibling_loops_reuse_label(compiler):
    src = """
    pub fn main() i32 {
        let mut total = 0;
        lbl: while (true) {
            total = total + 1;
            if (total == 2) { break lbl; };
        };
        lbl: while (true) {
            total = total + 10;
            if (total == 22) { break lbl; };
        };
        return total;
    }
    """
    compiler.check(src, exit_status=22)


def test_break_terminates_block_as_never(compiler):
    # Mirrors test_never.py's equivalent `return`-based test: a block
    # whose only statement diverges (here, via `break`) is `never`-typed,
    # so it coerces into an annotated `let`'s declared type.
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (true) {
            let x: i32 = { break; };
            i = x;
        };
        return i;
    }
    """
    compiler.check(src)


def test_break_in_if_arm_inside_while(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 1;
        while (true) {
            i = 2 * i;
            if (i >= 120) {
                break;
            }
        };
        return i;
    }
    """
    compiler.check(src, exit_status=128)


def test_comptime_break(compiler):
    src = """
    let x = {
        let mut i = 0;
        while (true) {
            i = i + 1;
            if (i == 7) { break; };
        };
        i
    };
    pub fn main() i32 {
        return x;
    }
    """
    compiler.check(src, exit_status=7)


def test_comptime_continue(compiler):
    src = """
    let x = {
        let mut i = 0;
        let mut sum = 0;
        while (i < 5) {
            i = i + 1;
            if (i == 2) { continue; };
            sum = sum + i;
        };
        sum
    };
    pub fn main() i32 {
        return x;
    }
    """
    # 1+3+4+5, skipping 2
    compiler.check(src, exit_status=13)


def test_break_outside_loop(compiler):
    src = """
    pub fn main() i32 {
        break;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.BREAK_OUTSIDE_LOOP,)


def test_continue_outside_loop(compiler):
    src = """
    pub fn main() i32 {
        continue;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONTINUE_OUTSIDE_LOOP,)


def test_labeled_break_outside_loop(compiler):
    # No enclosing loop at all beats "unknown label" - same shape as
    # BreakNotInLoopError firing regardless of whether a label is given.
    src = """
    pub fn main() i32 {
        break nope;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.BREAK_OUTSIDE_LOOP,)


def test_break_unknown_label(compiler):
    src = """
    pub fn main() i32 {
        while (true) {
            break nope;
        };
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNKNOWN_LOOP_LABEL,)


def test_continue_unknown_label(compiler):
    src = """
    pub fn main() i32 {
        while (true) {
            continue nope;
        };
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNKNOWN_LOOP_LABEL,)


def test_comptime_break_outside_loop(compiler):
    src = """
    let x = {
        break;
    };
    pub fn main() i32 { 0 }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.BREAK_OUTSIDE_LOOP,)
