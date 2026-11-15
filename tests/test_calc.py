"""Tests for the pure calculation helpers (no Home Assistant needed)."""

import importlib.util
import pathlib
import sys

import pytest

_path = pathlib.Path(__file__).parent.parent / "custom_components" / "heat_pump_performance" / "calc.py"
_spec = importlib.util.spec_from_file_location("hpp_calc", _path)
calc = importlib.util.module_from_spec(_spec)
# dataclasses look the module up by name, so it must be registered first.
sys.modules["hpp_calc"] = calc
_spec.loader.exec_module(calc)


def test_power_heating():
    # 0.685 m3/h, 30 -> 25 degC => 0.685 * 1.163 * 5
    assert calc.thermal_power_kw(0.685, 30, 25) == pytest.approx(3.983, abs=1e-3)


def test_power_negative_during_defrost():
    assert calc.thermal_power_kw(0.685, 14, 22) < 0


def test_power_idle_flow_is_zero():
    assert calc.thermal_power_kw(0.0, 56, 23) == 0.0


def test_power_rejects_glitches():
    assert calc.thermal_power_kw(0.7, 150, 25) is None
    assert calc.thermal_power_kw(0.7, 60, 10, max_abs_delta_t=25) is None
    assert calc.thermal_power_kw(None, 30, 25) is None


def test_flow_units():
    assert calc.flow_to_m3h(10, "l/min") == pytest.approx(0.6)
    assert calc.flow_to_m3h(685, "l/h") == pytest.approx(0.685)


def test_split_trapezoid_positive_only():
    heat, loss = calc.split_trapezoid(2.0, 4.0, 1.0)
    assert heat == pytest.approx(3.0) and loss == 0.0


def test_split_trapezoid_negative_only():
    heat, loss = calc.split_trapezoid(-2.0, -4.0, 1.0)
    assert heat == 0.0 and loss == pytest.approx(3.0)


def test_split_trapezoid_zero_crossing_symmetric():
    heat, loss = calc.split_trapezoid(2.0, -2.0, 1.0)
    assert heat == pytest.approx(0.5) and loss == pytest.approx(0.5)
    heat, loss = calc.split_trapezoid(-2.0, 2.0, 1.0)
    assert heat == pytest.approx(0.5) and loss == pytest.approx(0.5)


def test_integrator_books_and_skips_gaps():
    integ = calc.ThermalIntegrator(max_gap_s=300)
    assert integ.step(0, 3.6) == (0.0, 0.0)  # first sample books nothing
    heat, loss = integ.step(3600 / 10, 3.6)  # 6 min > max gap -> skipped
    assert (heat, loss) == (0.0, 0.0)
    assert integ.gap_seconds_total == pytest.approx(360)
    heat, loss = integ.step(360 + 100, 3.6)  # 100 s at 3.6 kW = 0.1 kWh
    assert heat == pytest.approx(0.1) and loss == 0.0


def test_integrator_invalid_sample_breaks_chain():
    integ = calc.ThermalIntegrator()
    integ.step(0, 2.0)
    assert integ.step(10, None) == (0.0, 0.0)
    assert integ.step(20, 2.0) == (0.0, 0.0)  # previous was invalid


def test_defrost_detector_counts_sustained_negative_only():
    det = calc.DefrostDetector(threshold_k=0.5, min_duration_s=20)
    assert not det.update(0, -1.0)
    assert not det.update(10, -1.0)
    assert det.update(25, -1.0)  # confirmed after 20+ s
    assert det.active and det.count == 1
    det.update(40, 1.0)  # back to heating
    assert not det.active and det.duration_s_total == pytest.approx(40 - 0)
    # short dip: no cycle
    det.update(100, -1.0)
    det.update(105, 1.0)
    assert det.count == 1


