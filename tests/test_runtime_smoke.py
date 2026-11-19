"""Smoke tests for the runtime with minimal Home Assistant stand-ins.

Home Assistant itself is not required: the few HA symbols the runtime imports are
replaced by tiny fakes. This exercises the real runtime code paths (self-
calculation, plausibility, buckets, persistence and migration).
"""

import asyncio
import importlib
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

PKG_DIR = pathlib.Path(__file__).parent.parent / "custom_components" / "heat_pump_performance"
TZ = timezone(timedelta(hours=1))


class FakeClock:
    def __init__(self):
        self.current = datetime(2026, 11, 15, 12, 0, 0, tzinfo=TZ)

    def now(self):
        return self.current

    def utcnow(self):
        return self.current.astimezone(timezone.utc)

    def as_utc(self, value):
        return value.astimezone(timezone.utc)

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class FakeState:
    def __init__(self, state, reported):
        self.state = state
        self.last_reported = reported
        self.last_updated = reported
        self.attributes = {}


class FakeStates:
    def __init__(self, clock):
        self.clock = clock
        self.items = {}

    def set(self, entity_id, value):
        self.items[entity_id] = FakeState(str(value), self.clock.now())

    def get(self, entity_id):
        return self.items.get(entity_id)


class FakeStore:
    saved = None

    def __init__(self, *args, **kwargs):
        pass

    async def async_load(self):
        return FakeStore.saved

    async def async_save(self, data):
        FakeStore.saved = data


@pytest.fixture()
def rt_module(monkeypatch):
    clock = FakeClock()
    ha = types.ModuleType("homeassistant")
    mods = {
        "homeassistant": ha,
        "homeassistant.config_entries": types.ModuleType("homeassistant.config_entries"),
        "homeassistant.const": types.ModuleType("homeassistant.const"),
        "homeassistant.core": types.ModuleType("homeassistant.core"),
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.event": types.ModuleType("homeassistant.helpers.event"),
        "homeassistant.helpers.storage": types.ModuleType("homeassistant.helpers.storage"),
        "homeassistant.util": types.ModuleType("homeassistant.util"),
    }
    mods["homeassistant.config_entries"].ConfigEntry = object
    mods["homeassistant.const"].__version__ = "2026.11.0"
    core = mods["homeassistant.core"]
    core.Event = core.HomeAssistant = core.State = object
    ev = mods["homeassistant.helpers.event"]
    ev.async_track_state_change_event = lambda hass, ents, cb: (lambda: None)
    ev.async_track_time_interval = lambda hass, cb, interval: (lambda: None)
    pending: list = []

    def _call_later(hass, delay, cb):
        pending.append(cb)
        return lambda: None

    ev.async_call_later = _call_later
    clock.pending = pending

    class _Store:
        def __class_getitem__(cls, item):
            return FakeStore

    mods["homeassistant.helpers.storage"].Store = _Store
    mods["homeassistant.util"].dt = clock
    ha.util = mods["homeassistant.util"]
    ha.util.dt = clock
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    # "homeassistant.util.dt" is imported with "from homeassistant.util import dt".
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", clock)

    pkg = types.ModuleType("hpp_pkg")
    pkg.__path__ = [str(PKG_DIR)]
    monkeypatch.setitem(sys.modules, "hpp_pkg", pkg)
    for name in list(sys.modules):
        if name.startswith("hpp_pkg."):
            monkeypatch.delitem(sys.modules, name)
    runtime = importlib.import_module("hpp_pkg.runtime")
    const = importlib.import_module("hpp_pkg.const")
    FakeStore.saved = None
    return runtime, const, clock


def make_runtime(rt_module, mode="calculated", extra=None):
    runtime, const, clock = rt_module
    states = FakeStates(clock)

    class Hass:
        pass

    hass = Hass()
    hass.states = states
    data = {
        const.CONF_NAME: "WP",
        const.CONF_THERMAL_ENERGY: "sensor.thermal",
        const.CONF_THERMAL_ENERGY_UNIT: "kWh",
        const.CONF_ELECTRICAL_ENERGY: "sensor.elec",
        const.CONF_ELECTRICAL_ENERGY_UNIT: "kWh",
        const.CONF_FLOW: "sensor.flow",
        const.CONF_FLOW_UNIT: "m³/h",
        const.CONF_SUPPLY_TEMP: "sensor.supply",
        const.CONF_RETURN_TEMP: "sensor.return",
        const.CONF_THERMAL_MODE: mode,
        const.CONF_INITIAL_YEAR_MODE: "from_now",
    }
    data.update(extra or {})
    entry = types.SimpleNamespace(data=data, entry_id="abc")
    FakeStore.saved = None  # every runtime starts from empty storage
    states.set("sensor.thermal", 100)
    states.set("sensor.elec", 50)
    states.set("sensor.flow", 0.7)
    states.set("sensor.supply", 30)
    states.set("sensor.return", 25)
    rt = runtime.HeatPumpPerformanceRuntime(hass, entry)
    asyncio.run(rt.async_setup())
    return rt, states, clock


