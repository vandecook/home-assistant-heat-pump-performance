"""Runtime and persistence for Heat Pump Performance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calc import (
    MAX_INPUT_AGE_S,
    STATUS_IDLE,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_STALE,
    DefrostDetector,
    DefrostFlagTracker,
    PlausibilityStats,
    SampleLimits,
    ThermalIntegrator,
    assess_health,
    check_energy_increment,
    check_meter_against_calc,
    cop_status,
    defrost_share_pct,
    evaluate_thermal_sample,
    flow_to_m3h,
    meter_deviation_pct,
    reference_cop,
)
from .const import (
    CONF_FLOW,
    CONF_FLOW_UNIT,
    CONF_MAX_DELTA_T,
    CONF_MAX_ELECTRICAL_POWER,
    CONF_MAX_FLOW,
    CONF_MAX_INPUT_AGE,
    CONF_MIN_FLOW,
    CONF_OUTDOOR_TEMP,
    CONF_REF_COP_COLD,
    CONF_REF_COP_WARM,
    CONF_RETURN_TEMP,
    CONF_SUPPLY_TEMP,
    CONF_THERMAL_MODE,
    MODE_CALCULATED,
    MODE_METER,
    MODE_METER_MINUS_DEFROST,
    CONF_AUX_ENERGY,
    CONF_AUX_ENERGY_UNIT,
    CONF_DEFROST_ACTIVE,
    CONF_DEFROST_DELTA_T,
    CONF_DEFROST_MIN_DURATION,
    CONF_DEFROST_SOURCE,
    DEFROST_SOURCE_HEAT_PUMP,
    CONF_DHW_ACTIVE,
    CONF_ELECTRICAL_ENERGY,
    CONF_ELECTRICAL_ENERGY_UNIT,
    CONF_ELECTRICAL_POWER,
    CONF_ELECTRICAL_POWER_UNIT,
    CONF_HEATING_ACTIVE,
    CONF_HEATING_SEASON_ENABLED,
    CONF_HEATING_SEASON_END,
    CONF_HEATING_SEASON_START,
    CONF_INITIAL_YEAR_DATE,
    CONF_INITIAL_YEAR_MODE,
    CONF_THERMAL_ENERGY,
    CONF_THERMAL_ENERGY_UNIT,
    CONF_AUX_POWER,
    CONF_AUX_POWER_UNIT,
    CONF_MAX_THERMAL_POWER,
    CONF_THERMAL_SAFETY_FACTOR,
    CONF_THERMAL_RESOLUTION,
    QUALITY_HISTORY_UNAVAILABLE,
    QUALITY_INVALID,
    QUALITY_OK,
    QUALITY_WAITING,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    YEAR_FULL,
    YEAR_PARTIAL,
)

_LOGGER = logging.getLogger(__name__)

# The calculation is re-evaluated at this interval even when no input changed, so
# a legitimately constant reading keeps integrating instead of looking like a gap.
CALC_TICK_S = 30
# Flow, supply and return usually arrive as separate state changes of one device
# payload. Evaluating after each one would judge half-updated, inconsistent
# combinations (e.g. new supply with old return) and reject them as glitches.
# Inputs are therefore collected and evaluated once, this long after the first.
CALC_DEBOUNCE_S = 1.0
# Self-calculated energy only lives in memory between meter steps; persist it
# at most this often.
SAVE_INTERVAL_S = 300.0

# Config key of each self-calculation input role.
_ROLE_KEYS = {
    "flow": CONF_FLOW,
    "supply": CONF_SUPPLY_TEMP,
    "return": CONF_RETURN_TEMP,
    "outdoor": CONF_OUTDOOR_TEMP,
}


@dataclass
class Bucket:
    thermal: float = 0.0
    electrical: float = 0.0
    aux: float = 0.0
    # Persist net energy independently. This prevents old/mismatched auxiliary
    # bucket totals from pinning the output at zero after tests or source changes.
    # The value may be temporarily negative when asynchronous source updates
    # arrive out of order; AZ remains unavailable until it is positive again.
    net_electrical_accum: float = 0.0
    start: str | None = None
    # Self-calculated heat (flow x delta T): delivered heat and defrost loss.
    calc_heat: float = 0.0
    calc_loss: float = 0.0
    # Effective thermal energy that feeds the COP, according to the thermal mode.
    # In the default "meter" mode this always equals ``thermal``.
    eff: float = 0.0
    # Domestic-hot-water share of ``eff`` and of the net electrical energy.
    eff_dhw: float = 0.0
    net_dhw: float = 0.0

    @property
    def net_electrical(self) -> float:
        return self.net_electrical_accum

    @staticmethod
    def _ratio(thermal: float, electrical: float) -> float | None:
        if thermal <= 0 or electrical <= 0:
            return None
        return thermal / electrical

    @property
    def az(self) -> float | None:
        """Working ratio on net electrical energy (without controls/ventilation)."""
        return self._ratio(self.eff, self.net_electrical)

    @property
    def cop_gross(self) -> float | None:
        """COP on the complete electrical energy of the system."""
        return self._ratio(self.eff, self.electrical)

    @property
    def az_dhw(self) -> float | None:
        return self._ratio(self.eff_dhw, self.net_dhw)

    @property
    def az_heating(self) -> float | None:
        """Everything that is not domestic hot water."""
        return self._ratio(self.eff - self.eff_dhw, self.net_electrical - self.net_dhw)


@dataclass
class RuntimeState:
    last_values: dict[str, float] = field(default_factory=dict)
    diagnostic_values: dict[str, float] = field(default_factory=dict)
    diagnostic_previous_values: dict[str, float] = field(default_factory=dict)
    diagnostic_deltas: dict[str, float] = field(default_factory=dict)
    diagnostic_updated: dict[str, str] = field(default_factory=dict)
    lifetime: Bucket = field(default_factory=Bucket)
    daily: Bucket = field(default_factory=Bucket)
    monthly: Bucket = field(default_factory=Bucket)
    yearly: Bucket = field(default_factory=Bucket)
    heating_season: Bucket = field(default_factory=Bucket)
    day_key: str | None = None
    month_key: str | None = None
    year_key: str | None = None
    season_key: str | None = None
    quality: str = QUALITY_WAITING
    history_backfill_attempted: bool = False
    history_backfill_version: int = 0
    history_backfill_info: dict[str, Any] = field(default_factory=dict)
    source_signature: str | None = None
    accepted_at: dict[str, str] = field(default_factory=dict)
    rejected_thermal_count: int = 0
    rejected_thermal_last: dict[str, Any] = field(default_factory=dict)
    previous_day: dict[str, Any] = field(default_factory=dict)
    previous_month: dict[str, Any] = field(default_factory=dict)
    # Self-calculation (flow x delta T) and its plausibility bookkeeping.
    calc_latest: dict[str, float] = field(default_factory=dict)
    calc_power_kw: float | None = None
    calc_status: str = "inactive"
    calc_signature: str | None = None
    # Calculated heat since the last accepted meter increment (not persisted).
    calc_heat_since_meter: float = 0.0
    calc_window_start_ts: float | None = None
    # Paired meter/calculation energy over windows without data gaps.
    cmp_meter_kwh: float = 0.0
    cmp_calc_kwh: float = 0.0
    sample_stats: PlausibilityStats = field(default_factory=PlausibilityStats)
    increment_stats: PlausibilityStats = field(default_factory=PlausibilityStats)


class HeatPumpPerformanceRuntime:
    """Maintain cumulative deltas and expose calculated working ratios."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.data = entry.data
        self.store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}"
        )
        self.state = RuntimeState()
        self._unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._integrator = ThermalIntegrator(max_gap_s=300.0)
        defrost_k = float(self.data.get(CONF_DEFROST_DELTA_T, 0.5))
        self._defrost = DefrostDetector(
            threshold_k=defrost_k,
            min_duration_s=float(self.data.get(CONF_DEFROST_MIN_DURATION, 60)),
        )
        self._defrost_flag = DefrostFlagTracker(threshold_k=defrost_k)
        self._calc_roles: dict[str, str] = self._build_calc_roles()
        self._unsub_tick: Callable[[], None] | None = None
        self._eval_unsub: Callable[[], None] | None = None
        self._last_save_ts: float = 0.0

    @property
    def defrost_from_heat_pump(self) -> bool:
        """True when the defrost count comes from the heat pump's own signal."""
        return (
            self.data.get(CONF_DEFROST_SOURCE) == DEFROST_SOURCE_HEAT_PUMP
            and bool(self.data.get(CONF_DEFROST_ACTIVE))
        )

    @property
    def defrost_count(self) -> int:
        return self._defrost_flag.flag_cycles if self.defrost_from_heat_pump else self._defrost.count

    @property
    def defrost_active(self) -> bool:
        return self._defrost_flag.flag_on if self.defrost_from_heat_pump else self._defrost.active

    @property
    def defrost_duration_s(self) -> float:
        if self.defrost_from_heat_pump:
            return self._defrost_flag.duration_running_s(dt_util.now().timestamp())
        return self._defrost.duration_s_total

    async def async_setup(self) -> None:
        """Restore state and start tracking inputs."""
        saved = await self.store.async_load()
        if saved:
            self._restore(saved)
        self._roll_periods(dt_util.now())
        self._rebase_if_sources_changed()
        self._rebase_if_calc_changed()
        self._prune_diagnostics_for_current_config()
        await self._seed_missing_last_values()
        self._seed_diagnostic_values()
        self._seed_calc_inputs()
        for entity_id in self.state.last_values:
            self.state.accepted_at.setdefault(entity_id, dt_util.now().isoformat())
        self._refresh_quality()
        await self._async_initial_year_backfill_if_needed()

        entities = [
            self.data[CONF_THERMAL_ENERGY],
            self.data[CONF_ELECTRICAL_ENERGY],
        ]
        if self.data.get(CONF_AUX_ENERGY):
            entities.append(self.data[CONF_AUX_ENERGY])
        if self.data.get(CONF_ELECTRICAL_POWER):
            entities.append(self.data[CONF_ELECTRICAL_POWER])
        if self.data.get(CONF_AUX_POWER):
            entities.append(self.data[CONF_AUX_POWER])
        if self.data.get(CONF_HEATING_ACTIVE):
            entities.append(self.data[CONF_HEATING_ACTIVE])
        if self.data.get(CONF_DHW_ACTIVE):
            entities.append(self.data[CONF_DHW_ACTIVE])
        if self.data.get(CONF_DEFROST_ACTIVE):
            entities.append(self.data[CONF_DEFROST_ACTIVE])
            self._defrost_flag.seed(dt_util.now().timestamp(), bool(self.is_on(self.data[CONF_DEFROST_ACTIVE])))
        entities.extend(self._calc_roles)
        self._unsub = async_track_state_change_event(
            self.hass, entities, self._async_state_changed
        )
        if self.calc_enabled:
            self._unsub_tick = async_track_time_interval(
                self.hass, self._async_calc_tick, timedelta(seconds=CALC_TICK_S)
            )
        await self._save()

    async def async_shutdown(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
        if self._eval_unsub:
            self._eval_unsub()
            self._eval_unsub = None
        await self._save()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)
        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _async_state_changed(self, event: Event) -> None:
        now = dt_util.now()
        self._roll_periods(now)
        entity_id = event.data["entity_id"]
        new_state: State | None = event.data.get("new_state")

        if entity_id in self._calc_roles:
            self._on_calc_input(entity_id, new_state, now)
            await self._save_if_due(now)
            return

        if self.data.get(CONF_DEFROST_ACTIVE) and entity_id == self.data[CONF_DEFROST_ACTIVE]:
            if new_state is not None and new_state.state.lower() not in ("unavailable", "unknown"):
                self._defrost_flag.flag_changed(now.timestamp(), bool(self.is_on(entity_id)))
            self._notify()
            return

        if entity_id in (
            self.data.get(CONF_HEATING_ACTIVE),
            self.data.get(CONF_DHW_ACTIVE),
        ):
            self._notify()
            return

        if new_state is None:
            return
        value = _state_float(new_state)
        if value is None:
            self.state.quality = QUALITY_INVALID
            self._notify()
            return

        descriptor = self._input_descriptor(entity_id)
        if descriptor is None:
            return
        role, unit, kind = descriptor
        normalized = _normalize_input(value, unit, kind)
        previous_diag = self.state.diagnostic_values.get(role)
        self.state.diagnostic_values[role] = normalized
        if previous_diag is not None:
            self.state.diagnostic_previous_values[role] = previous_diag
            self.state.diagnostic_deltas[role] = normalized - previous_diag
        self.state.diagnostic_updated[role] = now.isoformat()

        # Power inputs are diagnostic-only in development stage 1.
        if kind == "power":
            self._refresh_quality()
            await self._save()
            self._notify()
            return

        value_kwh = normalized
        previous = self.state.last_values.get(entity_id)
        if role == "thermal_energy" and previous is not None:
            last_time_raw = self.state.accepted_at.get(entity_id)
            try:
                last_time = datetime.fromisoformat(last_time_raw) if last_time_raw else now
                elapsed_hours = max(0.0, (now - last_time).total_seconds() / 3600.0)
            except (TypeError, ValueError):
                elapsed_hours = 0.0
            power = float(self.data.get(CONF_MAX_THERMAL_POWER, 12.0))
            factor = float(self.data.get(CONF_THERMAL_SAFETY_FACTOR, 1.2))
            resolution = float(self.data.get(CONF_THERMAL_RESOLUTION, 1.0))
            change = value_kwh - previous
            reason, allowed = check_energy_increment(change, elapsed_hours, power, factor, resolution)
            if reason == STATUS_OK and self.calc_enabled and change > 0:
                # Second opinion: the meter may not run far ahead of the
                # self-calculated heat (only judged over gap-free windows).
                reason, calc_allowed = check_meter_against_calc(
                    change,
                    self.state.calc_heat_since_meter,
                    resolution,
                    self._calc_window_valid(now),
                )
                if reason != STATUS_OK:
                    allowed = calc_allowed if calc_allowed is not None else allowed
            if reason != STATUS_OK:
                self.state.rejected_thermal_count += 1
                self.state.rejected_thermal_last = {
                    "value_kwh": value_kwh, "last_valid_kwh": previous,
                    "delta_kwh": change, "allowed_delta_kwh": round(allowed, 6),
                    "reason": reason,
                    "calc_heat_since_meter_kwh": round(self.state.calc_heat_since_meter, 4),
                    "timestamp": now.isoformat(),
                }
                self.state.increment_stats.record_reject(
                    now.timestamp(), reason, {"delta_kwh": change}
                )
                # Preserve accepted baseline and time; never book a rejected reading.
                self._refresh_quality()
                await self._save()
                self._notify()
                return
            if change > 0:
                self.state.increment_stats.record_ok(now.timestamp())
        self.state.last_values[entity_id] = value_kwh
        self.state.accepted_at[entity_id] = now.isoformat()

        if previous is None:
            self._refresh_quality()
            await self._save()
            self._notify()
            return
        if value_kwh < previous:
            # Counter reset/replacement: rebase without adding a negative delta.
            self.state.quality = QUALITY_INVALID
            await self._save()
            self._notify()
            return

        delta = value_kwh - previous
        if delta == 0:
            self._refresh_quality()
            await self._save()
            self._notify()
            return

        bucket_role = {
            "thermal_energy": "thermal",
            "electrical_energy": "electrical",
            "aux_energy": "aux",
        }[role]
        self._apply_delta(bucket_role, delta, now)
        if bucket_role == "thermal":
            self._pair_meter_with_calc(delta, now)
        self._refresh_quality()
        await self._save()
        self._notify()

    def _buckets_for(self, now: datetime) -> list[Bucket]:
        buckets = [self.state.lifetime, self.state.daily, self.state.monthly, self.state.yearly]
        if self._is_in_heating_season(now):
            buckets.append(self.state.heating_season)
        return buckets

    @property
    def thermal_mode(self) -> str:
        mode = self.data.get(CONF_THERMAL_MODE, MODE_METER)
        if mode != MODE_METER and not self.calc_enabled:
            return MODE_METER  # self-calculation not configured: fall back safely
        return mode

    def _dhw_active(self) -> bool:
        return bool(self.is_on(self.data.get(CONF_DHW_ACTIVE)))

    def _apply_delta(self, role: str, delta: float, now: datetime) -> None:
        dhw = self._dhw_active()
        mode = self.thermal_mode
        for bucket in self._buckets_for(now):
            if role == "thermal":
                bucket.thermal += delta
                if mode == MODE_METER:
                    bucket.eff += delta
                    if dhw:
                        bucket.eff_dhw += delta
                # Other modes derive ``eff`` from the calculation (see
                # _book_calc_energy) so defrost loss is accounted for exactly once.
            elif role == "electrical":
                bucket.electrical += delta
                bucket.net_electrical_accum += delta
                if dhw:
                    bucket.net_dhw += delta
            elif role == "aux":
                bucket.aux += delta
                bucket.net_electrical_accum -= delta
                if dhw:
                    bucket.net_dhw -= delta

    # ------------------------------------------------------------------
    # Self-calculation from flow and flow/return temperature
    # ------------------------------------------------------------------
    def _build_calc_roles(self) -> dict[str, str]:
        """Map entity_id -> role for the configured self-calculation inputs."""
        roles: dict[str, str] = {}
        for role, key in _ROLE_KEYS.items():
            entity_id = self.data.get(key)
            if entity_id:
                roles[entity_id] = role
        return roles

    @property
    def calc_enabled(self) -> bool:
        """True when flow and both temperatures are configured."""
        configured = set(self._calc_roles.values())
        return {"flow", "supply", "return"} <= configured

    def _calc_entity(self, role: str) -> str | None:
        return self.data.get(_ROLE_KEYS[role])

    def _calc_limits(self) -> SampleLimits:
        power = float(self.data.get(CONF_MAX_THERMAL_POWER, 12.0))
        factor = float(self.data.get(CONF_THERMAL_SAFETY_FACTOR, 1.2))
        return SampleLimits(
            min_flow_m3h=float(self.data.get(CONF_MIN_FLOW, 0.1)),
            max_flow_m3h=float(self.data.get(CONF_MAX_FLOW, 3.0)),
            max_abs_delta_t=float(self.data.get(CONF_MAX_DELTA_T, 25.0)),
            max_power_kw=power * factor,
        )

    def _calc_signature(self) -> str:
        return ";".join(f"{k}={self.data.get(k, '')}" for k in (CONF_FLOW, CONF_FLOW_UNIT, CONF_SUPPLY_TEMP, CONF_RETURN_TEMP))

    def _rebase_if_calc_changed(self) -> None:
        """Forget in-flight calculation state when its inputs were reconfigured."""
        signature = self._calc_signature()
        if self.state.calc_signature != signature:
            self._integrator.reset()
            self.state.calc_latest.clear()
            self.state.calc_heat_since_meter = 0.0
            self.state.calc_window_start_ts = None
            self.state.calc_signature = signature

    def _seed_calc_inputs(self) -> None:
        now_ts = dt_util.now().timestamp()
        for entity_id, role in self._calc_roles.items():
            value = _state_float(self.hass.states.get(entity_id))
            if value is not None:
                self._store_calc_value(role, value, now_ts)

    def _store_calc_value(self, role: str, value: float, ts: float) -> None:
        if role == "flow":
            value = flow_to_m3h(value, self.data.get(CONF_FLOW_UNIT, "m³/h"))
        self.state.calc_latest[role] = value
        self.state.calc_latest[f"{role}_ts"] = ts

    def _calc_input_issues(self, now_ts: float) -> dict[str, str]:
        """Per input: 'missing' (unavailable) or 'stale' (not updated lately)."""
        issues: dict[str, str] = {}
        max_age = float(self.data.get(CONF_MAX_INPUT_AGE, MAX_INPUT_AGE_S))
        for role in ("flow", "supply", "return"):
            entity_id = self._calc_entity(role)
            if not entity_id:
                continue
            if _state_float(self.hass.states.get(entity_id)) is None:
                issues[role] = "missing"
                continue
            # last_reported also advances when a source re-sends an unchanged
            # value, so a legitimately constant reading is not called stale.
            reported = self.hass.states.get(entity_id).last_reported.timestamp()
            if max_age > 0 and now_ts - reported > max_age:
                issues[role] = "stale"
        return issues

    def _on_calc_input(self, entity_id: str, new_state: State | None, now: datetime) -> None:
        """Handle a change of flow, supply, return or outdoor temperature."""
        role = self._calc_roles[entity_id]
        value = _state_float(new_state)
        ts = now.timestamp()
        if value is None:
            # Unavailable input: break the integration chain, count the reason.
            if role != "outdoor":
                self.state.sample_stats.record_reject(ts, STATUS_MISSING, {"role": role})
                self.state.calc_power_kw = None
                self.state.calc_status = STATUS_MISSING
                self._integrator.step(ts, None)
                self._defrost.update(ts, None)
            self._notify()
            return
        self._store_calc_value(role, value, ts)
        if role == "outdoor" or not self.calc_enabled:
            self._notify()
            return

        self._schedule_calc_eval()
        self._notify()

    def _schedule_calc_eval(self) -> None:
        """Evaluate once, shortly after the first input of a payload changed."""
        if self._eval_unsub is None:
            self._eval_unsub = async_call_later(self.hass, CALC_DEBOUNCE_S, self._async_calc_debounced)

    async def _async_calc_debounced(self, _now: datetime) -> None:
        self._eval_unsub = None
        now = dt_util.now()
        self._evaluate_calc_sample(now)
        await self._save_if_due(now)
        self._notify()

    def _evaluate_calc_sample(self, now: datetime) -> None:
        """Judge the latest flow/supply/return values, integrate and book them."""
        ts = now.timestamp()
        latest = self.state.calc_latest
        power, status = evaluate_thermal_sample(
            latest.get("flow"), latest.get("supply"), latest.get("return"), self._calc_limits()
        )
        self.state.calc_status = status
        self.state.calc_power_kw = power
        if power is None:
            self.state.sample_stats.record_reject(
                ts, status,
                {k: latest.get(k) for k in ("flow", "supply", "return")},
            )
        else:
            self.state.sample_stats.record_ok(ts)
        heat, loss = self._integrator.step(ts, power)
        if power is None:
            defrost_delta = None
        elif status == STATUS_IDLE:
            defrost_delta = 0.0
        else:
            defrost_delta = latest["supply"] - latest["return"]
        started = self._defrost.update(ts, defrost_delta)
        if self.data.get(CONF_DEFROST_ACTIVE):
            self._defrost_flag.delta_sample(ts, defrost_delta, started)
        if heat or loss:
            self._book_calc_energy(heat, loss, now)

    async def _async_calc_tick(self, _now: datetime) -> None:
        """Re-evaluate the last known inputs so constant readings keep integrating."""
        if not self.calc_enabled:
            return
        now = dt_util.now()
        ts = now.timestamp()
        issues = self._calc_input_issues(ts)
        if issues:
            role, issue = sorted(issues.items())[0]
            status = STATUS_MISSING if issue == "missing" else STATUS_STALE
            self.state.calc_status = status
            self.state.calc_power_kw = None
            self.state.sample_stats.record_reject(ts, status, {"role": role})
            self._integrator.step(ts, None)
            self._defrost.update(ts, None)
        else:
            self._evaluate_calc_sample(now)
        await self._save_if_due(now)
        self._notify()

    def _calc_window_valid(self, now: datetime) -> bool:
        """True when the calculation covered the window since the last meter step."""
        if not self.calc_enabled:
            return False
        start = self.state.calc_window_start_ts
        last_gap = self._integrator.last_gap_ts
        if start is None:
            return False
        if last_gap is not None and last_gap >= start:
            return False
        return not self._calc_input_issues(now.timestamp())

    def _book_calc_energy(self, heat: float, loss: float, now: datetime) -> None:
        mode = self.thermal_mode
        dhw = self._dhw_active()
        for bucket in self._buckets_for(now):
            bucket.calc_heat += heat
            bucket.calc_loss += loss
            if mode == MODE_CALCULATED:
                bucket.eff += heat - loss
                if dhw:
                    bucket.eff_dhw += heat - loss
            elif mode == MODE_METER_MINUS_DEFROST:
                # The meter only ever sees heat delivered (it may even run
                # backwards in defrost); the loss is subtracted separately.
                bucket.eff -= loss
                if dhw:
                    bucket.eff_dhw -= loss
        self.state.calc_heat_since_meter += heat

    def _pair_meter_with_calc(self, meter_delta: float, now: datetime) -> None:
        """Accumulate meter/calc pairs for the deviation diagnostic, then restart the window."""
        if self._calc_window_valid(now):
            self.state.cmp_meter_kwh += meter_delta
            self.state.cmp_calc_kwh += self.state.calc_heat_since_meter
        self.state.calc_heat_since_meter = 0.0
        self.state.calc_window_start_ts = now.timestamp()

    def calc_diagnostics(self) -> dict[str, Any]:
        """Diagnostic view of the self-calculation and all plausibility checks."""
        now_ts = dt_util.now().timestamp()
        enabled = self.calc_enabled
        life = self.state.lifetime
        issues = self._calc_input_issues(now_ts) if enabled else {}
        deviation = meter_deviation_pct(self.state.cmp_meter_kwh, self.state.cmp_calc_kwh) if enabled else None
        share = defrost_share_pct(life.calc_heat, life.calc_loss) if enabled else None
        cop_state = cop_status(life.eff, life.electrical)
        health, warnings = assess_health(
            configured=enabled,
            consecutive_rejects=self.state.sample_stats.consecutive_rejects,
            input_issues=issues,
            meter_deviation=deviation,
            defrost_share=share,
            cop_gross_status=cop_state if cop_state != "insufficient_data" else None,
            extra_warnings=self._defrost_flag.warnings() if self.data.get(CONF_DEFROST_ACTIVE) else None,
        )
        return {
            "enabled": enabled,
            "thermal_mode": self.thermal_mode,
            "health": health,
            "warnings": warnings,
            "power_kw": self.state.calc_power_kw,
            "last_sample_status": self.state.calc_status,
            "inputs": {role: self.state.calc_latest.get(role) for role in ("flow", "supply", "return", "outdoor")},
            "input_issues": issues,
            "samples": self.state.sample_stats.to_dict(),
            "meter_increments": self.state.increment_stats.to_dict(),
            "defrost": {
                "source": "heat_pump" if self.defrost_from_heat_pump else "calculated",
                "active": self.defrost_active,
                "calculated_active": self._defrost.active,
                "threshold_k": self._defrost.threshold_k,
                "min_duration_s": self._defrost.min_duration_s,
                "cycles": self.defrost_count,
                "duration_s_total": round(self.defrost_duration_s, 1),
                "calculated_cycles": self._defrost.count,
                "loss_kwh_lifetime": round(life.calc_loss, 4),
                "share_of_heat_pct": None if share is None else round(share, 2),
                "flag": self._defrost_flag.to_dict() if self.data.get(CONF_DEFROST_ACTIVE) else None,
            },
            "meter_vs_calc": {
                "paired_meter_kwh": round(self.state.cmp_meter_kwh, 3),
                "paired_calc_kwh": round(self.state.cmp_calc_kwh, 3),
                "deviation_pct": None if deviation is None else round(deviation, 2),
                "calc_heat_since_last_meter_step_kwh": round(self.state.calc_heat_since_meter, 4),
            },
            "gaps": {
                "gap_seconds_total": round(self._integrator.gap_seconds_total, 1),
                "last_gap": self._integrator.last_gap_ts,
            },
            "cop_gross_status": cop_state,
            "reference_cop": self.reference_cop(),
        }

    def reference_cop(self) -> float | None:
        """Manufacturer COP at the current outdoor temperature (flow 35 degC).

        Needs the outdoor temperature input and both reference values (COP at
        -7 degC and at 2 degC); interpolated linearly and clamped outside.
        """
        cold = self.data.get(CONF_REF_COP_COLD)
        warm = self.data.get(CONF_REF_COP_WARM)
        outdoor = self.state.calc_latest.get("outdoor")
        if not cold or not warm or outdoor is None:
            return None
        value = reference_cop(outdoor, ((-7.0, float(cold)), (2.0, float(warm))))
        return None if value is None else round(value, 3)

    def _input_descriptor(self, entity_id: str) -> tuple[str, str, str] | None:
        """Return diagnostic role, configured unit and quantity kind for an input."""
        mappings = (
            (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT, "thermal_energy", "energy"),
            (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT, "electrical_energy", "energy"),
            (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT, "aux_energy", "energy"),
            (CONF_ELECTRICAL_POWER, CONF_ELECTRICAL_POWER_UNIT, "electrical_power", "power"),
            (CONF_AUX_POWER, CONF_AUX_POWER_UNIT, "aux_power", "power"),
        )
        for entity_key, unit_key, role, kind in mappings:
            if entity_id == self.data.get(entity_key):
                unit = self.data.get(unit_key)
                if unit:
                    return role, unit, kind
        return None

    def _role_and_unit(self, entity_id: str) -> tuple[str | None, str | None]:
        descriptor = self._input_descriptor(entity_id)
        if descriptor is None:
            return None, None
        role, unit, kind = descriptor
        if kind != "energy":
            return None, None
        bucket_role = {
            "thermal_energy": "thermal",
            "electrical_energy": "electrical",
            "aux_energy": "aux",
        }.get(role)
        return bucket_role, unit

    def _current_source_signature(self) -> str:
        """Return a stable signature for energy sources and their configured units."""
        parts = []
        for key, unit_key in (
            (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT),
            (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT),
            (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT),
        ):
            parts.append(f"{key}={self.data.get(key, '')}|{unit_key}={self.data.get(unit_key, '')}")
        return ";".join(parts)

    def _rebase_if_sources_changed(self) -> None:
        """Rebase counters after an input entity/unit change without resetting periods.

        Existing accumulated buckets remain intact.  Only the raw counter baseline is
        reset, so the first reading from the newly configured counter can never be
        mistaken for newly consumed/produced energy.
        """
        signature = self._current_source_signature()
        if self.state.source_signature is None:
            # Upgrade path from versions that did not persist a source signature.
            self.state.source_signature = signature
            return
        if self.state.source_signature == signature:
            return
        self.state.last_values.clear()
        self.state.accepted_at.clear()
        self.state.source_signature = signature
        self.state.quality = QUALITY_WAITING


    def _prune_diagnostics_for_current_config(self) -> None:
        """Remove persisted diagnostics for inputs that were removed or changed."""
        configured_roles: set[str] = set()
        mappings = (
            (CONF_THERMAL_ENERGY, "thermal_energy"),
            (CONF_ELECTRICAL_ENERGY, "electrical_energy"),
            (CONF_AUX_ENERGY, "aux_energy"),
            (CONF_ELECTRICAL_POWER, "electrical_power"),
            (CONF_AUX_POWER, "aux_power"),
        )
        for entity_key, role in mappings:
            if self.data.get(entity_key):
                configured_roles.add(role)

        for store in (
            self.state.diagnostic_values,
            self.state.diagnostic_previous_values,
            self.state.diagnostic_deltas,
            self.state.diagnostic_updated,
        ):
            for role in list(store):
                if role not in configured_roles:
                    store.pop(role, None)

    async def _seed_missing_last_values(self) -> None:
        for key, unit_key in (
            (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT),
            (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT),
            (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT),
        ):
            entity_id = self.data.get(key)
            if not entity_id or entity_id in self.state.last_values:
                continue
            state = self.hass.states.get(entity_id)
            value = _state_float(state)
            if value is not None:
                self.state.last_values[entity_id] = _energy_to_kwh(value, self.data[unit_key])


    def _seed_diagnostic_values(self) -> None:
        """Seed current normalized values for all configured numeric inputs."""
        mappings = (
            (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT, "thermal_energy", "energy"),
            (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT, "electrical_energy", "energy"),
            (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT, "aux_energy", "energy"),
            (CONF_ELECTRICAL_POWER, CONF_ELECTRICAL_POWER_UNIT, "electrical_power", "power"),
            (CONF_AUX_POWER, CONF_AUX_POWER_UNIT, "aux_power", "power"),
        )
        now = dt_util.now().isoformat()
        for entity_key, unit_key, role, kind in mappings:
            entity_id = self.data.get(entity_key)
            unit = self.data.get(unit_key)
            if not entity_id or not unit:
                continue
            state = self.hass.states.get(entity_id)
            value = _state_float(state)
            if value is None:
                continue
            self.state.diagnostic_values[role] = _normalize_input(value, unit, kind)
            self.state.diagnostic_updated.setdefault(role, now)

    def _refresh_quality(self) -> None:
        """Derive data quality from current required inputs and observed energy deltas."""
        required = [
            (CONF_THERMAL_ENERGY, "thermal_energy"),
            (CONF_ELECTRICAL_ENERGY, "electrical_energy"),
        ]
        if self.data.get(CONF_AUX_ENERGY):
            required.append((CONF_AUX_ENERGY, "aux_energy"))

        for entity_key, _role in required:
            entity_id = self.data.get(entity_key)
            state = self.hass.states.get(entity_id) if entity_id else None
            if _state_float(state) is None:
                self.state.quality = QUALITY_INVALID
                return

        # A freshly initialized source has a valid current value, but no previous
        # sample yet. This is not invalid data; it is simply not enough to form
        # a diagnostic delta. Wait until every configured energy source has seen
        # at least one subsequent valid update.
        if any(role not in self.state.diagnostic_deltas for _, role in required):
            self.state.quality = QUALITY_WAITING
            return

        self.state.quality = QUALITY_OK

    def input_diagnostics(self) -> dict[str, Any]:
        """Return current input values and last deltas for diagnostic display."""
        result: dict[str, Any] = {}
        mappings = (
            (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT, "thermal_energy", "energy"),
            (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT, "electrical_energy", "energy"),
            (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT, "aux_energy", "energy"),
            (CONF_ELECTRICAL_POWER, CONF_ELECTRICAL_POWER_UNIT, "electrical_power", "power"),
            (CONF_AUX_POWER, CONF_AUX_POWER_UNIT, "aux_power", "power"),
        )
        for entity_key, unit_key, role, kind in mappings:
            entity_id = self.data.get(entity_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            raw = _state_float(state)
            configured_unit = self.data.get(unit_key)
            item: dict[str, Any] = {
                "entity_id": entity_id,
                "raw_state": state.state if state is not None else None,
                "configured_unit": configured_unit,
                "normalized_unit": "kWh" if kind == "energy" else "kW",
                "previous_value": self.state.diagnostic_previous_values.get(role),
                "normalized_value": self.state.diagnostic_values.get(role),
                "delta": self.state.diagnostic_deltas.get(role),
                "last_update": self.state.diagnostic_updated.get(role),
            }
            if raw is None:
                item["available"] = False
            else:
                item["available"] = True
            result[role] = item

        for entity_key, role in (
            (CONF_HEATING_ACTIVE, "heating_active"),
            (CONF_DHW_ACTIVE, "dhw_active"),
            (CONF_DEFROST_ACTIVE, "defrost_active"),
        ):
            entity_id = self.data.get(entity_key)
            if not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            result[role] = {
                "entity_id": entity_id,
                "raw_state": state.state if state is not None else None,
                "active": self.is_on(entity_id),
                "last_update": state.last_updated.isoformat() if state is not None else None,
                "available": state is not None and state.state not in {"unknown", "unavailable"},
            }
        result["thermal_energy_validation"] = {
            "max_power_kw": float(self.data.get(CONF_MAX_THERMAL_POWER, 12.0)),
            "safety_factor": float(self.data.get(CONF_THERMAL_SAFETY_FACTOR, 1.2)),
            "resolution_kwh": float(self.data.get(CONF_THERMAL_RESOLUTION, 1.0)),
            "last_valid_value_kwh": self.state.last_values.get(self.data.get(CONF_THERMAL_ENERGY)),
            "rejected_samples": self.state.rejected_thermal_count,
            "last_rejected": self.state.rejected_thermal_last,
        }
        result["initial_year_backfill"] = dict(self.state.history_backfill_info)
        result["internal_buckets"] = {
            name: {
                "thermal_kwh": round(bucket.thermal, 6),
                "electrical_kwh": round(bucket.electrical, 6),
                "aux_kwh": round(bucket.aux, 6),
                "net_electrical_kwh": round(bucket.net_electrical, 6),
                "effective_thermal_kwh": round(bucket.eff, 6),
                "calc_heat_kwh": round(bucket.calc_heat, 6),
                "calc_defrost_loss_kwh": round(bucket.calc_loss, 6),
                "dhw_thermal_kwh": round(bucket.eff_dhw, 6),
                "dhw_net_electrical_kwh": round(bucket.net_dhw, 6),
                "period_start": bucket.start,
            }
            for name, bucket in (
                ("lifetime", self.state.lifetime),
                ("daily", self.state.daily),
                ("monthly", self.state.monthly),
                ("yearly", self.state.yearly),
                ("heating_season", self.state.heating_season),
            )
        }
        result["calculation"] = self.calc_diagnostics()
        return result

    async def _async_initial_year_backfill_if_needed(self) -> None:
        # v0.1.10 backfill semantics: when a historical initial year start is
        # configured, calculate the yearly bucket immediately from the recorder
        # baseline to the current counter values. Retry once after upgrades from
        # older versions even if they had marked backfill as attempted.
        if self.state.history_backfill_attempted and self.state.history_backfill_version >= 2:
            return
        self.state.history_backfill_attempted = True
        self.state.history_backfill_version = 2
        self.state.history_backfill_info = {}
        mode = self.data.get(CONF_INITIAL_YEAR_MODE, "from_now")
        if mode not in (YEAR_FULL, YEAR_PARTIAL):
            return

        now = dt_util.now()
        if mode == YEAR_FULL:
            start_local = datetime.combine(date(now.year, 1, 1), time.min, tzinfo=now.tzinfo)
        else:
            raw = self.data.get(CONF_INITIAL_YEAR_DATE)
            if not raw:
                return
            d = date.fromisoformat(str(raw))
            start_local = datetime.combine(d, time.min, tzinfo=now.tzinfo)
        if start_local >= now:
            return

        try:
            baselines: dict[str, float] = {}
            baseline_by_role: dict[str, float] = {}
            for key, unit_key, role_name in (
                (CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT, "thermal_energy"),
                (CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT, "electrical_energy"),
                (CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT, "aux_energy"),
            ):
                entity_id = self.data.get(key)
                if not entity_id:
                    continue
                value = await self._history_value_at(entity_id, start_local)
                if value is None:
                    raise ValueError(f"No recorder state for {entity_id} at requested start")
                normalized_baseline = _energy_to_kwh(value, self.data[unit_key])
                baselines[entity_id] = normalized_baseline
                baseline_by_role[role_name] = normalized_baseline

            current_thermal = self.state.last_values[self.data[CONF_THERMAL_ENERGY]]
            current_electrical = self.state.last_values[self.data[CONF_ELECTRICAL_ENERGY]]
            self.state.yearly.thermal = max(0.0, current_thermal - baselines[self.data[CONF_THERMAL_ENERGY]])
            self.state.yearly.electrical = max(0.0, current_electrical - baselines[self.data[CONF_ELECTRICAL_ENERGY]])
            if self.data.get(CONF_AUX_ENERGY):
                aux_id = self.data[CONF_AUX_ENERGY]
                self.state.yearly.aux = max(0.0, self.state.last_values.get(aux_id, 0.0) - baselines[aux_id])
            self.state.yearly.net_electrical_accum = self.state.yearly.electrical - self.state.yearly.aux
            # History only contains meter counters, so the effective thermal
            # energy of a back-filled year is the meter value in every mode.
            self.state.yearly.eff = self.state.yearly.thermal
            self.state.yearly.start = start_local.isoformat()
            self.state.history_backfill_info = {
                "status": "ok",
                "requested_start": start_local.isoformat(),
                "calculated_until": now.isoformat(),
                "baselines_kwh": baseline_by_role,
                "current_kwh": {
                    "thermal_energy": current_thermal,
                    "electrical_energy": current_electrical,
                    **({"aux_energy": self.state.last_values.get(self.data[CONF_AUX_ENERGY])} if self.data.get(CONF_AUX_ENERGY) else {}),
                },
                "deltas_kwh": {
                    "thermal_energy": self.state.yearly.thermal,
                    "electrical_energy": self.state.yearly.electrical,
                    **({"aux_energy": self.state.yearly.aux} if self.data.get(CONF_AUX_ENERGY) else {}),
                    "net_electrical_energy": self.state.yearly.net_electrical,
                },
                "working_ratio": self.state.yearly.az,
            }
            self.state.quality = QUALITY_OK
        except Exception as err:  # Recorder may be disabled or history purged.
            _LOGGER.warning("Could not backfill initial yearly period: %s", err)
            self.state.quality = QUALITY_HISTORY_UNAVAILABLE
            self.state.history_backfill_info = {
                "status": "history_unavailable",
                "requested_start": start_local.isoformat(),
                "fallback_start": now.isoformat(),
                "error": str(err),
            }
            # Do not fabricate history: keep the yearly bucket starting now.
            self.state.yearly = Bucket(start=now.isoformat())

    async def _history_value_at(self, entity_id: str, start_local: datetime) -> float | None:
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.helpers.recorder import get_instance

        start_utc = dt_util.as_utc(start_local)
        end_utc = min(dt_util.utcnow(), start_utc + timedelta(days=1))
        recorder = get_instance(self.hass)
        result = await recorder.async_add_executor_job(
            get_significant_states,
            self.hass,
            start_utc,
            end_utc,
            [entity_id],
            None,
            True,
            False,
            False,
            True,
            False,
        )
        for state in result.get(entity_id, []):
            value = _state_float(state if isinstance(state, State) else None)
            if value is not None:
                return value
            if isinstance(state, dict):
                try:
                    return float(state.get("state"))
                except (TypeError, ValueError):
                    continue
        return None

    def _roll_periods(self, now: datetime) -> None:
        day_key = now.date().isoformat()
        if self.state.day_key != day_key:
            if self.state.day_key is not None:
                self.state.previous_day = dict(self.state.daily.__dict__)
            self.state.day_key = day_key
            self.state.daily = Bucket(start=datetime.combine(now.date(), time.min, tzinfo=now.tzinfo).isoformat())

        month_key = f"{now.year:04d}-{now.month:02d}"
        if self.state.month_key != month_key:
            if self.state.month_key is not None:
                self.state.previous_month = dict(self.state.monthly.__dict__)
            self.state.month_key = month_key
            self.state.monthly = Bucket(start=datetime(now.year, now.month, 1, tzinfo=now.tzinfo).isoformat())

        year_key = str(now.year)
        if self.state.year_key != year_key:
            self.state.year_key = year_key
            # Only the first configured year can be partial/backfilled. Following
            # calendar years always start on Jan 1.
            self.state.yearly = Bucket(start=datetime(now.year, 1, 1, tzinfo=now.tzinfo).isoformat())
            self.state.history_backfill_attempted = False

        season_key = self._heating_season_key(now)
        if season_key != self.state.season_key:
            self.state.season_key = season_key
            self.state.heating_season = Bucket(start=now.isoformat())

        if self.state.lifetime.start is None:
            self.state.lifetime.start = now.isoformat()

    def _heating_season_key(self, now: datetime) -> str | None:
        if not self.data.get(CONF_HEATING_SEASON_ENABLED):
            return None
        start_md = self.data.get(CONF_HEATING_SEASON_START, "10-01")
        end_md = self.data.get(CONF_HEATING_SEASON_END, "04-30")
        sm, sd = map(int, start_md.split("-"))
        em, ed = map(int, end_md.split("-"))
        md = (now.month, now.day)
        start = (sm, sd)
        end = (em, ed)
        if start <= end:
            if start <= md <= end:
                return f"{now.year}:{start_md}:{end_md}"
            return None
        if md >= start:
            return f"{now.year}-{now.year+1}:{start_md}:{end_md}"
        if md <= end:
            return f"{now.year-1}-{now.year}:{start_md}:{end_md}"
        return None

    def _is_in_heating_season(self, now: datetime) -> bool:
        return self._heating_season_key(now) is not None

    def is_on(self, entity_id: str | None) -> bool | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state.state.lower() in {"on", "true", "1", "heat", "heating", "active"}

    def bucket(self, key: str) -> Bucket:
        return getattr(self.state, key)

    def previous_bucket(self, name: str) -> Bucket | None:
        data = self.state.previous_day if name == "daily" else self.state.previous_month
        if not data:
            return None
        thermal = float(data.get("thermal", 0.0) or 0.0)
        electrical = float(data.get("electrical", 0.0) or 0.0)
        aux = float(data.get("aux", 0.0) or 0.0)
        net = data.get("net_electrical_accum")
        net = electrical - aux if net is None else float(net)
        return _bucket_from_dict(data, thermal, electrical, aux, net)

    async def async_reset_balances(self, reset_spikes: bool) -> None:
        """Rebase all inputs to current valid readings and start new periods."""
        now = dt_util.now()
        self.state.lifetime = Bucket(start=now.isoformat())
        self.state.daily = Bucket(start=now.isoformat())
        self.state.monthly = Bucket(start=now.isoformat())
        self.state.yearly = Bucket(start=now.isoformat())
        self.state.heating_season = Bucket(start=now.isoformat())
        self.state.day_key = now.date().isoformat()
        self.state.month_key = f"{now.year:04d}-{now.month:02d}"
        self.state.year_key = str(now.year)
        self.state.season_key = self._heating_season_key(now)
        self.state.previous_day = {}
        self.state.previous_month = {}
        self.state.history_backfill_attempted = True
        self.state.history_backfill_version = 2
        self.state.history_backfill_info = {"status": "reset", "reset_at": now.isoformat()}
        self.state.last_values.clear()
        self.state.accepted_at.clear()
        self.state.diagnostic_deltas.clear()
        self.state.cmp_meter_kwh = 0.0
        self.state.cmp_calc_kwh = 0.0
        self.state.calc_heat_since_meter = 0.0
        self.state.calc_window_start_ts = None
        self._integrator.reset()
        await self._seed_missing_last_values()
        for entity_id in self.state.last_values:
            self.state.accepted_at[entity_id] = now.isoformat()
        if reset_spikes:
            self.state.rejected_thermal_count = 0
            self.state.rejected_thermal_last = {}
            self.state.sample_stats = PlausibilityStats()
            self.state.increment_stats = PlausibilityStats()
            self._defrost.count = 0
            self._defrost.duration_s_total = 0.0
            self._defrost_flag.reset()
            self._integrator.gap_seconds_total = 0.0
        self._refresh_quality()
        await self._save()
        self._notify()

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_values": self.state.last_values,
            "diagnostic_values": self.state.diagnostic_values,
            "diagnostic_previous_values": self.state.diagnostic_previous_values,
            "diagnostic_deltas": self.state.diagnostic_deltas,
            "diagnostic_updated": self.state.diagnostic_updated,
            "lifetime": self.state.lifetime.__dict__,
            "daily": self.state.daily.__dict__,
            "monthly": self.state.monthly.__dict__,
            "yearly": self.state.yearly.__dict__,
            "heating_season": self.state.heating_season.__dict__,
            "day_key": self.state.day_key,
            "month_key": self.state.month_key,
            "year_key": self.state.year_key,
            "season_key": self.state.season_key,
            "quality": self.state.quality,
            "history_backfill_attempted": self.state.history_backfill_attempted,
            "history_backfill_version": self.state.history_backfill_version,
            "history_backfill_info": self.state.history_backfill_info,
            "source_signature": self.state.source_signature,
            "accepted_at": self.state.accepted_at,
            "rejected_thermal_count": self.state.rejected_thermal_count,
            "rejected_thermal_last": self.state.rejected_thermal_last,
            "previous_day": self.state.previous_day,
            "previous_month": self.state.previous_month,
            "calc": {
                "signature": self.state.calc_signature,
                "cmp_meter_kwh": self.state.cmp_meter_kwh,
                "cmp_calc_kwh": self.state.cmp_calc_kwh,
                "sample_stats": self.state.sample_stats.to_dict(),
                "increment_stats": self.state.increment_stats.to_dict(),
                "defrost_count": self._defrost.count,
                "defrost_duration_s": self._defrost.duration_s_total,
                "defrost_flag": self._defrost_flag.to_dict(),
                "gap_seconds_total": self._integrator.gap_seconds_total,
            },
        }

    def _restore(self, data: dict[str, Any]) -> None:
        self.state.last_values = {k: float(v) for k, v in data.get("last_values", {}).items()}
        self.state.diagnostic_values = {k: float(v) for k, v in data.get("diagnostic_values", {}).items()}
        self.state.diagnostic_previous_values = {k: float(v) for k, v in data.get("diagnostic_previous_values", {}).items()}
        self.state.diagnostic_deltas = {k: float(v) for k, v in data.get("diagnostic_deltas", {}).items()}
        self.state.diagnostic_updated = {k: str(v) for k, v in data.get("diagnostic_updated", {}).items()}
        for name in ("lifetime", "daily", "monthly", "yearly", "heating_season"):
            raw = data.get(name, {})
            thermal = float(raw.get("thermal", 0.0) or 0.0)
            electrical = float(raw.get("electrical", 0.0) or 0.0)
            aux = float(raw.get("aux", 0.0) or 0.0)
            # Migration from <=0.1.8: initialize the independent net accumulator
            # from the old bucket totals exactly once. Future updates maintain it
            # independently.
            net = raw.get("net_electrical_accum")
            if net is None:
                net = electrical - aux
            setattr(
                self.state,
                name,
                _bucket_from_dict(raw, thermal, electrical, aux, float(net)),
            )
        self.state.day_key = data.get("day_key")
        self.state.month_key = data.get("month_key")
        self.state.year_key = data.get("year_key")
        self.state.season_key = data.get("season_key")
        self.state.quality = data.get("quality", QUALITY_WAITING)
        self.state.history_backfill_attempted = bool(data.get("history_backfill_attempted", False))
        self.state.history_backfill_version = int(data.get("history_backfill_version", 0) or 0)
        self.state.history_backfill_info = dict(data.get("history_backfill_info", {}) or {})
        self.state.source_signature = data.get("source_signature")
        self.state.accepted_at = dict(data.get("accepted_at", {}))
        self.state.rejected_thermal_count = int(data.get("rejected_thermal_count", 0))
        self.state.rejected_thermal_last = dict(data.get("rejected_thermal_last", {}))
        self.state.previous_day = dict(data.get("previous_day", {}))
        self.state.previous_month = dict(data.get("previous_month", {}))
        calc = dict(data.get("calc", {}) or {})
        self.state.calc_signature = calc.get("signature")
        self.state.cmp_meter_kwh = float(calc.get("cmp_meter_kwh", 0.0) or 0.0)
        self.state.cmp_calc_kwh = float(calc.get("cmp_calc_kwh", 0.0) or 0.0)
        self.state.sample_stats = PlausibilityStats.from_dict(calc.get("sample_stats"))
        self.state.increment_stats = PlausibilityStats.from_dict(calc.get("increment_stats"))
        self._defrost.count = int(calc.get("defrost_count", 0) or 0)
        self._defrost.duration_s_total = float(calc.get("defrost_duration_s", 0.0) or 0.0)
        self._defrost_flag.restore(calc.get("defrost_flag"))
        self._integrator.gap_seconds_total = float(calc.get("gap_seconds_total", 0.0) or 0.0)

    async def _save(self) -> None:
        self._last_save_ts = dt_util.now().timestamp()
        await self.store.async_save(self.snapshot())

    async def _save_if_due(self, now: datetime) -> None:
        """Persist self-calculated energy at most every SAVE_INTERVAL_S seconds."""
        if now.timestamp() - self._last_save_ts >= SAVE_INTERVAL_S:
            await self._save()


