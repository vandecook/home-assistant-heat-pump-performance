"""Pure calculation and plausibility helpers for Heat Pump Performance.

This module intentionally has no Home Assistant imports so it can be unit tested
without a running instance.

Physics: thermal power of a water circuit is

    P [kW] = flow [m3/h] * 1.163 [kWh/(m3*K)] * (supply - return) [K]

The result is *signed*. During a defrost cycle a reversible heat pump takes heat
out of the heating water, so supply drops below return and the power becomes
negative. That energy is real and must be booked as loss instead of being
clipped to zero, otherwise the heat delivered (and therefore the COP) is
overstated.

Plausibility: every sample and every energy increment gets a status code. Only
``ok`` (and ``idle_flow``, a legitimate zero) is booked; everything else is
counted per reason so the diagnostic entity can show what was discarded and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 4.186 kJ/(kg*K) * 1000 kg/m3 / 3600 s/h
WATER_KWH_PER_M3_K = 1.163

FLOW_TO_M3H = {
    "m³/h": 1.0,
    "l/min": 0.06,
    "l/h": 0.001,
}

MIN_VALID_TEMP_C = -30.0
MAX_VALID_TEMP_C = 100.0

# --- Status codes -----------------------------------------------------------
STATUS_OK = "ok"
STATUS_IDLE = "idle_flow"  # legitimate: pump standing still, power is 0
STATUS_MISSING = "missing_input"
STATUS_STALE = "stale_input"
STATUS_TEMP_RANGE = "temp_out_of_range"
STATUS_FLOW_NEGATIVE = "flow_negative"
STATUS_FLOW_HIGH = "flow_too_high"
STATUS_DELTA_T = "delta_t_too_large"
STATUS_POWER_HIGH = "power_too_high"

STATUS_COUNTER_DECREASE = "counter_decrease"
STATUS_EXCESSIVE = "excessive_energy_delta"
STATUS_CALC_MISMATCH = "calc_mismatch"

# --- Thresholds for the health assessment -----------------------------------
MAX_INPUT_AGE_S = 600.0
CONSECUTIVE_REJECT_ERROR = 20
METER_DEVIATION_WARN_PCT = 10.0
METER_DEVIATION_MIN_KWH = 10.0
DEFROST_SHARE_WARN_PCT = 25.0
COP_MIN_PLAUSIBLE = 0.8
COP_MAX_PLAUSIBLE = 8.0
COP_MIN_ENERGY_KWH = 2.0
DEFROST_FLAG_GRACE_S = 120.0
DEFROST_FLAG_MIN_CYCLES = 5
DEFROST_FLAG_MISMATCH_WARN_PCT = 50.0


def flow_to_m3h(value: float, unit: str) -> float:
    """Convert a flow reading to m3/h."""
    return value * FLOW_TO_M3H.get(unit, 1.0)


# --- Sample plausibility ----------------------------------------------------
@dataclass(frozen=True)
class SampleLimits:
    """Limits for a single flow/supply/return sample."""

    min_flow_m3h: float = 0.1
    max_flow_m3h: float = 3.0
    max_abs_delta_t: float = 25.0
    # Absolute thermal power limit in kW (already including any safety factor).
    max_power_kw: float | None = None


def evaluate_thermal_sample(
    flow_m3h: float | None,
    supply_c: float | None,
    return_c: float | None,
    limits: SampleLimits = SampleLimits(),
) -> tuple[float | None, str]:
    """Return ``(power_kw, status)`` for one sample.

    ``power_kw`` is None for every rejected sample (nothing is booked) and 0.0
    for ``idle_flow``. Order matters: the idle-flow check comes before the
    delta-T limit, because with a standing pump the sensors show stagnant,
    very different temperatures that are not a glitch.
    """
    if flow_m3h is None or supply_c is None or return_c is None:
        return None, STATUS_MISSING
    if flow_m3h < 0:
        return None, STATUS_FLOW_NEGATIVE
    for temp in (supply_c, return_c):
        if not MIN_VALID_TEMP_C <= temp <= MAX_VALID_TEMP_C:
            return None, STATUS_TEMP_RANGE
    if flow_m3h > limits.max_flow_m3h:
        return None, STATUS_FLOW_HIGH
    if flow_m3h < limits.min_flow_m3h:
        return 0.0, STATUS_IDLE
    delta_t = supply_c - return_c
    if abs(delta_t) > limits.max_abs_delta_t:
        return None, STATUS_DELTA_T
    power = flow_m3h * WATER_KWH_PER_M3_K * delta_t
    if limits.max_power_kw is not None and abs(power) > limits.max_power_kw:
        return None, STATUS_POWER_HIGH
    return power, STATUS_OK


def thermal_power_kw(
    flow_m3h: float | None,
    supply_c: float | None,
    return_c: float | None,
    *,
    min_flow_m3h: float = 0.1,
    max_abs_delta_t: float = 25.0,
) -> float | None:
    """Convenience wrapper returning only the power (None if rejected)."""
    power, _ = evaluate_thermal_sample(
        flow_m3h,
        supply_c,
        return_c,
        SampleLimits(min_flow_m3h=min_flow_m3h, max_abs_delta_t=max_abs_delta_t),
    )
    return power


# --- Energy-increment plausibility ------------------------------------------
def check_energy_increment(
    delta_kwh: float,
    elapsed_h: float,
    max_power_kw: float,
    safety_factor: float,
    tolerance_kwh: float,
) -> tuple[str, float]:
    """Check a counter increment against what the hardware can deliver.

    Returns ``(status, allowed_delta_kwh)``.
    """
    allowed = max_power_kw * safety_factor * max(elapsed_h, 0.0) + tolerance_kwh
    if delta_kwh < -1e-7:
        return STATUS_COUNTER_DECREASE, allowed
    if delta_kwh > allowed + 1e-7:
        return STATUS_EXCESSIVE, allowed
    return STATUS_OK, allowed


def check_meter_against_calc(
    delta_kwh: float,
    calc_heat_since_kwh: float,
    tolerance_kwh: float,
    window_valid: bool,
) -> tuple[str, float | None]:
    """Cross-check a heat-meter increment against the self-calculated heat.

    ``calc_heat_since_kwh`` is the calculated heat accumulated since the last
    accepted meter reading. The check only applies when that window had no data
    gaps (``window_valid``). If the calculation under-measures, the allowance
    keeps growing with the window, so a legitimate meter value is eventually
    accepted again.
    """
    if not window_valid:
        return STATUS_OK, None
    allowed = calc_heat_since_kwh * 1.5 + tolerance_kwh + 0.5
    if delta_kwh > allowed:
        return STATUS_CALC_MISMATCH, allowed
    return STATUS_OK, allowed


# --- Cross checks -----------------------------------------------------------
def meter_deviation_pct(meter_kwh: float, calc_heat_kwh: float) -> float | None:
    """Deviation of the meter from the calculated heat in percent.

    None while there is too little energy to say anything meaningful.
    """
    if calc_heat_kwh < METER_DEVIATION_MIN_KWH:
        return None
    return (meter_kwh - calc_heat_kwh) / calc_heat_kwh * 100.0


def defrost_share_pct(heat_kwh: float, loss_kwh: float) -> float | None:
    """Share of defrost loss relative to delivered heat, in percent."""
    if heat_kwh < METER_DEVIATION_MIN_KWH:
        return None
    return loss_kwh / heat_kwh * 100.0


def cop_status(thermal_kwh: float, electrical_kwh: float) -> str:
    """Classify a period COP as ok / too_low / too_high / insufficient_data."""
    if electrical_kwh < COP_MIN_ENERGY_KWH:
        return "insufficient_data"
    cop = thermal_kwh / electrical_kwh
    if cop > COP_MAX_PLAUSIBLE:
        return "too_high"
    if cop < COP_MIN_PLAUSIBLE:
        return "too_low"
    return STATUS_OK


# --- Statistics and health --------------------------------------------------
class PlausibilityStats:
    """Count accepted and rejected samples/increments per reason."""

    def __init__(self) -> None:
        self.accepted = 0
        self.rejected: dict[str, int] = {}
        self.consecutive_rejects = 0
        self.last_rejected: dict[str, Any] = {}
        self.last_accepted_ts: float | None = None

    def record_ok(self, ts: float) -> None:
        self.accepted += 1
        self.consecutive_rejects = 0
        self.last_accepted_ts = ts

    def record_reject(self, ts: float, reason: str, details: dict[str, Any] | None = None) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1
        self.consecutive_rejects += 1
        self.last_rejected = {"reason": reason, "ts": ts, **(details or {})}

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "rejected": dict(self.rejected),
            "consecutive_rejects": self.consecutive_rejects,
            "last_rejected": dict(self.last_rejected),
            "last_accepted_ts": self.last_accepted_ts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlausibilityStats":
        stats = cls()
        if not data:
            return stats
        stats.accepted = int(data.get("accepted", 0))
        stats.rejected = {str(k): int(v) for k, v in (data.get("rejected") or {}).items()}
        stats.consecutive_rejects = int(data.get("consecutive_rejects", 0))
        stats.last_rejected = dict(data.get("last_rejected") or {})
        stats.last_accepted_ts = data.get("last_accepted_ts")
        return stats


def assess_health(
    *,
    configured: bool,
    consecutive_rejects: int,
    input_issues: dict[str, str],
    meter_deviation: float | None,
    defrost_share: float | None,
    cop_gross_status: str | None,
    extra_warnings: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Derive ``(state, warnings)`` with state in inactive/ok/warning/error."""
    if not configured:
        return "inactive", []
    warnings: list[str] = []
    error = consecutive_rejects >= CONSECUTIVE_REJECT_ERROR
    if error:
        warnings.append(f"consecutive_rejects:{consecutive_rejects}")
    for role, issue in sorted(input_issues.items()):
        warnings.append(f"{issue}_input:{role}")
        if issue == "missing":
            error = True
    if meter_deviation is not None and abs(meter_deviation) > METER_DEVIATION_WARN_PCT:
        warnings.append(f"meter_deviation:{meter_deviation:.1f}%")
    if defrost_share is not None and defrost_share > DEFROST_SHARE_WARN_PCT:
        warnings.append(f"defrost_share_high:{defrost_share:.1f}%")
    if cop_gross_status in ("too_high", "too_low"):
        warnings.append(f"cop_{cop_gross_status}")
    warnings.extend(extra_warnings or [])
    if error:
        return "error", warnings
    return ("warning" if warnings else "ok"), warnings