def test_sample_status_codes():
    ev = calc.evaluate_thermal_sample
    lim = calc.SampleLimits(max_flow_m3h=3.0, max_power_kw=14.4)
    assert ev(0.7, 30, 25, lim) == (pytest.approx(4.07, abs=0.01), calc.STATUS_OK)
    assert ev(0.0, 56, 23, lim) == (0.0, calc.STATUS_IDLE)
    assert ev(None, 30, 25, lim) == (None, calc.STATUS_MISSING)
    assert ev(-0.1, 30, 25, lim) == (None, calc.STATUS_FLOW_NEGATIVE)
    assert ev(0.7, 150, 25, lim) == (None, calc.STATUS_TEMP_RANGE)
    assert ev(9.0, 30, 25, lim) == (None, calc.STATUS_FLOW_HIGH)
    assert ev(0.7, 60, 20, lim) == (None, calc.STATUS_DELTA_T)
    # 2.9 m3/h * 1.163 * 5 K = 16.9 kW > 14.4 kW limit
    assert ev(2.9, 30, 25, lim) == (None, calc.STATUS_POWER_HIGH)


def test_energy_increment_check():
    chk = calc.check_energy_increment
    assert chk(1.0, 0.01, 12.0, 1.2, 1.0)[0] == calc.STATUS_OK
    assert chk(-1.0, 0.1, 12.0, 1.2, 1.0)[0] == calc.STATUS_COUNTER_DECREASE
    status, allowed = chk(755_673_093.0, 0.5, 12.0, 1.2, 1.0)
    assert status == calc.STATUS_EXCESSIVE and allowed == pytest.approx(8.2)


def test_meter_against_calc():
    chk = calc.check_meter_against_calc
    # calc saw 0.2 kWh, meter jumps by 5 kWh -> mismatch
    assert chk(5.0, 0.2, 1.0, True)[0] == calc.STATUS_CALC_MISMATCH
    # normal 1 kWh quantisation step while calc saw 0.9 kWh
    assert chk(1.0, 0.9, 1.0, True)[0] == calc.STATUS_OK
    # no check when the calc window had gaps
    assert chk(5.0, 0.2, 1.0, False) == (calc.STATUS_OK, None)
    # allowance grows with the window, so the meter is accepted eventually
    assert chk(5.0, 4.0, 1.0, True)[0] == calc.STATUS_OK


def test_cross_checks():
    assert calc.meter_deviation_pct(100.0, 5.0) is None  # too little energy
    assert calc.meter_deviation_pct(110.0, 100.0) == pytest.approx(10.0)
    assert calc.defrost_share_pct(100.0, 6.0) == pytest.approx(6.0)
    assert calc.cop_status(10.0, 1.0) == "insufficient_data"
    assert calc.cop_status(50.0, 10.0) == calc.STATUS_OK
    assert calc.cop_status(100.0, 10.0) == "too_high"
    assert calc.cop_status(5.0, 10.0) == "too_low"


def test_stats_roundtrip_and_counting():
    stats = calc.PlausibilityStats()
    stats.record_ok(1.0)
    stats.record_reject(2.0, calc.STATUS_DELTA_T, {"delta_t": 33.0})
    stats.record_reject(3.0, calc.STATUS_DELTA_T)
    assert stats.rejected == {calc.STATUS_DELTA_T: 2}
    assert stats.consecutive_rejects == 2 and stats.rejected_total == 2
    stats.record_ok(4.0)
    assert stats.consecutive_rejects == 0
    again = calc.PlausibilityStats.from_dict(stats.to_dict())
    assert again.rejected == stats.rejected and again.accepted == 2
    assert calc.PlausibilityStats.from_dict(None).accepted == 0


def test_health_assessment():
    base = dict(
        consecutive_rejects=0,
        input_issues={},
        meter_deviation=None,
        defrost_share=None,
        cop_gross_status=None,
    )
    assert calc.assess_health(configured=False, **base) == ("inactive", [])
    assert calc.assess_health(configured=True, **base) == ("ok", [])
    state, warns = calc.assess_health(
        configured=True, **{**base, "input_issues": {"flow": "stale"}}
    )
    assert state == "warning" and warns == ["stale_input:flow"]
    state, _ = calc.assess_health(
        configured=True, **{**base, "input_issues": {"flow": "missing"}}
    )
    assert state == "error"
    state, _ = calc.assess_health(configured=True, **{**base, "consecutive_rejects": 25})
    assert state == "error"
    state, warns = calc.assess_health(
        configured=True, **{**base, "meter_deviation": 14.0, "defrost_share": 30.0, "cop_gross_status": "too_high"}
    )
    assert state == "warning" and len(warns) == 3