def _bucket_from_dict(raw: dict[str, Any], thermal: float, electrical: float, aux: float, net: float) -> Bucket:
    """Build a Bucket from stored data, migrating buckets saved before v0.2.0.

    Before v0.2.0 the COP was always computed from the meter, so the effective
    thermal energy (``eff``) of an old bucket equals its ``thermal`` value.
    """
    eff_raw = raw.get("eff")
    return Bucket(
        thermal=thermal,
        electrical=electrical,
        aux=aux,
        net_electrical_accum=net,
        start=raw.get("start"),
        calc_heat=float(raw.get("calc_heat", 0.0) or 0.0),
        calc_loss=float(raw.get("calc_loss", 0.0) or 0.0),
        eff=thermal if eff_raw is None else float(eff_raw),
        eff_dhw=float(raw.get("eff_dhw", 0.0) or 0.0),
        net_dhw=float(raw.get("net_dhw", 0.0) or 0.0),
    )


def _state_float(state: State | None) -> float | None:
    if state is None or state.state in {"unknown", "unavailable", "none", ""}:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _normalize_input(value: float, unit: str, kind: str) -> float:
    if kind == "power":
        if unit == "W":
            return value / 1000.0
        return value
    return _energy_to_kwh(value, unit)


def _energy_to_kwh(value: float, unit: str) -> float:
    if unit == "Wh":
        return value / 1000.0
    if unit == "MWh":
        return value * 1000.0
    return value
