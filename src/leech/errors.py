# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""User-facing diagnostics: error/warning types, and their rendering."""

import dataclasses
import enum
import sys
from collections.abc import Collection, Sequence
from typing import Final, Optional

from leech import asserts, src


class Level(enum.IntEnum):
    """The severity of a diagnostic message."""

    NOTE = 0
    WARNING = 1
    ERROR = 2


NOTE = Level.NOTE
WARNING = Level.WARNING
ERROR = Level.ERROR


@dataclasses.dataclass(frozen=True)
class Message:
    """A single diagnostic message, optionally located in source."""

    level: Level
    message: str
    span: Optional[src.SrcSpan]


class UserError(Exception):
    """A diagnostic with a primary message and optional accompanying messages."""

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


class UnexpectedCharacterError(UserError):
    """Raised when the lexer encounters a character that can't start any
    valid token, given where it appears in the source."""

    def __init__(self, char: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Unexpected character "{char}"', span)


class UnexpectedTokenError(UserError):
    """Raised for an unexpected token or premature end of input."""

    def __init__(self, found: str, span: src.SrcSpan, expected: Collection[str]) -> None:
        super().__init__(ERROR, f"Unexpected {found}", span)
        if expected:
            self._add_extra(NOTE, f"Expected one of: {', '.join(expected)}", None)


class ItemNotFoundError(UserError):
    """Raised when a name cannot be resolved in scope."""

    def __init__(self, item_kind: str, name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'{item_kind.capitalize()} "{name}" not found.', span)


class MissingComptimeArgsError(UserError):
    """Raised when a generic item is used as a value without required comptime arguments."""

    def __init__(self, item_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Generic item "{item_name}" used without required comptime arguments',
            span,
        )


class CannotInferComptimeArgError(UserError):
    """Raised when a generic function call can't determine one of its
    comptime parameters from its arguments' types, and no explicit
    comptime argument was given for it either."""

    def __init__(self, fn_name: str, typ_param_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            (
                f'Cannot infer argument for parameter "{typ_param_name}"'
                f' of generic function "{fn_name}"'
            ),
            span,
        )
        self._add_extra(NOTE, f'Give it explicitly, e.g. "{fn_name}[...]"', None)


class WrongNumberOfComptimeArgsError(UserError):
    """Raised when a generic item is given the wrong number of explicit
    comptime arguments."""

    def __init__(
        self, item_name: str, given: int, expected: int, span: Optional[src.SrcSpan]
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Wrong number of comptime arguments for generic item "{item_name}":'
                f" got {given}, expected {expected}"
            ),
            span,
        )


class ComptimeArgsOnNonGenericItemError(UserError):
    """Raised when comptime arguments are given for an item that isn't generic."""

    def __init__(self, item_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"{item_name}" is not generic and cannot take arguments',
            span,
        )


class WrongKindOfComptimeArgError(UserError):
    """Raised when a type argument is given for a value parameter, or a
    value argument is given for a type parameter."""

    def __init__(self, param_name: str, arg_desc: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'{arg_desc} does not match the kind of parameter "{param_name}" expects',
            span,
        )


class WrongComptimeValueTypError(UserError):
    """Raised when a value argument's type doesn't match its parameter's
    declared type."""

    def __init__(self, arg_name: str, expected_typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Value "{arg_name}" does not have the expected type "{expected_typ_name}"',
            span,
        )


