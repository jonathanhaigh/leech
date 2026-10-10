# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""User-facing diagnostics: error/warning types, and their rendering."""

import dataclasses
import pathlib
import sys
from collections.abc import Sequence
from typing import ClassVar, Final, Optional

from leech import asserts, diag, diag_kinds, patterns, src

Level = diag.Level
NOTE = diag.NOTE
WARNING = diag.WARNING
ERROR = diag.ERROR


def _sentence_case(text: str) -> str:
    """Upper-case only the first character, so a message can open with ``text``.

    ``str.capitalize`` lower-cases everything after it, which would rewrite
    any name the text quotes.
    """
    return text[:1].upper() + text[1:]


@dataclasses.dataclass(frozen=True)
class Message:
    """A single diagnostic message, optionally located in source."""

    level: Level
    message: str
    span: Optional[src.SrcSpan]


class UserError(Exception):
    """A diagnostic with a primary message and optional accompanying messages.

    ``kind`` is the diagnostic's entry in the catalogue.
    """

    kind: ClassVar[diag.DiagKind]
    message: Final[Message]
    extra: Final[list[Message]]

    def __init__(self, level: Level, message: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(message)
        self.message = Message(level, message, span)
        self.extra = []

    def _add_extra(self, level: Level, message: str, span: Optional[src.SrcSpan]) -> None:
        self.extra.append(Message(level, message, span))

    @property
    def level(self):
        """The severity of this error's primary message."""
        return self.message.level

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The primary message's location."""
        return self.message.span


class CannotInferComptimeArgError(UserError):
    """Raised when a generic item's use can't determine one of its
    comptime parameters from the types around it, and no explicit
    comptime argument was given for it either."""

    kind = diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT

    def __init__(
        self,
        item_kind: str,
        item_name: str,
        typ_param_name: str,
        span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Cannot infer argument for parameter "{typ_param_name}"'
                f' of generic {item_kind} "{item_name}"'
            ),
            span,
        )
        self._add_extra(NOTE, f'Give it explicitly, e.g. "{item_name}[...]"', None)


class AssignToConstError(UserError):
    """Raised when assigning through a const pointer or to a const place."""

    kind = diag_kinds.ASSIGNMENT_TO_IMMUTABLE_PLACE

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot assign to const place expression", span)


class IncompatibleAssignmentTypError(UserError):
    """Raised when an assigned value's type doesn't match the place's."""

    kind = diag_kinds.ASSIGNMENT_TYPE_MISMATCH

    def __init__(
        self,
        given_typ: str,
        place_typ: str,
        span: Optional[src.SrcSpan],
        place_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Cannot assign value of type "{given_typ}" to place of type "{place_typ}"',
            span,
        )
        if place_span is not None:
            self._add_extra(NOTE, f'Place has type "{place_typ}"', place_span)


class IncompatibleLetTypError(UserError):
    """Raised when a ``let`` initializer's type doesn't match, and doesn't
    coerce to, its declared type."""

    kind = diag_kinds.LET_TYPE_MISMATCH

    def __init__(
        self,
        var_name: str,
        declared_typ: str,
        given_typ: str,
        given_span: Optional[src.SrcSpan],
        declared_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Variable "{var_name}" of declared type "{declared_typ}"'
                f' cannot be initialized with value of type "{given_typ}"'
            ),
            given_span,
        )
        if declared_span is not None:
            self._add_extra(NOTE, f'Declared with type "{declared_typ}" here', declared_span)


class NotCallableError(UserError):
    """Raised when calling a value whose type isn't a function pointer."""

    kind = diag_kinds.NON_FUNCTION_CALL

    def __init__(self, callee_diag: str, given_typ: str, span: Optional[src.SrcSpan]):
        super().__init__(ERROR, f'{callee_diag} of type "{given_typ}" is not callable', span)


