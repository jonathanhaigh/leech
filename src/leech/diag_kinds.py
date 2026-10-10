# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The catalogue of diagnostics: every kind of diagnostic, and every label and note message.

A message template is formatted with ``str.format``: its replacement fields are exactly the
arguments a diagnostic built from it takes. A template starts with a lowercase word or a
double-quoted name, quotes Leech names and types with double quotes, and has no trailing
period.
"""

from typing import Final, Optional

from leech import diag

_CHECK_CC: Final = "check that CC names a C compiler that can link programs, such as gcc"

# Syntax

UNEXPECTED_CHARACTER: Final = diag.DiagKind(
    "unexpected-character", diag.ERROR, 'unexpected character "{char}"'
)
UNEXPECTED_TOKEN: Final = diag.DiagKind("unexpected-token", diag.ERROR, "unexpected {found}")

# Names and paths

UNKNOWN_NAME: Final = diag.DiagKind("unknown-name", diag.ERROR, 'cannot find {item_kind} "{name}"')
PATH_KIND_MISMATCH: Final = diag.DiagKind(
    "path-kind-mismatch",
    diag.ERROR,
    'path "{path}" names a {actual_kind}, not a {expected_kind}',
)
PATH_QUALIFIER_KIND_MISMATCH: Final = diag.DiagKind(
    "path-qualifier-kind-mismatch",
    diag.ERROR,
    'cannot qualify a path with {item_kind} "{name}"',
)
PRIVATE_ITEM_ACCESS: Final = diag.DiagKind(
    "private-item-access", diag.ERROR, 'cannot access private {item_kind} "{name}"'
)
DUPLICATE_DEFINITION: Final = diag.DiagKind(
    "duplicate-definition", diag.ERROR, 'duplicate definition of {item_kind} "{name}"'
)
RESERVED_NAME: Final = diag.DiagKind(
    "reserved-name", diag.ERROR, '"{name}" is reserved and cannot be used as a name'
)
MODULE_USED_AS_TYPE: Final = diag.DiagKind(
    "module-used-as-type", diag.ERROR, 'module "{mod}" cannot be used as a type'
)
TRAIT_USED_AS_TYPE: Final = diag.DiagKind(
    "trait-used-as-type", diag.ERROR, 'trait "{trait}" cannot be used as a type'
)
VALUE_USED_AS_TYPE: Final = diag.DiagKind(
    "value-used-as-type", diag.ERROR, 'value "{name}" cannot be used as a type'
)

# Comptime arguments

MISSING_COMPTIME_ARGUMENT: Final = diag.DiagKind(
    "missing-comptime-argument",
    diag.ERROR,
    'missing comptime arguments for generic item "{item}"',
)
UNINFERABLE_COMPTIME_ARGUMENT: Final = diag.DiagKind(
    "uninferable-comptime-argument",
    diag.ERROR,
    'cannot infer argument for parameter "{param}" of generic {item_kind} "{item}"',
)
COMPTIME_ARGUMENT_COUNT_MISMATCH: Final = diag.DiagKind(
    "comptime-argument-count-mismatch",
    diag.ERROR,
    'wrong number of comptime arguments for generic item "{item}": got {given}, expected '
    "{expected}",
)
UNEXPECTED_COMPTIME_ARGUMENT: Final = diag.DiagKind(
    "unexpected-comptime-argument",
    diag.ERROR,
    '"{item}" is not generic and cannot take arguments',
)
COMPTIME_ARGUMENT_KIND_MISMATCH: Final = diag.DiagKind(
    "comptime-argument-kind-mismatch",
    diag.ERROR,
    'comptime argument {arg} does not match the kind of parameter "{param}"',
)
COMPTIME_ARGUMENT_TYPE_MISMATCH: Final = diag.DiagKind(
    "comptime-argument-type-mismatch",
    diag.ERROR,
    'value "{arg}" does not have the expected type "{expected_typ}"',
)
UNSUPPORTED_VALUE_PARAMETER_TYPE: Final = diag.DiagKind(
    "unsupported-value-parameter-type",
    diag.ERROR,
    '"{typ}" is not a supported comptime value parameter type',
)

# Assignments and variables

ASSIGNMENT_TO_IMMUTABLE_PLACE: Final = diag.DiagKind(
    "assignment-to-immutable-place", diag.ERROR, "cannot assign to an immutable place"
)
ASSIGNMENT_TYPE_MISMATCH: Final = diag.DiagKind(
    "assignment-type-mismatch",
    diag.ERROR,
    'cannot assign a value of type "{given_typ}" to a place of type "{place_typ}"',
)
LET_TYPE_MISMATCH: Final = diag.DiagKind(
    "let-type-mismatch",
    diag.ERROR,
    'cannot initialize "{var}" of type "{declared_typ}" with a value of type "{given_typ}"',
)
VOID_INITIALIZER: Final = diag.DiagKind(
    "void-initializer", diag.ERROR, 'variable initializer cannot have type "void"'
)
RECURSIVE_INITIALIZER: Final = diag.DiagKind(
    "recursive-initializer",
    diag.ERROR,
    'initializer of variable "{var}" depends on itself',
)

# Functions and calls

CONFLICTING_EXTERN_DECLARATIONS: Final = diag.DiagKind(
    "conflicting-extern-declarations",
    diag.ERROR,
    'extern function "{name}" is declared with type "{typ}", but was declared with type '
    '"{earlier_typ}"',
)
NON_FUNCTION_CALL: Final = diag.DiagKind(
    "non-function-call", diag.ERROR, 'cannot call {callee} of type "{typ}"'
)
ARGUMENT_TYPE_MISMATCH: Final = diag.DiagKind(
    "argument-type-mismatch",
    diag.ERROR,
    'argument {arg_num} in call to {callee} has type "{given_typ}", expected "{expected_typ}"',
)
ARGUMENT_COUNT_MISMATCH: Final = diag.DiagKind(
    "argument-count-mismatch",
    diag.ERROR,
    "wrong number of arguments in call to {callee}: got {given}, expected {expected}",
)
RETURN_TYPE_MISMATCH: Final = diag.DiagKind(
    "return-type-mismatch",
    diag.ERROR,
    'return expression has type "{given_typ}" in function "{fn}" that returns "{ret_typ}"',
)
MISSING_RETURN_VALUE: Final = diag.DiagKind(
    "missing-return-value",
    diag.ERROR,
    'cannot return without a value in function "{fn}" that returns "{ret_typ}"',
)
RETURN_OUTSIDE_FUNCTION: Final = diag.DiagKind(
    "return-outside-function", diag.ERROR, '"return" statement outside a function'
)
MISSING_RETURN: Final = diag.DiagKind(
    "missing-return",
    diag.ERROR,
    'missing return statement or tail expression in function "{fn}" that returns "{ret_typ}"',
)
SELF_PARAMETER_OUTSIDE_IMPL: Final = diag.DiagKind(
    "self-parameter-outside-impl",
    diag.ERROR,
    '"self" parameter is only allowed on functions defined inside an "impl" block',
)
ASSOCIATED_FUNCTION_USED_AS_METHOD: Final = diag.DiagKind(
    "associated-function-used-as-method",
    diag.ERROR,
    'associated function "{fn}" of struct "{struct_typ}" has no "self" parameter and cannot '
    "be called as a method",
)

# Operators

BINARY_OPERAND_TYPE_MISMATCH: Final = diag.DiagKind(
    "binary-operand-type-mismatch",
    diag.ERROR,
    'invalid type "{given_typ}" for {side} operand of "{op}", expected "{expected_typ}"',
)
UNARY_OPERAND_TYPE_MISMATCH: Final = diag.DiagKind(
    "unary-operand-type-mismatch",
    diag.ERROR,
    'invalid type "{given_typ}" for operand of "{op}", expected "{expected_typ}"',
)
CONFLICTING_OPERAND_TYPES: Final = diag.DiagKind(
    "conflicting-operand-types",
    diag.ERROR,
    'left and right operands of "{op}" have incompatible types',
)

# Control flow

BREAK_OUTSIDE_LOOP: Final = diag.DiagKind(
    "break-outside-loop", diag.ERROR, '"break" statement outside a loop'
)
CONTINUE_OUTSIDE_LOOP: Final = diag.DiagKind(
    "continue-outside-loop", diag.ERROR, '"continue" statement outside a loop'
)
UNKNOWN_LOOP_LABEL: Final = diag.DiagKind(
    "unknown-loop-label", diag.ERROR, 'cannot find loop label "{label}"'
)
UNREACHABLE_CODE: Final = diag.DiagKind("unreachable-code", diag.WARNING, "unreachable {code}")
CONFLICTING_BRANCH_TYPES: Final = diag.DiagKind(
    "conflicting-branch-types", diag.ERROR, '"if" and "else" have mismatching types'
)
IF_WITHOUT_ELSE_TYPE_MISMATCH: Final = diag.DiagKind(
    "if-without-else-type-mismatch",
    diag.ERROR,
    '"if" without "else" must have type "void", found "{typ}"',
)
IF_CONDITION_TYPE_MISMATCH: Final = diag.DiagKind(
    "if-condition-type-mismatch",
    diag.ERROR,
    '"if" condition must have type "bool", found {expr} of type "{typ}"',
)
WHILE_BODY_TYPE_MISMATCH: Final = diag.DiagKind(
    "while-body-type-mismatch",
    diag.ERROR,
    '"while" loop body must have type "void", found "{typ}"',
)
WHILE_CONDITION_TYPE_MISMATCH: Final = diag.DiagKind(
    "while-condition-type-mismatch",
    diag.ERROR,
    '"while" condition must have type "bool", found {expr} of type "{typ}"',
)

# Arrays, indexing and pointers

ARRAY_ELEMENT_TYPE_MISMATCH: Final = diag.DiagKind(
    "array-element-type-mismatch",
    diag.ERROR,
    'array element {index} of type "{element_typ}" is incompatible with array type "{array_typ}"',
)
ARRAY_ELEMENT_COUNT_MISMATCH: Final = diag.DiagKind(
    "array-element-count-mismatch",
    diag.ERROR,
    'wrong number of elements for array literal of type "{array_typ}": got {given}, expected '
    "{expected}",
)
NAMED_FIELD_IN_ARRAY_LITERAL: Final = diag.DiagKind(
    "named-field-in-array-literal",
    diag.ERROR,
    'array literal cannot contain named field "{field}"',
)
GENERIC_ARRAY_LITERAL_LENGTH: Final = diag.DiagKind(
    "generic-array-literal-length",
    diag.ERROR,
    "array literal's length must be a concrete value, not a generic value parameter",
)
NON_ARRAY_INDEX: Final = diag.DiagKind(
    "non-array-index", diag.ERROR, 'cannot index into a value of type "{typ}"'
)
INDEX_TYPE_MISMATCH: Final = diag.DiagKind(
    "index-type-mismatch", diag.ERROR, 'invalid index type "{typ}"'
)
INTEGER_LITERAL_OVERFLOW: Final = diag.DiagKind(
    "integer-literal-overflow", diag.ERROR, 'integer literal {value} does not fit in type "{typ}"'
)
NON_POINTER_DEREFERENCE: Final = diag.DiagKind(
    "non-pointer-dereference", diag.ERROR, 'cannot dereference a value of type "{typ}"'
)

# Traits and impls

MISSING_SELF_PARAMETER: Final = diag.DiagKind(
    "missing-self-parameter",
    diag.ERROR,
    'trait method "{method}" must take a "self" parameter',
)
ORPHAN_IMPL: Final = diag.DiagKind(
    "orphan-impl",
    diag.ERROR,
    'cannot implement trait "{trait}" for type "{typ}": neither is defined in this module',
)
UNCONSTRAINED_IMPL_PARAMETER: Final = diag.DiagKind(
    "unconstrained-impl-parameter",
    diag.ERROR,
    'impl parameter "{param}" is not constrained by the impl self type',
)
CONFLICTING_IMPLS: Final = diag.DiagKind(
    "conflicting-impls",
    diag.ERROR,
    'conflicting implementations of trait "{trait}" for type "{typ}"',
)
MISSING_TRAIT_METHOD: Final = diag.DiagKind(
    "missing-trait-method",
    diag.ERROR,
    'missing implementation of "{method}" required by trait "{trait}" in "impl {trait} for {typ}"',
)
UNKNOWN_TRAIT_METHOD: Final = diag.DiagKind(
    "unknown-trait-method", diag.ERROR, '"{method}" is not a method of trait "{trait}"'
)
TRAIT_METHOD_TYPE_MISMATCH: Final = diag.DiagKind(
    "trait-method-type-mismatch",
    diag.ERROR,
    'method "{method}" of "impl {trait}" has type "{given_typ}", expected "{expected_typ}"',
)
AMBIGUOUS_METHOD_CALL: Final = diag.DiagKind(
    "ambiguous-method-call",
    diag.ERROR,
    'call to "{method}" on type "{typ}" is ambiguous between multiple traits',
)
RECURSIVE_TRAIT_BOUND: Final = diag.DiagKind(
    "recursive-trait-bound",
    diag.ERROR,
    'trait bound "{bound}" is part of a recursive bound cycle',
)
RECURSIVE_IMPL_SELECTION: Final = diag.DiagKind(
    "recursive-impl-selection",
    diag.ERROR,
    'selecting an implementation of trait "{trait}" for type "{typ}" is recursive',
)
UNSATISFIED_TRAIT_BOUND: Final = diag.DiagKind(
    "unsatisfied-trait-bound",
    diag.ERROR,
    'type "{typ_arg}" does not implement trait "{trait}", required by bound on type parameter '
    '"{param}"',
)
UNSUPPORTED_IMPL_TYPE: Final = diag.DiagKind(
    "unsupported-impl-type",
    diag.ERROR,
    '"impl" blocks are only supported for struct and union types, found {typ}',
)
IMPL_OUTSIDE_TYPE_MODULE: Final = diag.DiagKind(
    "impl-outside-type-module",
    diag.ERROR,
    '"impl" blocks are only supported for types defined in the same module, found {typ}',
)
NON_TRAIT_IMPL: Final = diag.DiagKind(
    "non-trait-impl", diag.ERROR, '"impl ... for ..." blocks require a trait, found {name}'
)
CONFLICTING_VARIANT_AND_FUNCTION_NAMES: Final = diag.DiagKind(
    "conflicting-variant-and-function-names",
    diag.ERROR,
    'associated function "{fn}" has the same name as a variant of union "{union}"',
)

# Structs, unions and enums

NON_STRUCT_OR_ARRAY_LITERAL: Final = diag.DiagKind(
    "non-struct-or-array-literal",
    diag.ERROR,
    'cannot create a value of non-struct, non-array type "{typ}" using literal syntax',
)
POSITIONAL_VALUE_IN_STRUCT_EXPRESSION: Final = diag.DiagKind(
    "positional-value-in-struct-expression",
    diag.ERROR,
    'struct expression of type "{struct_typ}" requires named fields, not positional values',
)
UNKNOWN_STRUCT_FIELD: Final = diag.DiagKind(
    "unknown-struct-field", diag.ERROR, 'struct "{struct_typ}" has no field named "{field}"'
)
PRIVATE_FIELD_ACCESS: Final = diag.DiagKind(
    "private-field-access",
    diag.ERROR,
    'field "{field}" of struct "{struct_typ}" is private',
)
STRUCT_FIELD_TYPE_MISMATCH: Final = diag.DiagKind(
    "struct-field-type-mismatch",
    diag.ERROR,
    'cannot initialize field "{field}" of struct "{struct_typ}", which has type "{field_typ}", '
    'with a value of type "{given_typ}"',
)
MISSING_STRUCT_FIELD: Final = diag.DiagKind(
    "missing-struct-field",
    diag.ERROR,
    'missing field "{field}" in struct expression of type "{struct_typ}"',
)
DUPLICATE_STRUCT_FIELD_VALUE: Final = diag.DiagKind(
    "duplicate-struct-field-value",
    diag.ERROR,
    'duplicate field "{field}" in struct expression',
)
DUPLICATE_STRUCT_FIELD: Final = diag.DiagKind(
    "duplicate-struct-field",
    diag.ERROR,
    'duplicate field "{field}" in struct definition',
)
DUPLICATE_UNION_VARIANT: Final = diag.DiagKind(
    "duplicate-union-variant",
    diag.ERROR,
    'duplicate variant "{variant}" in union definition',
)
DUPLICATE_ENUM_VARIANT: Final = diag.DiagKind(
    "duplicate-enum-variant",
    diag.ERROR,
    'duplicate variant "{variant}" in enum definition',
)
NON_INTEGER_ENUM_BACKING_TYPE: Final = diag.DiagKind(
    "non-integer-enum-backing-type",
    diag.ERROR,
    'enum backing type "{typ}" is not an integer type',
)
ENUM_DISCRIMINANT_TYPE_MISMATCH: Final = diag.DiagKind(
    "enum-discriminant-type-mismatch",
    diag.ERROR,
    'enum variant value has type "{value_typ}", but the enum\'s backing type is "{backing_typ}"',
)
ENUM_DISCRIMINANT_OVERFLOW: Final = diag.DiagKind(
    "enum-discriminant-overflow",
    diag.ERROR,
    "enum discriminant {value} does not fit in any built-in integer type",
)
INFINITELY_SIZED_TYPE: Final = diag.DiagKind(
    "infinitely-sized-type", diag.ERROR, 'recursive {typ_kind} "{typ}" has infinite size'
)
NON_STRUCT_FIELD_ACCESS: Final = diag.DiagKind(
    "non-struct-field-access",
    diag.ERROR,
    'cannot access a field of a value of type "{typ}"',
)
MISSING_VARIANT_PAYLOAD: Final = diag.DiagKind(
    "missing-variant-payload",
    diag.ERROR,
    'variant "{variant}" carries a payload, so it names a value only when called',
)
UNEXPECTED_VARIANT_PAYLOAD: Final = diag.DiagKind(
    "unexpected-variant-payload",
    diag.ERROR,
    'variant "{variant}" carries no payload, so it is named without a call',
)

# Patterns and matches

NON_EXHAUSTIVE_MATCH: Final = diag.DiagKind(
    "non-exhaustive-match", diag.ERROR, '"match" is not exhaustive'
)
UNREACHABLE_MATCH_ARM: Final = diag.DiagKind(
    "unreachable-match-arm", diag.WARNING, "unreachable match arm"
)
CONFLICTING_MATCH_ARM_TYPES: Final = diag.DiagKind(
    "conflicting-match-arm-types", diag.ERROR, '"match" arms have mismatching types'
)
PATTERN_TYPE_MISMATCH: Final = diag.DiagKind(
    "pattern-type-mismatch",
    diag.ERROR,
    'mismatched types: {pattern} has type "{pattern_typ}", but the scrutinee has type '
    '"{scrutinee_typ}"',
)
BINDING_IN_OR_PATTERN: Final = diag.DiagKind(
    "binding-in-or-pattern", diag.ERROR, '"let" bindings are not allowed inside or-patterns'
)
NON_PATTERN_PATH: Final = diag.DiagKind(
    "non-pattern-path", diag.ERROR, 'path "{path}" is not a pattern'
)
PAYLOAD_PATTERN_COUNT_MISMATCH: Final = diag.DiagKind(
    "payload-pattern-count-mismatch",
    diag.ERROR,
    'wrong number of payload patterns for variant "{variant}": got {given}, expected {expected}',
)

# Compile-time evaluation

COMPTIME_ADDRESS_OF_TEMPORARY: Final = diag.DiagKind(
    "comptime-address-of-temporary",
    diag.ERROR,
    "cannot take the address of a comptime value",
)
COMPTIME_EXTERN_CALL: Final = diag.DiagKind(
    "comptime-extern-call", diag.ERROR, "cannot call an extern function at comptime"
)
COMPTIME_NON_LOCAL_WRITE: Final = diag.DiagKind(
    "comptime-non-local-write", diag.ERROR, "cannot set a non-local variable at comptime"
)
COMPTIME_PANIC: Final = diag.DiagKind(
    "comptime-panic", diag.ERROR, "compile-time evaluation panicked"
)
COMPTIME_POINTER_CAST: Final = diag.DiagKind(
    "comptime-pointer-cast", diag.ERROR, "cannot cast a pointer at comptime"
)

# Modules and the entry point

UNKNOWN_MODULE: Final = diag.DiagKind("unknown-module", diag.ERROR, 'cannot find module "{name}"')
MODULE_LOCATION_MISMATCH: Final = diag.DiagKind(
    "module-location-mismatch",
    diag.ERROR,
    'module name "{name}" does not match the location of "{path}"',
)
MODULE_OUTSIDE_PACKAGES: Final = diag.DiagKind(
    "module-outside-packages",
    diag.ERROR,
    'module "{name}" is "{path}", which links to a file outside the root package and the '
    "standard library",
)
RESERVED_MODULE_NAME: Final = diag.DiagKind(
    "reserved-module-name", diag.ERROR, 'module name "{name}" for "{path}" is reserved'
)
MISSING_MAIN_FUNCTION: Final = diag.DiagKind(
    "missing-main-function", diag.ERROR, 'entry module "{mod}" ({path}) has no "main" function'
)
NON_FUNCTION_MAIN: Final = diag.DiagKind(
    "non-function-main",
    diag.ERROR,
    'the program entry point "main" must be a function defined with a body',
)
GENERIC_MAIN: Final = diag.DiagKind(
    "generic-main", diag.ERROR, 'the program entry point "main" cannot be generic'
)
MAIN_TYPE_MISMATCH: Final = diag.DiagKind(
    "main-type-mismatch",
    diag.ERROR,
    'the program entry point "main" must have type "fn() i32", not "{fn_typ}"',
)
CONFLICTING_MAIN_DECLARATIONS: Final = diag.DiagKind(
    "conflicting-main-declarations",
    diag.ERROR,
    '"extern fn main" has type "{fn_typ}", but the program entry point defines the C "main" '
    'symbol with type "fn() i32"',
)

# The toolchain

MALFORMED_C_COMPILER_COMMAND: Final = diag.DiagKind(
    "malformed-c-compiler-command", diag.ERROR, 'invalid C compiler command "CC={value}": {reason}'
)
MISSING_C_COMPILER: Final = diag.DiagKind(
    "missing-c-compiler", diag.ERROR, 'cannot find C compiler "{program}"'
)
LINK_FAILURE: Final = diag.DiagKind(
    "link-failure", diag.ERROR, "linking failed: `{command}` {problem}"
)
UNWRITABLE_OUTPUT: Final = diag.DiagKind(
    "unwritable-output", diag.ERROR, "cannot write build output: {reason}"
)
RUN_FAILURE: Final = diag.DiagKind("run-failure", diag.ERROR, 'cannot run "{exe}": {reason}')
TOOLCHAIN_CHECK_FAILURE: Final = diag.DiagKind(
    "toolchain-check-failure", diag.ERROR, "toolchain check failed: {problem}"
)
C_COMPILER_HINT: Final = diag.DiagKind("c-compiler-hint", diag.NOTE, _CHECK_CC)

# Labels and notes

EXPECTED_ONE_OF: Final = diag.MsgKind("expected one of: {expected}")
DEFINED_HERE: Final = diag.MsgKind('"{name}" defined here')
PREVIOUS_DEFN_HERE: Final = diag.MsgKind("previous definition here")
PREVIOUS_VALUE_HERE: Final = diag.MsgKind("previous value here")
PREVIOUS_IMPL_HERE: Final = diag.MsgKind("previous implementation here")
EARLIER_DECL_HERE: Final = diag.MsgKind("earlier declaration here")
DECLARED_TYP_HERE: Final = diag.MsgKind('declared with type "{typ}" here')
PLACE_TYP: Final = diag.MsgKind('place has type "{typ}"')
RET_TYP_HERE: Final = diag.MsgKind("return type specified here")
OP_HERE: Final = diag.MsgKind('for "{op}" operation here')
LEFT_OPERAND_TYP: Final = diag.MsgKind('left operand has type "{typ}"')
RIGHT_OPERAND_TYP: Final = diag.MsgKind('right operand has type "{typ}"')
IF_TYP: Final = diag.MsgKind('"if" has type "{typ}"')
ELSE_TYP: Final = diag.MsgKind('"else" has type "{typ}"')
MATCH_ARM_TYP: Final = diag.MsgKind('match arm has type "{typ}"')
UNCOVERED_PATTERN: Final = diag.MsgKind('uncovered pattern "{pattern}"')
GIVE_COMPTIME_ARGS: Final = diag.MsgKind('give it explicitly, e.g. "{item}[...]"')
MOD_QUALIFIES_PATHS: Final = diag.MsgKind(
    'a module name can only qualify a path, e.g. "{mod}::SomeTyp"'
)
VARIANT_SHADOWS_FN: Final = diag.MsgKind(
    'a path into "{union}" names the variant, so "{union}::{fn}" could not name this function'
)
BOUND_IN_CYCLE: Final = diag.MsgKind('trait bound "{bound}" participates in this cycle')
IMPL_IN_CYCLE: Final = diag.MsgKind('implementation "{impl}" participates in this cycle')
FIELD_CONTAINS_BY_VALUE: Final = diag.MsgKind(
    'field "{field}" of struct "{container}" contains "{contained}" by value'
)
PAYLOAD_CONTAINS_BY_VALUE: Final = diag.MsgKind(
    'payload {index} of variant "{variant}" of union "{container}" contains "{contained}" by value'
)
PANIC_MESSAGE: Final = diag.MsgKind('panic message: "{message}"')
MOD_NAME_LOCATION: Final = diag.MsgKind(
    'a module named "x::a" must be the file x/a.leech in its package directory'
)
STD_NAMES_RESERVED: Final = diag.MsgKind(
    'names starting with "std" belong to the bundled standard library'
)
INSTALL_CC: Final = diag.MsgKind("install gcc or clang, or set CC to a C compiler")
LINKER_OUTPUT: Final = diag.MsgKind("the C compiler printed:\n{output}")
CHECK_CC: Final = diag.MsgKind(_CHECK_CC)

DIAG_KINDS: Final[tuple[diag.DiagKind, ...]] = tuple(
    value for value in globals().values() if isinstance(value, diag.DiagKind)
)
"""Every kind of diagnostic, in catalogue order."""

MSG_KINDS: Final[tuple[diag.MsgKind, ...]] = tuple(
    value for value in globals().values() if isinstance(value, diag.MsgKind)
)
"""Every label and note message, in catalogue order."""


def lookup(name: str) -> Optional[diag.DiagKind]:
    """Return the kind of diagnostic with the current or former name ``name``, if any."""
    for kind in DIAG_KINDS:
        if name == kind.name or name in kind.aliases:
            return kind
    return None
