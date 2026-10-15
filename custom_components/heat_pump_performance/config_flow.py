"""Config flow for Heat Pump Performance."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUX_ENERGY,
    CONF_AUX_ENERGY_UNIT,
    CONF_AUX_POWER,
    CONF_AUX_POWER_UNIT,
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
    CONF_NAME,
    CONF_SYSTEM_TYPE,
    CONF_THERMAL_ENERGY,
    CONF_THERMAL_ENERGY_UNIT,
    CONF_MAX_THERMAL_POWER,
    CONF_THERMAL_SAFETY_FACTOR,
    CONF_THERMAL_RESOLUTION,
    DOMAIN,
    ENERGY_UNITS,
    POWER_UNITS,
    SYSTEM_HP,
    SYSTEM_TYPES,
    YEAR_MODES,
    YEAR_PARTIAL,
)

_MM_DD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
_OPTIONAL_INPUT_KEYS = (
    CONF_ELECTRICAL_POWER,
    CONF_AUX_ENERGY,
    CONF_AUX_POWER,
    CONF_HEATING_ACTIVE,
    CONF_DHW_ACTIVE,
)
_OPTIONAL_UNIT_KEYS = (
    CONF_ELECTRICAL_POWER_UNIT,
    CONF_AUX_ENERGY_UNIT,
    CONF_AUX_POWER_UNIT,
)
_OPTIONAL_PERIOD_KEYS = (
    CONF_HEATING_SEASON_START,
    CONF_HEATING_SEASON_END,
)


def _set_optional_values(
    target: dict[str, Any],
    submitted: dict[str, Any],
    keys: tuple[str, ...],
) -> None:
    """Replace optional values, removing keys that were cleared in the form.

    Home Assistant selectors may represent a cleared optional field by omitting
    it completely, or by returning None/an empty string.  Rebuilding these
    fields rather than merging dictionaries ensures stale values never survive.
    """
    for key in keys:
        target.pop(key, None)
        value = submitted.pop(key, None)
        if value not in (None, ""):
            target[key] = value


def _entity_selector() -> selector.EntitySelector:
    # Do not filter by device_class/state_class. In addition to regular sensor
    # entities, Home Assistant numeric helpers (input_number) and number
    # entities can be used as sources. Runtime parsing remains authoritative.
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["sensor", "input_number", "number"],
            multiple=False,
        )
    )


def _operation_flag_selector() -> selector.EntitySelector:
    # Flags may be binary_sensors, switches or vendor-specific entities.
    return selector.EntitySelector(selector.EntitySelectorConfig(multiple=False))


class HeatPumpPerformanceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Collect system identity and input entities."""
        if user_input is not None:
            submitted = dict(user_input)
            _set_optional_values(self._data, submitted, _OPTIONAL_INPUT_KEYS)
            self._data.update(submitted)
            return await self.async_step_units()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="WP"): selector.TextSelector(),
                vol.Required(CONF_SYSTEM_TYPE, default=SYSTEM_HP): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SYSTEM_TYPES,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="system_type",
                    )
                ),
                vol.Required(CONF_THERMAL_ENERGY): _entity_selector(),
                vol.Required(CONF_ELECTRICAL_ENERGY): _entity_selector(),
                vol.Optional(CONF_ELECTRICAL_POWER): _entity_selector(),
                vol.Optional(CONF_AUX_ENERGY): _entity_selector(),
                vol.Optional(CONF_AUX_POWER): _entity_selector(),
                vol.Optional(CONF_HEATING_ACTIVE): _operation_flag_selector(),
                vol.Optional(CONF_DHW_ACTIVE): _operation_flag_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_units(self, user_input: dict[str, Any] | None = None):
        """Collect explicit units, independent of HA metadata."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_periods()

        schema_dict = self._unit_schema(self._data)
        return self.async_show_form(step_id="units", data_schema=vol.Schema(schema_dict))

    async def async_step_periods(self, user_input: dict[str, Any] | None = None):
        """Collect initial yearly period and optional heating season."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_INITIAL_YEAR_MODE] == YEAR_PARTIAL and not user_input.get(CONF_INITIAL_YEAR_DATE):
                errors[CONF_INITIAL_YEAR_DATE] = "date_required"
            _validate_heating_season(user_input, errors)
            if not errors:
                self._data.update(user_input)
                return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        schema = vol.Schema(
            {
                vol.Required(CONF_INITIAL_YEAR_MODE, default="from_now"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=YEAR_MODES,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="year_mode",
                    )
                ),
                vol.Optional(CONF_INITIAL_YEAR_DATE): selector.DateSelector(),
                vol.Required(CONF_MAX_THERMAL_POWER, default=12.0): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=1000)),
                vol.Required(CONF_THERMAL_SAFETY_FACTOR, default=1.2): vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
                vol.Required(CONF_THERMAL_RESOLUTION, default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Required(CONF_HEATING_SEASON_ENABLED, default=False): selector.BooleanSelector(),
                vol.Optional(CONF_HEATING_SEASON_START, default="10-01"): selector.TextSelector(),
                vol.Optional(CONF_HEATING_SEASON_END, default="04-30"): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="periods", data_schema=schema, errors=errors)

    def _unit_schema(self, data: dict[str, Any]) -> dict[Any, Any]:
        return _build_unit_schema(self.hass, data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return HeatPumpPerformanceOptionsFlow(config_entry)


class HeatPumpPerformanceOptionsFlow(config_entries.OptionsFlow):
    """Edit source entities and settings without changing the initial yearly start."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.data)
        # Older config entries may contain explicit None/empty values for optional
        # selectors. EntitySelector validates a provided default as an entity id,
        # so such values must be treated as truly unset.
        for key in (*_OPTIONAL_INPUT_KEYS, *_OPTIONAL_UNIT_KEYS):
            if self._data.get(key) in (None, ""):
                self._data.pop(key, None)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Edit system identity and source entities."""
        if user_input is not None:
            # Rebuild optional entity settings from the submitted form.  Home
            # Assistant may represent a cleared EntitySelector either by omitting
            # the key or by submitting None/"".  In both cases the previous
            # entity and its associated unit must be removed from the config.
            submitted = dict(user_input)
            _set_optional_values(self._data, submitted, _OPTIONAL_INPUT_KEYS)

            # Remove units belonging to optional inputs that are no longer set.
            optional_units = {
                CONF_ELECTRICAL_POWER: CONF_ELECTRICAL_POWER_UNIT,
                CONF_AUX_ENERGY: CONF_AUX_ENERGY_UNIT,
                CONF_AUX_POWER: CONF_AUX_POWER_UNIT,
            }
            for entity_key, unit_key in optional_units.items():
                if not self._data.get(entity_key):
                    self._data.pop(unit_key, None)

            self._data.update(submitted)
            return await self.async_step_units()

        data = self._data
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=data[CONF_NAME]): selector.TextSelector(),
            vol.Required(CONF_SYSTEM_TYPE, default=data.get(CONF_SYSTEM_TYPE, SYSTEM_HP)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=SYSTEM_TYPES,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="system_type",
                )
            ),
            vol.Required(CONF_THERMAL_ENERGY, default=data[CONF_THERMAL_ENERGY]): _entity_selector(),
            vol.Required(CONF_ELECTRICAL_ENERGY, default=data[CONF_ELECTRICAL_ENERGY]): _entity_selector(),
        }

        # IMPORTANT: Optional EntitySelectors must not receive default=None.
        # Home Assistant interprets that as a supplied entity value and rejects it
        # with "Entity None is neither a valid entity ID nor a valid UUID".
        optional_selectors = (
            (CONF_ELECTRICAL_POWER, _entity_selector()),
            (CONF_AUX_ENERGY, _entity_selector()),
            (CONF_AUX_POWER, _entity_selector()),
            (CONF_HEATING_ACTIVE, _operation_flag_selector()),
            (CONF_DHW_ACTIVE, _operation_flag_selector()),
        )
        for key, entity_selector in optional_selectors:
            current = data.get(key)
            marker = vol.Optional(key, default=current) if current not in (None, "") else vol.Optional(key)
            schema_dict[marker] = entity_selector

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))

    async def async_step_units(self, user_input: dict[str, Any] | None = None):
        """Edit units for the currently selected source entities."""
        if user_input is not None:
            for key in _OPTIONAL_UNIT_KEYS:
                self._data.pop(key, None)
            self._data.update(user_input)
            return await self.async_step_periods()

        return self.async_show_form(
            step_id="units",
            data_schema=vol.Schema(_build_unit_schema(self.hass, self._data, preserve_existing=True)),
        )

    async def async_step_periods(self, user_input: dict[str, Any] | None = None):
        """Edit the heating-season settings; initial yearly start stays immutable."""
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_heating_season(user_input, errors)
            if not errors:
                submitted = dict(user_input)
                enabled = bool(submitted.get(CONF_HEATING_SEASON_ENABLED, False))

                # Heating-season dates are optional configuration.  Disabling
                # the season must actually remove the old dates instead of
                # leaving stale values in the config entry.  If enabled, both
                # dates are conditionally required by validation above.
                _set_optional_values(self._data, submitted, _OPTIONAL_PERIOD_KEYS)
                if not enabled:
                    for key in _OPTIONAL_PERIOD_KEYS:
                        self._data.pop(key, None)

                self._data.update(submitted)
                # The initial yearly start/date are intentionally preserved from
                # the original config entry and are never re-asked here.
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    title=self._data[CONF_NAME],
                    data=self._data,
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HEATING_SEASON_ENABLED,
                    default=self._data.get(CONF_HEATING_SEASON_ENABLED, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_HEATING_SEASON_START,
                    default=self._data.get(CONF_HEATING_SEASON_START, "10-01"),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_HEATING_SEASON_END,
                    default=self._data.get(CONF_HEATING_SEASON_END, "04-30"),
                ): selector.TextSelector(),
            }
        )
        return self.async_show_form(step_id="periods", data_schema=schema, errors=errors)


