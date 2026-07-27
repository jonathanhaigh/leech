import pytest

from l0.l0errors import IncompatibleLetTypError, IncompatibleTypInArrayExpr
from l0.typs import BOOL, I32, NEVER
from util import check_prog_output, compile_str


def test_never_coerces_to_any_type():
    # A direct check of the type-lattice rule, independent of any
    # particular call site.
    assert NEVER.coerces_to(I32)
    assert NEVER.coerces_to(BOOL)
    assert NEVER.coerces_to(NEVER)


# --- Coercion sites (call args, assignment, let, struct/array literals,
# return, array index) - previously all rejected a diverging value ---


def test_annotated_let_initializer_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        let x: i32 = { return 5; };
        return x + 100;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_comptime_annotated_let_initializer_diverges(tmp_path):
    # A module-level let initializer can't itself contain `return` (there's
    # no function to return from), but a function it calls can - so this
    # is the shape that actually exercises the comptime interpreter here.
    src = """
    fn f() i32 {
        let x: i32 = { return 5; };
        return x;
    }
    let y = f();
    pub fn main() i32 {
        return y;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_assignment_rhs_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 0;
        x = { return 5; };
        return x + 100;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_call_arg_diverges(tmp_path):
    src = """
    fn f(a: i32) i32 {
        return a;
    }
    pub fn main() i32 {
        return f({ return 5; });
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_struct_field_diverges(tmp_path):
    src = """
    struct T { a: i32 }
    pub fn main() i32 {
        let t = T { a: { return 5; } };
        return t.a + 100;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_array_element_diverges(tmp_path):
    # Not the first element - see test_array_first_element_diverges_still_rejected.
    src = """
    pub fn main() i32 {
        let a = [1, { return 5; }, 3];
        return a[0usize] + 100;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_array_index_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        let a = [1, 2, 3];
        return a[{ return 5; }] + 100;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


# --- Operators - deliberately a different mechanism from _coerce, since
# operators never coerce toward a target type - but never still unifies ---


def test_arithmetic_lhs_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return ({ return 5; }) + 1;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_arithmetic_rhs_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return 1 + ({ return 5; });
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_comparison_operand_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return if (1 == ({ return 5; })) { 0 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_unary_neg_operand_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return -({ return 5; });
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_unary_not_operand_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return if (not ({ return 5; })) { 0 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 5)


# --- if/while conditions: the same class of check as operator operands
# (a type comparison outside _coerce) ---


def test_if_condition_diverges(tmp_path):
    src = """
    pub fn main() i32 {
        return if ({ return 5; }) { 0 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_while_condition_diverges(tmp_path):
    src = """
    fn f() i32 {
        while ({ return 5; }) {
        };
        return 100;
    }
    pub fn main() i32 {
        return f();
    }
    """
    check_prog_output(tmp_path, src, "", 5)


# --- and/or's left-operand asymmetry (see tests/test_bool_ops.py for the
# full parametrized coverage) - one example here for this file's own
# completeness ---


def test_and_lhs_diverges(tmp_path):
    src = """
    fn f() i32 {
        let a = ({ return 5; }) and true;
        return 100;
    }
    pub fn main() i32 {
        return f();
    }
    """
    check_prog_output(tmp_path, src, "", 5)


# --- An initializer with no declared type never calls _coerce at all,
# so it takes a different path through build_let_stmt ---


def test_unannotated_let_diverges_still_works(tmp_path):
    # No declared type, so x just takes the initializer's own (never)
    # type directly, with no coercion involved.
    src = """
    pub fn main() i32 {
        let x = { return 5; };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 5)


# --- Known limitation ---


def test_array_first_element_diverges_still_rejected(tmp_path):
    # Array literal element type is inferred from the first element when
    # there's no expected-type hint. If the first element diverges, the
    # inferred "common type" becomes never, and the other (concrete)
    # elements then fail to coerce into it - a rough edge in that
    # inference, unrelated to whether a never-typed value itself coerces.
    src = """
    pub fn main() i32 {
        let a = [{ return 5; }, 2, 3];
        return 0;
    }
    """
    with pytest.raises(IncompatibleTypInArrayExpr):
        compile_str(tmp_path, src)


def test_not_diverges_does_not_propagate_past_bool(tmp_path):
    # not's operand coerces to bool via _coerce (like if/while's condition
    # and and/or's operands), rather than propagating never as the whole
    # not-expression's own type. Its result is only ever used as bool -
    # e.g. as an if/while condition, or an and/or operand - so this
    # doesn't come up in practice, but it does mean the never-ness stops
    # at the not, rather than continuing to coerce to some other type a
    # diverging expression could otherwise stand in for directly.
    src = """
    pub fn main() i32 {
        let x: i32 = not ({ return 5; });
        return x;
    }
    """
    with pytest.raises(IncompatibleLetTypError):
        compile_str(tmp_path, src)
