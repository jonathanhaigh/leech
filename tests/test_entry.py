# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors
from tests import harness


def test_private_main_is_entry_point(compiler):
    compiler.check("fn main() i32 { 4 }", exit_status=4)


def test_root_not_named_main_is_entry_point(compiler):
    program = harness.TestProgram(harness.ModSrc("app", "pub fn main() i32 { 5 }"))

    compiled = compiler.compile(program, entry=True)

    llvm_ir = compiled.mods["app"].llvm_ir
    assert 'define i32 @"app::main"()' in llvm_ir
    assert 'call i32 @"app::main"()' in llvm_ir
    compiler.check(program, exit_status=5)


def test_entry_module_can_be_imported_by_name(compiler):
    program = harness.TestProgram(
        harness.ModSrc(
            "app", "import helper; pub fn base() i32 { 5 } fn main() i32 { helper::twice() }"
        ),
        (harness.ModSrc("helper", "import app; pub fn twice() i32 { app::base() * 2 }"),),
    )

    compiler.check(program, exit_status=10)


def test_entry_is_off_by_default(compiler):
    llvm_ir = compiler.compile("pub fn main() i32 { 0 }").mods["main"].llvm_ir

    assert 'define i32 @"main::main"()' in llvm_ir
    assert 'define i32 @"main"()' not in llvm_ir


def test_missing_main_is_reported(compiler):
    with pytest.raises(errors.EntryMainMissingError) as exc_info:
        compiler.compile("pub fn other() i32 { 0 }", entry=True)

    assert exc_info.value.message.span is None
    assert str(exc_info.value).startswith('Entry module "main" (')
    assert str(exc_info.value).endswith('main.leech) has no "main" function')


@pytest.mark.parametrize(
    ("src", "span_substring"),
    (
        ("pub let main: i32 = 0;", "pub let main"),
        ("extern fn main() i32;", "extern fn main"),
    ),
)
def test_main_that_is_not_a_defined_fn_is_reported(compiler, src, span_substring):
    with pytest.raises(errors.EntryMainNotDefinedFnError) as exc_info:
        compiler.compile(src, entry=True)

    harness.assert_span_at(exc_info.value.message.span, src, span_substring)


def test_generic_main_is_reported(compiler):
    src = "pub fn main[T]() i32 { 0 }"

    with pytest.raises(errors.EntryMainGenericError) as exc_info:
        compiler.compile(src, entry=True)

    harness.assert_span_at(exc_info.value.message.span, src, "main")


@pytest.mark.parametrize(
    ("src", "fn_typ_name"),
    (
        ("pub fn main(x: i32) i32 { x }", "fn(i32) i32"),
        ("pub fn main() {}", "fn() void"),
        ("pub fn main() u8 { 0 }", "fn() u8"),
    ),
)
def test_main_with_wrong_signature_is_reported(compiler, src, fn_typ_name):
    with pytest.raises(errors.EntryMainSignatureError) as exc_info:
        compiler.compile(src, entry=True)

    assert str(exc_info.value) == (
        f'The program entry point "main" must have type "fn() i32", not "{fn_typ_name}"'
    )
    harness.assert_span_at(exc_info.value.message.span, src, "main")


@pytest.mark.parametrize(
    "program",
    (
        harness.TestProgram.from_main("pub struct main {}"),
        harness.TestProgram(
            harness.ModSrc("app", "import main;"),
            (harness.ModSrc("main", "pub fn thing() i32 { 1 }"),),
        ),
    ),
    ids=("type", "imported module"),
)
def test_type_or_module_named_main_is_not_a_main_function(compiler, program):
    with pytest.raises(errors.EntryMainMissingError):
        compiler.compile(program, entry=True)


def test_main_function_is_found_beside_a_type_named_main(compiler):
    compiler.check("pub struct main {} pub fn main() i32 { 2 }", exit_status=2)


def test_imported_extern_main_with_entry_type_shares_the_entry_symbol(compiler):
    program = harness.TestProgram.from_main(
        "import helper; pub fn main() i32 { 3 }",
        harness.ModSrc("helper", "extern fn main() i32;"),
    )

    llvm_ir = compiler.compile(program, entry=True).mods["main"].llvm_ir

    assert llvm_ir.count('@"main"()') == 1
    assert 'define i32 @"main"()' in llvm_ir
    compiler.check(program, exit_status=3)


def test_imported_extern_main_with_other_type_is_reported(compiler):
    helper_src = "extern fn main(argc: i32) i32;"
    program = harness.TestProgram.from_main(
        "import helper; pub fn main() i32 { 0 }", harness.ModSrc("helper", helper_src)
    )

    with pytest.raises(errors.EntryMainExternConflictError) as exc_info:
        compiler.compile(program, entry=True)

    assert '"fn(i32) i32"' in str(exc_info.value)
    harness.assert_span_at(exc_info.value.message.span, helper_src, "extern fn main")
