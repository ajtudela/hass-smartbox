# Controller profile audit

Source of truth: vendor mobile **2.54.2** (`new_vendors.decoded.js`, shared by
the SmartControl/Lucht LHZ APK; sha256
`f4687447d9cff9bd9c04c4651d320501bb3e28020091b463ac177e153797ea96`,
5,856,496 bytes). The canonical wire record is `docs/api-contract.md` §1.9, §5.7 and §7 item 16; this page is the formula/audit summary. Values are pinned by the shared fixture and the library device tests.

## The max-stemp rule

`ElectricHeaterController.getUserDefinedTempLimits()` replaces `maxCTemp` with
`getMaxStempLimitInCelsius()` when that is non-zero, and
`getProgTempProperties()` then derives every profile from those limits
`[app mobile 2.54.2 new_vendors.decoded.js@3627000]`; the Haverland override
has its own fixed frost range `[app mobile 2.54.2 new_vendors.decoded.js@5338169]`:

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
- **Modelled:** the profile now carries a `programme_kind`: `profiles` for
  the heater catalogue, `htr_mod`, `thresholds` for thermostats, `on_off_auto`
  for water storage and `none` for power monitors. Thermostat threshold
  constants are heat eco/comfort 17/20 and cold eco/comfort 27/24 °C (the
  runtime `tempThresholds` override is 15/21.5 and 26/22.5); the integration
  does not expose threshold entities, so the profile reports no programme
  ranges. Water storage likewise reports `on_off_auto` with no ranges.
- **Max-limit branch for storage/Tevolve:** the setting is not exposed on those
  families, but the app's `getUserDefinedTempLimits` override applies whenever
  a node reports `max_stemp_limit`. The library clamps the programme ranges
  for those families too; tests cover storage (23 °C) and triac (30 °C) nodes.
- **Open:** the thermostat threshold values themselves are not exposed as
  entities; that is an intentional surface decision, not a profile gap.

## Reproduce

```bash
# library checkout
uv run smartbox profile --pid 0402 --node-type htr --fw-version 1.9 --max-limit-c 23
```

The `prog_bounds` block should read `ice_temp [0,22]`, `eco_temp [0.5,22.5]`,
`comf_temp [1,23]`.


## Where each fact lives

| App source | Library owner | Adapter consumer |
|---|---|---|
| `EProductCodeValue` enum | `device/codes.py: PRODUCT_CODES` (family, limits family, surface, label, flags) | `product.label`, `control_modes`, profile sets |
| controller factory switch | `device/codes.py` family column | `control_modes`, `profile` |
| `getActuationType` | `device/codes.py` actuation column | `control_modes` UI array |
| `getUserDefinedTempLimits`/`getProgTempProperties` | `device/profile.py` limits/bounds | climate, programme numbers, max-limit correction |
| `getUIHeatingControlModes`/wire arrays | `device/control_modes.py` | control-mode select |
| storage `getUIHeatingModes`/`write*` | `device/storage.py` | climate/select adapter |
| `getSettings`/capability gates | `device/settings.py` | entity builders |
| lock/boost methods | `device/modes.py` | lock/boost entities |
