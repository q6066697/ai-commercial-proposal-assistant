# Services Catalog (synthetic demo data)

The full list of services, units and rates is in `data/price_list.json`
(`services`). Work listed under `manual_estimate_only` is quoted only after a site
inspection.

Legacy ids from the v0.1 demo (still used by the extractor and the eval set) map onto
v0.3 services:

| Legacy key | v0.3 service | Unit |
|---|---|---|
| facade_cleaning | facade_washing | m2 |
| seam_sealing | joint_sealing | linear m |
| metal_structures_painting | — (not in v0.3, manual estimate) | m2 |

Each service requires a site survey for buildings above 30m or with irregular geometry
(atriums, cantilevers, decorative elements). Standard lead time is 5–10 business days
from contract signing, weather-dependent for exterior works.
