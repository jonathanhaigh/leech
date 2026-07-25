"""User-facing diagnostics: error/warning types, and their rendering."""

from collections.abc import Collection
from dataclasses import dataclass
from enum import IntEnum
import sys
from typing import Optional

from l0.asserts import assert_gt
from l0.src import SrcSpan


class Level(IntEnum):
    """The severity of a diagnostic message."""

    NOTE = 0
    WARNING = 1
    ERROR = 2


NOTE = Level.NOTE
WARNING = Level.WARNING
ERROR = Level.ERROR


@dataclass
class Message:
    """A single diagnostic message, optionally located in source."""

    level: Level
    message: str
    span: Optional[SrcSpan]


class UserError(Exception):
    """Base class for diagnosable compile errors and warnings.

    Carries a primary :class:`Message` plus any number of extra messages
    (e.g. "previous definition here" notes) that get rendered alongside
    it.

    :param level: The severity of the primary message.
    :param message: The primary message text.
    :param span: The source location the primary message refers to, if
        any.
    """

    message: Message
    extra: list[Message]

    def __init__(self, level: Level, message: str, span: Optional[SrcSpan]) -> None:
        super().__init__(message)
        self.message = Message(level, message, span)
        self.extra = []

    def add_extra(self, level: Level, message: str, span: Optional[SrcSpan]) -> None:
        """Attach an extra message to be rendered alongside the primary one.

        :param level: The severity of the extra message.
        :param message: The extra message text.
        :param span: The source location the extra message refers to, if
            any.
        """
        self.extra.append(Message(level, message, span))

    @property
    def level(self):
        """The severity of this error's primary message."""
        return self.message.level


class UnexpectedCharacterError(UserError):
    """Raised when the lexer encounters a character that can't start any
    valid token, given where it appears in the source."""

    def __init__(self, char: str, span: SrcSpan) -> None:
        super().__init__(ERROR, f'Unexpected character "{char}"', span)


class UnexpectedTokenError(UserError):
    """Raised when the parser encounters a token that doesn't fit anywhere
    in the grammar at that point - a syntax error. Also raised when the
    input ends before a complete module has been parsed, since l0's LALR
    parser reports that the same way, as an unexpected end-of-input
    "token".

    :param found: A short description of what was found instead of
        something valid, e.g. ``'token ";"'`` or ``'end of input'``.
    :param span: Where ``found`` occurs (or, for end-of-input, the
        position immediately after the last real token).
    :param expected: Human-readable descriptions of what would have been
        valid instead (e.g. ``'";"'``, ``'IDENT'``), used to add an
        explanatory note. May be empty if nothing more specific than
        ``found`` is known.
    """

    def __init__(
        self, found: str, span: SrcSpan, expected: Collection[str]
    ) -> None:
        super().__init__(ERROR, f"Unexpected {found}", span)
        if expected:
            self.add_extra(NOTE, f"Expected one of: {', '.join(expected)}", None)


class ItemNotFoundError(UserError):
    """Raised when a name cannot be resolved in scope."""

    def __init__(self, item_kind: str, name: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'{item_kind.capitalize()} "{name}" not found.', span)


class AssignToConstError(UserError):
    """Raised when assigning through a const pointer or to a const place."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot assign to const place expression", span)


class DuplicateItemDefnError(UserError):
    """Raised when a name is defined more than once in the same scope."""

    def __init__(
        self,
        item_kind: str,
        name: str,
        span: Optional[SrcSpan],
        existing_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(ERROR, f'Duplicate definition of {item_kind} "{name}"', span)
        if existing_span is not None:
            self.add_extra(NOTE, "Previous definition here", existing_span)


class NotCallableError(UserError):
    """Raised when calling a value whose type isn't a function pointer."""

    def __init__(self, callee_diag: str, given_typ: str, span: Optional[SrcSpan]):
        super().__init__(
            ERROR, f'{callee_diag} of type "{given_typ}" is not callable', span
        )


class InvalidArgTypError(UserError):
    """Raised when a call argument's type doesn't match the parameter's type."""

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
    """Raised when a binary operator's operand has an unsupported type."""

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
                f' expecting "{expected_typ}"'
            ),
            arg_span,
        )
        if op_span is not None:
            self.add_extra(NOTE, f'For "{op}" operation here', op_span)