def test_integrator_remembers_gaps_for_cross_checks():
    integ = calc.ThermalIntegrator(max_gap_s=300)
    integ.step(0, 1.0)
    integ.step(10, 1.0)
    assert integ.last_gap_ts is None
    integ.step(20, None)  # rejected sample
    assert integ.last_gap_ts == 20
    integ2 = calc.ThermalIntegrator(max_gap_s=300)
    integ2.step(0, 1.0)
    integ2.step(1000, 1.0)  # long gap
    assert integ2.last_gap_ts == 1000


def test_reference_cop_interpolation_and_clamp():
    pts = calc.LWZ303_REFERENCE_COP
    assert calc.reference_cop(-7, pts) == pytest.approx(2.7)
    assert calc.reference_cop(2, pts) == pytest.approx(3.3)
    assert calc.reference_cop(-2.5, pts) == pytest.approx(3.0)
    assert calc.reference_cop(-20, pts) == pytest.approx(2.7)
    assert calc.reference_cop(10, pts) == pytest.approx(3.3)


# --- Defrost flag cross check -----------------------------------------------
def _tracker():
    return calc.DefrostFlagTracker()


def test_defrost_flag_confirmed_by_negative_power():
    t = _tracker()
    t.flag_changed(0, True)
    t.delta_sample(30, -1.5, False)
    t.flag_changed(200, False)
    assert (t.flag_cycles, t.confirmed, t.unconfirmed) == (1, 1, 0)
    assert t.flag_duration_s == 200


def test_defrost_flag_without_negative_power_is_unconfirmed():
    t = _tracker()
    t.flag_changed(0, True)
    t.delta_sample(30, 2.0, False)
    t.flag_changed(200, False)
    assert (t.confirmed, t.unconfirmed) == (0, 1)


def test_defrost_flag_late_reaction_within_grace_confirms():
    t = _tracker()
    t.flag_changed(0, True)
    t.flag_changed(100, False)
    assert t.unconfirmed == 1
    t.delta_sample(150, -2.0, False)
    assert (t.confirmed, t.unconfirmed) == (1, 0)


def test_detected_cycle_without_flag_is_counted_outside_grace_only():
    t = _tracker()
    t.delta_sample(1000, -2.0, True)
    assert t.detected_without_flag == 1
    t.flag_changed(2000, True)
    t.flag_changed(2100, False)
    t.delta_sample(2150, -2.0, True)  # inside the grace period after the flag
    assert t.detected_without_flag == 1


def test_defrost_flag_seed_does_not_count_a_cycle_and_warnings():
    t = _tracker()
    t.seed(0, True)
    assert t.flag_cycles == 0 and t.flag_on
    t = _tracker()
    t.flag_cycles = 6
    t.unconfirmed = 5
    assert t.warnings() and t.warnings()[0].startswith("defrost_flag_without_negative_power")
    t = _tracker()
    t.flag_cycles = 1
    t.detected_without_flag = 5
    assert t.warnings() and t.warnings()[0].startswith("negative_power_without_defrost_flag")


def test_defrost_flag_persistence_roundtrip():
    t = _tracker()
    t.flag_cycles, t.confirmed, t.unconfirmed, t.detected_without_flag = 4, 3, 1, 2
    t.flag_duration_s = 640.0
    u = _tracker()
    u.restore(t.to_dict())
    assert u.to_dict()["cycles"] == 4 and u.confirmed == 3 and u.detected_without_flag == 2
    u.reset()
    assert u.flag_cycles == 0 and u.unconfirmed == 0
