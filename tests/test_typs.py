import pathlib

import pytest
from l0 import l0ast as ast
from l0.ir_env import Env
from l0.l0errors import IntLitOverflowError
from l0.parse import build_parser
from l0.src import SrcFile
from l0.typs import Typ
from util import check_prog_output, compile_str, find_pos


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
    file = SrcFile(pathlib.Path("test.l0"))
    tree = build_parser("typ").parse(src)
    typ = Typ.from_ast(ast.Typ.from_tree(file, tree), Env())
    assert typ.name == expected
