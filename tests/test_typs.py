import pathlib

import pytest
from util import check_prog_output, compile_str, find_pos

from leech import ast
from leech.errors import IncompatibleLetTypError, IntLitOverflowError
from leech.ir_env import Env
from leech.parse import build_parser
from leech.src import SrcFile
from leech.typs import Typ


@pytest.mark.parametrize(
    "typ,value",
    (
        ("bool", "true"),
        ("i32", "1"),
        ("i32", "1i32"),
        ("u17", "10u17"),
        ("usize", "99usize"),
        ("isize", "54isize"),
    ),
)
def test_builtin_typ_lookup(tmp_path, typ, value):
    src = f"""
    fn f() {typ} {{ {value} }}

    pub fn main() i32 {{
        f();
        return 0;
    }}
    """
    check_prog_output(tmp_path, src, "", 0)


def test_int_lit_at_typ_width_boundary_is_allowed(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 255u8;
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_int_lit_overflow(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 256u8;
        return 0;
    }
    """
    with pytest.raises(IntLitOverflowError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "256u8")


def test_comptime_int_lit_overflow(tmp_path):
    src = """
    let x = 256u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntLitOverflowError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "256" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "256u8")


def test_int_lit_at_signed_typ_max_is_allowed(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 127i8;
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_int_lit_overflow_signed(tmp_path):
    # 128 doesn't fit in i8 even though it fits in 8 bits, since i8's
    # range is -128..127, not 0..255 - a signed literal one past its
    # type's max positive value must still be rejected.
    src = """
    pub fn main() i32 {
        let x = 128i8;
        return 0;
    }
    """
    with pytest.raises(IntLitOverflowError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "128i8")


def test_comptime_int_lit_overflow_signed(tmp_path):
    src = """
    let x = 128i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntLitOverflowError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "128" in msg
    assert '"i8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "128i8")


def test_int_lit_infers_declared_let_typ(tmp_path):
    # 10 is a u8 here, not an i32 that coerces to one - i32 -> u8 is a
    # narrowing conversion the language rejects.
    src = """
    pub fn main() i32 {
        let x: u8 = 10;
        let y = x + 200u8;
        return if (y == 210u8) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize(
    "prelude,expr",
    (
        # Every context with a single unambiguous target type.
        ("fn takes(v: u8) u8 { return v; }", "takes(200)"),
        ("struct T { a: u8 }", "T { a: 200 }.a"),
        ("fn first(a: [u8; 2]) u8 { return a.[0]; }", "first([200, 1])"),
        ("fn ret() u8 { return 200; }", "ret()"),
        ("fn tail() u8 { 200 }", "tail()"),
        ("fn cond() u8 { if (true) { 200 } else { 1 } }", "cond()"),
    ),
)
def test_int_lit_infers_at_coercion_points(tmp_path, prelude, expr):
    # 200 doesn't fit i8 and i32 doesn't coerce to u8, so each of these
    # only compiles if the literal is inferred as u8 in the first place.
    src = f"""
    {prelude}
    pub fn main() i32 {{
        let x: u8 = {expr};
        return if (x == 200u8) {{ 7 }} else {{ 0 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 7)


def test_int_lit_infers_in_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 0u8;
        x = 200;
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_comptime_int_lit_infers_declared_typ(tmp_path):
    src = """
    let x: u8 = 200;
    pub fn main() i32 {
        return if (x == 200u8) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_int_lit_inference_reaches_operands(tmp_path):
    # Neither operand's type is decided by the operand itself, so both
    # take the declared type of the variable they end up in.
    src = """
    pub fn main() i32 {
        let x: u8 = 200 + 55;
        return if (x == 255u8) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_int_lit_inference_does_not_reach_across_a_typed_operand(tmp_path):
    # An operand whose type *is* decided still has to match its peer
    # exactly: nothing coerces i32 to u8, so this stays an error.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32 + 1;
        return 0;
    }
    """
    with pytest.raises(IncompatibleLetTypError):
        compile_str(tmp_path, src)


def test_explicit_int_lit_suffix_beats_inference(tmp_path):
    # A written suffix fixes the type, so this is an i32 -> u8 narrowing.
    src = """
    pub fn main() i32 {
        let x: u8 = 1i32;
        return 0;
    }
    """
    with pytest.raises(IncompatibleLetTypError):
        compile_str(tmp_path, src)


def test_int_lit_too_big_for_inferred_typ(tmp_path):
    src = """
    pub fn main() i32 {
        let x: u8 = 300;
        return 0;
    }
    """
    with pytest.raises(IntLitOverflowError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "300" in msg
    assert '"u8"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "300")


@pytest.mark.parametrize(
    "src,expected",
    (
        ("*i32", "*i32"),
        ("*mut i32", "*mut i32"),
        ("**mut u8", "**mut u8"),
        ("[*mut i32; 2]", "[*mut i32; 2]"),
    ),
)
def test_ptr_typ_name_matches_source_syntax(src, expected):
    # Type names appear in diagnostics, so they should read the way the
    # type would be written.
    file = SrcFile(pathlib.Path("test.leech"))
    tree = build_parser("typ").parse(src)
    typ = Typ.from_ast(ast.Typ.from_tree(file, tree), Env())
    assert typ.name == expected