def flush(rt, clock):
    """Run the debounced evaluation callbacks that are due."""
    while clock.pending:
        cb = clock.pending.pop(0)
        asyncio.run(cb(clock.now()))


def feed(rt, states, clock, seconds, step, flow=None, supply=None, ret=None):
    """Advance the clock in ``step`` seconds; every given input fires its own event.

    Like a real device payload, all events of one step arrive together and are
    evaluated once, one debounce interval later.
    """
    for _ in range(int(seconds / step)):
        clock.advance(step)
        for entity, value in (("sensor.flow", flow), ("sensor.supply", supply), ("sensor.return", ret)):
            if value is not None:
                states.set(entity, value)
                rt._on_calc_input(entity, states.get(entity), clock.now())
        clock.advance(1.0)  # debounce interval
        flush(rt, clock)


def test_heating_defrost_and_calculated_mode(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 600, 10, flow=0.7, supply=30, ret=25)
    life = rt.state.lifetime
    # 60 steps of 11 s (10 s + 1 s debounce); the first sample books nothing.
    expected = 0.7 * 1.163 * 5 * (59 * 11) / 3600
    assert life.calc_heat == pytest.approx(expected, rel=0.02)
    assert life.calc_loss == 0.0
    assert life.eff == pytest.approx(life.calc_heat)

    feed(rt, states, clock, 120, 10, flow=0.7, supply=14, ret=22)  # defrost
    assert life.calc_loss > 0
    assert rt._defrost.count == 1
    assert life.eff == pytest.approx(life.calc_heat - life.calc_loss)
    # Daily bucket got the same booking.
    assert rt.state.daily.calc_loss == pytest.approx(life.calc_loss)


def test_rejected_samples_are_counted_and_not_booked(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 60, 10, flow=0.7, supply=30, ret=25)
    before = rt.state.lifetime.calc_heat
    feed(rt, states, clock, 30, 10, supply=150)  # sensor glitch
    assert rt.state.calc_status == "temp_out_of_range"
    assert rt.state.sample_stats.rejected.get("temp_out_of_range") == 3
    assert rt.state.lifetime.calc_heat == before
    feed(rt, states, clock, 30, 10, supply=75, ret=25)  # delta T 50 K
    assert rt.state.sample_stats.rejected.get("delta_t_too_large") == 3
    diag = rt.calc_diagnostics()
    assert diag["samples"]["rejected"]["delta_t_too_large"] == 3
    assert diag["samples"]["rejected"]["temp_out_of_range"] == 3
    assert diag["last_sample_status"] == "delta_t_too_large"


def test_unavailable_input_breaks_chain(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 30, 10, flow=0.7, supply=30, ret=25)
    states.set("sensor.supply", "unavailable")
    clock.advance(10)
    rt._on_calc_input("sensor.supply", states.get("sensor.supply"), clock.now())
    assert rt.state.sample_stats.rejected.get("missing_input") == 1
    assert rt.state.calc_power_kw is None
    diag = rt.calc_diagnostics()
    assert diag["input_issues"]["supply"] == "missing"
    assert diag["health"] == "error"


def test_stale_input_detected_from_last_reported(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 30, 10, flow=0.7, supply=30, ret=25)
    clock.advance(1200)  # nothing reported for 20 min
    asyncio.run(rt._async_calc_tick(clock.now()))
    assert rt.state.calc_status == "stale_input"
    assert rt.calc_diagnostics()["input_issues"]


def test_constant_inputs_keep_integrating_via_tick(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 30, 10, flow=0.7, supply=30, ret=25)
    start = rt.state.lifetime.calc_heat
    for _ in range(10):  # 5 minutes with unchanged values, but re-reported
        clock.advance(30)
        states.set("sensor.flow", 0.7)
        states.set("sensor.supply", 30)
        states.set("sensor.return", 25)
        asyncio.run(rt._async_calc_tick(clock.now()))
    gained = rt.state.lifetime.calc_heat - start
    assert gained == pytest.approx(0.7 * 1.163 * 5 * 300 / 3600, rel=0.05)


def test_meter_mode_unchanged_and_meter_minus_defrost(rt_module):
    runtime, const, clock0 = rt_module
    rt, states, clock = make_runtime(rt_module, "meter")
    event = types.SimpleNamespace(data={"entity_id": "sensor.thermal", "new_state": None})

    async def meter_step(value):
        states.set("sensor.thermal", value)
        event.data["new_state"] = states.get("sensor.thermal")
        await rt._async_state_changed(event)

    clock.advance(3600)
    asyncio.run(meter_step(101))
    assert rt.state.lifetime.thermal == pytest.approx(1.0)
    assert rt.state.lifetime.eff == pytest.approx(1.0)

    rt2, states2, clock2 = make_runtime(rt_module, "meter_minus_defrost")
    feed(rt2, states2, clock2, 120, 10, flow=0.7, supply=14, ret=22)
    loss = rt2.state.lifetime.calc_loss
    assert loss > 0 and rt2.state.lifetime.eff == pytest.approx(-loss)