class InvalidArgTypError(UserError):
    """Raised when a call argument's type doesn't match the parameter's type."""

    kind = diag_kinds.ARGUMENT_TYPE_MISMATCH

    def __init__(
        self,
        callee_diag: str,
        arg_num: int,
        given_typ: str,
        expected_typ: str,
        arg_span: Optional[src.SrcSpan],
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

    kind = diag_kinds.BINARY_OPERAND_TYPE_MISMATCH

    def __init__(
        self,
        op: str,
        op_span: Optional[src.SrcSpan],
        arg_name: str,
        given_typ: str,
        expected_typ: str,
        arg_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'{_sentence_case(arg_name)} operand of binary operation "{op}"'
                f' has invalid type "{given_typ}",'
                f' expecting "{expected_typ}"'
            ),
            arg_span,
        )
        if op_span is not None:
            self._add_extra(NOTE, f'For "{op}" operation here', op_span)


class InvalidUnaryOpArgTypError(UserError):
    """Raised when a unary operator's operand has an unsupported type."""

    kind = diag_kinds.UNARY_OPERAND_TYPE_MISMATCH

    def __init__(
        self,
        op: str,
        op_span: Optional[src.SrcSpan],
        given_typ: str,
        expected_typ: str,
        arg_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Operand of unary operation "{op}"'
                f' has invalid type "{given_typ}",'
                f' expecting "{expected_typ}"'
            ),
            arg_span,
        )
        if op_span is not None:
            self._add_extra(NOTE, f'For "{op}" operation here', op_span)


class IncompatibleBinOpArgTypsError(UserError):
    """Raised when a binary operator's operands have differing types."""

    kind = diag_kinds.CONFLICTING_OPERAND_TYPES

    def __init__(
        self,
        op: str,
        op_span: Optional[src.SrcSpan],
        lhs_typ: str,
        lhs_span: Optional[src.SrcSpan],
        rhs_typ: str,
        rhs_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Left and right operands to binary operation "{op}" have incompatible types',
            op_span,
        )
        self._add_extra(NOTE, f'Left operand type is "{lhs_typ}"', lhs_span)
        self._add_extra(NOTE, f'Right operand type is "{rhs_typ}"', rhs_span)


class TooManyArgsError(UserError):
    """Raised when a call passes more arguments than the callee takes."""

    kind = diag_kinds.ARGUMENT_COUNT_MISMATCH

    def __init__(
        self,
        callee_diag: str,
        arg_span: Optional[src.SrcSpan],
        got: int,
        expected: int,
    ):
        super().__init__(
            ERROR,
            (f'Too many args in call to callable "{callee_diag}": got {got}, expected {expected}'),
            arg_span,
        )


class NotEnoughArgsError(UserError):
    """Raised when a call passes fewer arguments than the callee takes."""

    kind = diag_kinds.ARGUMENT_COUNT_MISMATCH

    def __init__(
        self,
        callee_diag: str,
        fn_span: Optional[src.SrcSpan],
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

    kind = diag_kinds.RETURN_TYPE_MISMATCH

    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_span: Optional[src.SrcSpan],
        ret_expr_typ: str,
        ret_expr_span: Optional[src.SrcSpan],
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
            self._add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_span,
            )


class InvalidVoidRetError(UserError):
    """Raised when a value-less ``return`` appears in a non-void function."""

    kind = diag_kinds.MISSING_RETURN_VALUE

    def __init__(
        self,
        fn_name: str,
        ret_typ: str,
        ret_typ_span: Optional[src.SrcSpan],
        ret_stmt_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Cannot return without a value in function "{fn_name}" that returns "{ret_typ}"',
            ret_stmt_span,
        )
        if ret_typ_span is not None:
            self._add_extra(
                NOTE,
                "Return type specified here",
                ret_typ_span,
            )


