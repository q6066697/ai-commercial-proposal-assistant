# Pricing Rules (synthetic demo data)

Source of truth: `data/price_list.json` (v0.3, client-calibrated). The calculator
(`src/calculator.py`) hardcodes no prices or coefficients — everything below is read
from that file. Each rule carries `source`: `client` = confirmed by the client
(industrial climber, interview 03.11.2026), `market` = market rate, `assumption` =
demo assumption.

Order (`pricing_rules.application_order`):

```
works: rate × volume × height × complex access
  → + urgency (only if requested)
  → + materials as a separate line
  → minimum order
```

| Rule | Value | Source | How it is applied |
|---|---|---|---|
| `minimum_order` | 650 BYN | client | If works + materials < 650, the total is 650; the quote has a separate line and a sentence explaining it |
| `height_coefficients` | ≤ 15 m ×1.0, > 15 m ×1.2 | client (15 m boundary — assumption) | Floor → meters as floor × 3 m. Not applied to `cargo_lift` and `tree_removal`. Height unknown → ×1.2, and height goes to "уточнить у клиента" |
| `urgency` | +20% to works | client | Only if the request explicitly asks to speed up ("срочно", "в кратчайшие сроки"…); "не срочно" and tender deadlines don't count. Not applied to materials |
| `complex_access` | ×1.25 | assumption | When the request contains one of `triggers` (e.g. "над проезжей частью") |
| `dismantle_factor` | 0.6 | market | Dismantling a `mounting` service = mounting rate × 0.6 (`sign_dismantle` has its own rate) |
| `materials` | separate line | client | `material_rate` × volume, no height/urgency on top; "не требуются" line for services without materials |
| `weight_tiers_kg` (`cargo_lift`) | 180 / 200 / 300 / 400 / 800 per item | market | First tier with weight ≤ `max_kg`, × number of items. Over 500 kg → manual estimate |
| `winter_peak` | — | client | `snow_removal` / `icicle_removal` with "снегопад", "после снегопада", "завалило" → manual estimate; otherwise priced with a warning that peak prices are much higher |
| `manual_estimate_only` | list + `triggers` | client | Any request matching a trigger gets no price: "требуется осмотр объекта / ручной расчёт" |

Statuses of a calculation: `ok` (priced), `manual_estimate` (no number, by design),
`needs_clarification` (volume / cargo weight / service missing — no number, list of
fields to ask the client).

All figures are illustrative and used only to demonstrate the calculation engine in
this repository.