def test_implausible_meter_jump_is_rejected(rt_module):
    rt, states, clock = make_runtime(rt_module, "meter")
    event = types.SimpleNamespace(data={"entity_id": "sensor.thermal", "new_state": None})
    clock.advance(60)
    states.set("sensor.thermal", 755_697_303)
    event.data["new_state"] = states.get("sensor.thermal")
    asyncio.run(rt._async_state_changed(event))
    assert rt.state.rejected_thermal_count == 1
    assert rt.state.rejected_thermal_last["reason"] == "excessive_energy_delta"
    assert rt.state.increment_stats.rejected == {"excessive_energy_delta": 1}
    assert rt.state.lifetime.thermal == 0.0


def test_snapshot_roundtrip_keeps_new_fields(rt_module):
    rt, states, clock = make_runtime(rt_module, "calculated")
    feed(rt, states, clock, 300, 10, flow=0.7, supply=30, ret=25)
    feed(rt, states, clock, 120, 10, supply=14, ret=22)
    feed(rt, states, clock, 30, 10, supply=150)
    snap = rt.snapshot()

    rt2, _, _ = make_runtime(rt_module, "calculated")
    rt2._restore(snap)
    a, b = rt.state.lifetime, rt2.state.lifetime
    for field in ("calc_heat", "calc_loss", "eff", "eff_dhw", "net_dhw"):
        assert getattr(b, field) == pytest.approx(getattr(a, field))
    assert rt2._defrost.count == rt._defrost.count == 1
    assert rt2.state.sample_stats.rejected == rt.state.sample_stats.rejected
    assert rt2.state.sample_stats.accepted == rt.state.sample_stats.accepted


def test_old_storage_is_migrated_with_eff_equal_to_thermal(rt_module):
    rt, states, clock = make_runtime(rt_module, "meter")
    old = {
        "lifetime": {"thermal": 1180.0, "electrical": 337.92, "aux": 88.63,
                     "net_electrical_accum": 249.29, "start": "2026-08-13T13:34:32+02:00"},
        "daily": {"thermal": 33.0, "electrical": 8.66, "aux": 1.32, "net_electrical_accum": 7.34},
        "previous_day": {"thermal": 40.0, "electrical": 10.0, "aux": 2.0, "net_electrical_accum": 8.0},
    }
    rt._restore(old)
    assert rt.state.lifetime.eff == 1180.0
    assert rt.state.lifetime.az == pytest.approx(1180.0 / 249.29)
    assert rt.state.lifetime.cop_gross == pytest.approx(1180.0 / 337.92)
    prev = rt.previous_bucket("daily")
    assert prev.eff == 40.0 and prev.az == pytest.approx(5.0)
    assert rt.state.sample_stats.accepted == 0  # no calc block in old storage


def test_dhw_split(rt_module):
    rt, states, clock = make_runtime(
        rt_module, "calculated", {"dhw_active": "binary_sensor.dhw"}
    )
    states.set("binary_sensor.dhw", "on")
    feed(rt, states, clock, 300, 10, flow=0.7, supply=45, ret=40)
    life = rt.state.lifetime
    assert life.eff_dhw == pytest.approx(life.eff) and life.eff > 0
    states.set("binary_sensor.dhw", "off")
    before = life.eff_dhw
    feed(rt, states, clock, 300, 10, supply=30, ret=25)
    assert life.eff_dhw == pytest.approx(before)
    assert life.eff > before


def test_calc_disabled_without_inputs(rt_module):
    runtime, const, clock = rt_module
    rt, states, clock = make_runtime(rt_module, "calculated", {const.CONF_FLOW: None})
    rt.data = {k: v for k, v in rt.data.items() if v is not None}
    rt._calc_roles = rt._build_calc_roles()
    assert not rt.calc_enabled
    assert rt.thermal_mode == "meter"  # falls back safely
    assert rt.calc_diagnostics()["health"] == "inactive"


def test_diagnostics_contain_versions_and_config(rt_module):
    rt, _states, _clock = make_runtime(rt_module)
    diag = rt.calc_diagnostics()
    assert diag["integration_version"] not in ("", "unknown")
    assert diag["ha_version"] == "2026.11.0"
    assert diag["config"]["defrost"]["delta_t_k"] == 0.5
    assert "entities" in diag["config"] and "limits" in diag["config"]
