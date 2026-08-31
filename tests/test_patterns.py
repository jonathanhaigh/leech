# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Sequence

from leech import patterns


def _wildcard() -> patterns.WildcardPattern:
    return patterns.WildcardPattern()


def _variant(discriminant: int, name: str) -> patterns.ConstructorPattern:
    return patterns.ConstructorPattern(patterns.VariantConstructor(discriminant, name), ())


def _bool(value: bool) -> patterns.ConstructorPattern:
    return patterns.ConstructorPattern(patterns.BoolConstructor(value), ())


def _int(value: int) -> patterns.ConstructorPattern:
    return patterns.ConstructorPattern(patterns.IntConstructor(value), ())


def _or(*alts: patterns.PatternKind) -> patterns.OrPattern:
    return patterns.OrPattern(tuple(alts))


def _enum_space(*pairs: tuple[int, str]) -> patterns.ConstructorSpace:
    return patterns.ConstructorSpace.from_constructors(
        [patterns.VariantConstructor(discriminant, name) for discriminant, name in pairs], False
    )


def _bool_space() -> patterns.ConstructorSpace:
    return patterns.ConstructorSpace.from_constructors(
        [patterns.BoolConstructor(True), patterns.BoolConstructor(False)], False
    )


def _int_space() -> patterns.ConstructorSpace:
    return patterns.ConstructorSpace.from_constructors([], True)


def _never_space() -> patterns.ConstructorSpace:
    return patterns.ConstructorSpace.from_constructors([], False)


def _missing_renders(
    matrix: Sequence[patterns.PatternKind], space: patterns.ConstructorSpace
) -> list[str]:
    return [witness.render() for witness in patterns.missing_patterns(matrix, space)]


def test_constructor_equality_keys_on_discriminant_not_name():
    assert patterns.VariantConstructor(1, "A") == patterns.VariantConstructor(1, "B")
    assert patterns.VariantConstructor(1, "A") != patterns.VariantConstructor(2, "A")
    assert patterns.BoolConstructor(True) == patterns.BoolConstructor(True)
    assert patterns.BoolConstructor(True) != patterns.BoolConstructor(False)
    assert patterns.IntConstructor(3) == patterns.IntConstructor(3)
    assert patterns.IntConstructor(3) != patterns.IntConstructor(4)


def test_constructor_space_dedups_by_discriminant_preserving_order():
    space = patterns.ConstructorSpace.from_constructors(
        [
            patterns.VariantConstructor(1, "A"),
            patterns.VariantConstructor(1, "B"),
            patterns.VariantConstructor(2, "C"),
        ],
        False,
    )
    assert space.constructors == (
        patterns.VariantConstructor(1, "A"),
        patterns.VariantConstructor(2, "C"),
    )


def test_exhaustive_enum_has_no_missing_patterns():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    matrix = [_variant(0, "Red"), _variant(1, "Green"), _variant(2, "Blue")]
    assert patterns.missing_patterns(matrix, space) == []


def test_non_exhaustive_enum_names_missing_variant():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    matrix = [_variant(0, "Red"), _variant(1, "Green")]
    assert _missing_renders(matrix, space) == ["Blue"]


def test_wildcard_after_exhaustive_enum_is_unreachable():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    matrix = [_variant(0, "Red"), _variant(1, "Green"), _variant(2, "Blue")]
    assert not patterns.is_useful(matrix, _wildcard(), space)


def test_aliased_discriminants_are_one_case():
    space = _enum_space((1, "A"), (1, "B"))
    assert patterns.missing_patterns([_variant(1, "A")], space) == []
    assert not patterns.is_useful([_variant(1, "A")], _variant(1, "B"), space)


def test_bool_empty_matrix_is_exhausted_by_both_values():
    space = _bool_space()
    assert _missing_renders([], space) == ["true", "false"]


def test_bool_single_value_needs_the_other():
    space = _bool_space()
    assert _missing_renders([_bool(True)], space) == ["false"]


def test_bool_both_values_are_exhaustive():
    space = _bool_space()
    assert patterns.missing_patterns([_bool(True), _bool(False)], space) == []


def test_open_int_space_requires_wildcard():
    space = _int_space()
    assert _missing_renders([], space) == ["_"]
    assert _missing_renders([_int(0)], space) == ["_"]
    assert patterns.missing_patterns([_wildcard()], space) == []


