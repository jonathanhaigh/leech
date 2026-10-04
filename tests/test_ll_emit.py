# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import ll_emit

_ADD_IR = """target triple = "x86_64-linux-gnu"

define i32 @"add"(i32 %"a", i32 %"b") {
entry:
  %"a.addr" = alloca i32
  store i32 %"a", ptr %"a.addr"
  %"a.val" = load i32, ptr %"a.addr"
  %"sum" = add i32 %"a.val", %"b"
  ret i32 %"sum"
}
"""

_CALLER_IR = """declare i32 @"add"(i32, i32)

define i32 @"three"() {
entry:
  %"r" = call i32 @"add"(i32 1, i32 2)
  ret i32 %"r"
}
"""


@pytest.mark.parametrize(
    ("kind", "suffix"),
    (
        (ll_emit.EmitKind.LLVM_IR, ".ll"),
        (ll_emit.EmitKind.LLVM_BC, ".bc"),
        (ll_emit.EmitKind.ASM, ".s"),
        (ll_emit.EmitKind.OBJ, ".o"),
    ),
)
def test_emit_kind_suffixes(kind, suffix):
    assert kind.suffix == suffix


@pytest.mark.parametrize(
    ("kind", "prefix"),
    (
        (ll_emit.EmitKind.LLVM_BC, b"BC\xc0\xde"),
        (ll_emit.EmitKind.OBJ, b"\x7fELF"),
    ),
)
def test_emit_binary_formats_have_magic_numbers(kind, prefix):
    assert ll_emit.emit_from_ir(_ADD_IR, kind, 0).startswith(prefix)


def test_emit_asm_is_text_assembly():
    asm = ll_emit.emit_from_ir(_ADD_IR, ll_emit.EmitKind.ASM, 0).decode()

    assert ".text" in asm
    assert "add:" in asm


def test_unoptimized_llvm_ir_is_returned_unchanged():
    assert ll_emit.emit_from_ir(_ADD_IR, ll_emit.EmitKind.LLVM_IR, 0) == _ADD_IR.encode()


def test_optimization_removes_redundant_memory_traffic():
    unoptimized = ll_emit.emit_from_ir(_ADD_IR, ll_emit.EmitKind.LLVM_IR, 0).decode()
    optimized = ll_emit.emit_from_ir(_ADD_IR, ll_emit.EmitKind.LLVM_IR, 2).decode()

    assert "alloca" in unoptimized
    assert "alloca" not in optimized


def test_link_resolves_declarations_across_modules():
    linked = ll_emit.link([ll_emit.parse(_CALLER_IR), ll_emit.parse(_ADD_IR)])

    ll_emit.optimize(linked, 2)

    assert "ret i32 3" in str(linked)


def test_optimize_rejects_unsupported_level():
    with pytest.raises(AssertionError):
        ll_emit.optimize(ll_emit.parse(_ADD_IR), 4)
