# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""A tree-walking interpreter for evaluating IR at compile time.

Used to evaluate module-level ``let`` initializers (see
:class:`leech.ir_module.ModVar`), which must be computable without running
generated code.
"""

from typing import Final, Optional

from leech import asserts, errors, ir_module, ir_values, target, typs


def _is_power_of_two(n: int) -> bool:
    return n & (n - 1) == 0


def _panic_message(args: tuple[ir_values.ComptimeValue, ...]) -> Optional[str]:
    """Extract ``panic``'s message argument as plain text, if possible.

    :param args: ``panic``'s (already-evaluated) call arguments.
    :return: The message, or ``None`` if it isn't a compile-time-known
        string literal (e.g. a pointer computed some other way).
    """
    if len(args) != 1 or not isinstance(args[0], ir_values.ComptimeCStr):
        return None
    return args[0].value.rstrip(b"\0").decode()


def _wrap(value: int, typ: typs.IntTyp) -> int:
    """Reduce ``value`` to the value a real, ``typ``-width two's-complement
    ``add``/``sub``/``mul``/``neg`` would actually produce.

    Mirrors LLVM's wrapping arithmetic (and codegen's real,
    ``llvm.*.with.overflow``-based instructions - see
    :class:`~leech.ir_values.CheckedAddInstr`): this is the interpreter's
    equivalent computation, used unconditionally rather than only on
    overflow, so a wrapped result and an in-range one are computed (and
    look) exactly the same way. Overflow itself is reported separately,
    by whatever compiler-synthesized check (see
    :meth:`~leech.ir_builder.CfgBuilder._panic_if`) follows the operation
    in the same CFG - this never raises.

    :param value: The exact, unbounded mathematical result.
    :param typ: The type to wrap it into.
    :return: The wrapped value, canonicalized the same way
        :class:`~leech.ir_values.ComptimeInt` always is (negative for a
        signed type, never negative for an unsigned one).
    """
    span = typ.max_value - typ.min_value + 1
    return (value - typ.min_value) % span + typ.min_value


class Interpreter:
    """Executes the instructions of a :class:`~leech.ir_values.Cfg` at compile time.

    Walks the control-flow graph from its entry block, evaluating each
    instruction in turn and recording its result, until a ``ret``
    instruction is reached.

    :param cfg: The control-flow graph to execute.
    :param params: The callee's formal parameters, used to bind ``args``.
    :param args: The compile-time-known argument values, positionally
        matched to ``params``.
    :param panic_fn: The program's real ``panic`` function (see
        :attr:`~leech.ir_env.Env.panic_fn`), if known yet - a call to
        exactly this function is recognized and reported as
        :class:`~leech.errors.PanicAtComptimeError` instead of being
        recursed into (where it would otherwise fail with a confusing
        :class:`~leech.errors.CallExternFnAtComptimeError`, from the
        ``write``/``abort`` extern calls inside ``panic``'s own body).
        This is how every compiler-synthesized runtime check (array
        bounds, integer overflow, division by zero) is caught at compile
        time too - there's no separate compile-time-only detection of any
        of those; they're all just ``panic`` calls, like any the source
        itself might write. Propagated unchanged into every nested call's
        own ``Interpreter``.
    """

    _registers: Final[dict[ir_values.Value, ir_values.ComptimeValue]]
    _cfg: Final[ir_values.Cfg]
    _curr_bb: ir_values.BasicBlock
    _prev_bb: Optional[ir_values.BasicBlock]
    _curr_instr_index: int
    _ret_value: Optional[ir_values.ComptimeValue]
    _panic_fn: Final[Optional[ir_module.FnSpec]]
    #: Whether a CheckedAddInstr/CheckedSubInstr/CheckedMulInstr's own
    #: result overflowed, keyed by the instruction itself - consulted by
    #: its paired OverflowFlagInstr. Mirrors codegen's own
    #: Compiler._FnBuilderContext.overflow_calls, which the same pairing
    #: relies on at runtime.
    _overflow_flags: Final[dict[ir_values.Instr, bool]]

    def __init__(
        self,
        cfg: ir_values.Cfg,
        params: tuple[ir_values.Param, ...],
        args: tuple[ir_values.ComptimeValue, ...],
        panic_fn: Optional[ir_module.FnSpec] = None,
    ) -> None:
        self._cfg = cfg
        self._registers = dict(zip(params, args, strict=True))
        self._curr_bb = self._cfg.entry
        self._prev_bb = None
        self._curr_instr_index = 0
        self._ret_value = None
        self._panic_fn = panic_fn
        self._overflow_flags = {}

    def eval(self) -> ir_values.ComptimeValue:
        """Run the interpreter to completion and return the result.

        :return: The value passed to the ``ret`` instruction that ended
            execution.
        :raises CannotTakeAddressOfComptimeValueError: If the returned
            value, or a value returned by a nested call evaluated along
            the way, is or contains a pointer to a compile-time-only
            temporary that has no address at runtime.
        :raises SetNonLocalVarAtComptimeError: If a ``store`` instruction
            (directly, or in a nested call) writes through a pointer to a
            non-temporary (i.e. a variable outside the expression being
            evaluated).
        :raises CallExternFnAtComptimeError: If a ``call`` instruction
            (directly, or in a nested call) calls a function with no body.
        :raises PanicAtComptimeError: If a ``call`` instruction (directly,
            or in a nested call) calls ``panic_fn`` - including a call a
            compiler-synthesized runtime check made, so this is also how
            array-bounds, integer-overflow, and division-by-zero failures
            are reported at compile time.
        """
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

    def _eval_instr(self, instr: ir_values.Instr) -> None:
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
                        # CfgBuilder._build_checked_div always builds a
                        # runtime check ahead of the actual sdiv that
                        # panics on a zero divisor (an unchecked divide by
                        # zero is a trap, not a wrapping result, unlike
                        # add/sub/mul/neg - see that method's docstring) -
                        # so reaching an sdiv at all already proves rhs
                        # isn't zero.
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
                    case _:
                        raise AssertionError(f"invalid bin op instr {instr}")
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
                    case _:
                        raise AssertionError(f"invalid icmp op {instr.op!r}")
                # ComptimeInt.value already holds each operand's canonical
                # mathematical value (negative for signed types, always >= 0
                # for unsigned ones - see ComptimeInt), so plain Python
                # comparison is correct here regardless of whether this is a
                # signed or unsigned icmp.
                self._registers[instr] = ir_values.ComptimeBool(ret, instr.ast)

            case ir_values.OverflowFlagInstr():
                # instr.checked_op - a CheckedAddInstr/CheckedSubInstr/
                # CheckedMulInstr - already ran (registers are only ever
                # read after being written) and recorded whether it
                # overflowed in _overflow_flags, in the BinOpInstr case
                # above.
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
                base_agg = self._get_comptime_value(instr.aggregate).copy()
                agg = base_agg
                for index in instr.indeces[:-1]:
                    agg = asserts.checked_cast(agg, ir_values.ComptimeAggregate)
                    agg = agg.get_element(index.value)

                agg = asserts.checked_cast(agg, ir_values.ComptimeAggregate)
                agg.set_element(value, instr.indeces[-1].value)
                self._registers[instr] = base_agg
            case ir_values.CallInstr():
                callee = asserts.checked_cast(
                    self._get_comptime_value(instr.callee), ir_module.FnSpec
                )
                args = tuple(self._get_comptime_value(arg) for arg in instr.args)
                if self._panic_fn is not None and callee is self._panic_fn:
                    raise errors.PanicAtComptimeError(_panic_message(args), instr.span)
                cfg = callee.body_cfg()
                if cfg is None:
                    raise errors.CallExternFnAtComptimeError(callee.span)
                self._registers[instr] = Interpreter(
                    cfg, callee.params, args, self._panic_fn
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
                match instr.sized_typ:
                    case typs.BoolTyp():
                        size = 1
                    case typs.IntTyp() if instr.sized_typ.width >= 8 and _is_power_of_two(
                        instr.sized_typ.width
                    ):
                        # Only a width that's itself one of LLVM's own
                        # primitive integer sizes (8/16/32/64/128/...) has
                        # an unambiguous, target-independent byte size.
                        # Anything else - a width that isn't a whole number
                        # of bytes (e.g. i3), or is but isn't a power of
                        # two (e.g. i24, confirmed by hand to actually take
                        # 4 bytes, not 3, under this target's ABI rules) -
                        # gets padded by LLVM's own (target-dependent)
                        # alignment rules in a way this compiler doesn't
                        # replicate in Python, to avoid silently disagreeing
                        # with the real (GEP-idiom-based) runtime answer.
                        size = instr.sized_typ.width // 8
                    case typs.PtrTyp():
                        size = target.ADDR_SIZE // 8
                    case _:
                        raise errors.SizeOfNotComptimeEvaluableError(
                            instr.sized_typ.name, instr.span
                        )
                self._registers[instr] = ir_values.ComptimeInt(typs.USIZE, size, instr.ast)
            case ir_values.PtrCastInstr():
                # The Comptime* value model is value-oriented, not
                # byte-oriented (a ComptimeAlloc holds a typed
                # ComptimeValue, not a byte buffer, and never records its
                # own mutability separately from its pointee's), so it has
                # no sound way to reinterpret a pointer as a different
                # type - not even a mutability-only change.
                raise errors.PtrCastNotComptimeEvaluableError(instr.span)
            case ir_values.IsNullInstr():
                # No Comptime* pointer value the interpreter can ever
                # produce represents "null" - ComptimeAlloc/ComptimeGep/
                # FnSpec/ModVar all refer to something real; the only way
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
            case _:
                raise AssertionError(f"Invalid instruction {instr}")
