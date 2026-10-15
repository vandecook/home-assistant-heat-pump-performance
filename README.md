# Heat Pump Performance

**Current version: 0.1.14**

Repository: https://github.com/vandecook/home-assistant-heat-pump-performance

Issues: https://github.com/vandecook/home-assistant-heat-pump-performance/issues

Custom Home Assistant integration for calculating heat-pump working ratios from cumulative energy counters.

## Development stage 1

Required inputs:

- Thermal energy (cumulative)
- Electrical energy of the complete system (cumulative)

Optional inputs:

- Electrical power of the complete system
- Electrical energy of controls + residential ventilation
- Electrical power of controls + residential ventilation
- Heating-active flag
- Domestic-hot-water-active flag

Numeric inputs can use regular `sensor` entities as well as Home Assistant `input_number` helpers and `number` entities. They do **not** need a Home Assistant energy `device_class`, `state_class` or even a `unit_of_measurement`. The unit can be defined explicitly in the config flow. Operation flags remain unrestricted, so `binary_sensor`, `input_boolean`, `switch` and vendor-specific entities can be selected.

## Calculation

If an auxiliary energy counter is configured:

`AZ = thermal_energy_delta / (electrical_energy_delta - auxiliary_energy_delta)`

Otherwise:

`AZ = thermal_energy_delta / electrical_energy_delta`

All energy values are internally normalized to kWh.

## Output entities

For a system name `LWZ303` and system type `hp`:

- `sensor.lwz303_hp_cop`
- `sensor.lwz303_hp_cop_daily`
- `sensor.lwz303_hp_cop_monthly`
- `sensor.lwz303_hp_spf_yearly`
- `sensor.lwz303_hp_spf_heating_season` (optional)
- `sensor.lwz303_hp_spf_lifetime`
- `sensor.lwz303_hp_thermal_energy`
- `sensor.lwz303_hp_electrical_energy`

Home Assistant may preserve an existing entity_id after renaming. `unique_id` is based on the config-entry ID, not on the editable display prefix.

## Initial yearly start

This setting is asked only when a new config entry is created:

- Start from now
- Full year (January 1)
- Partial year from a selected date

For Full year / Partial year the integration attempts to read the source-counter states from Recorder history. If the required history is not available, it does **not** fabricate historical values: the yearly bucket starts at installation and reports `history_unavailable` as data quality.

From the following calendar year onward, the yearly calculation automatically starts on January 1.

## Immediate calculation for historical initial-year start

For `Full year` or `Partial year`, when Recorder contains valid source-counter values at the requested start, the yearly bucket is initialized immediately as `current counter - historical start counter` for thermal, electrical, and optional auxiliary energy. The yearly working ratio is therefore available immediately after setup; no additional live counter change is required. The diagnostic sensor exposes the selected baseline, current values, deltas, and backfill status.

## Persistence

Calculation state and reference values are persisted in Home Assistant storage per config entry. Home Assistant restarts and integration updates therefore do not reset the working-ratio periods.

Multiple config entries are supported.

## Notes

- Negative source-counter deltas are treated as counter resets and rebased rather than subtracted.
- The optional power inputs and operation flags are stored/configured for later development stages; stage 1 uses cumulative energy counters for the actual working-ratio calculation.
- `cop` in the entity ID is retained as a compact technical identifier. The current stage computes energy-based working ratios, not a high-resolution instantaneous COP.

## Editing input entities

From version 0.1.2 all source entities and their units can be changed from the integration's Options flow. The initial yearly-start selection remains immutable after first setup. If an energy source entity or its configured unit changes, the integration rebases the raw counter baseline at the current reading so the new absolute counter value is not counted as new energy. Existing accumulated period totals are kept.


## Input diagnostics

Version 0.1.3 adds `sensor.<prefix>_<system>_input_diagnostics` as a diagnostic entity.
Its attributes expose each configured input entity, raw current state, configured unit, normalized current value, latest normalized delta and update timestamp. Heating and DHW inputs expose their raw and interpreted boolean state. Energy values are normalized to kWh and power values to kW.


## Version 0.1.4

- Input diagnostics now expose `previous_value` in addition to the current normalized value and `delta`.
- Freshly initialized, valid energy inputs report `waiting_for_energy_delta` instead of `invalid` until each configured energy input has received a subsequent valid update.
- Diagnostic previous values and deltas are persisted across Home Assistant restarts.


## Version 0.1.5

- Numeric source selectors now accept `sensor`, `input_number`, and `number` entities.
- Heating/DHW operation flags remain unrestricted and therefore also support `input_boolean`.
- Unit handling and runtime numeric validation are unchanged; helper values are normalized exactly like sensor values.


## Version 0.1.6

- Fixed optional entity selectors in the options flow: empty optional inputs no longer pass `None` to Home Assistant's EntitySelector.
- Existing config entries containing legacy `None`/empty optional values are sanitized when the options flow opens.
- Optional `sensor`, `input_number`, `number`, operation-flag and auxiliary inputs can be left empty and the options can still be saved.


## v0.1.8

- All optional source entries can be removed again in the options flow.
- Clearing `electrical_power`, `aux_energy`, `aux_power`, `heating_active` or `dhw_active` removes the stored key instead of retaining the previous value.
- Units associated with removed optional numeric sources are removed as well.
- Disabling the optional heating-season calculation removes its stored start/end values.
- Required inputs (`thermal_energy`, `electrical_energy`) remain protected and cannot be cleared.


## v0.1.9

- Net electrical energy is accumulated independently per period.
- Existing <=0.1.8 state is migrated from `electrical - aux` on first load.
- Input diagnostics now expose internal lifetime/day/month/year/heating-season bucket totals, including net electrical energy.
- Source signatures are persisted so input-source changes rebase correctly across restarts.

