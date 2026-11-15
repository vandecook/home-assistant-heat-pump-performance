# Defrost handling

[English](defrost.md) | [Deutsch](defrost.de.md) | [Back to the README](../README.md)

This page explains why defrost cycles are taken into account in the working ratio
and how the integration finds them. It applies when flow rate, supply and return
temperature are configured (see *Inputs* in the README).

## What happens during a defrost

At low outdoor temperatures the evaporator ices up. To melt the ice, the heat pump
reverses its cycle for a short time and takes heat **out of the heating water**.
The unit keeps running and uses electricity, but no heat reaches the house. On the
contrary, the heating system loses heat.

You can see it in the water temperatures: the **supply (flow, VL) temperature drops
below the return (RL) temperature**. The difference supply − return becomes
**negative**.

## How the negative difference is used

The thermal power is calculated from the flow and the temperature difference:

`P [kW] = flow [m³/h] × 1.163 × (supply − return) [K]`

Because the difference is negative during a defrost, the power is negative as well.
The integration deliberately keeps this sign and does **not** clip it to zero:

- Positive power is booked as delivered heat (`…_calc_thermal_energy`).
- Negative power is booked as **defrost loss** (`…_defrost_energy`).

The energy is integrated over time with the trapezoid rule. If the power changes
its sign between two samples, the interval is split exactly at the zero crossing,
so heat and loss are not mixed up.

## Detecting a defrost cycle

A cycle is counted (`…_defrost_count`) when the power stays below **−0.3 kW for at
least 20 seconds**. It ends when the power is back at 0 kW or above. Short dips
caused by sensor noise or a half-updated reading do not count as a cycle. The total
duration of all cycles is available as the attribute `duration_s_total`.

Note that the energy booking and the cycle detection are separate: every negative
power sample is booked as loss, while the counter only reacts to sustained dips.

## Why this matters for the working ratio

The electricity used during the defrost is always counted by the electrical energy
meter. If the negative heat were ignored, the COP would count the electricity but not
the heat the system lost, and it would come out **too high**. Many heat meters behave
exactly like this, because they do not count negative heat flow.

You choose in the thermal mode (options) what feeds the working ratio:

| Mode | Effect of the defrost |
| --- | --- |
| `meter` (default) | not considered, as with a plain heat meter |
| `meter_minus_defrost` | heat meter minus the calculated defrost loss |
| `calculated` | calculated delivered heat minus the defrost loss |

## Optional input: "Defrost active"

If your heat pump reports when it is defrosting (a `binary_sensor`, `switch`,
`input_boolean` or similar that is `on` during the defrost), you can select it as
**Defrost active** in the config or the options.

The signal is a **cross check only**. The energy is still booked from the sign of the
calculated power, so wrong or missing flags never change the working ratio. What the
integration does with it:

- Every flagged cycle is checked: did the water temperatures show the defrost
  (power below −0.3 kW while the flag was on, or within 2 minutes after it went
  off)? The result is counted as `confirmed_by_temperatures` or
  `not_seen_in_temperatures`.
- Cycles detected from the temperatures that start without the flag are counted as
  `detected_without_flag`.
- Everything is shown in the attribute `defrost.flag` of `…_plausibility` and kept
  across restarts. `reset_history` with `reset_spikes: true` clears it.
- With at least 5 cycles the plausibility entity warns when more than half of the
  flagged cycles are not seen in the temperatures
  (`defrost_flag_without_negative_power`) or when more than half of all cycles start
  without the flag (`negative_power_without_defrost_flag`).

A flag that is `on` all the time (for example an "ice monitoring" state that is not a
real defrost signal) produces many unconfirmed cycles and shows up as a warning. Check
what your entity really means before relying on it.

## What you see in Home Assistant

| Entity | Content |
| --- | --- |
| `…_defrost_count` | detected defrost cycles (attributes: `active`, `duration_s_total`) |
| `…_defrost_energy` | accumulated defrost loss in kWh |
| `…_plausibility` | attribute `defrost` with cycles, duration, loss and share of the heat |

The plausibility entity warns when the defrost loss exceeds **25 %** of the delivered
heat. That is a hint that a sensor is wrong, or that the unit defrosts unusually often.

## Limits

- The loss is only as good as the flow and temperature sensors. A supply sensor that
  reads lower than the return for other reasons (wrong placement, sensor offset)
  looks like a defrost.
- In the first minutes after start the value is small. The share is only judged from
  10 kWh of delivered heat.
- Whether manufacturer figures (data sheet COP) include defrost losses depends on the
  test procedure. Check the data sheet of your unit before comparing.