class RetNotInFnError(UserError):
    """Raised when a ``return`` statement appears outside a function body."""

    kind = diag_kinds.RETURN_OUTSIDE_FUNCTION

    def __init__(self, ret_span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Return statement not in function", ret_span)


class BreakNotInLoopError(UserError):
    """Raised when a ``break`` statement appears outside any loop."""

    kind = diag_kinds.BREAK_OUTSIDE_LOOP

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Break statement not in a loop", span)


class ContinueNotInLoopError(UserError):
    """Raised when a ``continue`` statement appears outside any loop."""

    kind = diag_kinds.CONTINUE_OUTSIDE_LOOP

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Continue statement not in a loop", span)


class LoopLabelNotFoundError(UserError):
    """Raised when a ``break``/``continue`` names a label no enclosing loop has."""

    kind = diag_kinds.UNKNOWN_LOOP_LABEL

    def __init__(self, label_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Loop label "{label_name}" not found', span)


class UnreachableCodeWarning(UserError):  # noqa: N818 - a warning, not an error
    """Warns about code that can never be executed."""

    kind = diag_kinds.UNREACHABLE_CODE

    def __init__(self, code_typ: str, code_span: Optional[src.SrcSpan]) -> None:
        super().__init__(WARNING, f"{code_typ} is unreachable", code_span)


class MissingRetError(UserError):
    """Raised when a non-void function's body doesn't return or diverge."""

    kind = diag_kinds.MISSING_RETURN

    def __init__(
        self,
        fn_name: str,
        fn_span: Optional[src.SrcSpan],
        ret_typ: str,
        ret_typ_span: Optional[src.SrcSpan],
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
            self._add_extra(NOTE, "return type specified here", ret_typ_span)


class IncompatibleTypInArrayExprError(UserError):
    """Raised when an array literal's elements don't all have the same type."""

    kind = diag_kinds.ARRAY_ELEMENT_TYPE_MISMATCH

    def __init__(
        self,
        element_typ: str,
        element_index: int,
        element_span: src.SrcSpan,
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


class WrongNumberOfArrayLitElementsError(UserError):
    """Raised when an array literal's element count doesn't match its declared length."""

    kind = diag_kinds.ARRAY_ELEMENT_COUNT_MISMATCH

    def __init__(
        self, array_typ: str, given: int, expected: int, span: Optional[src.SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Wrong number of elements for array literal of type "{array_typ}":'
                f" got {given}, expected {expected}"
            ),
            span,
        )


class NamedFieldInArrayLitError(UserError):
    """Raised when an array literal's brace list contains a named ``field: value`` entry."""

    kind = diag_kinds.NAMED_FIELD_IN_ARRAY_LITERAL

    def __init__(self, field_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Array literal cannot contain named field "{field_name}"', span)


class ArrayLitLengthNotConcreteError(UserError):
    """Raised when an array literal's own length argument isn't a concrete value.

    Every array literal states its element count directly, so this can
    only happen when the length names an in-scope but still-abstract
    value parameter (e.g. ``array[i32, N]{1, 2, 3}`` inside a function
    generic over ``N``): there's no way to check the literal's fixed
    element count against a value that isn't known until the enclosing
    generic declaration is instantiated with a concrete argument for ``N``.
    """

    kind = diag_kinds.GENERIC_ARRAY_LITERAL_LENGTH

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            "Array literal's length must be a concrete value, not a generic value parameter",
            span,
        )


class TypeOfBraceExprInvalidError(UserError):
    """Raised when a struct or array literal's named type isn't actually a
    struct or array type."""

    kind = diag_kinds.NON_STRUCT_OR_ARRAY_LITERAL

    def __init__(self, typ: str, span: Optional[src.SrcSpan]):
        super().__init__(
            ERROR,
            f'Cannot create value of non-struct, non-array type "{typ}" using literal syntax',
            span,
        )


class PositionalElementInStructExprError(UserError):
    """Raised when a struct literal's brace list contains a bare positional value."""

    kind = diag_kinds.POSITIONAL_VALUE_IN_STRUCT_EXPRESSION

    def __init__(self, struct_typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            (
                f'Struct expression of type "{struct_typ}" requires named fields,'
                " not positional values"
            ),
            span,
        )


class NotAMethodError(UserError):
    """Raised when calling ``x.name(...)`` where ``name`` is an associated
    function of ``x``'s struct type, but that function has no ``self``
    receiver, so it can't be called via dot syntax."""

    kind = diag_kinds.ASSOCIATED_FUNCTION_USED_AS_METHOD

    def __init__(
        self,
        fn_name: str,
        struct_typ: str,
        span: Optional[src.SrcSpan],
        fn_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Associated function "{fn_name}" of struct "{struct_typ}" has no'
                ' "self" parameter and cannot be called as a method'
            ),
            span,
        )
        if fn_span is not None:
            self._add_extra(NOTE, f'Function "{fn_name}" defined here', fn_span)


class InvalidStructFieldError(UserError):
    """Raised when referring to a field a struct type doesn't have."""

    kind = diag_kinds.UNKNOWN_STRUCT_FIELD

    def __init__(
        self,
        field_name: str,
        field_span: Optional[src.SrcSpan],
        struct_typ: str,
        struct_span: Optional[src.SrcSpan],
    ):
        super().__init__(
            ERROR,
            f'Struct "{struct_typ}" has no field named "{field_name}"',
            field_span,
        )
        if struct_span is not None:
            self._add_extra(NOTE, f'Struct "{struct_typ}" defined here', struct_span)


class PrivateStructFieldAccessError(UserError):
    """Raised when reading, writing, or initializing a private struct
    field from outside the module the struct is defined in."""

    kind = diag_kinds.PRIVATE_FIELD_ACCESS

    def __init__(
        self,
        field_name: str,
        access_span: Optional[src.SrcSpan],
        struct_typ: str,
        field_defn_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Field "{field_name}" of struct "{struct_typ}" is private',
            access_span,
        )
        if field_defn_span is not None:
            self._add_extra(NOTE, f'Field "{field_name}" defined here', field_defn_span)


class IncompatibleStructFieldTypError(UserError):
    """Raised when a struct literal field's value has the wrong type."""

    kind = diag_kinds.STRUCT_FIELD_TYPE_MISMATCH

    def __init__(
        self,
        field_name: str,
        struct_typ: str,
        given_typ: str,
        given_span: Optional[src.SrcSpan],
        field_typ: str,
        field_span: Optional[src.SrcSpan],
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
            self._add_extra(NOTE, f'Field "{field_name}" defined here', field_span)


class MissingFieldInStructExprError(UserError):
    """Raised when a struct literal omits a required field."""

    kind = diag_kinds.MISSING_STRUCT_FIELD

    def __init__(
        self,
        field_name: str,
        field_span: Optional[src.SrcSpan],
        struct_name: str,
        struct_expr_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Missing field "{field_name}" in struct expression of type "{struct_name}"',
            struct_expr_span,
        )
        if field_span is not None:
            self._add_extra(NOTE, f'Field "{field_name}" defined here', field_span)


class DuplicateFieldInStructExprError(UserError):
    """Raised when a struct literal gives a value for the same field twice."""

    kind = diag_kinds.DUPLICATE_STRUCT_FIELD_VALUE

    def __init__(
        self,
        field_name: str,
        duplicate_span: Optional[src.SrcSpan],
        previous_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Duplicate field "{field_name}" in struct expression',
            duplicate_span,
        )
        if previous_span is not None:
            self._add_extra(
                NOTE,
                f'Value for field "{field_name}" previously given here',
                previous_span,
            )


class FieldAccessIntoInvalidTypError(UserError):
    """Raised when using ``.`` field access on a non-struct type."""

    kind = diag_kinds.NON_STRUCT_FIELD_ACCESS

    def __init__(self, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Field access into invalid type "{typ}"', span)


class IfElsTypMismatchError(UserError):
    """Raised when an ``if`` expression's two branches have differing types."""

    kind = diag_kinds.CONFLICTING_BRANCH_TYPES

    def __init__(
        self,
        if_span: Optional[src.SrcSpan],
        then_typ: str,
        then_span: Optional[src.SrcSpan],
        els_typ: str,
        els_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(ERROR, '"if" and "else" have mismatching types', if_span)
        self._add_extra(NOTE, f'"if" type is "{then_typ}"', then_span)
        self._add_extra(NOTE, f'"else" type is "{els_typ}"', els_span)


class NonExhaustiveMatchError(UserError):
    """Raised when a ``match`` expression doesn't cover every scrutinee value."""

    kind = diag_kinds.NON_EXHAUSTIVE_MATCH

    def __init__(
        self,
        match_span: Optional[src.SrcSpan],
        witnesses: Sequence[patterns.Witness],
    ) -> None:
        super().__init__(ERROR, '"match" is not exhaustive', match_span)
        for witness in witnesses:
            self._add_extra(NOTE, f'Uncovered pattern "{witness.render()}"', None)


class UnreachableMatchArmWarning(UserError):  # noqa: N818 - a warning, not an error
    """Warns about a match arm an earlier arm already covers."""

    kind = diag_kinds.UNREACHABLE_MATCH_ARM

    def __init__(self, arm_span: Optional[src.SrcSpan]) -> None:
        super().__init__(WARNING, "match arm is unreachable", arm_span)


class MatchArmTypMismatchError(UserError):
    """Raised when two non-diverging match arms have differing types."""

    kind = diag_kinds.CONFLICTING_MATCH_ARM_TYPES

    def __init__(
        self,
        match_span: Optional[src.SrcSpan],
        first_typ: str,
        first_span: Optional[src.SrcSpan],
        second_typ: str,
        second_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(ERROR, '"match" arms have mismatching types', match_span)
        self._add_extra(NOTE, f'Match arm type is "{first_typ}"', first_span)
        self._add_extra(NOTE, f'Match arm type is "{second_typ}"', second_span)


class PatternTypMismatchError(UserError):
    """Raised when a literal pattern's type cannot match the scrutinee type."""

    kind = diag_kinds.PATTERN_TYPE_MISMATCH

    def __init__(
        self,
        pattern_diag: str,
        pattern_typ: str,
        scrutinee_typ: str,
        pattern_span: Optional[src.SrcSpan],
        scrutinee_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'{_sentence_case(pattern_diag)} has type "{pattern_typ}",'
                f' which cannot match scrutinee of type "{scrutinee_typ}"'
            ),
            pattern_span,
        )
        if scrutinee_span is not None:
            self._add_extra(NOTE, f'Scrutinee has type "{scrutinee_typ}"', scrutinee_span)


class BindingInOrPatternError(UserError):
    """Raised when an or-pattern alternative contains a ``let`` binding."""

    kind = diag_kinds.BINDING_IN_OR_PATTERN

    def __init__(self, binding_span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            '"let" bindings are not allowed inside or-patterns',
            binding_span,
        )


class VariantConstructorNotAValueError(UserError):
    """Raised when a union variant that carries a payload is named outside a call."""

    kind = diag_kinds.MISSING_VARIANT_PAYLOAD

    def __init__(self, variant: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Variant "{variant}" carries a payload, so it names a value only when called',
            span,
        )


class UnitVariantCalledError(UserError):
    """Raised when a union variant that carries no payload is called."""

    kind = diag_kinds.UNEXPECTED_VARIANT_PAYLOAD

    def __init__(self, variant: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Variant "{variant}" carries no payload, so it is named without a call',
            span,
        )


class NotAPatternError(UserError):
    """Raised when a path resolves to something that can't be used as a pattern."""

    kind = diag_kinds.NON_PATTERN_PATH

    def __init__(self, path: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Path "{path}" is not a pattern', span)


class WrongNumberOfPayloadPatternsError(UserError):
    """Raised when a pattern destructures a variant with the wrong number of sub-patterns.

    A variant carrying no payload expects zero, so writing any payload list
    for one is this error rather than a separate diagnostic.
    """

    kind = diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH

    def __init__(
        self,
        variant: str,
        span: Optional[src.SrcSpan],
        got: int,
        expected: int,
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Wrong number of payload patterns for variant "{variant}":'
                f" got {got}, expected {expected}"
            ),
            span,
        )


class IfTypNotVoidError(UserError):
    """Raised when an ``if`` without ``else`` has a non-void ``then`` type."""

    kind = diag_kinds.IF_WITHOUT_ELSE_TYPE_MISMATCH

    def __init__(
        self,
        then_typ: str,
        then_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'"if" without "else" must have type "void", found "{then_typ}"',
            then_span,
        )


class IfCondNotBoolError(UserError):
    """Raised when an ``if`` condition's type isn't ``bool``."""

    kind = diag_kinds.IF_CONDITION_TYPE_MISMATCH

    def __init__(self, expr_diag: str, expr_typ: str, expr_span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"if" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class WhileTypNotVoidError(UserError):
    """Raised when a ``while`` loop's body has a non-void type."""

    kind = diag_kinds.WHILE_BODY_TYPE_MISMATCH

    def __init__(
        self,
        block_typ: str,
        block_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'block expression for "while" loop must have type "void", found "{block_typ}"',
            block_span,
        )


class WhileCondNotBoolError(UserError):
    """Raised when a ``while`` loop's condition's type isn't ``bool``."""

    kind = diag_kinds.WHILE_CONDITION_TYPE_MISMATCH

    def __init__(self, expr_diag: str, expr_typ: str, expr_span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"while" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class IndexIntoInvalidTypError(UserError):
    """Raised when using ``[]`` indexing on a non-array type."""

    kind = diag_kinds.NON_ARRAY_INDEX

    def __init__(self, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Index into invalid type "{typ}"', span)


class InvalidIndexTypError(UserError):
    """Raised when an array index expression's type isn't ``usize``."""

    kind = diag_kinds.INDEX_TYPE_MISMATCH

    def __init__(self, typ: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Invalid index typ "{typ}"', span)


class DerefInvalidTypError(UserError):
    """Raised when dereferencing a value whose type isn't a data pointer."""

    kind = diag_kinds.NON_POINTER_DEREFERENCE

    def __init__(self, typ: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Cannot dereference value of type "{typ}"', span)


class VoidVarInitializerError(UserError):
    """Raised when a ``let`` initializer (module-level or local) has type
    ``void``."""

    kind = diag_kinds.VOID_INITIALIZER

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Variable initializer cannot be void", span)


class CannotTakeAddressOfComptimeValueError(UserError):
    """Raised when a compile-time-evaluated expression's result would need
    the address of a temporary that has no address at runtime."""

    kind = diag_kinds.COMPTIME_ADDRESS_OF_TEMPORARY

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot take address of comptime value", span)


class CallExternFnAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to call a function with no body."""

    kind = diag_kinds.COMPTIME_EXTERN_CALL

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot call extern function at comptime", span)


class SetNonLocalVarAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to write through a pointer
    to a variable outside the expression being evaluated."""

    kind = diag_kinds.COMPTIME_NON_LOCAL_WRITE

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot set non-local variable at comptime", span)


class PanicAtComptimeError(UserError):
    """Raised when compile-time evaluation calls ``panic`` - either directly,
    or via a compiler-synthesized runtime check (array bounds, integer
    overflow, division by zero) evaluated at compile time.
    """

    kind = diag_kinds.COMPTIME_PANIC

    def __init__(self, message: Optional[str], span: Optional[src.SrcSpan]) -> None:
        text = "Compile-time evaluation panicked"
        if message is not None:
            text += f': "{message}"'
        super().__init__(ERROR, text, span)


class PtrCastNotComptimeEvaluableError(UserError):
    """Raised when ``__ptr_cast_mut`` is evaluated at compile time - the
    ``Comptime*`` value model is value-oriented, not byte-oriented, and
    never records a pointer's mutability separately from its pointee, so
    it has no sound way to reinterpret one as a different pointer type,
    not even a mutability-only change."""

    kind = diag_kinds.COMPTIME_POINTER_CAST

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot cast pointer at comptime", span)


class CcInvalidError(UserError):
    """Raised when the ``CC`` environment variable can't be split into a command."""

    kind = diag_kinds.MALFORMED_C_COMPILER_COMMAND

    def __init__(self, value: str, reason: str) -> None:
        super().__init__(ERROR, f"The C compiler command CC={value!r} is invalid: {reason}", None)


class CcNotFoundError(UserError):
    """Raised when the C compiler used for linking isn't on ``PATH``."""

    kind = diag_kinds.MISSING_C_COMPILER

    def __init__(self, program: str) -> None:
        super().__init__(
            ERROR,
            f'C compiler "{program}" not found; install gcc or clang, or set CC to a C compiler',
            None,
        )


class LinkFailedError(UserError):
    """Raised when the C compiler fails to link an executable; notes carry its output."""

    kind = diag_kinds.LINK_FAILURE

    def __init__(self, command: str, problem: str, output: str) -> None:
        super().__init__(ERROR, f"Linking failed: `{command}` {problem}", None)
        if output.strip():
            self._add_extra(NOTE, output.rstrip("\n"), None)


class BuildOutputError(UserError):
    """Raised when a build's output files can't be written."""

    kind = diag_kinds.UNWRITABLE_OUTPUT

    def __init__(self, reason: str) -> None:
        super().__init__(ERROR, f"Cannot write build output: {reason}", None)


class RunFailedError(UserError):
    """Raised when a built program can't be started."""

    kind = diag_kinds.RUN_FAILURE

    def __init__(self, exe: pathlib.Path, reason: str) -> None:
        super().__init__(ERROR, f"Cannot run {exe}: {reason}", None)


class DoctorCheckError(UserError):
    """Raised when a ``leech doctor`` toolchain check fails; a note suggests a fix."""

    kind = diag_kinds.TOOLCHAIN_CHECK_FAILURE

    def __init__(self, problem: str, fix: str) -> None:
        super().__init__(ERROR, problem, None)
        self._add_extra(NOTE, fix, None)


class DoctorFixNote(UserError):  # noqa: N818 - a note, not an error
    """A note from ``leech doctor`` suggesting how to fix the problems reported before it."""

    kind = diag_kinds.C_COMPILER_HINT

    def __init__(self, fix: str) -> None:
        super().__init__(NOTE, fix, None)


class TextErrorRenderer:
    """Renders diagnostics as plain text, with a source excerpt, to stderr."""

    def display_errors(self, errs: Sequence[diag.AnyDiag]) -> None:
        """Render each error in order."""
        for err in errs:
            self._display_error(err)

    def display_internal_error(
        self, errs: Sequence[diag.AnyDiag], err: Exception, tool: str
    ) -> None:
        """Render the diagnostics found before ``tool`` crashed with ``err``, then report the
        crash as a bug in ``tool``."""
        self.display_errors(errs)
        self._display_message(
            Message(ERROR, f"internal compiler error: {type(err).__name__}: {err}", None)
        )
        self._display_message(
            Message(
                NOTE,
                f"this is a bug in {tool}; please report it with the program that triggered it",
                None,
            )
        )

    def _display_error(self, err: diag.AnyDiag) -> None:
        match err:
            case diag.Diag():
                # Labels are shown as spanned notes.
                self._display_message(Message(err.level, err.msg.text(), err.span))
                if err.primary_label is not None:
                    self._display_message(Message(NOTE, err.primary_label.text(), err.span))
                for label in err.labels:
                    self._display_message(Message(NOTE, label.msg.text(), label.span))
                for note in err.notes:
                    self._display_message(Message(NOTE, note.msg.text(), note.span))
            case UserError():
                self._display_message(err.message)
                for message in err.extra:
                    self._display_message(message)

    def _display_message(self, message: Message) -> None:
        print(f"{message.level.name}: {message.message}", file=sys.stderr)
        if message.span is not None:
            line_num_width = len(str(len(message.span.file.lines)))
            self._display_line(message.span, line_num_width)
            self._display_col_pos(message.span, line_num_width)

    def _display_line(self, span: src.SrcSpan, line_num_width) -> None:
        asserts.assert_gt(span.start_line, 0)
        line = span.file.lines[span.start_line - 1]
        print(f"{span.start_line:{line_num_width}d}| {line}", file=sys.stderr)

    def _display_col_pos(self, span: src.SrcSpan, line_num_width) -> None:
        prefix = "-" * (span.start_col + line_num_width + 1)
        print(f"{prefix}^", file=sys.stderr)