# --- Integration ------------------------------------------------------------
def split_trapezoid(p0_kw: float, p1_kw: float, dt_h: float) -> tuple[float, float]:
    """Integrate a linear power ramp, returning ``(heat_kwh, loss_kwh)``.

    ``heat_kwh`` is the positive part, ``loss_kwh`` the magnitude of the negative
    part. A zero crossing inside the interval is split exactly, so a sample pair
    like +2 kW / -2 kW books equal heat and loss.
    """
    if dt_h <= 0:
        return 0.0, 0.0
    if p0_kw >= 0 and p1_kw >= 0:
        return (p0_kw + p1_kw) / 2.0 * dt_h, 0.0
    if p0_kw <= 0 and p1_kw <= 0:
        return 0.0, -(p0_kw + p1_kw) / 2.0 * dt_h
    # Sign change: the ramp crosses zero after fraction f of the interval.
    f = abs(p0_kw) / (abs(p0_kw) + abs(p1_kw))
    if p0_kw > 0:
        heat = p0_kw * f * dt_h / 2.0
        loss = -p1_kw * (1.0 - f) * dt_h / 2.0
    else:
        loss = -p0_kw * f * dt_h / 2.0
        heat = p1_kw * (1.0 - f) * dt_h / 2.0
    return heat, loss


