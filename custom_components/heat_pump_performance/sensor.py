"""Sensor platform for Heat Pump Performance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from . import HeatPumpPerformanceData
from .const import (
    ATTR_AUX_ENERGY_KWH,
    ATTR_DATA_QUALITY,
    ATTR_DHW_ACTIVE,
    ATTR_ELECTRICAL_ENERGY_KWH,
    ATTR_HEATING_ACTIVE,
    ATTR_NET_ELECTRICAL_ENERGY_KWH,
    ATTR_PARTIAL_YEAR,
    ATTR_PERIOD_START,
    ATTR_THERMAL_ENERGY_KWH,
    ATTR_YEAR_MODE,
    CONF_DHW_ACTIVE,
    CONF_HEATING_ACTIVE,
    CONF_HEATING_SEASON_ENABLED,
    CONF_INITIAL_YEAR_MODE,
    CONF_NAME,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    YEAR_FULL,
)
from .runtime import Bucket, HeatPumpPerformanceRuntime


@dataclass(frozen=True)
class RatioDescription:
    key: str
    translation_key: str
    bucket: str


RATIO_SENSORS = (
    RatioDescription("cop", "working_ratio", "lifetime"),
    RatioDescription("cop_daily", "working_ratio_daily", "daily"),
    RatioDescription("cop_monthly", "working_ratio_monthly", "monthly"),
    RatioDescription("spf_yearly", "working_ratio_yearly", "yearly"),
    RatioDescription("spf_heating_season", "working_ratio_heating_season", "heating_season"),
    RatioDescription("spf_lifetime", "working_ratio_lifetime", "lifetime"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    data: HeatPumpPerformanceData = entry.runtime_data
    runtime = data.runtime
    descriptions = list(RATIO_SENSORS)
    if not entry.data.get(CONF_HEATING_SEASON_ENABLED):
        descriptions = [d for d in descriptions if d.bucket != "heating_season"]

    entities: list[SensorEntity] = [
        HeatPumpRatioSensor(entry, runtime, desc) for desc in descriptions
    ]
    entities.extend(
        [
            HeatPumpPreviousRatioSensor(entry, runtime, "daily", "cop_previous_day", "Arbeitszahl Vortag"),
            HeatPumpPreviousRatioSensor(entry, runtime, "monthly", "cop_previous_month", "Arbeitszahl Vormonat"),
            HeatPumpDailyEnergySensor(entry, runtime, "thermal", "thermal_energy_daily", "Wärmeenergie heute"),
            HeatPumpDailyEnergySensor(entry, runtime, "net_electrical", "electrical_energy_daily", "Elektrische Energie heute"),
            HeatPumpSpikeCountSensor(entry, runtime),
            HeatPumpEnergySensor(entry, runtime, "thermal", "thermal_energy"),
            HeatPumpEnergySensor(entry, runtime, "net_electrical", "electrical_energy"),
            HeatPumpInputDiagnosticsSensor(entry, runtime),
        ]
    )
    async_add_entities(entities)


class HeatPumpBaseSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, runtime: HeatPumpPerformanceRuntime, key: str) -> None:
        self.entry = entry
        self.runtime = runtime
        self._key = key
        system_type = entry.data[CONF_SYSTEM_TYPE]
        prefix = slugify(entry.data[CONF_NAME])
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self.entity_id = f"sensor.{prefix}_{system_type}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Heat Pump Performance",
            model=system_type,
        )
        self._remove_listener: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _update() -> None:
            self.async_write_ha_state()
        self._remove_listener = self.runtime.add_listener(_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None


class HeatPumpRatioSensor(HeatPumpBaseSensor):
    _attr_native_unit_of_measurement = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry: ConfigEntry, runtime: HeatPumpPerformanceRuntime, description: RatioDescription) -> None:
        super().__init__(entry, runtime, description.key)
        self.description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> float | None:
        value = self.runtime.bucket(self.description.bucket).az
        return round(value, 3) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bucket = self.runtime.bucket(self.description.bucket)
        attrs: dict[str, Any] = {
            ATTR_PERIOD_START: bucket.start,
            ATTR_THERMAL_ENERGY_KWH: round(bucket.thermal, 3),
            ATTR_ELECTRICAL_ENERGY_KWH: round(bucket.electrical, 3),
            ATTR_AUX_ENERGY_KWH: round(bucket.aux, 3),
            ATTR_NET_ELECTRICAL_ENERGY_KWH: round(bucket.net_electrical, 3),
            ATTR_DATA_QUALITY: self.runtime.state.quality,
            ATTR_HEATING_ACTIVE: self.runtime.is_on(self.entry.data.get(CONF_HEATING_ACTIVE)),
            ATTR_DHW_ACTIVE: self.runtime.is_on(self.entry.data.get(CONF_DHW_ACTIVE)),
        }
        if self.description.bucket == "yearly":
            mode = self.entry.data.get(CONF_INITIAL_YEAR_MODE, "from_now")
            attrs[ATTR_YEAR_MODE] = mode
            attrs[ATTR_PARTIAL_YEAR] = mode != YEAR_FULL
        return attrs


class HeatPumpEnergySensor(HeatPumpBaseSensor):
    _attr_native_unit_of_measurement = "kWh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 3

    def __init__(self, entry: ConfigEntry, runtime: HeatPumpPerformanceRuntime, role: str, key: str) -> None:
        super().__init__(entry, runtime, key)
        self.role = role
        self._attr_translation_key = key
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = (
            SensorStateClass.TOTAL_INCREASING
            if role == "thermal"
            else SensorStateClass.TOTAL
        )

    @property
    def native_value(self) -> float:
        bucket = self.runtime.bucket("lifetime")
        if self.role == "thermal":
            return round(bucket.thermal, 3)
        return round(bucket.net_electrical, 3)


class HeatPumpInputDiagnosticsSensor(HeatPumpBaseSensor):
    """Diagnostic entity exposing source values and the latest input deltas."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:stethoscope"

    def __init__(self, entry: ConfigEntry, runtime: HeatPumpPerformanceRuntime) -> None:
        super().__init__(entry, runtime, "input_diagnostics")
        self._attr_translation_key = "input_diagnostics"

    @property
    def native_value(self) -> str:
        return self.runtime.state.quality

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_DATA_QUALITY: self.runtime.state.quality,
            "inputs": self.runtime.input_diagnostics(),
        }