class PrivateItemAccessError(UserError):
    """Raised when accessing a private module-level item (function,
    variable, or type) from outside the module it's defined in (or, for an
    associated function, from outside the module its struct is defined
    in)."""

    def __init__(
        self,
        item_kind: str,
        name: str,
        access_span: Optional[src.SrcSpan],
        defn_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(ERROR, f'{item_kind.capitalize()} "{name}" is private', access_span)
        if defn_span is not None:
            self._add_extra(NOTE, f'{item_kind.capitalize()} "{name}" defined here', defn_span)


class AssignToConstError(UserError):
    """Raised when assigning through a const pointer or to a const place."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot assign to const place expression", span)


class IncompatibleAssignmentTypError(UserError):
    """Raised when an assigned value's type doesn't match the place's."""

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


class DuplicateItemDefnError(UserError):
    """Raised when a name is defined more than once in the same scope."""

    def __init__(
        self,
        item_kind: str,
        name: str,
        span: Optional[src.SrcSpan],
        existing_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(ERROR, f'Duplicate definition of {item_kind} "{name}"', span)
        if existing_span is not None:
            self._add_extra(NOTE, "Previous definition here", existing_span)


class NotCallableError(UserError):
    """Raised when calling a value whose type isn't a function pointer."""

    def __init__(self, callee_diag: str, given_typ: str, span: Optional[src.SrcSpan]):
        super().__init__(ERROR, f'{callee_diag} of type "{given_typ}" is not callable', span)


class InvalidArgTypError(UserError):
    """Raised when a call argument's type doesn't match the parameter's type."""

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
                f'{arg_name.capitalize()} operand of binary operation "{op}"'
                f' has invalid type "{given_typ}",'
                f' expecting "{expected_typ}"'
            ),
            arg_span,
        )
        if op_span is not None:
            self._add_extra(NOTE, f'For "{op}" operation here', op_span)


class InvalidUnaryOpArgTypError(UserError):
    """Raised when a unary operator's operand has an unsupported type."""

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

    def __init__(self, ret_span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Return statement not in function", ret_span)


class BreakNotInLoopError(UserError):
    """Raised when a ``break`` statement appears outside any loop."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Break statement not in a loop", span)


class ContinueNotInLoopError(UserError):
    """Raised when a ``continue`` statement appears outside any loop."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Continue statement not in a loop", span)