class ThermalIntegrator:
    """Integrate signed thermal power samples into heat and loss energy."""

    def __init__(self, max_gap_s: float = 300.0) -> None:
        self.max_gap_s = max_gap_s
        self.last_ts: float | None = None
        self.last_power_kw: float | None = None
        self.gap_seconds_total: float = 0.0
        self.last_gap_ts: float | None = None

    def reset(self) -> None:
        self.last_ts = None
        self.last_power_kw = None

    def step(self, ts: float, power_kw: float | None) -> tuple[float, float]:
        """Add a sample at unix time ``ts``; return ``(heat_kwh, loss_kwh)``.

        The interval since the previous sample is only booked when both ends are
        valid and the gap is not longer than ``max_gap_s``. Longer gaps (restart,
        unavailable sensor) are skipped rather than extrapolated and remembered
        in ``last_gap_ts`` so cross-checks know the window is incomplete.
        """
        heat = loss = 0.0
        prev_ts, prev_p = self.last_ts, self.last_power_kw
        if prev_ts is not None:
            gap = ts - prev_ts
            if gap > self.max_gap_s:
                self.gap_seconds_total += gap
                self.last_gap_ts = ts
            elif prev_p is not None and power_kw is not None and gap > 0:
                heat, loss = split_trapezoid(prev_p, power_kw, gap / 3600.0)
            elif prev_p is None or power_kw is None:
                # A rejected sample breaks the chain: treat as a (short) gap.
                self.last_gap_ts = ts
        self.last_ts = ts
        self.last_power_kw = power_kw
        return heat, loss