def _build_unit_schema(
    hass,
    data: dict[str, Any],
    *,
    preserve_existing: bool = False,
) -> dict[Any, Any]:
    """Build unit selectors for the currently configured input entities."""

    def preferred(entity_key: str, unit_key: str, allowed: list[str], fallback: str) -> str:
        if preserve_existing and data.get(unit_key) in allowed:
            return data[unit_key]
        entity_id = data.get(entity_key)
        state = hass.states.get(entity_id) if entity_id else None
        if state:
            unit = state.attributes.get("unit_of_measurement")
            if unit in allowed:
                return unit
        if data.get(unit_key) in allowed:
            return data[unit_key]
        return fallback

    schema_dict: dict[Any, Any] = {
        vol.Required(
            CONF_THERMAL_ENERGY_UNIT,
            default=preferred(CONF_THERMAL_ENERGY, CONF_THERMAL_ENERGY_UNIT, ENERGY_UNITS, "kWh"),
        ): selector.SelectSelector(selector.SelectSelectorConfig(options=ENERGY_UNITS)),
        vol.Required(
            CONF_ELECTRICAL_ENERGY_UNIT,
            default=preferred(CONF_ELECTRICAL_ENERGY, CONF_ELECTRICAL_ENERGY_UNIT, ENERGY_UNITS, "kWh"),
        ): selector.SelectSelector(selector.SelectSelectorConfig(options=ENERGY_UNITS)),
    }
    if data.get(CONF_ELECTRICAL_POWER):
        schema_dict[
            vol.Required(
                CONF_ELECTRICAL_POWER_UNIT,
                default=preferred(CONF_ELECTRICAL_POWER, CONF_ELECTRICAL_POWER_UNIT, POWER_UNITS, "W"),
            )
        ] = selector.SelectSelector(selector.SelectSelectorConfig(options=POWER_UNITS))
    if data.get(CONF_AUX_ENERGY):
        schema_dict[
            vol.Required(
                CONF_AUX_ENERGY_UNIT,
                default=preferred(CONF_AUX_ENERGY, CONF_AUX_ENERGY_UNIT, ENERGY_UNITS, "kWh"),
            )
        ] = selector.SelectSelector(selector.SelectSelectorConfig(options=ENERGY_UNITS))
    if data.get(CONF_AUX_POWER):
        schema_dict[
            vol.Required(
                CONF_AUX_POWER_UNIT,
                default=preferred(CONF_AUX_POWER, CONF_AUX_POWER_UNIT, POWER_UNITS, "W"),
            )
        ] = selector.SelectSelector(selector.SelectSelectorConfig(options=POWER_UNITS))
    return schema_dict


def _validate_heating_season(data: dict[str, Any], errors: dict[str, str]) -> None:
    if not data.get(CONF_HEATING_SEASON_ENABLED):
        return
    for key in (CONF_HEATING_SEASON_START, CONF_HEATING_SEASON_END):
        value = data.get(key)
        if not value or not _MM_DD.match(value):
            errors[key] = "invalid_mm_dd"