class LoopLabelNotFoundError(UserError):
    """Raised when a ``break``/``continue`` names a label no enclosing loop has."""

    def __init__(self, label_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Loop label "{label_name}" not found', span)


class UnreachableCodeWarning(UserError):  # noqa: N818 - a warning, not an error
    """Warns about code that can never be executed."""

    def __init__(self, code_typ: str, code_span: Optional[src.SrcSpan]) -> None:
        super().__init__(WARNING, f"{code_typ} is unreachable", code_span)


class MissingRetError(UserError):
    """Raised when a non-void function's body doesn't return or diverge."""

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


class VoidArrayElementError(UserError):
    """Raised when an array literal's element type is ``void``."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, 'Array element cannot have type "void"', span)


class EmptyArrayTypUnknownError(UserError):
    """Raised when an empty array literal's element type can't be inferred.

    An empty array literal has no elements to infer a type from, so it can
    only be typed when the surrounding context (a function argument,
    return value, struct field, or assignment target) already has a known
    array type.
    """

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot infer element type of empty array expression", span)


class ModUsedAsTypError(UserError):
    """Raised when a module name is used where a type is required.

    Modules share a namespace with types (so the two can't share a name),
    but a module isn't a type: it can only qualify a path.
    """

    def __init__(self, mod_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Module "{mod_name}" cannot be used as a type', span)
        self._add_extra(
            NOTE,
            f'A module name can only qualify a path, e.g. "{mod_name}::SomeTyp"',
            None,
        )


class TraitUsedAsTypError(UserError):
    """Raised when a trait name is used where a type is required.

    Traits share the ``CONTAINERS`` namespace with types and modules (so
    none of the three can share a name), but a trait isn't itself a type -
    only a bound on one, or the target of an ``impl ... for ...`` block.
    """

    def __init__(self, trait_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Trait "{trait_name}" cannot be used as a type', span)


class ValueUsedAsTypError(UserError):
    """Raised when a comptime value or value parameter is used where a type is required.

    A value parameter's type constrains it, but the parameter itself - or
    a concrete value substituted for it - is never itself a type.
    """

    def __init__(self, name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Value "{name}" cannot be used as a type', span)


class TraitMethodMissingReceiverError(UserError):
    """Raised when a trait method prototype has no ``self``/``mut self``
    receiver. Every trait method dispatches on its receiver's type, so an
    associated-function-style prototype with none isn't supported yet."""

    def __init__(self, method_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Trait method "{method_name}" must take a "self" parameter',
            span,
        )


class OrphanImplError(UserError):
    """Raised when neither a trait impl's trait nor its self type is
    defined in the current module - Leech's orphan rule (similar to
    Rust's), which keeps any two modules from being able to write
    conflicting impls of the same trait for the same type."""

    def __init__(self, trait_name: str, typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            (
                f'Cannot implement trait "{trait_name}" for type "{typ_name}": '
                "neither is defined in this module"
            ),
            span,
        )


class UnconstrainedImplComptimeParamError(UserError):
    """Raised when impl selection cannot determine one of its comptime parameters."""

    def __init__(self, name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Impl parameter "{name}" is not constrained by the impl self type',
            span,
        )


class ConflictingImplsError(UserError):
    """Raised when two impls of the same trait could apply to overlapping
    self types."""

    def __init__(
        self,
        trait_name: str,
        typ_name: str,
        span: Optional[src.SrcSpan],
        existing_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Conflicting implementations of trait "{trait_name}" for type "{typ_name}"',
            span,
        )
        if existing_span is not None:
            self._add_extra(NOTE, "Previous implementation here", existing_span)


class TraitMethodNotImplementedError(UserError):
    """Raised when an ``impl Trait for ...`` block omits a method the
    trait declares."""

    def __init__(
        self,
        trait_name: str,
        method_name: str,
        typ_name: str,
        span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Missing implementation of "{method_name}" required by trait "{trait_name}"'
                f' in "impl {trait_name} for {typ_name}"'
            ),
            span,
        )


class ExtraMethodInImplError(UserError):
    """Raised when an ``impl Trait for ...`` block defines a method the
    trait doesn't declare."""

    def __init__(self, trait_name: str, method_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"{method_name}" is not a method of trait "{trait_name}"',
            span,
        )


class TraitMethodSignatureMismatchError(UserError):
    """Raised when an ``impl Trait for ...`` block's method doesn't match
    the signature the trait declares for it."""

    def __init__(
        self,
        trait_name: str,
        method_name: str,
        given_typ: str,
        expected_typ: str,
        span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Method "{method_name}" of "impl {trait_name}" has type "{given_typ}",'
                f' expected "{expected_typ}"'
            ),
            span,
        )


class AmbiguousMethodError(UserError):
    """Raised when a method call could resolve to more than one trait's
    method of the same name for the receiver's type."""

    def __init__(self, method_name: str, typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Call to "{method_name}" on type "{typ_name}" is ambiguous between multiple traits',
            span,
        )


class ReservedNameError(UserError):
    """Raised when a declaration takes a name the language reserves."""

    def __init__(self, name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'"{name}" is reserved and cannot be used as a name', span)


class InvalidComptimeBoundError(UserError):
    """Raised when a comptime parameter's declared bound names neither a
    trait nor a supported value parameter type."""

    def __init__(self, name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"{name}" is not a trait or a supported value type, so it cannot be used as a bound',
            span,
        )


class RecursiveTraitBoundError(UserError):
    """Raised because recursive trait bounds are not supported."""

    def __init__(
        self,
        bound_name: str,
        bound_span: Optional[src.SrcSpan],
        cycle: Sequence[tuple[str, Optional[src.SrcSpan]]],
    ) -> None:
        super().__init__(
            ERROR,
            f'Trait bound "{bound_name}" is part of a recursive bound cycle',
            bound_span,
        )
        for name, span in cycle:
            self._add_extra(NOTE, f'Trait bound "{name}" participates in this cycle', span)


