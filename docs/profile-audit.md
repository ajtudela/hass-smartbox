# Controller profile audit

Source of truth: vendor mobile **2.54.2** (`new_vendors.decoded.js`, shared by the
SmartControl/Lucht LHZ APK). Method references below are the decoded controller
methods; the integration values are pinned by `tests/test_product_code_parity.py`
and the library's device-profile test.

## The max-stemp rule

`ElectricHeaterController.getUserDefinedTempLimits()` replaces `maxCTemp` with
`getMaxStempLimitInCelsius()` when that is non-zero, and
`getProgTempProperties()` then derives every profile from those limits:

| Profile | Floor | Ceiling |
|---|---|---|
| ICE / frost | controller minimum | `max - 2 * offset` |
| ECO | `min + offset` | `max - offset` |
| COMFORT | `min + 2 * offset` | `max` |

The same rule applies to the Haverland family (fixed frost 5-7, offset 2), the
ATC ICE override (5-10), and the PIR comfort/eco gap (3.5 °C). `htr_mod`
uses fixed eco/comfort ranges and no frost slider; the Elnur water storage has
no editable programme temperatures.

Live confirmation: a fw-1.9 `0402` relay heater with a 23 °C stored limit shows
frost 0-22, eco 0.5-22.5, comfort 1-23 in the app `[live 2026-11-23]`. The
controller itself is 0-30.

## Family table

| Controller (example PID) | Global range | Offset | Profiles | Max-limit rule |
|---|---|---|---|---|
| ElectricHeater base (`0402`) | 0-30 °C | 0.5 | frost/eco/comfort | replaces the ceiling |
| Helki/Lhz heater (`0132`) | 0-30 °C | 0.5 | + OFF | replaces the ceiling |
| Haverland (`0117`) | 5-30 °C, frost 5-7 | 2.0 | + OFF | eco/comfort ceilings follow the limit; frost stays 5-7 |
| Tevolve triac/towel/WiFi (`011A`) | 7-35 °C | 0.5 | base | controller does not expose the setting |
| ATC (`014C`) | 5-30 °C, frost 5-10 | 0.5 | base | replaces the ceiling |
| Tevolve relay (`011E`) | 0-35 °C | 0.5 | base | replaces the ceiling |
| PIR (`0127`) | 7-35 °C, comfort/eco gap 3.5 | 0.5 | base, ICE non-editable | controller does not expose the setting |
| Climastar WiFi (`013D`) | 0-30 °C | 0.5 | base | replaces the ceiling |
| Helki storage (`081C`) | setup min/max (5/30 fallback) | 0.5 | base; + OFF at fw 1.8 | setup range is the ceiling |
| Elnur storage (`0814`) | 3-30 °C | 0.5 | base | not exposed |
| Water storage (`082A`) | 3-30 °C | 0.5 | ON/OFF_AUTO, not modelled | not exposed |
| htr_mod (`010A`) | 5-28 °C | 1.0 | off-first; eco 10-18, comfort 18-28 | ignored |
| Thermostat (`0104`) | 7-35 °C | 0.5 | thresholds, not programme temps | not exposed |

## Findings and status

- **Fixed (2026-11-23):** programme slider bounds and the write sanitizer used
  the controller maximum, so a 23 °C limit still allowed 23-30 °C programme
  writes. The app clamps at the stored limit; the integration now computes
  programme bounds with `max_limit_c` and the entity/write paths pass the
  stored value. A side effect of the old bug: an out-of-range write made the
  controller echo adjusted profile values (frost at maximum and the other two
  raised), matching the live report.
- **Pinned:** `profile_bounds`/`prog_bounds_c` accept `max_limit_c`; the base
  case is 0-22 / 0.5-22.5 / 1-23 for a 23 °C limit. The Haverland, ATC, relay
  and Climastar formulas are asserted in the library audit test; the
  programme-number entity and `set_max_stemp_limit` correction are asserted in
  the integration.
- **Open:** the thermostat uses threshold temperatures rather than the
  programme catalogue; the water storage ON/OFF_AUTO catalogue is not modelled;
  storage/Tevolve families do not expose the max-stemp setting, so their
  max-limit branch is only reachable through a manually reported setup field.

## Reproduce

```bash
# library checkout
uv run smartbox profile --pid 0402 --node-type htr --fw-version 1.9 --max-limit-c 23
```

The `prog_bounds` block should read `ice_temp [0,22]`, `eco_temp [0.5,22.5]`,
`comf_temp [1,23]`.
