# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Callable

import pytest

from leech import asserts, compilation, diag, diag_kinds, ir_env, ir_module, opt_util, typs


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
            compilation.CycleDomain.TYPE_LAYOUT, identity, "other"
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
        ctx.detect_cycle(compilation.CycleDomain.TYPE_LAYOUT, 2, "first") as outer_cycle,
    ):
        assert outer_cycle is None
        with ctx.detect_cycle(
            compilation.CycleDomain.TYPE_LAYOUT,
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


class _Owner:
    """An analysis unit's owner whose computation is supplied by each test."""

    def __init__(self, ctx: compilation.Ctx, compute: Callable[[], int]) -> None:
        self._ctx = ctx
        self._compute = compute
        self.calls = 0

    @property
    def ctx(self) -> compilation.Ctx:
        return self._ctx

    @property
    @compilation.unit
    def value(self) -> int:
        self.calls += 1
        return self._compute()


def test_unit_computes_once():
    owner = _Owner(compilation.Ctx(), lambda: 7)

    assert owner.value == 7
    assert owner.value == 7
    assert owner.calls == 1


def test_unit_reports_a_failure_once_and_memoizes_it():
    ctx = compilation.Ctx()

    def fail() -> int:
        ctx.diags.raise_error(diag_kinds.MISSING_C_COMPILER, None, program="cc")

    owner = _Owner(ctx, fail)

    proofs = []
    for _ in range(2):
        with pytest.raises(diag.ReportedError) as exc_info:
            _ = owner.value
        proofs.append(exc_info.value.reported)

    assert proofs[0] is proofs[1]
    assert owner.calls == 1
    assert [d.kind for d in ctx.diags.all()] == [diag_kinds.MISSING_C_COMPILER]


def test_unit_does_not_report_an_already_reported_error_again():
    ctx = compilation.Ctx()

    def fail() -> int:
        ctx.diags.raise_error(diag_kinds.MISSING_C_COMPILER, None, program="cc")

    inner = _Owner(ctx, fail)
    outer = _Owner(ctx, lambda: inner.value)

    with pytest.raises(diag.ReportedError) as exc_info:
        _ = outer.value

    assert exc_info.value.reported is ctx.diags.any_error()
    assert len(ctx.diags.all()) == 1


def test_unit_lets_internal_errors_propagate_uncached():
    def crash() -> int:
        raise RuntimeError("bug")

    owner = _Owner(compilation.Ctx(), crash)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            _ = owner.value
    assert owner.calls == 2


def test_unit_tracks_the_units_being_computed():
    ctx = compilation.Ctx()
    seen: list[list[compilation.UnitId]] = []

    def record() -> int:
        seen.append(list(ctx.unit_stack))
        return 0

    owner = _Owner(ctx, record)
    _ = owner.value

    assert seen[0] == [compilation.UnitId(owner, "value")]
    assert ctx.unit_stack == []


def test_unit_results_belong_to_one_compilation():
    first = compilation.Ctx()
    second = compilation.Ctx()

    def fail() -> int:
        first.diags.raise_error(diag_kinds.MISSING_C_COMPILER, None, program="cc")

    owner = _Owner(first, fail)
    with pytest.raises(diag.ReportedError):
        _ = owner.value

    assert second.unit(owner, "value", lambda: 7) == 7
    assert len(second.diags.all()) == 0


def test_recovering_keeps_the_proof_of_a_reported_error():
    ctx = compilation.Ctx()
    reached_end = False

    with ctx.recovering() as first:
        ctx.diags.raise_error(diag_kinds.MISSING_C_COMPILER, None, program="cc")
    reported = opt_util.opt_unwrap(first.failure)
    with ctx.recovering() as again:
        raise diag.ReportedError(reported)
    with ctx.recovering() as clean:
        reached_end = True

    assert again.failure is reported
    assert clean.failure is None
    assert reached_end
    assert len(ctx.diags.all()) == 1


def test_recovering_lets_internal_errors_propagate():
    with pytest.raises(RuntimeError), compilation.Ctx().recovering():
        raise RuntimeError("bug")


def test_every_kind_of_unit_owner_reaches_its_compilation(compiler):
    src = """
    struct S { x: i32 }
    union U { A, B(i32) }
    enum E { X }
    pub let v: i32 = 1;
    pub fn f() i32 { return 0; }
    """
    mod = compiler.build(src)
    ctx = mod.ctx

    def value(name: str, ns: ir_env.Env.Namespace = ir_env.Env.Namespace.CONTAINERS):
        item = mod.get_item(ns, name)
        assert item is not None
        return item.value

    struct = asserts.checked_cast(value("S"), typs.StructTyp)
    union = asserts.checked_cast(value("U"), typs.UnionTyp)
    fn = asserts.checked_cast(value("f", ir_env.Env.Namespace.VARS), ir_module.SrcFnSymbol)
    owners = [
        struct,
        struct.fields["x"],
        union,
        union.variants[1],
        union.variants[1].template,
        asserts.checked_cast(value("E"), typs.EnumTyp),
        asserts.checked_cast(value("v", ir_env.Env.Namespace.VARS), ir_module.ModVar),
        fn,
        fn.instantiate(()),
    ]
    assert all(owner.ctx is ctx for owner in owners)


def test_unit_ids_compare_owners_by_identity():
    first = [1]
    equal = [1]

    assert compilation.UnitId(first, "x") == compilation.UnitId(first, "x")
    assert hash(compilation.UnitId(first, "x")) == hash(compilation.UnitId(first, "x"))
    assert compilation.UnitId(first, "x") != compilation.UnitId(equal, "x")
    assert compilation.UnitId(first, "x") != compilation.UnitId(first, "y")


def test_unit_result_captures_a_value():
    result = compilation.UnitResult.capture(compilation.Ctx(), lambda: 7)

    assert result.get() == 7
    assert result.failure is None


def test_unit_result_passes_on_an_already_reported_error():
    ctx = compilation.Ctx()
    reported = ctx.diags.error(diag_kinds.MISSING_C_COMPILER, None, program="cc")

    def fail() -> int:
        raise diag.ReportedError(reported)

    assert compilation.UnitResult.capture(ctx, fail).failure is reported
    assert len(ctx.diags.all()) == 1


def test_unit_result_lets_internal_errors_propagate():
    def crash() -> int:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        compilation.UnitResult.capture(compilation.Ctx(), crash)


def test_fn_instances_are_cached_and_requested_once(compiler):
    mod = compiler.build("pub fn f[T](x: T) T { return x; }")
    fn = asserts.checked_cast(
        opt_util.opt_unwrap(mod.get_item(ir_env.Env.Namespace.VARS, "f")).value,
        ir_module.SrcFnSymbol,
    )

    instance = fn.instantiate((typs.I32,))

    assert fn.instantiate((typs.I32,)) is instance
    assert fn.instantiate((typs.BOOL,)) is not instance
    assert list(mod.ctx.requested_fn_instances()).count(instance) == 1


def test_unit_that_reaches_itself_is_an_internal_error():
    owner: _Owner

    def reenter() -> int:
        return owner.value

    owner = _Owner(compilation.Ctx(), reenter)

    with pytest.raises(AssertionError, match="value re-entered itself"):
        _ = owner.value
