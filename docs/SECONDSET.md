Ranked by value to the rescue demo
1. Residential listing features: fire sources and safety equipment (high value, about half a day).
1,899 homes have structured features. Counts across all listings:

Feature	Listings
Heating system uses gas	1,164
Fire and smoke detector	743
Gas range / oven / cooktop	545 / 484 / 369
Fireplaces (gas-log 314, wood-burning 167)	—
Security gate / electric gate	172 / 139
"Sprinkler System"	222
How it would be used:

Gas cooking, fireplaces and gas heating become likely ignition sources. Presets could start fires in kitchens or fireplace rooms with a stated reason, instead of the current "far corner" rule.
Detectors could shorten the time until anyone notices the fire, where the scenario models that.
Security or electric gates become an egress note: the verified exit leads to a gate the robot can't open.
One caution: in MLS data, "Sprinkler System" usually means lawn irrigation, not fire sprinklers, so it must not count as fire protection. Each value would carry source (MLS vs a CoStar machine-learning model) into provenance.

2. Residential listings: house scenes with CoStar data (high value).
2,120 scanned homes. This opens the residential join path (ListingId → listing → media[].videoKey). It gives a true single-family house scene with bedrooms, stories and year built for the property card, plus the features above. Most of the work is picking and preparing a new scan, the same pipeline as today, and checking its exits.

3. Location occupancy + company detail: building use from real tenants (high value for commercial buildings).
74k tenancies and 49.6k tenant locations, with industry type, employee count and suite. This is the cleanest way to set the building-use profile automatically: a fitness or religious tenant means assembly, a restaurant means restaurant, and an office tenant means office. Today that comes only from the broad property type or the operator.

numEmployees per suite could drive a "workday load" preset, labelled exactly like capacity: researched headcount, not live occupancy.
toDate separates current tenants from past ones, and a null tenantLocationId is an unknown tenant, not "empty".
Caveat: our current property has no tenancy records, so this needs a different, commercial scan.
4. Commercial listing summary: vacant space (medium value).
1,022 for-lease and for-sale listings with space-level details. Space actively listed for lease is a reason to lower that suite's search priority, labelled "listed vacant per marketing data, not verified empty". Building condition and renovation year could also go on the property card.

5. Apartment listing, used further (small win).
Units available per floor plan show vacancy. That could scale the capacity presets, e.g. "8 marketed, community 12% available", but only as context. Asking rents add nothing to a rescue.

6. Commercial sale comps: skip. Prices and transaction terms don't change any rescue decision.

Beyond CoStar
Matterport furnished vs. defurnished panoramas: this could unblock furniture without the MatterPak. Both versions exist at the same scan positions. Comparing the furnished and defurnished panos marks where furniture is in each image, and the defurnished scan's geometry places it on the floor plan. That gives approximate furniture obstacles from real imagery. It's a bigger build (about 1–2 days) and only approximate, but it's the one route to real furniture that's open today.
Vexcel and the drone splat: exterior views for responders (approach, roof, staging). They're useful for the pitch, but registering the exterior to the interior isn't solved, so they belong after the interior work.
Recommendation
Residential listing features, as fire sources and egress notes. This is fast, works with a scene type we can add, and makes the fire scenarios evidence-based rather than arbitrary.
Tenancy-based building use, on one commercial scan with tenants. That makes the yoga-studio idea come from real data instead of a menu choice.
Furniture from furnished vs. defurnished panoramas, if furniture still matters before the sponsor replies.