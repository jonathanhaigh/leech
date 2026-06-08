from dataclasses import dataclass
from enum import IntEnum
import sys
from typing import Optional

from l0.src import SrcSpan


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
    span: Optional[SrcSpan]


class UserError(Exception):
    message: Message
    extra: list[Message]

    def __init__(self, level: Level, message: str, span: Optional[SrcSpan]) -> None:
        super().__init__(message)
        self.message = Message(level, message, span)
        self.extra = []

    def add_extra(self, level: Level, message: str, span: Optional[SrcSpan]) -> None:
        self.extra.append(Message(level, message, span))

    @property
    def level(self):
        return self.message.level


class TypNotFoundError(UserError):
    def __init__(self, name: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Type "{name}" not found.', span)


class VarNotFoundError(UserError):
    def __init__(self, name: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Variable "{name}" not found.', span)


class AssignToConstError(UserError):
    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot assign to const place expression", span)


class AssignToVoidError(UserError):
    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot assign to void place expression", span)


class DuplicateVarDefnError(UserError):
    def __init__(
        self, name: str, span: Optional[SrcSpan], existing_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(ERROR, f'Duplicate definition of variable "{name}"', span)
        if existing_span is not None:
            self.add_extra(NOTE, "Previous definition here", existing_span)


class DuplicateTypDefnError(UserError):
    def __init__(
        self, name: str, span: Optional[SrcSpan], existing_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(ERROR, f'Duplicate definition of type "{name}"', span)
        if existing_span is not None:
            self.add_extra(NOTE, "Previous definition here", existing_span)


class NotCallableError(UserError):
    def __init__(self, callee_diag: str, given_typ: str, span: Optional[SrcSpan]):
        super().__init__(
            ERROR, f'{callee_diag} of type "{given_typ}" is not callable', span
        )


class InvalidArgTypError(UserError):
    def __init__(
        self,
        callee_diag: str,
        _fn_span: Optional[SrcSpan],
        arg_num: int,
        given_typ: str,
        expected_typ: str,
        arg_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Argument {arg_num} to callable "{callee_diag}"'
                f' has invalid type "{given_typ}",'
                f' expecting "{expected_typ}"'
            ),
            arg_span,
        )


class InvalidBinOpArgTypError(UserError):
    def __init__(
        self,
        op: str,
        op_span: Optional[SrcSpan],
        arg_name: str,
        given_typ: str,
        expected_typ: str,
        arg_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'{arg_name.capitalize()} operand of binary operation "{op}"'
                f' has invalid type "{given_typ}",'
                f' expecting {expected_typ}"'
            ),
            arg_span,
        )
        if op_span is not None:
            self.add_extra(NOTE, f'For "{op}" operation here', op_span)


class IncompatibleBinOpArgTypsError(UserError):
    def __init__(
        self,
        op: str,
        op_span: Optional[SrcSpan],
        lhs_typ: str,
        lhs_span: Optional[SrcSpan],
        rhs_typ: str,
        rhs_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Left and right operands to binary operation "{op}" have incompatible types',
            op_span,
        )
        self.add_extra(NOTE, f'Left operand type is "{lhs_typ}"', lhs_span)
        self.add_extra(NOTE, f'Right operand type is "{rhs_typ}"', rhs_span)


class TooManyArgsError(UserError):
    def __init__(
        self,
        callee_diag: str,
        _fn_span: Optional[SrcSpan],
        arg_span: Optional[SrcSpan],
        got: int,
        expected: int,
    ):
        super().__init__(
            ERROR,
            (
                f'Too many args in call to callable "{callee_diag}":'
                f" got {got}, expected {expected}"
            ),
            arg_span,
        )


class NotEnoughArgsError(UserError):
    def __init__(
        self,
        callee_diag: str,
        fn_span: Optional[SrcSpan],
        got: int,
        expected: int,
    ):
        super().__init__(
            ERROR,
            (
                f'Not enough args in call to callable "{callee_diag}":'
                f" got {got}, expected {expected}"
            ),
            fn_span,
        )


class InvalidRetTypError(UserError):
    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_span: Optional[SrcSpan],
        ret_expr_typ: str,
        ret_expr_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Return expression has invalid type "{ret_expr_typ}"'
                f' in function "{fn_name}" that returns "{ret_typ}"'
            ),
            ret_expr_span,
        )
        if ret_typ_span is not None:
            self.add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_span,
            )


class InvalidVoidRetError(UserError):
    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_span: Optional[SrcSpan],
        ret_stmt_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Cannot return without a value in function "{fn_name}" that returns "{ret_typ}"',
            ret_stmt_span,
        )
        if ret_typ_span is not None:
            self.add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_span,
            )


class RetNotInFnError(UserError):
    def __init__(self, ret_span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Return statement not in function", ret_span)


class UnreachableCodeWarning(UserError):
    def __init__(self, code_typ: str, code_span: Optional[SrcSpan]) -> None:
        super().__init__(WARNING, f"{code_typ} is unreachable", code_span)


class MissingRetError(UserError):
    def __init__(
        self,
        fn_name: str,
        fn_span: Optional[SrcSpan],
        ret_typ: str,
        ret_typ_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f"Missing return statement or tail expression"
                f' in function "{fn_name}"'
                f' returning "{ret_typ}"'
            ),
            fn_span,
        )
        if ret_typ_span is not None:
            self.add_extra(NOTE, "return type specified here", ret_typ_span)


class IncompatibleTypInArrayExpr(UserError):
    def __init__(
        self,
        element_typ: str,
        element_index: int,
        element_span: SrcSpan,
        array_typ: str,
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Array element {element_index} of type "{element_typ}"'
                f' is incompatible with array type "{array_typ}"'
            ),
            element_span,
        )