def test_empty_space_is_exhaustive_with_zero_rows():
    space = _never_space()
    assert patterns.missing_patterns([], space) == []
    assert not patterns.is_useful([], _wildcard(), space)


def test_or_pattern_in_matrix_covers_each_alternative():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    matrix = [_or(_variant(0, "Red"), _variant(1, "Green"))]
    assert patterns.is_useful(matrix, _variant(2, "Blue"), space)
    assert not patterns.is_useful(matrix, _variant(1, "Green"), space)
    assert _missing_renders(matrix, space) == ["Blue"]


def test_or_pattern_query_is_useful_if_any_alternative_is():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    matrix = [_variant(0, "Red"), _variant(1, "Green")]
    query = _or(_variant(1, "Green"), _variant(2, "Blue"))
    assert patterns.is_useful(matrix, query, space)


def test_nested_or_pattern_rows_agree_with_exhaustiveness():
    space = _bool_space()
    nested = _or(_or(_bool(False)), _bool(True))
    assert patterns.missing_patterns([nested], space) == []
    assert not patterns.is_useful([nested], _wildcard(), space)


def test_redundant_row_detected_at_right_index():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    rows = [_variant(0, "Red"), _variant(1, "Green"), _variant(2, "Blue"), _wildcard()]
    assert patterns.is_useful(rows[:2], rows[2], space)
    assert not patterns.is_useful(rows[:3], rows[3], space)


def test_witness_rendering():
    assert patterns.Witness(_variant(5, "Color::Blue")).render() == "Color::Blue"
    assert patterns.Witness(_bool(True)).render() == "true"
    assert patterns.Witness(_bool(False)).render() == "false"
    assert patterns.Witness(_wildcard()).render() == "_"


def test_build_match_plan_wildcard_terminated_chain():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    rows = [_variant(0, "Red"), _variant(1, "Green"), _wildcard()]
    plan = patterns.build_match_plan(rows, space)
    assert plan.reachable_arms == (0, 1, 2)
    assert plan.missing == ()
    assert [test.arm_index for test in plan.tests] == [0, 1, 2]
    assert [test.constructors for test in plan.tests] == [
        (patterns.VariantConstructor(0, "Red"),),
        (patterns.VariantConstructor(1, "Green"),),
        (),
    ]


def test_build_match_plan_last_arm_covers_remainder():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    rows = [_or(_variant(0, "Red"), _variant(1, "Green")), _variant(2, "Blue")]
    plan = patterns.build_match_plan(rows, space)
    assert plan.reachable_arms == (0, 1)
    assert plan.missing == ()
    assert [test.arm_index for test in plan.tests] == [0, 1]
    assert plan.tests[0].constructors == (
        patterns.VariantConstructor(0, "Red"),
        patterns.VariantConstructor(1, "Green"),
    )
    assert plan.tests[1].constructors == ()


def test_build_match_plan_non_exhaustive_matrix():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    rows = [_variant(0, "Red"), _variant(1, "Green")]
    plan = patterns.build_match_plan(rows, space)
    assert plan.reachable_arms == (0, 1)
    assert [test.arm_index for test in plan.tests] == [0, 1]
    assert plan.tests[0].constructors == (patterns.VariantConstructor(0, "Red"),)
    assert plan.tests[1].constructors == (patterns.VariantConstructor(1, "Green"),)
    assert [witness.render() for witness in plan.missing] == ["Blue"]


def test_build_match_plan_unreachable_arm():
    space = _enum_space((0, "Red"), (1, "Green"), (2, "Blue"))
    rows = [_wildcard(), _variant(0, "Red")]
    plan = patterns.build_match_plan(rows, space)
    assert plan.reachable_arms == (0,)
    assert plan.missing == ()
    assert [test.arm_index for test in plan.tests] == [0]
    assert plan.tests[0].constructors == ()


def test_build_match_plan_or_pattern_with_wildcard_alternative():
    space = _enum_space((0, "Red"), (1, "Green"))
    rows = [_or(_variant(0, "Red"), _wildcard())]
    plan = patterns.build_match_plan(rows, space)
    assert plan.reachable_arms == (0,)
    assert plan.missing == ()
    assert [test.arm_index for test in plan.tests] == [0]
    assert plan.tests[0].constructors == ()


def test_build_match_plan_empty_arm_list():
    space = _never_space()
    plan = patterns.build_match_plan([], space)
    assert plan.reachable_arms == ()
    assert plan.tests == ()
    assert plan.missing == ()
