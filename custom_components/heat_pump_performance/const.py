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
CONF_DEFROST_ACTIVE = "defrost_active"
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

# Self-calculated heat from flow and flow/return temperatures (v0.2.0).
CONF_FLOW = "flow_rate"
CONF_FLOW_UNIT = "flow_rate_unit"
CONF_SUPPLY_TEMP = "supply_temp"
CONF_RETURN_TEMP = "return_temp"
CONF_OUTDOOR_TEMP = "outdoor_temp"
CONF_THERMAL_MODE = "thermal_mode"
CONF_MIN_FLOW = "min_flow_m3h"
CONF_MAX_FLOW = "max_flow_m3h"
CONF_MAX_DELTA_T = "max_delta_t"
CONF_DEFROST_DELTA_T = "defrost_delta_t"
CONF_DEFROST_MIN_DURATION = "defrost_min_duration_s"
CONF_DEFROST_SOURCE = "defrost_source"
DEFROST_SOURCE_CALCULATED = "calculated"
DEFROST_SOURCE_HEAT_PUMP = "heat_pump"
DEFROST_SOURCES = [DEFROST_SOURCE_CALCULATED, DEFROST_SOURCE_HEAT_PUMP]
CONF_MAX_ELECTRICAL_POWER = "max_electrical_power_kw"
CONF_MAX_INPUT_AGE = "max_input_age_s"
CONF_REF_COP_COLD = "ref_cop_cold"
CONF_REF_COP_WARM = "ref_cop_warm"

# Which thermal energy feeds the COP:
#   meter               - the heat meter counter (behaviour of <= 0.1.14)
#   meter_minus_defrost - meter counter minus the self-calculated defrost loss
#   calculated          - fully self-calculated, signed (heat minus defrost loss)
MODE_METER = "meter"
MODE_METER_MINUS_DEFROST = "meter_minus_defrost"
MODE_CALCULATED = "calculated"
THERMAL_MODES = [MODE_METER, MODE_METER_MINUS_DEFROST, MODE_CALCULATED]

FLOW_UNITS = ["m³/h", "l/min", "l/h"]
