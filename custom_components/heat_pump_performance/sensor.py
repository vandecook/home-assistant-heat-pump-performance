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
    CONF_OUTDOOR_TEMP,
    CONF_REF_COP_COLD,
    CONF_REF_COP_WARM,
    CONF_SYSTEM_TYPE,
    DOMAIN,
    YEAR_FULL,
)
from .calc import METER_DEVIATION_MIN_KWH, meter_deviation_pct
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
            HeatPumpGrossRatioSensor(entry, runtime, "lifetime", "cop_gross", "Brutto-Arbeitszahl"),
            HeatPumpGrossRatioSensor(entry, runtime, "daily", "cop_gross_daily", "Brutto-Arbeitszahl täglich"),
            HeatPumpGrossRatioSensor(entry, runtime, "monthly", "cop_gross_monthly", "Brutto-Arbeitszahl monatlich"),
            HeatPumpGrossRatioSensor(entry, runtime, "yearly", "cop_gross_yearly", "Brutto-Jahresarbeitszahl"),
        ]
    )
    if entry.data.get(CONF_DHW_ACTIVE):
        entities.extend(
            [
                HeatPumpSplitRatioSensor(entry, runtime, "lifetime", "heating", "cop_heating", "Arbeitszahl Heizen"),
                HeatPumpSplitRatioSensor(entry, runtime, "lifetime", "dhw", "cop_dhw", "Arbeitszahl Warmwasser"),
                HeatPumpSplitRatioSensor(entry, runtime, "yearly", "heating", "spf_heating_yearly", "Jahresarbeitszahl Heizen"),
                HeatPumpSplitRatioSensor(entry, runtime, "yearly", "dhw", "spf_dhw_yearly", "Jahresarbeitszahl Warmwasser"),
            ]
        )
    if runtime.calc_enabled:
        entities.extend(
            [
                HeatPumpCalcPowerSensor(entry, runtime),
                HeatPumpCalcEnergySensor(entry, runtime, "calc_heat", "calc_thermal_energy", "Berechnete Wärmeenergie"),
                HeatPumpCalcEnergySensor(entry, runtime, "calc_loss", "defrost_energy", "Abtau-Energieverlust"),
                HeatPumpDailyEnergySensor(entry, runtime, "calc_heat", "calc_thermal_energy_daily", "Berechnete Wärmeenergie heute"),
                HeatPumpDeviationSensor(entry, runtime, "paired", "meter_deviation", "Abweichung Zähler/Berechnung"),
                HeatPumpDeviationSensor(entry, runtime, "daily", "meter_deviation_daily", "Abweichung Zähler/Berechnung heute"),
                HeatPumpDefrostCountSensor(entry, runtime),
                HeatPumpPlausibilitySensor(entry, runtime),
            ]
        )
        if entry.data.get(CONF_OUTDOOR_TEMP) and entry.data.get(CONF_REF_COP_COLD) and entry.data.get(CONF_REF_COP_WARM):
            entities.append(HeatPumpReferenceCopSensor(entry, runtime))
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
            "effective_thermal_energy_kwh": round(bucket.eff, 3),
            "thermal_mode": self.runtime.thermal_mode,
            "gross_ratio": None if bucket.cop_gross is None else round(bucket.cop_gross, 3),
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


class HeatPumpGrossRatioSensor(HeatPumpBaseSensor):
    """COP on the complete electrical energy (including controls and ventilation)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry, runtime, bucket, key, name):
        super().__init__(entry, runtime, key)
        self.bucket_name = bucket
        self._attr_name = name

    @property
    def native_value(self):
        value = self.runtime.bucket(self.bucket_name).cop_gross
        return round(value, 3) if value is not None else None

    @property
    def extra_state_attributes(self):
        bucket = self.runtime.bucket(self.bucket_name)
        return {
            "period_start": bucket.start,
            "effective_thermal_energy_kwh": round(bucket.eff, 3),
            "electrical_energy_kwh": round(bucket.electrical, 3),
        }


class HeatPumpSplitRatioSensor(HeatPumpBaseSensor):
    """Working ratio for space heating or domestic hot water only.

    The split follows the "domestic hot water active" flag at the time the
    energy was booked.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, entry, runtime, bucket, part, key, name):
        super().__init__(entry, runtime, key)
        self.bucket_name = bucket
        self.part = part
        self._attr_name = name

    @property
    def native_value(self):
        bucket = self.runtime.bucket(self.bucket_name)
        value = bucket.az_dhw if self.part == "dhw" else bucket.az_heating
        return round(value, 3) if value is not None else None

    @property
    def extra_state_attributes(self):
        bucket = self.runtime.bucket(self.bucket_name)
        if self.part == "dhw":
            thermal, electrical = bucket.eff_dhw, bucket.net_dhw
        else:
            thermal, electrical = bucket.eff - bucket.eff_dhw, bucket.net_electrical - bucket.net_dhw
        return {
            "period_start": bucket.start,
            "thermal_energy_kwh": round(thermal, 3),
            "net_electrical_energy_kwh": round(electrical, 3),
        }


