# Heat Pump Performance

**English** | [Deutsch](README.de.md)

**Current version: 0.2.0**

Repository: https://github.com/vandecook/home-assistant-heat-pump-performance

Issues: https://github.com/vandecook/home-assistant-heat-pump-performance/issues

Custom Home Assistant integration that calculates heat-pump working ratios
(COP / SPF) from cumulative energy counters. The thermal energy can come from a
**heat meter** (the default) or be **calculated inside the integration** from
flow rate and supply/return temperature. Both paths run side by side and are
checked against each other.

No template sensors, helpers or utility meters are needed.

## Contents

- [Inputs](#inputs)
- [Thermal energy: heat meter or calculation](#thermal-energy-heat-meter-or-calculation)
- [Working ratios](#working-ratios)
- [Defrost](#defrost)
- [Plausibility checks and diagnostics](#plausibility-checks-and-diagnostics)
- [Entities](#entities)
- [Setup, periods and persistence](#setup-periods-and-persistence)
- [Installation](#installation)
- [Maintenance actions](#maintenance-actions)
- [Changelog](#changelog)

## Inputs

Numeric inputs can be `sensor`, `input_number` or `number` entities. They do
**not** need a Home Assistant energy `device_class`, `state_class` or even a
`unit_of_measurement`; the unit is chosen explicitly in the config flow. Operation
flags are unrestricted (`binary_sensor`, `input_boolean`, `switch`, vendor entities).
All input entities and units can be changed later in the options flow.

| Input | Required | Purpose |
| --- | --- | --- |
| Thermal energy (cumulative) | yes | heat meter counter; also used as cross-check |
| Electrical energy, complete system (cumulative) | yes | gross COP |
| Electrical energy, controls and ventilation (cumulative) | no | net COP (subtracted from the total) |
| Electrical power, complete system / controls | no | stored for later; the working ratio uses the energy counters |
| Heating active / domestic hot water active | no | split into heating and DHW |
| Flow rate (m³/h, l/min, l/h) | no* | calculated heat |
| Supply (flow) temperature | no* | calculated heat |
| Return temperature | no* | calculated heat |
| Outdoor temperature | no | only for the manufacturer reference COP |

\* Flow rate, supply and return temperature must be configured **together**.
Without them the integration behaves exactly like 0.1.14.

All energy values are normalized to kWh internally, power to kW.

## Thermal energy: heat meter or calculation

The heat meter counter is always kept and always filtered (spike protection, see
below). Once flow, supply and return are configured, the delivered heat is also
calculated:

`P [kW] = flow [m³/h] × 1.163 × (supply − return) [K]`

The power is signed and integrated over time (trapezoid rule, exact split at zero
crossings). Choose which energy feeds the working ratio:

| Mode | Thermal energy used |
| --- | --- |
| `meter` (default) | heat meter counter, as in 0.1.14 |
| `meter_minus_defrost` | heat meter minus the calculated defrost loss |
| `calculated` | fully calculated, delivered heat minus defrost loss |

Without the three hydraulic inputs the mode falls back to `meter`.

Why calculate at all? Many heat meters count only in whole kWh, ignore the
negative flow during defrost and occasionally report spikes. The calculation has a
much finer resolution and includes the defrost loss, so over longer periods it is
usually the more accurate figure. The comparison between both is shown in the
diagnostics, so you can decide on your own data. Recommended: start with `meter`,
watch the plausibility entity for a few days, then switch.

## Working ratios

- **Net** working ratio: effective thermal energy divided by the electrical
  energy *without* controls and ventilation (needs the auxiliary energy input).
  Without it, net equals gross.
- **Gross** COP (`cop_gross…`): the same thermal energy divided by the *complete*
  electrical energy. This is the value to compare with manufacturer figures
  (EN 14511).
- **Heating / DHW split** (`cop_heating`, `cop_dhw`, `spf_heating_yearly`,
  `spf_dhw_yearly`): each energy step is booked to the state of the *domestic hot
  water active* flag at that moment. Only with that flag configured.

Periods: current, day, month, year (optionally heating season) and lifetime.
Previous day and previous month are captured at rollover. Negative source-counter
deltas are treated as counter resets and rebased.

### Outdoor temperature and reference COP

The outdoor temperature is used only for `cop_reference`; it has no effect on
any calculated value. Prefer the heat pump's own outdoor sensor, because the
manufacturer figures refer to the air temperature at the unit. Use a weather
station only if the unit's sensor is slow, in the sun or affected by exhaust air,
and place it close to the unit in the shade.

The pre-filled reference values (COP 2.7 at -7 °C and 3.3 at 2 °C, both at 35 °C
flow) are taken from the LWZ 303 Integral data. Check them against the data sheet
of your own unit and change them in the options if they differ. They are a
comparison value only: measured values also contain defrost, part load and
domestic hot water, so they are not directly comparable to a test-bench figure.

## Defrost

During a defrost the heat pump takes heat out of the heating water, so supply
drops below return and the power becomes **negative**. This is real energy, not a
sensor error. It is booked separately as *defrost loss* instead of being clipped
to zero. A cycle is detected after the power stayed below −0.3 kW for at least
20 s.

## Plausibility checks and diagnostics

Every check is counted per reason and shown in the diagnostics.

| Check | Rejects / warns when |
| --- | --- |
| Missing input | flow, supply or return unavailable |
| Stale input | not reported for longer than the configured age (default 600 s, `0` disables) |
| Temperature range | outside −30 … 100 °C |
| Flow | negative, or above the maximum plausible flow (default 3 m³/h) |
| Spread | \|supply − return\| above the limit (default 25 K) |
| Power | \|power\| above maximum thermal power × safety factor |
| Idle flow | flow below the minimum (default 0.1 m³/h): booked as 0 kW, not as an error |
| Meter counter | decrease, or increase above what the hardware can deliver (spike filter) |
| Meter vs. calculation | a meter step far ahead of the calculated heat is rejected (only judged over windows without data gaps) |
| Meter deviation | warning above 10 % between meter and calculation (needs ≥ 10 kWh) |
| Defrost share | warning above 25 % of the delivered heat |
| COP range | warning when the lifetime gross COP is below 0.8 or above 8 (needs ≥ 2 kWh of electrical energy) |
| Consecutive rejects | error after 20 rejected samples in a row |

Rejected samples are never booked. A rejected sample or a gap longer than 5 minutes
breaks the integration chain; the missing interval is skipped, not extrapolated.
Flow, supply and return normally arrive as separate state changes, so they are
collected and evaluated once per second. The calculation is also evaluated every
30 s with the last known values, so a constant reading keeps integrating.

The spike filter for the heat meter uses a configurable maximum thermal output
(default 12 kW), a safety factor (1.2) and a meter resolution allowance (1 kWh).
Set the maximum to the real rated output of your heat pump.

**Diagnostic entities**

- `…_plausibility`: state `inactive`, `ok`, `warning` or `error`; all details
  (counters per reason, meter vs. calculation, defrost share, last rejection) are
  attributes.
- `…_input_diagnostics`: every configured input with raw state, unit, normalized
  value, previous value and delta, bucket totals per period, backfill status and a
  `calculation` block with effective thermal energy, calculated heat and defrost
  loss per period.

## Entities

For a system name `LWZ303` and system type `hp` (Home Assistant may keep an
existing entity ID after a rename; the `unique_id` is based on the config entry):

| Entity | Content |
| --- | --- |
| `sensor.lwz303_hp_cop`, `_daily`, `_monthly` | working ratio (net) |
| `sensor.lwz303_hp_spf_yearly`, `_heating_season`, `_lifetime` | seasonal / lifetime working ratio |
| `…_cop_previous_day`, `…_cop_previous_month` | values of the previous period |
| `…_cop_gross`, `_daily`, `_monthly`, `_yearly` | COP on the complete electrical energy |
| `…_cop_heating`, `…_cop_dhw`, `…_spf_heating_yearly`, `…_spf_dhw_yearly` | heating / DHW split |
| `…_thermal_energy`, `…_electrical_energy` and `_daily` variants | accumulated energy |
| `…_calc_thermal_power` | signed thermal power in kW |
| `…_calc_thermal_energy` | calculated delivered heat |
| `…_defrost_energy`, `…_defrost_count` | calculated defrost loss and detected cycles |
| `…_cop_reference` | manufacturer COP at the current outdoor temperature (valid for 35 °C flow) |
| `…_spike_count` | rejected heat meter spikes |
| `…_plausibility`, `…_input_diagnostics` | diagnostics (see above) |

Ratio sensors also expose attributes such as `effective_thermal_energy_kwh`,
`thermal_mode` and `gross_ratio`. `cop` in an entity ID is a compact technical
identifier; the sensors show energy-based working ratios, not an instantaneous COP.

## Setup, periods and persistence

The initial yearly start is asked only when a config entry is created:

- Start from now
- Full year (January 1)
- Partial year from a selected date

For the last two the integration reads the source-counter states from Recorder
history and initializes the yearly bucket immediately as *current counter −
historical start counter*. If the history is not available nothing is invented:
the year starts at installation and reports `history_unavailable`. A back-filled
initial year always uses the meter counter, because history contains only meter
values. From the next calendar year on, the year starts on January 1.

State is stored per config entry in Home Assistant storage, so restarts and updates
do not reset the periods. Older stored data is migrated automatically. Several
config entries are supported. If an energy source or its unit is changed in the
options, the counter baseline is rebased at the current reading; accumulated
totals are kept.

## Installation

Via HACS: add the repository as a custom repository (category *Integration*),
install it, restart Home Assistant and add **Heat Pump Performance** under
*Settings > Devices & services*.

HACS offers only releases (and the default branch), not feature branches. To test
a beta, publish a GitHub release marked as *pre-release*, switch on *Show beta
versions* for the integration in HACS and redownload that version. Back up Home
Assistant first and restart it afterwards.

## Maintenance actions

Both actions work in two steps: preview first, apply with `confirm: true`. A
timestamped backup of the integration state is saved under `.storage` before
anything is changed. Recorder history and long-term statistics are never rewritten.
Back up Home Assistant before applying.

Find the config entry ID in **Developer Tools > Template**:

```jinja2
{{ config_entry_id('sensor.lwz303_hp_spf_lifetime') }}
```

Use the returned ID, **not** a device ID or an entity ID.

**Correct a known false thermal increment** (verified value only, never estimated
from a graph; do not apply the same correction twice):

```yaml
action: heat_pump_performance.repair_thermal_balance
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  affected_at: "2026-10-12T03:52:10+02:00"
  correction_kwh: 1000  # example only
  confirm: false
```

The preview lists all affected periods with before/after thermal energy and working
ratio. The effective thermal energy is corrected too, except in the fully
calculated mode, where the false meter increment was never part of it.

**Reset the calculated history of one instance** (internal periods, previous
day/month, initial-year backfill; counters are rebased at the current readings):

```yaml
action: heat_pump_performance.reset_history
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  reset_spikes: false
  confirm: false
```

`reset_spikes: true` also clears the spike count, the plausibility statistics and
the defrost counter. After a reset the year is a partial year beginning at the
reset time.

## Changelog

- **0.2.0**: Self-calculated heat from flow and temperatures, signed power and
  defrost detection, thermal modes, plausibility checks with diagnostic entity,
  gross COP, heating/DHW split, manufacturer reference COP, outdoor temperature,
  extended config and options flow, stored state migration.
- **0.1.14**: Hassfest and HACS validation fixes (`services.yaml`,
  `after_dependencies`, brand icon, manifest key order). No calculation changes.
- **0.1.13**: Previous day/month, daily energy sensors, spike counter,
  `reset_history` action.
- **0.1.12**: Guarded `repair_thermal_balance` action (preview before apply).
- **0.1.11**: Heat meter spike protection.
- **0.1.9**: Net electrical energy accumulated per period; migration of older state.
- **0.1.8**: All optional inputs can be removed again in the options flow.
- **0.1.6**: Fixed optional selectors in the options flow.
- **0.1.5**: `input_number` and `number` entities accepted as numeric inputs.
- **0.1.4**: Input diagnostics show previous values and deltas; they persist
  across restarts.
- **0.1.3**: Input diagnostics entity.
- **0.1.2**: Source entities and units editable in the options flow.