class DefrostDetector:
    """Detect defrost cycles from sustained negative thermal power.

    The criterion is the temperature difference ``supply - return`` in kelvin: a
    cycle starts when it stays below ``-threshold_k`` for at least
    ``min_duration_s`` and ends once it is back at zero or above. Short dips caused
    by sensor noise do not count. The caller passes ``None`` for invalid samples
    and ``0.0`` for an idle flow (which therefore ends a cycle).
    """

    def __init__(self, threshold_k: float = 0.5, min_duration_s: float = 60.0) -> None:
        self.threshold_k = threshold_k
        self.min_duration_s = min_duration_s
        self.active = False
        self.count = 0
        self.duration_s_total = 0.0
        self._below_since: float | None = None
        self._cycle_started: float | None = None

    def update(self, ts: float, delta_t_k: float | None) -> bool:
        """Feed a sample; return True when a new cycle was just confirmed."""
        if delta_t_k is None:
            return False
        started = False
        if delta_t_k <= -self.threshold_k:
            if self._below_since is None:
                self._below_since = ts
            if not self.active and ts - self._below_since >= self.min_duration_s:
                self.active = True
                self._cycle_started = self._below_since
                self.count += 1
                started = True
        elif delta_t_k >= 0:
            if self.active and self._cycle_started is not None:
                self.duration_s_total += max(0.0, ts - self._cycle_started)
            self.active = False
            self._below_since = None
            self._cycle_started = None
        return started


