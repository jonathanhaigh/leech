from dataclasses import dataclass
from enum import IntEnum
import sys
from typing import Optional

from lark.tree import Meta


class Level(IntEnum):
    NOTE = 0
    WARNING = 1
    ERROR = 2


NOTE = Level.NOTE
WARNING = Level.WARNING
ERROR = Level.ERROR


@dataclass
class Message:
    level: Level
    message: str
    meta: Optional[Meta]


class UserError(Exception):
    message: Message
    extra: list[Message]

    def __init__(self, level: Level, message: str, meta: Optional[Meta]) -> None:
        super().__init__(message)
        self.message = Message(level, message, meta)
        self.extra = []

    def add_extra(self, level: Level, message: str, meta: Optional[Meta]) -> None:
        self.extra.append(Message(level, message, meta))

    @property
    def level(self):
        return self.message.level


class TypNotFoundError(UserError):
    def __init__(self, name: str, meta: Optional[Meta]) -> None:
        super().__init__(ERROR, f'Type "{name}" not found.', meta)


class VarNotFoundError(UserError):
    def __init__(self, name: str, meta: Optional[Meta]) -> None:
        super().__init__(ERROR, f'Variable "{name}" not found.', meta)


class AssignToConstError(UserError):
    def __init__(self, name: str, meta: Optional[Meta]) -> None:
        super().__init__(
            ERROR, f'Attempt to set value of const variable "{name}"', meta
        )


class DuplicateVarDefnError(UserError):
    def __init__(
        self, name: str, meta: Optional[Meta], existing_meta: Optional[Meta]
    ) -> None:
        super().__init__(ERROR, f'Duplicate definition of variable "{name}"', meta)
        if existing_meta is not None:
            self.add_extra(NOTE, "Previous definition here", existing_meta)


class NotCallableError(UserError):
    def __init__(self, callee_diag: str, given_typ: str, meta: Optional[Meta]):
        super().__init__(
            ERROR, f'{callee_diag} of type "{given_typ}" is not callable', meta
        )


class InvalidArgTypError(UserError):
    def __init__(
        self,
        callee_diag: str,
        _fn_meta: Optional[Meta],
        arg_num: int,
        given_typ: str,
        expected_typ: str,
        arg_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Argument {arg_num} to callable "{callee_diag}"'
                f' has invalid type "{given_typ}",'
                f' expecting "{expected_typ}"'
            ),
            arg_meta,
        )


class TooManyArgsError(UserError):
    def __init__(
        self,
        callee_diag: str,
        _fn_meta: Optional[Meta],
        arg_meta: Optional[Meta],
        got: int,
        expected: int,
    ):
        super().__init__(
            ERROR,
            (
                f'Too many args in call to callable "{callee_diag}":'
                f" got {got}, expected {expected}"
            ),
            arg_meta,
        )


class NotEnoughArgsError(UserError):
    def __init__(
        self,
        callee_diag: str,
        fn_meta: Optional[Meta],
        got: int,
        expected: int,
    ):
        super().__init__(
            ERROR,
            (
                f'Not enough args in call to callable "{callee_diag}":'
                f" got {got}, expected {expected}"
            ),
            fn_meta,
        )


class InvalidRetTypError(UserError):
    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_meta: Optional[Meta],
        ret_expr_typ: str,
        ret_expr_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Return expression has invalid type "{ret_expr_typ}"'
                f' in function "{fn_name}" that returns "{ret_typ}"'
            ),
            ret_expr_meta,
        )
        if ret_typ_meta is not None:
            self.add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_meta,
            )


class InvalidVoidRetError(UserError):
    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_meta: Optional[Meta],
        ret_stmt_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            f'Cannot return without a value in function "{fn_name}" that returns "{ret_typ}"',
            ret_stmt_meta,
        )
        if ret_typ_meta is not None:
            self.add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_meta,
            )


class RetNotInFnError(UserError):
    def __init__(self, ret_meta: Optional[Meta]) -> None:
        super().__init__(ERROR, "Return statement not in function", ret_meta)


class UnreachableCodeWarning(UserError):
    def __init__(self, code_typ: str, code_meta: Optional[Meta]) -> None:
        super().__init__(WARNING, f"{code_typ} is unreachable", code_meta)


class MissingRetError(UserError):
    def __init__(
        self,
        fn_name: str,
        fn_meta: Optional[Meta],
        ret_typ: str,
        ret_typ_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f"Missing return statement or tail expression"
                f' in function "{fn_name}"'
                f' returning "{ret_typ}"'
            ),
            fn_meta,
        )
        if ret_typ_meta is not None:
            self.add_extra(NOTE, "return type specified here", ret_typ_meta)


class IfElsTypMismatchError(UserError):
    def __init__(
        self,
        then_typ: str,
        then_meta: Optional[Meta],
        els_typ: str,
        els_meta: Optional[Meta],
    ) -> None:
        super().__init__(ERROR, '"if" and "else" have mismatching types"', None)
        self.add_extra(NOTE, f'"if" type is "{then_typ}"', then_meta)
        self.add_extra(NOTE, f'"else" type is "{els_typ}"', els_meta)


class IfTypNotVoidError(UserError):
    def __init__(
        self,
        then_typ: str,
        then_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" without "else" must have type "void", found "{then_typ}"',
            then_meta,
        )


class IfCondNotBoolError(UserError):
    def __init__(
        self, expr_diag: str, expr_typ: str, expr_meta: Optional[Meta]
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_meta,
        )


class WhileTypNotVoidError(UserError):
    def __init__(
        self,
        block_typ: str,
        block_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR,
            f'block expression for "while" loop must have type "void", found "{block_typ}"',
            block_meta,
        )


class WhileCondNotBoolError(UserError):
    def __init__(
        self, expr_diag: str, expr_typ: str, expr_meta: Optional[Meta]
    ) -> None:
        super().__init__(
            ERROR,
            f'"while" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_meta,
        )


class VarInitFromVoidError(UserError):
    def __init__(
        self,
        var_name: str,
        var_meta: Optional[Meta],
        expr_diag: str,
        expr_meta: Optional[Meta],
    ) -> None:
        super().__init__(
            ERROR, f'Variable "{var_name}" cannot have type "void"', var_meta
        )
        self.add_extra(NOTE, f'Initialized by "{expr_diag}" of type "void"', expr_meta)


class TextErrorRenderer:
    src_lines: list[str]

    def __init__(self, src: str) -> None:
        self.src_lines = src.splitlines()

    def display_errors(self, errs: list[UserError]) -> None:
        for err in errs:
            self.display_error(err)

    def display_error(self, err: UserError) -> None:

        self.display_message(err.message)
        for message in err.extra:
            self.display_message(message)

    def display_message(self, message: Message) -> None:

        print(f"{message.level.name}: {message.message}", file=sys.stderr)
        if message.meta is not None:
            self._display_line(message.meta.line)
            self._display_col_pos(message.meta.column)

    def _display_line(self, num: int) -> None:
        line = self.src_lines[num - 1]
        print(f"{num}| {line}", file=sys.stderr)

    def _display_col_pos(self, num: int) -> None:
        prefix = "-" * (num + 2)
        print(f"{prefix}^", file=sys.stderr)


_errors: list[UserError] = []
_error_level: Level = NOTE


def register_error(err: UserError) -> None:
    global _error_level
    if err.level > _error_level:
        _error_level = err.level
    _errors.append(err)


def raise_error(err: UserError) -> None:
    register_error(err)
    raise err


def errors() -> list[UserError]:
    return _errors


def error_level() -> Level:
    return _error_level
