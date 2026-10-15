"""Runtime and persistence for Heat Pump Performance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUX_ENERGY,
    CONF_AUX_ENERGY_UNIT,
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

    @property
    def net_electrical(self) -> float:
        return self.net_electrical_accum

    @property
    def az(self) -> float | None:
        net = self.net_electrical
        if self.thermal <= 0 or net <= 0:
            return None
        return self.thermal / net


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

    async def async_setup(self) -> None:
        """Restore state and start tracking inputs."""
        saved = await self.store.async_load()
        if saved:
            self._restore(saved)
        self._roll_periods(dt_util.now())
        self._rebase_if_sources_changed()
        self._prune_diagnostics_for_current_config()
        await self._seed_missing_last_values()
        self._seed_diagnostic_values()
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
        self._unsub = async_track_state_change_event(
            self.hass, entities, self._async_state_changed
        )
        await self._save()

    async def async_shutdown(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
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
            allowed = power * factor * elapsed_hours + resolution
            change = value_kwh - previous
            if change < -1e-7 or change > allowed + 1e-7:
                self.state.rejected_thermal_count += 1
                self.state.rejected_thermal_last = {
                    "value_kwh": value_kwh, "last_valid_kwh": previous,
                    "delta_kwh": change, "allowed_delta_kwh": round(allowed, 6),
                    "reason": "counter_decrease" if change < 0 else "excessive_energy_delta",
                    "timestamp": now.isoformat(),
                }
                # Preserve accepted baseline and time; never book a rejected reading.
                self._refresh_quality()
                await self._save()
                self._notify()
                return
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
        self._refresh_quality()
        await self._save()
        self._notify()

    def _apply_delta(self, role: str, delta: float, now: datetime) -> None:
        buckets = [self.state.lifetime, self.state.daily, self.state.monthly, self.state.yearly]
        if self._is_in_heating_season(now):
            buckets.append(self.state.heating_season)
        for bucket in buckets:
            if role == "thermal":
                bucket.thermal += delta
            elif role == "electrical":
                bucket.electrical += delta
                bucket.net_electrical_accum += delta
            elif role == "aux":
                bucket.aux += delta
                bucket.net_electrical_accum -= delta

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
        return Bucket(**{k: data.get(k, 0.0 if k != "start" else None)
                         for k in ("thermal", "electrical", "aux", "net_electrical_accum", "start")})

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
        await self._seed_missing_last_values()
        for entity_id in self.state.last_values:
            self.state.accepted_at[entity_id] = now.isoformat()
        if reset_spikes:
            self.state.rejected_thermal_count = 0
            self.state.rejected_thermal_last = {}
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
                Bucket(
                    thermal=thermal,
                    electrical=electrical,
                    aux=aux,
                    net_electrical_accum=float(net),
                    start=raw.get("start"),
                ),
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

    async def _save(self) -> None:
        await self.store.async_save(self.snapshot())


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