class DefrostFlagTracker:
    """Compare an optional "defrost active" signal of the heat pump with the
    defrost detected from negative thermal power.

    The flag never changes how energy is booked (that always follows the sign of
    the power); it is a cross check. Per flagged cycle it is noted whether the
    water temperatures showed the defrost (power below the threshold while the
    flag was on, or shortly after it went off). Detected cycles that start without
    the flag are counted as well.
    """

    def __init__(self, threshold_k: float = 0.5, grace_s: float = DEFROST_FLAG_GRACE_S) -> None:
        self.threshold_k = threshold_k
        self.grace_s = grace_s
        self.flag_on = False
        self.flag_cycles = 0
        self.flag_duration_s = 0.0
        self.confirmed = 0
        self.unconfirmed = 0
        self.detected_without_flag = 0
        self._cycle_start: float | None = None
        self._seen_negative = False
        self._last_off_ts: float | None = None
        self._last_unconfirmed = False
        self._pending_detected: float | None = None

    def seed(self, ts: float, on: bool) -> None:
        """Take over the current flag state at startup without counting a cycle."""
        self.flag_on = on
        self._cycle_start = ts if on else None
        self._seen_negative = False

    def flag_changed(self, ts: float, on: bool) -> None:
        if on and not self.flag_on:
            if self._pending_detected is not None and ts - self._pending_detected <= self.grace_s:
                # The flag arrived a little after the temperatures showed the defrost.
                self._pending_detected = None
                late = True
            else:
                late = False
            self.flag_on = True
            self.flag_cycles += 1
            self._cycle_start = ts
            self._seen_negative = late
        elif not on and self.flag_on:
            self.flag_on = False
            if self._cycle_start is not None:
                self.flag_duration_s += max(0.0, ts - self._cycle_start)
            if self._seen_negative:
                self.confirmed += 1
                self._last_unconfirmed = False
            else:
                self.unconfirmed += 1
                self._last_unconfirmed = True
            self._cycle_start = None
            self._last_off_ts = ts

    def delta_sample(self, ts: float, delta_t_k: float | None, cycle_started: bool) -> None:
        negative = delta_t_k is not None and delta_t_k <= -self.threshold_k
        if (
            self._pending_detected is not None
            and not self.flag_on
            and ts - self._pending_detected > self.grace_s
        ):
            # No flag followed within the tolerance: now it counts as without flag.
            self.detected_without_flag += 1
            self._pending_detected = None
        if self.flag_on:
            if negative:
                self._seen_negative = True
            return
        in_grace = self._last_off_ts is not None and ts - self._last_off_ts <= self.grace_s
        if negative and in_grace and self._last_unconfirmed:
            # The temperatures reacted a little after the flag went off.
            self.unconfirmed = max(0, self.unconfirmed - 1)
            self.confirmed += 1
            self._last_unconfirmed = False
        if cycle_started and not in_grace and self._pending_detected is None:
            self._pending_detected = ts

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.flag_cycles >= DEFROST_FLAG_MIN_CYCLES:
            share = self.unconfirmed / self.flag_cycles * 100.0
            if share > DEFROST_FLAG_MISMATCH_WARN_PCT:
                out.append(f"defrost_flag_without_negative_power:{share:.0f}%")
        total = self.flag_cycles + self.detected_without_flag
        if total >= DEFROST_FLAG_MIN_CYCLES:
            share = self.detected_without_flag / total * 100.0
            if share > DEFROST_FLAG_MISMATCH_WARN_PCT:
                out.append(f"negative_power_without_defrost_flag:{share:.0f}%")
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.flag_on,
            "cycles": self.flag_cycles,
            "duration_s_total": round(self.flag_duration_s, 1),
            "confirmed_by_temperatures": self.confirmed,
            "not_seen_in_temperatures": self.unconfirmed,
            "detected_without_flag": self.detected_without_flag,
        }

    def restore(self, raw: dict[str, Any] | None) -> None:
        raw = raw or {}
        self.flag_cycles = int(raw.get("cycles", 0) or 0)
        self.flag_duration_s = float(raw.get("duration_s_total", 0.0) or 0.0)
        self.confirmed = int(raw.get("confirmed_by_temperatures", 0) or 0)
        self.unconfirmed = int(raw.get("not_seen_in_temperatures", 0) or 0)
        self.detected_without_flag = int(raw.get("detected_without_flag", 0) or 0)

    def reset(self) -> None:
        self.flag_cycles = 0
        self.flag_duration_s = 0.0
        self.confirmed = 0
        self.unconfirmed = 0
        self.detected_without_flag = 0
        self._pending_detected = None


def reference_cop(outdoor_c: float, points: tuple[tuple[float, float], ...]) -> float | None:
    """Linearly interpolate (and clamp) a manufacturer COP curve.

    ``points`` are ``(outdoor_temp_c, cop)`` pairs sorted by temperature.
    """
    if not points:
        return None
    if outdoor_c <= points[0][0]:
        return points[0][1]
    if outdoor_c >= points[-1][0]:
        return points[-1][1]
    for (t0, c0), (t1, c1) in zip(points, points[1:]):
        if t0 <= outdoor_c <= t1:
            return c0 + (c1 - c0) * (outdoor_c - t0) / (t1 - t0)
    return None


# Stiebel Eltron LWZ 303 Integral, EN 14511, flow temperature 35 degC.
LWZ303_REFERENCE_COP = ((-7.0, 2.7), (2.0, 3.3))
