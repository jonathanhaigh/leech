# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import lark
import pytest
import util

from leech import errors, parse, reserved

_DECLARATIONS = {
    "struct_name": "struct if { mut x: i32 }\npub fn main() i32 { return 0; }",
    "enum_name": "enum if(u8) { A }\npub fn main() i32 { return 0; }",
    "trait_name": "trait if { fn f(*self) i32; }\npub fn main() i32 { return 0; }",
    "fn_name": "fn struct() i32 { return 0; }\npub fn main() i32 { return 0; }",
    "let_binding": "pub fn main() i32 { let return = 5; return 0; }",
    "fn_param": "fn f(if: i32) i32 { return 0; }\npub fn main() i32 { return 0; }",
    # Prototypes have no body, so nothing type-checks their parameters.
    "extern_fn_param": "extern fn f(if: i32) i32;\npub fn main() i32 { return 0; }",
    "trait_method_param": (
        "trait T { fn f(*self, if: i32) i32; }\npub fn main() i32 { return 0; }"
    ),
    "struct_field": "struct Foo { mut if: i32 }\npub fn main() i32 { return 0; }",
    "enum_variant": "enum E(u8) { if }\npub fn main() i32 { return 0; }",
    "trait_method": "trait T { fn if(*self) i32; }\npub fn main() i32 { return 0; }",
    "generic_param": "fn f[if](x: if) i32 { return 0; }\npub fn main() i32 { return 0; }",
    "value_param": (
        "fn f[value value: usize]() i32 { return 0; }\npub fn main() i32 { return 0; }"
    ),
    # "value" is a keyword only where a comptime parameter may start, so
    # the lexer lets it through everywhere else and the check must run.
    "value_keyword_as_struct_field": (
        "struct Foo { mut value: i32 }\npub fn main() i32 { return 0; }"
    ),
    "inherent_method": (
        "struct Foo { mut x: i32 }\n"
        "impl Foo { fn if(*self) i32 { 0 } }\n"
        "pub fn main() i32 { return 0; }"
    ),
}


@pytest.mark.parametrize("decl", list(_DECLARATIONS))
def test_keyword_rejected_as_declared_name(tmp_path, decl):
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, _DECLARATIONS[decl])


_BUILTIN_TYP_NAMES = {
    "int_typ_as_struct": "struct i32 { mut x: bool }\npub fn main() i32 { return 0; }",
    "int_typ_as_local": "pub fn main() i32 { let i32 = 5; return 0; }",
    "int_typ_as_field": "struct Foo { mut i32: i32 }\npub fn main() i32 { return 0; }",
    "bool_as_struct": "struct bool { mut x: i32 }\npub fn main() i32 { return 0; }",
    "usize_as_struct": "struct usize { mut x: i32 }\npub fn main() i32 { return 0; }",
    "never_as_struct": "struct never { mut x: i32 }\npub fn main() i32 { return 0; }",
    "self_typ_as_local": "pub fn main() i32 { let Self = 5; return 0; }",
}


@pytest.mark.parametrize("decl", list(_BUILTIN_TYP_NAMES))
def test_compiler_bound_typ_name_rejected_as_declared_name(tmp_path, decl):
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, _BUILTIN_TYP_NAMES[decl])


def test_keyword_set_matches_grammar():
    """The hand-written keyword list must not drift from the grammar."""
    terminals = parse.build_parser("mod").terminals
    from_grammar = {
        t.pattern.value
        for t in terminals
        if isinstance(t.pattern, lark.lexer.PatternStr) and t.pattern.value.isidentifier()
    }
    assert from_grammar == reserved.KEYWORDS


def test_ordinary_names_are_not_reserved():
    for name in ("foo", "Foo", "i32x", "iffy", "selfish", "Self2", "boolean", "i", "u"):
        assert not reserved.is_reserved(name)


def test_reserved_names_are_reserved():
    for name in ("if", "struct", "self", "Self", "never", "bool", "usize", "i32", "u8", "i7"):
        assert reserved.is_reserved(name)


_LOOP_LABELS = {
    "int_typ": "pub fn main() i32 { i32: while (true) { break i32; } return 0; }",
    "bool": "pub fn main() i32 { bool: while (true) { break bool; } return 0; }",
    "self_typ": "pub fn main() i32 { Self: while (true) { break Self; } return 0; }",
    "never": "pub fn main() i32 { never: while (true) { break never; } return 0; }",
    # Keyword labels are only inconsistently caught by the lexer: "if" fails
    # to parse where "struct" reaches type checking, so the check must run.
    "keyword": "pub fn main() i32 { struct: while (true) { break struct; } return 0; }",
}


@pytest.mark.parametrize("label", list(_LOOP_LABELS))
def test_reserved_name_rejected_as_loop_label(tmp_path, label):
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, _LOOP_LABELS[label])


#: Longer than CPython's int-from-string digit limit, so parsing the width
#: would raise rather than yield a number.
_UNPARSEABLE_WIDTH_TYP_NAME = "i" + "9" * 5000


def test_int_typ_name_is_reserved_however_long_its_width():
    # Reservation is about spelling, not about whether the width names a
    # type Leech could actually build.
    assert reserved.is_reserved(_UNPARSEABLE_WIDTH_TYP_NAME)


def test_int_typ_name_with_unparseable_width_rejected_as_declaration(tmp_path):
    src = (
        f"struct {_UNPARSEABLE_WIDTH_TYP_NAME} {{ mut x: i32 }}\npub fn main() i32 {{ return 0; }}"
    )
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, src)
