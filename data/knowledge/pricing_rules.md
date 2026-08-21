# Pricing Rules (synthetic demo data)

Pricing = `base_price * area_or_length * height_coefficient * urgency_surcharge`,
with a minimum order value applied afterward.

- **Height coefficient** scales with the tallest point of the work area — higher
  elevation means longer rope descents, more anchoring points, and slower work rate.
- **Urgency surcharge** applies when the client needs mobilization within a week
  instead of the standard 2–4 week lead time.
- **Minimum order** protects against unprofitable small jobs where mobilization cost
  (equipment, certified technician travel) exceeds the calculated price.

All figures in this file are illustrative and used only to demonstrate the calculation
engine in this repository.