@dataclasses.dataclass(frozen=True)
class ImplSelectionHop:
    """One implementation obligation participating in a selection cycle."""

    impl_name: str
    impl_span: Optional[src.SrcSpan]


class RecursiveImplSelectionError(UserError):
    """Raised because recursively conditional implementations are unsupported."""

    def __init__(
        self,
        trait_name: str,
        typ_name: str,
        impl_span: Optional[src.SrcSpan],
        cycle: Sequence[ImplSelectionHop],
    ) -> None:
        super().__init__(
            ERROR,
            f'Selecting an implementation of trait "{trait_name}" for type '
            f'"{typ_name}" is recursive',
            impl_span,
        )
        for hop in cycle:
            self._add_extra(
                NOTE,
                f'Implementation "{hop.impl_name}" participates in this cycle',
                hop.impl_span,
            )


class UnsatisfiedBoundError(UserError):
    """Raised when a generic instantiation's type argument doesn't
    implement a bound its type parameter declares."""

    def __init__(
        self,
        typ_arg_name: str,
        trait_name: str,
        typ_param_name: str,
        span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            (
                f'Type "{typ_arg_name}" does not implement trait "{trait_name}",'
                f' required by bound on type parameter "{typ_param_name}"'
            ),
            span,
        )


class TypeOfStructExprNotStructError(UserError):
    """Raised when a struct literal's named type isn't actually a struct type."""

    def __init__(self, typ: str, span: Optional[src.SrcSpan]):
        super().__init__(
            ERROR,
            f'Cannot create value of not struct type "{typ}" using struct expression',
            span,
        )


class ImplForNonStructTypError(UserError):
    """Raised when an ``impl`` block targets a non-struct type."""

    def __init__(self, typ_diag: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"impl" blocks are only supported for struct types, found {typ_diag}',
            span,
        )


class ImplForNonLocalStructTypError(UserError):
    """Raised when an ``impl`` block targets a struct defined in another module."""

    def __init__(self, typ_diag: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            (
                '"impl" blocks are only supported for structs defined in the'
                f" same module, found {typ_diag}"
            ),
            span,
        )


class ImplForNonTraitError(UserError):
    """Raised when an ``impl ... for ...`` block's head doesn't name a trait."""

    def __init__(self, name_diag: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"impl ... for ..." blocks require a trait, found {name_diag}',
            span,
        )


class SelfParamOutsideImplError(UserError):
    """Raised when a function outside an ``impl`` block - including an
    ``extern`` declaration, which can never be an associated function -
    declares a ``*self``/``*mut self`` receiver."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            '"self" parameter is only allowed on functions defined inside an "impl" block',
            span,
        )


class NotAMethodError(UserError):
    """Raised when calling ``x.name(...)`` where ``name`` is an associated
    function of ``x``'s struct type, but that function has no ``self``
    receiver, so it can't be called via dot syntax."""

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


class DuplicateFieldInStructDefnError(UserError):
    """Raised when a struct declaration defines the same field name twice."""

    def __init__(
        self,
        field_name: str,
        duplicate_span: Optional[src.SrcSpan],
        previous_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Duplicate field "{field_name}" in struct definition',
            duplicate_span,
        )
        if previous_span is not None:
            self._add_extra(
                NOTE,
                f'Definition of field "{field_name}" previously given here',
                previous_span,
            )


class DuplicateVariantInEnumDefnError(UserError):
    """Raised when an enum declaration defines the same variant name twice."""

    def __init__(
        self,
        variant_name: str,
        duplicate_span: Optional[src.SrcSpan],
        previous_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'Duplicate variant "{variant_name}" in enum definition',
            duplicate_span,
        )
        if previous_span is not None:
            self._add_extra(
                NOTE,
                f'Variant "{variant_name}" previously given here',
                previous_span,
            )


