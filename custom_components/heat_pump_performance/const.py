"""Constants for Heat Pump Performance."""

from __future__ import annotations

DOMAIN = "heat_pump_performance"
PLATFORMS = ["sensor"]
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.state"

CONF_NAME = "name"
CONF_SYSTEM_TYPE = "system_type"
CONF_THERMAL_ENERGY = "thermal_energy"
CONF_THERMAL_ENERGY_UNIT = "thermal_energy_unit"
CONF_ELECTRICAL_ENERGY = "electrical_energy"
CONF_ELECTRICAL_ENERGY_UNIT = "electrical_energy_unit"
CONF_ELECTRICAL_POWER = "electrical_power"
CONF_ELECTRICAL_POWER_UNIT = "electrical_power_unit"
CONF_AUX_ENERGY = "aux_energy"
CONF_AUX_ENERGY_UNIT = "aux_energy_unit"
CONF_AUX_POWER = "aux_power"
CONF_AUX_POWER_UNIT = "aux_power_unit"
CONF_HEATING_ACTIVE = "heating_active"
CONF_DHW_ACTIVE = "dhw_active"
CONF_INITIAL_YEAR_MODE = "initial_year_mode"
CONF_INITIAL_YEAR_DATE = "initial_year_date"
CONF_HEATING_SEASON_ENABLED = "heating_season_enabled"
CONF_HEATING_SEASON_START = "heating_season_start"
CONF_HEATING_SEASON_END = "heating_season_end"

SYSTEM_HP = "hp"
SYSTEM_DHW_HP = "dhw_hp"
SYSTEM_TYPES = [SYSTEM_HP, SYSTEM_DHW_HP]

YEAR_FROM_NOW = "from_now"
YEAR_FULL = "full_year"
YEAR_PARTIAL = "partial_year"
YEAR_MODES = [YEAR_FROM_NOW, YEAR_FULL, YEAR_PARTIAL]

ENERGY_UNITS = ["Wh", "kWh", "MWh"]
POWER_UNITS = ["W", "kW"]

ATTR_PERIOD_START = "period_start"
ATTR_PERIOD_END = "period_end"
ATTR_YEAR_MODE = "year_mode"
ATTR_PARTIAL_YEAR = "partial_year"
ATTR_THERMAL_ENERGY_KWH = "thermal_energy_kwh"
ATTR_ELECTRICAL_ENERGY_KWH = "electrical_energy_kwh"
ATTR_AUX_ENERGY_KWH = "aux_energy_kwh"
ATTR_NET_ELECTRICAL_ENERGY_KWH = "net_electrical_energy_kwh"
ATTR_HEATING_ACTIVE = "heating_active"
ATTR_DHW_ACTIVE = "dhw_active"
ATTR_DATA_QUALITY = "data_quality"

QUALITY_OK = "ok"
QUALITY_WAITING = "waiting_for_energy_delta"
QUALITY_INVALID = "invalid"
QUALITY_HISTORY_UNAVAILABLE = "history_unavailable"

CONF_MAX_THERMAL_POWER = "max_thermal_power_kw"
CONF_THERMAL_SAFETY_FACTOR = "thermal_safety_factor"
CONF_THERMAL_RESOLUTION = "thermal_resolution_kwh"