class IncompatibleBinOpArgTypsError(UserError):
    """Raised when a binary operator's operands have differing types."""

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
    """Raised when a call passes more arguments than the callee takes."""

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
    """Raised when a call passes fewer arguments than the callee takes."""

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
    """Raised when a ``return`` expression's type doesn't match the
    function's return type."""

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
    """Raised when a value-less ``return`` appears in a non-void function."""

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
    """Raised when a ``return`` statement appears outside a function body."""

    def __init__(self, ret_span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Return statement not in function", ret_span)


class UnreachableCodeWarning(UserError):
    """Warns about code that can never be executed."""

    def __init__(self, code_typ: str, code_span: Optional[SrcSpan]) -> None:
        super().__init__(WARNING, f"{code_typ} is unreachable", code_span)


class MissingRetError(UserError):
    """Raised when a non-void function's body doesn't return or diverge."""

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
    """Raised when an array literal's elements don't all have the same type."""

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


class VoidArrayElementError(UserError):
    """Raised when an array literal's element type is ``void``."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, 'Array element cannot have type "void"', span)


class EmptyArrayTypUnknownError(UserError):
    """Raised when an empty array literal's element type can't be inferred.

    An empty array literal has no elements to infer a type from, so it can
    only be typed when the surrounding context (a function argument,
    return value, struct field, or assignment target) already has a known
    array type.
    """

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(
            ERROR, "Cannot infer element type of empty array expression", span
        )


class TypeOfStructExprNotStructError(UserError):
    """Raised when a struct literal's named type isn't actually a struct type."""

    def __init__(self, typ: str, span: Optional[SrcSpan]):
        super().__init__(
            ERROR,
            f'Cannot create value of not struct type "{typ}" using struct expression',
            span,
        )


class ImplForNonStructTypError(UserError):
    """Raised when an ``impl`` block targets a non-struct type."""

    def __init__(self, typ_diag: str, span: Optional[SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"impl" blocks are only supported for struct types, found {typ_diag}',
            span,
        )


class ImplForNonLocalStructTypError(UserError):
    """Raised when an ``impl`` block targets a struct defined in another module."""

    def __init__(self, typ_diag: str, span: Optional[SrcSpan]) -> None:
        super().__init__(
            ERROR,
            (
                '"impl" blocks are only supported for structs defined in the'
                f" same module, found {typ_diag}"
            ),
            span,
        )


class InvalidStructFieldError(UserError):
    """Raised when referring to a field a struct type doesn't have."""

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
    """Raised when a struct literal field's value has the wrong type."""

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
    """Raised when a struct literal omits a required field."""

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
    """Raised when a struct literal gives a value for the same field twice."""

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
    """Raised when a struct declaration defines the same field name twice."""

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
    """Raised when using ``.`` field access on a non-struct type."""

    def __init__(self, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Field access into invalid type "{typ}"', span)


class IfElsTypMismatchError(UserError):
    """Raised when an ``if`` expression's two branches have differing types."""

    def __init__(
        self,
        then_typ: str,
        then_span: Optional[SrcSpan],
        els_typ: str,
        els_span: Optional[SrcSpan],
    ) -> None:
        super().__init__(ERROR, '"if" and "else" have mismatching types', None)
        self.add_extra(NOTE, f'"if" type is "{then_typ}"', then_span)
        self.add_extra(NOTE, f'"else" type is "{els_typ}"', els_span)


class IfTypNotVoidError(UserError):
    """Raised when an ``if`` without ``else`` has a non-void ``then`` type."""

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
    """Raised when an ``if`` condition's type isn't ``bool``."""

    def __init__(
        self, expr_diag: str, expr_typ: str, expr_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class WhileTypNotVoidError(UserError):
    """Raised when a ``while`` loop's body has a non-void type."""

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
    """Raised when a ``while`` loop's condition's type isn't ``bool``."""

    def __init__(
        self, expr_diag: str, expr_typ: str, expr_span: Optional[SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            f'"while" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class IndexIntoInvalidTypError(UserError):
    """Raised when using ``[]`` indexing on a non-array type."""

    def __init__(self, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, f'Index into invalid type "{typ}"', span)


class InvalidIndexTypError(UserError):
    """Raised when an array index expression's type isn't ``usize``."""

    def __init__(self, typ: str, span: SrcSpan) -> None:
        super().__init__(ERROR, f'Invalid index typ "{typ}"', span)


class ArrayIndexOutOfBoundsError(UserError):
    """Raised when a compile-time-known array index is out of bounds."""

    def __init__(self, index: int, index_span: SrcSpan, array_typ: str) -> None:
        super().__init__(
            ERROR,
            f'Index {index} out of bounds for array of type "{array_typ}"',
            index_span,
        )


class IntLitOverflowError(UserError):
    """Raised when an integer literal doesn't fit in its type's width."""

    def __init__(self, value: int, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(
            ERROR, f'Integer literal {value} does not fit in type "{typ}"', span
        )


class IntOverflowAtComptimeError(UserError):
    """Raised when a compile-time arithmetic operation's result doesn't fit
    in its operands' type.

    Unlike :class:`IntLitOverflowError`, this is raised for the *result*
    of an operation (e.g. ``add``, ``sub``, ``mul``, ``div``) evaluated at
    compile time, not for an integer literal written in source.
    """

    def __init__(self, value: int, typ: str, span: Optional[SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Result of compile-time integer operation ({value}) does not fit'
            f' in type "{typ}"',
            span,
        )


class DerefInvalidTypError(UserError):
    """Raised when dereferencing a value whose type isn't a data pointer."""

    def __init__(self, typ: str, span: SrcSpan) -> None:
        super().__init__(ERROR, f'Cannot dereference value of type "{typ}"', span)


class VoidVarInitializerError(UserError):
    """Raised when a module-level ``let`` initializer has type ``void``."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Module variable initializer cannot be void", span)


class CannotTakeAddressOfComptimeValueError(UserError):
    """Raised when a compile-time-evaluated expression's result would need
    the address of a temporary that has no address at runtime."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot take address of comptime value", span)


class CallExternFnAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to call a function with no body."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot call extern function at comptime", span)


class SetNonLocalVarAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to write through a pointer
    to a variable outside the expression being evaluated."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot set non-local variable at comptime", span)


class DivisionByZeroAtComptimeError(UserError):
    """Raised when compile-time evaluation divides by a value of zero."""

    def __init__(self, span: Optional[SrcSpan]) -> None:
        super().__init__(ERROR, "Division by zero", span)


class ModDoesNotExistError(UserError):
    """Raised when an ``import`` names a module file that doesn't exist."""

    def __init__(self, name: str, span: SrcSpan) -> None:
        super().__init__(ERROR, f'Cannot find module "{name}"', span)


class TextErrorRenderer:
    """Renders diagnostics as plain text, with a source excerpt, to stderr."""

    def display_errors(self, errs: list[UserError]) -> None:
        """Render each error in ``errs``, in order.

        :param errs: The errors to render.
        """
        for err in errs:
            self.display_error(err)

    def display_error(self, err: UserError) -> None:
        """Render an error's primary message followed by its extra messages.

        :param err: The error to render.
        """

        self.display_message(err.message)
        for message in err.extra:
            self.display_message(message)

    def display_message(self, message: Message) -> None:
        """Render a single message, with a source excerpt if it has a span.

        :param message: The message to render.
        """

        print(f"{message.level.name}: {message.message}", file=sys.stderr)
        if message.span is not None:
            line_num_width = len(str(len(message.span.file.lines)))
            self._display_line(message.span, line_num_width)
            self._display_col_pos(message.span, line_num_width)

    def _display_line(self, span: SrcSpan, line_num_width) -> None:
        assert_gt(span.start_line, 0)
        line = span.file.lines[span.start_line - 1]
        print(f"{span.start_line:{line_num_width}d}| {line}", file=sys.stderr)

    def _display_col_pos(self, span: SrcSpan, line_num_width) -> None:
        prefix = "-" * (span.start_col + line_num_width + 1)
        print(f"{prefix}^", file=sys.stderr)


_errors: list[UserError] = []
_error_level: Level = NOTE


def register_error(err: UserError) -> None:
    """Record a diagnostic and raise the process's overall error level if needed.

    :param err: The diagnostic to record.
    """
    global _error_level
    if err.level > _error_level:
        _error_level = err.level
    _errors.append(err)


def raise_error(err: UserError) -> None:
    """Record a diagnostic (see :func:`register_error`) and then raise it.

    :param err: The diagnostic to record and raise.
    :raises UserError: Always; raises ``err`` itself.
    """
    register_error(err)
    raise err


def errors() -> list[UserError]:
    """Return all diagnostics recorded so far, in registration order."""
    return _errors


def error_level() -> Level:
    """Return the highest severity level among all recorded diagnostics."""
    return _error_level