class EnumBackingTypNotIntError(UserError):
    """Raised when an enum's explicit backing type isn't an integer type."""

    def __init__(self, typ_name: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Enum backing type "{typ_name}" is not an integer type', span)


class EnumVariantValueTypMismatchError(UserError):
    """Raised when a variant's explicitly-suffixed discriminant literal
    doesn't match its enum's explicit backing type."""

    def __init__(self, value_typ: str, backing_typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'Enum variant value has type "{value_typ}", but the enum\'s backing '
            f'type is "{backing_typ}"',
            span,
        )


class EnumDiscriminantOverflowError(UserError):
    """Raised when an enum has no explicit backing type and one of its
    discriminants doesn't fit any builtin integer type, so no backing type
    can be inferred."""

    def __init__(self, value: int, span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR, f"Enum discriminant {value} does not fit in any built-in integer type", span
        )


@dataclasses.dataclass(frozen=True)
class StructLayoutHop:
    """One by-value field edge followed while checking a struct layout."""

    containing_struct: str
    field_name: str
    field_span: Optional[src.SrcSpan]
    contained_struct: str


class InfiniteSizeStructError(UserError):
    """Raised when struct layout follows a recursive by-value declaration cycle.

    The recursion may be direct, pass through other structs or arrays, or recur through
    structurally growing arguments of one generic declaration. A field behind a pointer
    does not count because a pointer's size does not depend on its pointee. Arrays of any
    length do count, including zero-length arrays, because LLVM rejects recursive identified
    struct layouts even when an intervening array has no elements.
    """

    def __init__(
        self,
        struct_name: str,
        struct_span: Optional[src.SrcSpan],
        cycle: Sequence[StructLayoutHop],
    ) -> None:
        super().__init__(ERROR, f'Struct "{struct_name}" has infinite size', struct_span)
        for hop in cycle:
            self._add_extra(
                NOTE,
                f'Field "{hop.field_name}" of struct "{hop.containing_struct}" contains '
                f'"{hop.contained_struct}" by value',
                hop.field_span,
            )


class FieldAccessIntoInvalidTypError(UserError):
    """Raised when using ``.`` field access on a non-struct type."""

    def __init__(self, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Field access into invalid type "{typ}"', span)


class IfElsTypMismatchError(UserError):
    """Raised when an ``if`` expression's two branches have differing types."""

    def __init__(
        self,
        then_typ: str,
        then_span: Optional[src.SrcSpan],
        els_typ: str,
        els_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(ERROR, '"if" and "else" have mismatching types', None)
        self._add_extra(NOTE, f'"if" type is "{then_typ}"', then_span)
        self._add_extra(NOTE, f'"else" type is "{els_typ}"', els_span)


class IfTypNotVoidError(UserError):
    """Raised when an ``if`` without ``else`` has a non-void ``then`` type."""

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

    def __init__(self, expr_diag: str, expr_typ: str, expr_span: Optional[src.SrcSpan]) -> None:
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
        block_span: Optional[src.SrcSpan],
    ) -> None:
        super().__init__(
            ERROR,
            f'block expression for "while" loop must have type "void", found "{block_typ}"',
            block_span,
        )


class WhileCondNotBoolError(UserError):
    """Raised when a ``while`` loop's condition's type isn't ``bool``."""

    def __init__(self, expr_diag: str, expr_typ: str, expr_span: Optional[src.SrcSpan]) -> None:
        super().__init__(
            ERROR,
            f'"while" condition must have type "bool", found {expr_diag} of type "{expr_typ}"',
            expr_span,
        )


class IndexIntoInvalidTypError(UserError):
    """Raised when using ``[]`` indexing on a non-array type."""

    def __init__(self, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Index into invalid type "{typ}"', span)


class InvalidIndexTypError(UserError):
    """Raised when an array index expression's type isn't ``usize``."""

    def __init__(self, typ: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Invalid index typ "{typ}"', span)


class IntLitOverflowError(UserError):
    """Raised when an integer literal doesn't fit in its type's width."""

    def __init__(self, value: int, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Integer literal {value} does not fit in type "{typ}"', span)


class DerefInvalidTypError(UserError):
    """Raised when dereferencing a value whose type isn't a data pointer."""

    def __init__(self, typ: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Cannot dereference value of type "{typ}"', span)


class VoidVarInitializerError(UserError):
    """Raised when a ``let`` initializer (module-level or local) has type
    ``void``."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Variable initializer cannot be void", span)


class CircularVarInitializerError(UserError):
    """Raised when a module variable's initializer depends on itself,
    directly or transitively through other module variables."""

    def __init__(
        self,
        var_name: str,
        var_span: Optional[src.SrcSpan],
        cycle: Sequence[tuple[str, Optional[src.SrcSpan]]],
    ) -> None:
        super().__init__(ERROR, f'Initializer of variable "{var_name}" depends on itself', var_span)
        for name, span in cycle:
            self._add_extra(NOTE, f'Variable "{name}" defined here', span)


class CannotTakeAddressOfComptimeValueError(UserError):
    """Raised when a compile-time-evaluated expression's result would need
    the address of a temporary that has no address at runtime."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot take address of comptime value", span)


class CallExternFnAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to call a function with no body."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot call extern function at comptime", span)


class SetNonLocalVarAtComptimeError(UserError):
    """Raised when compile-time evaluation needs to write through a pointer
    to a variable outside the expression being evaluated."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot set non-local variable at comptime", span)


class PanicAtComptimeError(UserError):
    """Raised when compile-time evaluation calls ``panic`` - either directly,
    or via a compiler-synthesized runtime check (array bounds, integer
    overflow, division by zero) evaluated at compile time.
    """

    def __init__(self, message: Optional[str], span: Optional[src.SrcSpan]) -> None:
        text = "Compile-time evaluation panicked"
        if message is not None:
            text += f': "{message}"'
        super().__init__(ERROR, text, span)


class SizeOfNotComptimeEvaluableError(UserError):
    """Raised when ``__size_of`` is evaluated at compile time for a type
    (a struct or array) whose layout isn't computed in Python, to avoid
    disagreeing with LLVM's own struct-layout/padding rules."""

    def __init__(self, typ: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, f'Cannot compute size of "{typ}" at comptime', span)


class PtrCastNotComptimeEvaluableError(UserError):
    """Raised when ``__ptr_cast_mut`` is evaluated at compile time - the
    ``Comptime*`` value model is value-oriented, not byte-oriented, and
    never records a pointer's mutability separately from its pointee, so
    it has no sound way to reinterpret one as a different pointer type,
    not even a mutability-only change."""

    def __init__(self, span: Optional[src.SrcSpan]) -> None:
        super().__init__(ERROR, "Cannot cast pointer at comptime", span)


class ModDoesNotExistError(UserError):
    """Raised when an ``import`` names a module file that doesn't exist."""

    def __init__(self, name: str, span: src.SrcSpan) -> None:
        super().__init__(ERROR, f'Cannot find module "{name}"', span)


class TextErrorRenderer:
    """Renders diagnostics as plain text, with a source excerpt, to stderr."""

    def display_errors(self, errs: list[UserError]) -> None:
        """Render each error in order."""
        for err in errs:
            self._display_error(err)

    def _display_error(self, err: UserError) -> None:
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


_errors: Final[list[UserError]] = []
_error_level: Level = NOTE


def register_error(err: UserError) -> None:
    """Record a diagnostic and update the overall error level."""
    global _error_level  # noqa: PLW0603 - single module-level counter, not worth a class
    if err.level > _error_level:
        _error_level = err.level
    _errors.append(err)


def all_errors() -> list[UserError]:
    """Return all diagnostics recorded so far, in registration order."""
    return _errors


def error_level() -> Level:
    """Return the highest severity level among all recorded diagnostics."""
    return _error_level