class HeatPumpCalcPowerSensor(HeatPumpBaseSensor):
    """Signed thermal power from flow and flow/return temperature."""

    _attr_native_unit_of_measurement = "kW"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 2

    def __init__(self, entry, runtime):
        super().__init__(entry, runtime, "calc_thermal_power")
        self._attr_name = "Berechnete thermische Leistung"

    @property
    def native_value(self):
        value = self.runtime.state.calc_power_kw
        return round(value, 3) if value is not None else None

    @property
    def extra_state_attributes(self):
        return {"sample_status": self.runtime.state.calc_status}


class HeatPumpCalcEnergySensor(HeatPumpBaseSensor):
    """Lifetime energy booked by the self-calculation (heat or defrost loss)."""

    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 3

    def __init__(self, entry, runtime, attribute, key, name):
        super().__init__(entry, runtime, key)
        self.attribute = attribute
        self._attr_name = name

    @property
    def native_value(self):
        return round(getattr(self.runtime.bucket("lifetime"), self.attribute), 3)


class HeatPumpDeviationSensor(HeatPumpBaseSensor):
    """Deviation of the heat meter from the calculated heat in percent.

    ``paired``: only over the windows in which both values were booked for the
    same time span (lifetime, the figure used for the plausibility warning).
    ``daily``: today's meter energy against today's calculated heat.
    Positive means the meter shows more heat than the calculation. Unknown until
    at least METER_DEVIATION_MIN_KWH of calculated heat is available.
    """

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:scale-unbalanced"

    def __init__(self, entry, runtime, mode, key, name):
        super().__init__(entry, runtime, key)
        self.mode = mode
        self._attr_name = name

    @property
    def native_value(self):
        if self.mode == "daily":
            bucket = self.runtime.bucket("daily")
            value = meter_deviation_pct(bucket.thermal, bucket.calc_heat)
        else:
            state = self.runtime.state
            value = meter_deviation_pct(state.cmp_meter_kwh, state.cmp_calc_kwh)
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self):
        if self.mode == "daily":
            bucket = self.runtime.bucket("daily")
            return {
                "meter_kwh": round(bucket.thermal, 3),
                "calculated_kwh": round(bucket.calc_heat, 3),
                "period_start": bucket.start,
                "minimum_kwh": METER_DEVIATION_MIN_KWH,
            }
        state = self.runtime.state
        return {
            "meter_kwh": round(state.cmp_meter_kwh, 3),
            "calculated_kwh": round(state.cmp_calc_kwh, 3),
            "minimum_kwh": METER_DEVIATION_MIN_KWH,
        }


class HeatPumpDefrostCountSensor(HeatPumpBaseSensor):
    """Number of defrost cycles detected from sustained negative thermal power."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:snowflake-melt"

    def __init__(self, entry, runtime):
        super().__init__(entry, runtime, "defrost_count")
        self._attr_name = "Abtauvorgänge"

    @property
    def native_value(self):
        return self.runtime._defrost.count

    @property
    def extra_state_attributes(self):
        defrost = self.runtime._defrost
        return {
            "active": defrost.active,
            "duration_s_total": round(defrost.duration_s_total, 1),
        }


class HeatPumpPlausibilitySensor(HeatPumpBaseSensor):
    """Health of the self-calculation: inactive / ok / warning / error.

    All plausibility results are in the attributes: rejected samples per reason,
    rejected meter increments, meter-vs-calculation deviation, defrost share,
    input problems (missing / stale) and data gaps.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check"

    def __init__(self, entry, runtime):
        super().__init__(entry, runtime, "plausibility")
        self._attr_name = "Plausibilität"

    @property
    def native_value(self):
        return self.runtime.calc_diagnostics()["health"]

    @property
    def extra_state_attributes(self):
        return self.runtime.calc_diagnostics()


class HeatPumpReferenceCopSensor(HeatPumpBaseSensor):
    """Manufacturer COP at the current outdoor temperature, for comparison."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 2

    def __init__(self, entry, runtime):
        super().__init__(entry, runtime, "cop_reference")
        self._attr_name = "Referenz-Arbeitszahl (Hersteller)"

    @property
    def native_value(self):
        return self.runtime.reference_cop()

    @property
    def extra_state_attributes(self):
        return {"outdoor_temperature": self.runtime.state.calc_latest.get("outdoor")}