class HeatPumpPreviousRatioSensor(HeatPumpBaseSensor):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry, runtime, period, key, name):
        super().__init__(entry, runtime, key)
        self.period = period
        self._attr_name = name

    @property
    def native_value(self):
        bucket = self.runtime.previous_bucket(self.period)
        return round(bucket.az, 3) if bucket and bucket.az is not None else None

    @property
    def extra_state_attributes(self):
        bucket = self.runtime.previous_bucket(self.period)
        return {} if bucket is None else {
            "period_start": bucket.start,
            "thermal_energy_kwh": round(bucket.thermal, 3),
            "net_electrical_energy_kwh": round(bucket.net_electrical, 3),
        }


class HeatPumpDailyEnergySensor(HeatPumpBaseSensor):
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(self, entry, runtime, role, key, name):
        super().__init__(entry, runtime, key)
        self.role = role
        self._attr_name = name

    @property
    def native_value(self):
        return round(getattr(self.runtime.bucket("daily"), self.role), 3)

    @property
    def extra_state_attributes(self):
        return {"period_start": self.runtime.bucket("daily").start}


class HeatPumpSpikeCountSensor(HeatPumpBaseSensor):
    _attr_state_class = SensorStateClass.TOTAL
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, runtime):
        super().__init__(entry, runtime, "spike_count")
        self._attr_name = "Verworfene WMZ-Spikes"
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self):
        return self.runtime.state.rejected_thermal_count

    @property
    def extra_state_attributes(self):
        return {"last_rejected": self.runtime.state.rejected_thermal_last}