## v0.1.11: Thermal counter spike protection

Configurable maximum thermal output (default 12 kW), safety factor (1.2), and meter quantization allowance (1 kWh). Each incoming thermal counter reading is checked against the last **accepted** counter value and its timestamp. Outliers (positive spikes or backwards jumps) are rejected without changing the accepted baseline or booking energy. Diagnostic attributes expose rejected count, last rejection and limits. The filter does not repair energy already accumulated by previous releases; backups/reconstruction are required.

After upgrading an existing installation, the time baseline is initialized at integration startup; the first 1-kWh step is permitted by the quantization allowance. Configure the actual rated thermal output for your heat pump.

## v0.1.12: thermal balance repair (manual, guarded)

A new Home Assistant action `heat_pump_performance.repair_thermal_balance` supports
**preview before apply** for a **known erroneous thermal energy increment**.
This is **not** an automatic Recorder-history rebuild: never estimate the correction
from a graph alone. Use a verified correction in kWh and the timestamp of the
bad reading. Required action parameters:

- `entry_id`: Config Entry ID for the affected heat pump
- `correction_kwh`: exact false positive thermal increment in kWh
- `affected_at`: timestamp of the erroneous reading with timezone
- `confirm`: `false` for preview, `true` to apply

The action returns a response (use Developer Tools > Actions), listing all
periods containing the event and before/after thermal energy and AZ. Applying
creates a timestamped backup of the integration's full persistent state under
`.storage/heat_pump_performance.repair_backup.*` before modifying any totals.
Electrical energy and counter baselines are unchanged. Periods that started
after the erroneous reading are not modified. Do not apply the same correction
twice. Historical Recorder states and statistics are not rewritten. A future
version can add automated Recorder reconstruction with gap validation.


## v0.1.13: Previous periods, daily energy, spikes, reset

New sensors: `cop_previous_day`, `cop_previous_month`, `thermal_energy_daily`, `electrical_energy_daily`, `spike_count`. Previous-period values are captured at rollover and persist across restarts. They are not retroactively reconstructed for earlier periods.

### Find the Config Entry ID (Template editor)

Home Assistant > Developer Tools > Template:

```jinja2
{{ config_entry_id('sensor.lwz303_hp_spf_lifetime') }}
```

Use the returned ID, **not** a device ID or sensor entity ID.

### Correct a known false thermal increment

Developer Tools > Actions > YAML mode:

```yaml
action: heat_pump_performance.repair_thermal_balance
data:
  entry_id: "YOUR_CONFIG_ENTRY_ID"
  affected_at: "2026-10-12T03:52:10+02:00"
  correction_kwh: 1000  # EXAMPLE ONLY: replace with verified error
  confirm: false
```

Inspect the returned preview. Only set `confirm: true` when the correction is verified. This modifies current internal balances, **not** Recorder state history or long-term statistics. Do not apply the same correction twice. A backup is saved before applying.

### Reset all calculated history for one instance

```yaml
action: heat_pump_performance.reset_history
data:
  entry_id: "YOUR_CONFIG_ENTRY_ID"
  reset_spikes: false
  confirm: false
```

Inspect preview, then run with `confirm: true`. Resets all internal period buckets, previous day/month, initial-year backfill, and rebases input counters at current readings. Set `reset_spikes: true` to also reset spike count. Config entries and entity IDs remain unchanged. A backup is saved. **Recorder history and statistics are not deleted or rewritten.** After reset, the yearly period is intentionally a partial year beginning at reset time; the following January starts a new year. The sensor history will show a new value, not an erased past.

**Safety:** back up Home Assistant before applying a repair or reset. A Home Assistant runtime integration test is still required.

## v0.1.14: Hassfest and HACS validation fixes

This release updates integration metadata and documentation only. **There are no changes to the energy calculations, spike filter, persisted balances, or entity IDs.**

- Added `custom_components/heat_pump_performance/services.yaml` with field descriptions for `repair_thermal_balance` and `reset_history`, as required by Hassfest for registered actions.
- Declared the optional Recorder integration initialization order via `after_dependencies: ["recorder"]` in `manifest.json`.
- Added `custom_components/heat_pump_performance/brand/icon.png` for HACS brand validation.
- Sorted `manifest.json` keys in Hassfest order: `domain`, `name`, followed by all remaining keys alphabetically.
- Updated documentation to match version `0.1.14`.

The release is intended to resolve the reported Hassfest `SERVICES`, `DEPENDENCIES`, and `MANIFEST` findings and the HACS brands check. **Final validation must still be confirmed by running the GitHub Actions workflow.**

The `actions/checkout@v4` Node.js deprecation warning and runner-image migration notices belong to the GitHub Actions environment and are separate from the integration changes.

## Maintenance actions

Find the config entry ID using **Developer Tools > Templates**:

```jinja2
{{ config_entry_id('sensor.lwz303_hp_spf_lifetime') }}
```

Use **Developer Tools > Actions** to preview a thermal correction:

```yaml
action: heat_pump_performance.repair_thermal_balance
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  affected_at: "2026-10-12T03:52:10+02:00"
  correction_kwh: 1000
  confirm: false
```

Only set `confirm: true` after checking the preview. The `1000` is an example, not a recommended correction.

Preview a reset of **internal** history (does not delete Home Assistant Recorder data):

```yaml
action: heat_pump_performance.reset_history
data:
  entry_id: YOUR_CONFIG_ENTRY_ID
  reset_spikes: false
  confirm: false
```

After reviewing the preview, set `confirm: true` to apply. The integration saves a backup of its state first.
