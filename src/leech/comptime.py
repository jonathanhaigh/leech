# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""A tree-walking IR interpreter for compile-time evaluation."""

from typing import Final, Optional

from llvmlite import ir as ll

from leech import asserts, errors, ir_module, ir_values, ll_typs, target, typs


def _size_of(sized_typ: typs.TypKind) -> int:
    """Return ``sized_typ``'s real, target-ABI-accurate size in bytes."""
    ctx = ll.Context()
    ll_typ = ll_typs.ll_typ(ctx, sized_typ)
    return ll_typ.get_abi_size(target.target_data(), context=ctx)


def _panic_message(args: tuple[ir_values.ComptimeValue, ...]) -> Optional[str]:
    """Return ``panic``'s message when it is a compile-time-known string."""
    if len(args) != 1 or not isinstance(args[0], ir_values.ComptimeCStr):
        return None
    return args[0].value.rstrip(b"\0").decode()


def _wrap(value: int, typ: typs.IntTyp) -> int:
    """Wrap ``value`` to ``typ``'s canonical two's-complement value without raising."""
    span = typ.max_value - typ.min_value + 1
    return (value - typ.min_value) % span + typ.min_value


class Interpreter:
    """Execute a control-flow graph with compile-time-known arguments.

    Calls to ``panic_ref`` become compile-time panic diagnostics, including calls emitted by
    runtime checks. The same function identity propagates into nested interpreters.
    """

    _registers: Final[dict[ir_values.Value, ir_values.ComptimeValue]]
    _cfg: Final[ir_values.Cfg]
    _curr_bb: ir_values.BasicBlock
    _prev_bb: Optional[ir_values.BasicBlock]
    _curr_instr_index: int
    _ret_value: Optional[ir_values.ComptimeValue]
    _panic_ref: Final[Optional[ir_module.FnRef]]
    #: Overflow results consumed by the checked operation's paired flag instruction.
    _overflow_flags: Final[dict[ir_values.Instr, bool]]

    def __init__(
        self,
        cfg: ir_values.Cfg,
        params: tuple[ir_values.Param, ...],
        args: tuple[ir_values.ComptimeValue, ...],
        panic_ref: Optional[ir_module.FnRef] = None,
    ) -> None:
        self._cfg = cfg
        self._registers = dict(zip(params, args, strict=True))
        self._curr_bb = self._cfg.entry
        self._prev_bb = None
        self._curr_instr_index = 0
        self._ret_value = None
        self._panic_ref = panic_ref
        self._overflow_flags = {}

    def eval(self) -> ir_values.ComptimeValue:
        """Run to ``ret`` and reject results or effects unavailable at runtime."""
        while True:
            if self._ret_value is not None:
                self._check_not_temporary(self._ret_value)
                return self._ret_value
            instr_index = self._curr_instr_index
            asserts.assert_lt(instr_index, len(self._curr_bb.instrs))
            self._curr_instr_index += 1
            self._eval_instr(self._curr_bb.instrs[instr_index])

    def _get_comptime_value(self, value: ir_values.Value) -> ir_values.ComptimeValue:
        if isinstance(value, ir_values.ComptimeValue):
            return value
        return self._registers[value]

    def _check_not_temporary(self, value: ir_values.ComptimeValue) -> None:
        if isinstance(value, ir_values.ComptimePtr) and value.is_temporary():
            raise errors.CannotTakeAddressOfComptimeValueError(value.span)
        if isinstance(value, ir_values.ComptimeAggregate):
            for elt in value.values():
                self._check_not_temporary(elt)

    def _eval_instr(self, instr: ir_values.InstrKind) -> None:
        match instr:
            case ir_values.BinOpInstr():
                lhs = asserts.checked_cast(
                    self._get_comptime_value(instr.lhs), ir_values.ComptimeInt
                )
                rhs = asserts.checked_cast(
                    self._get_comptime_value(instr.rhs), ir_values.ComptimeInt
                )
                asserts.assert_eq(lhs.typ, rhs.typ)
                match instr:
                    case ir_values.AddInstr():
                        ret = lhs.value + rhs.value
                        self._overflow_flags[instr] = not lhs.typ.fits(ret)
                    case ir_values.SubInstr():
                        ret = lhs.value - rhs.value
                        self._overflow_flags[instr] = not lhs.typ.fits(ret)
                    case ir_values.MulInstr():
                        ret = lhs.value * rhs.value
                        self._overflow_flags[instr] = not lhs.typ.fits(ret)
                    case ir_values.SdivInstr():
                        # A preceding checked-div guard ensures rhs is nonzero.
                        assert rhs.value != 0
                        # LLVM's sdiv truncates toward zero (like C's `/`),
                        # unlike Python's `//`, which floors toward negative
                        # infinity - they only differ when truncation is
                        # needed and the operands' signs differ.
                        ret = abs(lhs.value) // abs(rhs.value)
                        if (lhs.value < 0) != (rhs.value < 0):
                            ret = -ret
                    case ir_values.UdivInstr():
                        assert rhs.value != 0
                        ret = lhs.value // rhs.value
                self._registers[instr] = ir_values.ComptimeInt(
                    lhs.typ, _wrap(ret, lhs.typ), instr.ast
                )

            case ir_values.NegInstr():
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimeInt
                )
                self._registers[instr] = ir_values.ComptimeInt(
                    operand.typ, _wrap(-operand.value, operand.typ), instr.ast
                )

            case ir_values.NotInstr():
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimeBool
                )
                self._registers[instr] = ir_values.ComptimeBool(not operand.value, instr.ast)

            case ir_values.IcmpSignedInstr() | ir_values.IcmpUnsignedInstr():
                lhs = asserts.checked_cast(
                    self._get_comptime_value(instr.lhs), ir_values.ComptimeInt
                )
                rhs = asserts.checked_cast(
                    self._get_comptime_value(instr.rhs), ir_values.ComptimeInt
                )
                asserts.assert_eq(lhs.typ, rhs.typ)
                match instr.op:
                    case "<":
                        ret = lhs.value < rhs.value
                    case "<=":
                        ret = lhs.value <= rhs.value
                    case "==":
                        ret = lhs.value == rhs.value
                    case "!=":
                        ret = lhs.value != rhs.value
                    case ">=":
                        ret = lhs.value >= rhs.value
                    case ">":
                        ret = lhs.value > rhs.value
                # ComptimeInt.value already holds each operand's canonical
                # mathematical value (negative for signed types, always >= 0
                # for unsigned ones - see ComptimeInt), so plain Python
                # comparison is correct here regardless of whether this is a
                # signed or unsigned icmp.
                self._registers[instr] = ir_values.ComptimeBool(ret, instr.ast)

            case ir_values.OverflowFlagInstr():
                # Registers are read only after the checked operation recorded its flag.
                self._registers[instr] = ir_values.ComptimeBool(
                    self._overflow_flags[instr.checked_op], instr.ast
                )

            case ir_values.LoadInstr():
                src = asserts.checked_cast(
                    self._get_comptime_value(instr.src), ir_values.ComptimePtr
                )
                self._registers[instr] = src.load()
            case ir_values.IntExtInstr():
                # Widening never changes the value, only the type it's
                # held in.
                int_value = asserts.checked_cast(
                    self._get_comptime_value(instr.value), ir_values.ComptimeInt
                )
                self._registers[instr] = ir_values.ComptimeInt(
                    instr.typ, int_value.value, instr.ast
                )
            case ir_values.AllocaInstr():
                self._registers[instr] = ir_values.ComptimeAlloc(
                    instr.allocated_typ, instr.mut, instr.ast
                )
            case ir_values.StoreInstr():
                dest = asserts.checked_cast(
                    self._get_comptime_value(instr.dest), ir_values.ComptimePtr
                )
                if not dest.is_temporary():
                    raise errors.SetNonLocalVarAtComptimeError(instr.span)
                value = self._get_comptime_value(instr.value)
                dest.store(value)
            case ir_values.GepInstr():
                base = asserts.checked_cast(
                    self._get_comptime_value(instr.base), ir_values.ComptimePtr
                )
                index = asserts.checked_cast(
                    self._get_comptime_value(instr.index), ir_values.ComptimeInt
                )
                self._registers[instr] = ir_values.ComptimeGep(base, index, instr.ast)
            case ir_values.InsertValueInstr():
                value = self._get_comptime_value(instr.value)
                agg = self._get_comptime_value(instr.aggregate).copy()
                agg = asserts.checked_cast(agg, ir_values.ComptimeAggregate)
                agg.set_element(value, instr.index.value)
                self._registers[instr] = agg
            case ir_values.CallInstr():
                callee = asserts.checked_cast(
                    self._get_comptime_value(instr.callee), ir_module.FnRef
                )
                args = tuple(self._get_comptime_value(arg) for arg in instr.args)
                if self._panic_ref is not None and callee is self._panic_ref:
                    raise errors.PanicAtComptimeError(_panic_message(args), instr.span)
                fn = callee.instance
                if not fn.has_body:
                    raise errors.CallExternFnAtComptimeError(callee.span)
                self._registers[instr] = Interpreter(
                    fn.cfg, fn.params, args, self._panic_ref
                ).eval()
            case ir_values.PhiInstr():
                assert self._prev_bb is not None
                self._registers[instr] = self._get_comptime_value(instr.incoming[self._prev_bb])
            case ir_values.BranchInstr():
                self._prev_bb = self._curr_bb
                self._curr_bb = instr.target
                self._curr_instr_index = 0
            case ir_values.CbranchInstr():
                cond = asserts.checked_cast(
                    self._get_comptime_value(instr.condition), ir_values.ComptimeBool
                )
                self._prev_bb = self._curr_bb
                self._curr_bb = instr.true_target if cond.value else instr.false_target
                self._curr_instr_index = 0
            case ir_values.RetInstr():
                self._ret_value = self._get_comptime_value(instr.value)
            case ir_values.SizeOfInstr():
                # Every type the grammar can produce as __size_of[T]'s T
                # (bool, an integer width, a pointer, a struct, an array,
                # an enum) is handled by _size_of - nothing reaches this
                # instruction with a type it can't compute a real,
                # ABI-accurate size for.
                size = _size_of(instr.sized_typ)
                self._registers[instr] = ir_values.ComptimeInt(typs.USIZE, size, instr.ast)
            case ir_values.PtrCastInstr():
                # The Comptime* value model is value-oriented, not
                # byte-oriented (a ComptimeAlloc holds a typed
                # ComptimeValue, not a byte buffer, and never records its
                # own mutability separately from its pointee's), so it has
                # no sound way to reinterpret a pointer as a different
                # type - not even a mutability-only change.
                raise errors.PtrCastNotComptimeEvaluableError(instr.span)
            case ir_values.PtrMutRelaxInstr():
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimePtr
                )
                self._registers[instr] = ir_values.ComptimePtrMutRelax(operand, instr.ast)
            case ir_values.IsNullInstr():
                # No Comptime* pointer value the interpreter can ever
                # produce represents "null" - ComptimeAlloc/ComptimeGep/
                # FnRef/ModVar all refer to something real; the only way
                # to observe an actual null pointer is via malloc, which is
                # extern and already blocked at comptime above.
                self._registers[instr] = ir_values.ComptimeBool(False, instr.ast)
            case ir_values.EnumToIntInstr():
                # Sound, unlike PtrCastInstr: this reads the same value
                # under its backing type, not a different type's bytes.
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimeEnum
                )
                self._registers[instr] = ir_values.ComptimeInt(instr.typ, operand.value, instr.ast)
            case ir_values.UnreachableInstr():
                raise AssertionError("Should never reach unreachable instruction")