class TypeOfStructExprNotStructError(UserError):
    def __init__(self, typ: str, span: Optional[SrcSpan]):
        super().__init__(
            ERROR,
            f'Cannot create value of not struct type "{typ}" using struct expression',
            span,
        )


class InvalidStructFieldError(UserError):
    def __init__(
        self,
        field_name: str,
        field_span: Optional[SrcSpan],
        struct_typ: str,
        struct_span: Optional[SrcSpan],
    ):
        super().__init__(
            ERROR,
            f'Struct "{struct_typ}" has no field named "{field_name}"',
            field_span,
        )
        if struct_span is not None:
            self.add_extra(NOTE, f'Struct "{struct_typ}" defined here', struct_span)


class IncompatibleStructFieldTypError(UserError):
    def __init__(
        self,
        field_name: str,
        struct_typ: str,
        given_typ: str,
        given_span: Optional[SrcSpan],
        field_typ: str,
        field_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Struct "{struct_typ}" field "{field_name}" of type "{field_typ}"'
                f' cannot be initialized with value of type "{given_typ}"'
            ),
            given_span,
        )
        if field_span is not None:
            self.add_extra(NOTE, f'Field "{field_name}" defined here', field_span)


class MissingFieldInStructExprError(UserError):
    def __init__(
        self,
        field_name: str,
        field_span: Optional[SrcSpan],
        struct_name: str,
        struct_expr_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Missing field "{field_name}" in struct expression of type "{struct_name}"',
            struct_expr_span,
        )
        if field_span is not None:
            self.add_extra(NOTE, f'Field "{field_name}" defined here', field_span)


class DuplicateFieldInStructExprError(UserError):
    def __init__(
        self,
        field_name: str,
        duplicate_span: Optional[SrcSpan],
        previous_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Duplicate field "{field_name}" in struct expression',
            duplicate_span,
        )
        if previous_span is not None:
            self.add_extra(
                NOTE,
                f'Value for field "{field_name}" previously given here',
                previous_span,
            )


class DuplicateFieldInStructDefnError(UserError):
    def __init__(
        self,
        field_name: str,
        duplicate_span: Optional[SrcSpan],
        previous_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Duplicate field "{field_name}" in struct definition',
            duplicate_span,
        )
        if previous_span is not None:
            self.add_extra(
                NOTE,
                f'Definition of field "{field_name}" previously given here',
                previous_span,
            )


class FieldAccessIntoInvalidTypError(UserError):
    def __init__(self, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Field access into invalid type "{typ}"', span)


class IfElsTypMismatchError(UserError):
    def __init__(
        self,
        then_typ: str,
        then_span: Optional[SrcSpan],
        els_typ: str,
        els_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(ERROR, '"if" and "else" have mismatching types"', None)
        self.add_extra(NOTE, f'"if" type is "{then_typ}"', then_span)
        self.add_extra(NOTE, f'"else" type is "{els_typ}"', els_span)


class IfTypNotVoidError(UserError):
    def __init__(
        self,
        then_typ: str,
        then_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" without "else" must have type "void", found "{then_typ}"',
            then_span,
        )


class IfCondNotBoolError(UserError):
    def __init__(
        self, expr_diag: str, expr_typ: str, expr_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class WhileTypNotVoidError(UserError):
    def __init__(
        self,
        block_typ: str,
        block_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'block expression for "while" loop must have type "void", found "{block_typ}"',
            block_span,
        )


class WhileCondNotBoolError(UserError):
    def __init__(
        self, expr_diag: str, expr_typ: str, expr_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            f'"while" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class VarInitFromVoidError(UserError):
    def __init__(
        self,
        var_name: str,
        var_span: Optional[SrcSpan],
        expr_diag: str,
        expr_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(
            ERROR, f'Variable "{var_name}" cannot have type "void"', var_span
        )
        self.add_extra(NOTE, f'Initialized by "{expr_diag}" of type "void"', expr_span)


class IndexIntoInvalidTypError(UserError):
    def __init__(self, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Index into invalid type "{typ}"', span)


class InvalidIndexTypError(UserError):
    def __init__(self, typ: str, span: SrcSpan) -> None:
        super().__init__(ERROR, f'Invalid index typ "{typ}"', span)


class ArrayIndexOutOfBoundsError(UserError):
    def __init__(self, index: int, index_span: SrcSpan, array_typ: str) -> None:
        super().__init__(
            ERROR,
            f'Index {index} out of bounds for array of type "{array_typ}"',
            index_span,
        )


class TextErrorRenderer:
    def display_errors(self, errs: list[UserError]) -> None:
        for err in errs:
            self.display_error(err)

    def display_error(self, err: UserError) -> None:

        self.display_message(err.message)
        for message in err.extra:
            self.display_message(message)

    def display_message(self, message: Message) -> None:

        print(f"{message.level.name}: {message.message}", file=sys.stderr)
        if message.span is not None:
            line_num_width = len(str(len(message.span.file.lines)))
            self._display_line(message.span, line_num_width)
            self._display_col_pos(message.span, line_num_width)

    def _display_line(self, span: SrcSpan, line_num_width) -> None:
        assert span.start_line > 0
        line = span.file.lines[span.start_line - 1]
        print(f"{span.start_line:{line_num_width}d}| {line}", file=sys.stderr)

    def _display_col_pos(self, span: SrcSpan, line_num_width) -> None:
        prefix = "-" * (span.start_col + line_num_width + 1)
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
