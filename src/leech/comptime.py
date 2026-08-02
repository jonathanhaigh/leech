"""A tree-walking interpreter for evaluating IR at compile time.

Used to evaluate module-level ``let`` initializers (see
:class:`leech.ir_module.ModVar`), which must be computable without running
generated code.
"""

from typing import Final, Optional

from leech import asserts, errors, ir_module, ir_values


class Interpreter:
    """Executes the instructions of a :class:`~leech.ir_values.Cfg` at compile time.

    Walks the control-flow graph from its entry block, evaluating each
    instruction in turn and recording its result, until a ``ret``
    instruction is reached.

    :param cfg: The control-flow graph to execute.
    :param params: The callee's formal parameters, used to bind ``args``.
    :param args: The compile-time-known argument values, positionally
        matched to ``params``.
    """

    registers: Final[dict[ir_values.Value, ir_values.ComptimeValue]]
    cfg: Final[ir_values.Cfg]
    curr_bb: ir_values.BasicBlock
    prev_bb: Optional[ir_values.BasicBlock]
    curr_instr_index: int
    ret_value: Optional[ir_values.ComptimeValue]

    def __init__(
        self,
        cfg: ir_values.Cfg,
        params: tuple[ir_values.Param, ...],
        args: tuple[ir_values.ComptimeValue, ...],
    ) -> None:
        self.cfg = cfg
        self.registers = dict(zip(params, args, strict=True))
        self.curr_bb = self.cfg.entry
        self.prev_bb = None
        self.curr_instr_index = 0
        self.ret_value = None

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
        :raises DivisionByZeroAtComptimeError: If a ``div`` instruction
            (directly, or in a nested call) divides by zero.
        :raises IntOverflowAtComptimeError: If an arithmetic instruction's
            result (directly, or in a nested call) doesn't fit in its
            operands' type.
        """
        while True:
            if self.ret_value is not None:
                self._check_not_temporary(self.ret_value)
                return self.ret_value
            instr_index = self.curr_instr_index
            asserts.assert_lt(instr_index, len(self.curr_bb.instrs))
            self.curr_instr_index += 1
            self._eval_instr(self.curr_bb.instrs[instr_index])

    def _get_comptime_value(self, value: ir_values.Value) -> ir_values.ComptimeValue:
        if isinstance(value, ir_values.ComptimeValue):
            return value
        return self.registers[value]

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
                    case ir_values.SubInstr():
                        ret = lhs.value - rhs.value
                    case ir_values.MulInstr():
                        ret = lhs.value * rhs.value
                    case ir_values.SdivInstr() | ir_values.UdivInstr():
                        if rhs.value == 0:
                            raise errors.DivisionByZeroAtComptimeError(instr.span)
                        ret = lhs.value // rhs.value
                    case _:
                        raise AssertionError(f"invalid bin op instr {instr}")
                # TODO: sdiv vs udiv?
                if not lhs.typ.fits(ret):
                    raise errors.IntOverflowAtComptimeError(ret, lhs.typ.name, instr.span)
                self.registers[instr] = ir_values.ComptimeInt(lhs.typ, ret, instr.ast)

            case ir_values.NegInstr():
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimeInt
                )
                ret = -operand.value
                if not operand.typ.fits(ret):
                    raise errors.IntOverflowAtComptimeError(ret, operand.typ.name, instr.span)
                self.registers[instr] = ir_values.ComptimeInt(operand.typ, ret, instr.ast)

            case ir_values.NotInstr():
                operand = asserts.checked_cast(
                    self._get_comptime_value(instr.operand), ir_values.ComptimeBool
                )
                self.registers[instr] = ir_values.ComptimeBool(not operand.value, instr.ast)

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
                # TODO: signed vs unsigned?
                self.registers[instr] = ir_values.ComptimeBool(ret, instr.ast)

            case ir_values.LoadInstr():
                src = asserts.checked_cast(
                    self._get_comptime_value(instr.src), ir_values.ComptimePtr
                )
                self.registers[instr] = src.load()
            case ir_values.IntExtInstr():
                # Widening never changes the value, only the type it's
                # held in.
                int_value = asserts.checked_cast(
                    self._get_comptime_value(instr.value), ir_values.ComptimeInt
                )
                self.registers[instr] = ir_values.ComptimeInt(instr.typ, int_value.value, instr.ast)
            case ir_values.AllocaInstr():
                self.registers[instr] = ir_values.ComptimeAlloc(
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
                self.registers[instr] = ir_values.ComptimeGep(base, index, instr.ast)
            case ir_values.InsertValueInstr():
                value = self._get_comptime_value(instr.value)
                base_agg = self._get_comptime_value(instr.aggregate).copy()
                agg = base_agg
                for index in instr.indeces[:-1]:
                    agg = asserts.checked_cast(agg, ir_values.ComptimeAggregate)
                    agg = agg.get_element(index.value)

                agg = asserts.checked_cast(agg, ir_values.ComptimeAggregate)
                agg.set_element(value, instr.indeces[-1].value)
                self.registers[instr] = base_agg
            case ir_values.CallInstr():
                callee = asserts.checked_cast(
                    self._get_comptime_value(instr.callee), ir_module.FnSpec
                )
                cfg = getattr(callee, "cfg", None)
                if cfg is None:
                    raise errors.CallExternFnAtComptimeError(callee.span)
                args = tuple(self._get_comptime_value(arg) for arg in instr.args)
                self.registers[instr] = Interpreter(cfg, callee.params, args).eval()
            case ir_values.PhiInstr():
                assert self.prev_bb is not None
                self.registers[instr] = self._get_comptime_value(instr.incoming[self.prev_bb])
            case ir_values.BranchInstr():
                self.prev_bb = self.curr_bb
                self.curr_bb = instr.target
                self.curr_instr_index = 0
            case ir_values.CbranchInstr():
                cond = asserts.checked_cast(
                    self._get_comptime_value(instr.condition), ir_values.ComptimeBool
                )
                self.prev_bb = self.curr_bb
                self.curr_bb = instr.true_target if cond.value else instr.false_target
                self.curr_instr_index = 0
            case ir_values.RetInstr():
                self.ret_value = self._get_comptime_value(instr.value)
            case ir_values.UnreachableInstr():
                raise AssertionError("Should never reach unreachable instruction")
            case _:
                raise AssertionError(f"Invalid instruction {instr}")
