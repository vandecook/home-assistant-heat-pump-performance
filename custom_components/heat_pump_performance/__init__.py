"""Heat Pump Performance integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .runtime import HeatPumpPerformanceRuntime
from .repair import async_register_repair_services, async_register_reset_service


@dataclass
class HeatPumpPerformanceData:
    """Runtime data attached to a config entry."""

    runtime: HeatPumpPerformanceRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heat Pump Performance from a config entry."""
    if not hass.services.has_service(DOMAIN, "repair_thermal_balance"):
        await async_register_repair_services(hass)
    if not hass.services.has_service(DOMAIN, "reset_history"):
        await async_register_reset_service(hass)
    runtime = HeatPumpPerformanceRuntime(hass, entry)
    await runtime.async_setup()
    entry.runtime_data = HeatPumpPerformanceData(runtime=runtime)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data: HeatPumpPerformanceData = entry.runtime_data
        await data.runtime.async_shutdown()
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options/config change."""
    await hass.config_entries.async_reload(entry.entry_id)
