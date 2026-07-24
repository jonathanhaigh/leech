from typing import Final, Optional
from l0 import ir
from l0.asserts import assert_eq, assert_lt, checked_cast
from l0.l0errors import (
    CallExternFnAtComptimeError,
    CannotTakeAddressOfComptimeValueError,
    SetNonLocalVarAtComptimeError,
)


class Interpreter:
    registers: Final[dict[ir.Value, ir.ComptimeValue]]
    cfg: Final[ir.Cfg]
    curr_bb: ir.BasicBlock
    prev_bb: Optional[ir.BasicBlock]
    curr_instr_index: int
    ret_value: Optional[ir.ComptimeValue]

    def __init__(
        self,
        cfg: ir.Cfg,
        params: tuple[ir.Param, ...],
        args: tuple[ir.ComptimeValue, ...],
    ) -> None:
        self.cfg = cfg
        self.registers = {param: arg for param, arg in zip(params, args, strict=True)}
        self.curr_bb = self.cfg.entry
        self.prev_bb = None
        self.curr_instr_index = 0
        self.ret_value = None

    def eval(self) -> ir.ComptimeValue:
        while True:
            if self.ret_value is not None:
                self._check_not_temporary(self.ret_value)
                return self.ret_value
            instr_index = self.curr_instr_index
            assert_lt(instr_index, len(self.curr_bb.instrs))
            self.curr_instr_index += 1
            self._eval_instr(self.curr_bb.instrs[instr_index])

    def _get_comptime_value(self, value: ir.Value) -> ir.ComptimeValue:
        if isinstance(value, ir.ComptimeValue):
            return value
        return self.registers[value]

    def _check_not_temporary(self, value: ir.ComptimeValue) -> None:
        if isinstance(value, ir.ComptimePtr) and value.is_temporary():
            raise CannotTakeAddressOfComptimeValueError(value.span)
        elif isinstance(value, ir.ComptimeAggregate):
            for elt in value.values():
                self._check_not_temporary(elt)

    def _eval_instr(self, instr: ir.Instr) -> None:
        match instr:
            case ir.BinOpInstr():
                lhs = checked_cast(self._get_comptime_value(instr.lhs), ir.ComptimeInt)
                rhs = checked_cast(self._get_comptime_value(instr.rhs), ir.ComptimeInt)
                assert_eq(lhs.typ, rhs.typ)
                match instr:
                    case ir.AddInstr():
                        ret = lhs.value + rhs.value
                    case ir.SubInstr():
                        ret = lhs.value - rhs.value
                    case ir.MulInstr():
                        ret = lhs.value * rhs.value
                    case ir.SdivInstr() | ir.UdivInstr():
                        ret = lhs.value // rhs.value
                    case _:
                        assert False, f"invalid bin op instr {instr}"
                # TODO: overflow, sdiv vs udiv?
                self.registers[instr] = ir.ComptimeInt(lhs.typ, ret, instr.ast)

            case ir.IcmpSignedInstr() | ir.IcmpUnsignedInstr():
                lhs = checked_cast(self._get_comptime_value(instr.lhs), ir.ComptimeInt)
                rhs = checked_cast(self._get_comptime_value(instr.rhs), ir.ComptimeInt)
                assert_eq(lhs.typ, rhs.typ)
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
                # TODO: signed vs unsigned?
                self.registers[instr] = ir.ComptimeBool(ret, instr.ast)

            case ir.LoadInstr():
                src = checked_cast(self._get_comptime_value(instr.src), ir.ComptimePtr)
                self.registers[instr] = src.load()
            case ir.AllocaInstr():
                self.registers[instr] = ir.ComptimeAlloc(
                    instr.allocated_typ, instr.mut, instr.ast
                )
            case ir.StoreInstr():
                dest = checked_cast(
                    self._get_comptime_value(instr.dest), ir.ComptimePtr
                )
                if not dest.is_temporary():
                    raise SetNonLocalVarAtComptimeError(instr.span)
                value = self._get_comptime_value(instr.value)
                dest.store(value)
            case ir.GepInstr():
                base = checked_cast(
                    self._get_comptime_value(instr.base), ir.ComptimePtr
                )
                index = checked_cast(
                    self._get_comptime_value(instr.index), ir.ComptimeInt
                )
                self.registers[instr] = ir.ComptimeGep(base, index, instr.ast)
            case ir.InsertValueInstr():
                value = self._get_comptime_value(instr.value)
                base_agg = self._get_comptime_value(instr.aggregate).copy()
                agg = base_agg
                for index in instr.indeces[:-1]:
                    agg = checked_cast(agg, ir.ComptimeAggregate)
                    agg = agg.get_element(index.value)

                agg = checked_cast(agg, ir.ComptimeAggregate)
                agg.set_element(value, instr.indeces[-1].value)
                self.registers[instr] = base_agg
            case ir.CallInstr():
                callee = checked_cast(self._get_comptime_value(instr.callee), ir.FnSpec)
                cfg = getattr(callee, "cfg", None)
                if cfg is None:
                    raise CallExternFnAtComptimeError(callee.span)
                args = tuple(self._get_comptime_value(arg) for arg in instr.args)
                self.registers[instr] = Interpreter(cfg, callee.params, args).eval()
            case ir.PhiInstr():
                assert self.prev_bb is not None
                self.registers[instr] = self._get_comptime_value(
                    instr.incoming[self.prev_bb]
                )
            case ir.BranchInstr():
                self.prev_bb = self.curr_bb
                self.curr_bb = instr.target
                self.curr_instr_index = 0
            case ir.CbranchInstr():
                cond = checked_cast(
                    self._get_comptime_value(instr.condition), ir.ComptimeBool
                )
                self.prev_bb = self.curr_bb
                self.curr_bb = instr.true_target if cond.value else instr.false_target
                self.curr_instr_index = 0
            case ir.RetInstr():
                self.ret_value = self._get_comptime_value(instr.value)
            case ir.UnreachableInstr():
                assert False, "Should never reach unreachable instruction"
            case _:
                assert False, f"Invalid instruction {instr}"
