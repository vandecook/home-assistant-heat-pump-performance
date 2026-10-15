"""Safe thermal-balance repair services, scoped to one config entry."""
from __future__ import annotations

from datetime import datetime
import logging
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY_PREFIX

_LOGGER = logging.getLogger(__name__)
BUCKETS = ("lifetime", "daily", "monthly", "yearly", "heating_season")


def _preview(runtime, correction_kwh: float, affected_at: datetime) -> dict:
    """Return all affected balances; never mutate during preview."""
    result = {}
    for name in BUCKETS:
        bucket = runtime.bucket(name)
        if not bucket.start:
            continue
        start = datetime.fromisoformat(bucket.start)
        if start.tzinfo is None:
            raise ValueError(f"Missing timezone for {name} start")
        if affected_at < start or affected_at > dt_util.now():
            continue
        new_thermal = bucket.thermal - correction_kwh
        if new_thermal < -1e-6:
            raise ValueError(f"Correction exceeds {name} thermal balance")
        result[name] = {
            "before_kwh": round(bucket.thermal, 6),
            "after_kwh": round(max(0.0, new_thermal), 6),
            "before_az": bucket.az,
            "after_az": max(0.0, new_thermal) / bucket.net_electrical if bucket.net_electrical > 0 and new_thermal > 0 else None,
        }
    if not result:
        raise ValueError("No affected periods found")
    return result


async def async_register_repair_services(hass: HomeAssistant) -> None:
    """Register services once for all instances."""
    schema = vol.Schema({
        vol.Required("entry_id"): cv.string,
        vol.Required("correction_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.000001)),
        vol.Required("affected_at"): cv.datetime,
        vol.Optional("confirm", default=False): cv.boolean,
    })

    async def handle(call):
        entry_id = call.data["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if not entry or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            raise ValueError("Heat Pump Performance config entry not loaded")
        runtime = entry.runtime_data.runtime
        correction = call.data["correction_kwh"]
        affected_at = dt_util.as_local(call.data["affected_at"])
        preview = _preview(runtime, correction, affected_at)
        if not call.data["confirm"]:
            _LOGGER.warning("Heat Pump Performance repair PREVIEW for %s: %s", entry_id, preview)
            return {"applied": False, "preview": preview}
        # Store an immutable pre-repair snapshot before modifying any bucket.
        backup_key = f"{STORAGE_KEY_PREFIX}.repair_backup.{entry_id}.{dt_util.utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        backup = Store(hass, STORAGE_VERSION, backup_key)
        await backup.async_save(runtime.snapshot())
        for name, values in preview.items():
            runtime.bucket(name).thermal = values["after_kwh"]
        await runtime._save()
        runtime._notify()
        _LOGGER.warning("Heat Pump Performance repair APPLIED for %s; backup=%s; changes=%s", entry_id, backup_key, preview)
        return {"applied": True, "backup_key": backup_key, "changes": preview}

    hass.services.async_register(DOMAIN, "repair_thermal_balance", handle, schema=schema, supports_response=True)


async def async_register_reset_service(hass: HomeAssistant) -> None:
    """Register explicit two-phase reset for per-entry calculated history."""
    schema = vol.Schema({
        vol.Required("entry_id"): cv.string,
        vol.Optional("reset_spikes", default=False): cv.boolean,
        vol.Optional("confirm", default=False): cv.boolean,
    })

    async def handle(call):
        entry_id = call.data["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if not entry or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            raise ValueError("Heat Pump Performance config entry not loaded")
        runtime = entry.runtime_data.runtime
        preview = {
            key: {"thermal_kwh": runtime.bucket(key).thermal,
                  "net_electrical_kwh": runtime.bucket(key).net_electrical,
                  "working_ratio": runtime.bucket(key).az}
            for key in BUCKETS
        }
        preview["spike_count"] = runtime.state.rejected_thermal_count
        if not call.data["confirm"]:
            return {"applied": False, "preview": preview,
                    "recorder_history_untouched": True}
        backup_key = f"{STORAGE_KEY_PREFIX}.reset_backup.{entry_id}.{dt_util.utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        await Store(hass, STORAGE_VERSION, backup_key).async_save(runtime.snapshot())
        await runtime.async_reset_balances(call.data["reset_spikes"])
        return {"applied": True, "backup_key": backup_key,
                "recorder_history_untouched": True}

    hass.services.async_register(DOMAIN, "reset_history", handle,
                                 schema=schema, supports_response=True)
