# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import compilation


def test_ctx_detect_cycle_reports_cycle_details() -> None:
    ctx = compilation.Ctx()
    identity = object()

    with (
        pytest.raises(RuntimeError, match="translated cycle"),
        ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "first"
        ) as outer_cycle,
    ):
        assert outer_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "second"
        ) as cycle:
            assert cycle is not None
            assert cycle.details == ("first", "second")
            raise RuntimeError("translated cycle")


def test_ctx_detect_cycle_reports_only_the_cycle_suffix() -> None:
    ctx = compilation.Ctx()

    with (
        pytest.raises(RuntimeError, match="translated cycle"),
        ctx.detect_cycle(compilation.CycleDomain.MOD_VAR_INITIALIZER, 1, "before") as first_cycle,
    ):
        assert first_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, 2, "first"
        ) as second_cycle:
            assert second_cycle is None
            with ctx.detect_cycle(
                compilation.CycleDomain.MOD_VAR_INITIALIZER, 3, "second"
            ) as third_cycle:
                assert third_cycle is None
                with ctx.detect_cycle(
                    compilation.CycleDomain.MOD_VAR_INITIALIZER, 2, "closing"
                ) as cycle:
                    assert cycle is not None
                    assert cycle.details == ("first", "second", "closing")
                    raise RuntimeError("translated cycle")


def test_ctx_detect_cycle_isolates_domains_and_their_details() -> None:
    ctx = compilation.Ctx()
    identity = object()

    with (
        pytest.raises(RuntimeError, match="translated cycle"),
        ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "first"
        ) as outer_cycle,
    ):
        assert outer_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.STRUCT_LAYOUT, identity, "other"
        ) as other_cycle:
            assert other_cycle is None
            with ctx.detect_cycle(
                compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "closing"
            ) as cycle:
                assert cycle is not None
                assert cycle.details == ("first", "closing")
                raise RuntimeError("translated cycle")


def test_ctx_detect_cycle_requires_reported_cycle_to_be_raised() -> None:
    ctx = compilation.Ctx()
    identity = object()

    with (
        pytest.raises(AssertionError, match="cycle was not translated"),
        ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "first"
        ) as outer_cycle,
    ):
        assert outer_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "closing"
        ) as cycle:
            assert cycle is not None


def test_ctx_detect_cycle_can_use_domain_specific_identity_comparison() -> None:
    ctx = compilation.Ctx()

    with (
        pytest.raises(RuntimeError, match="translated cycle"),
        ctx.detect_cycle(compilation.CycleDomain.STRUCT_LAYOUT, 2, "first") as outer_cycle,
    ):
        assert outer_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.STRUCT_LAYOUT,
            4,
            "second",
            same_identity=lambda earlier, current: current % earlier == 0,
        ) as cycle:
            assert cycle is not None
            assert cycle.details == ("first", "second")
            raise RuntimeError("translated cycle")


def test_ctx_detect_cycle_cleans_up_after_unrelated_exception() -> None:
    ctx = compilation.Ctx()
    identity = object()

    with (
        pytest.raises(RuntimeError, match="unrelated"),
        ctx.detect_cycle(compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "first") as cycle,
    ):
        assert cycle is None
        raise RuntimeError("unrelated")

    with ctx.detect_cycle(compilation.CycleDomain.MOD_VAR_INITIALIZER, identity, "second") as cycle:
        assert cycle is None
