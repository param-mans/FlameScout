---
name: occupancy-hazards
description: Maps a building's use and tenant types (restaurant, e-bike or battery shop, pool or chemical supply, warehouse storage, auto repair, dry cleaner, residential over commercial, and so on) to the hazard flags a fire crew should hear about. Use it when the dossier lists tenants or an occupancy type and you are deciding which hazard considerations to draft.
---

# Occupancy hazards

Heuristic flags for decision support. They are not a code-compliance check and not operational
doctrine; thresholds and wording are placeholders a department would edit.

## How to use

1. Find the building use and every tenant in `dossier.json` (`facts["building.use"]`, `tenants`).
2. For each one, look it up in the table. No match: say nothing about it.
3. A flag is an inference from the tenant type, never an observation. Word it as "possible" or
   "likely", and cite the evidence id of the tenant or use record it comes from.
4. If the dossier already has a `hazard.*` fact for the same tenant, use that fact and its evidence
   instead of repeating the flag.
5. Never use the table to claim a quantity, a storage location inside the building, or a product
   name that the dossier does not state.

## Table

| Occupancy or tenant type | Flag to raise |
| --- | --- |
| Restaurant, commercial kitchen | Cooking and grease fire potential; grease ducts |
| E-bike, scooter, battery or electronics sales and repair | Lithium-ion batteries: re-ignition and toxic smoke potential |
| Pool or spa supply, cleaning chemicals | Oxidizers and corrosives: possible toxic gas when wet or mixed, keep runoff in mind |
| Auto repair or service | Flammable liquids, compressed gas cylinders |
| Salon or nail bar | Flammable solvents and aerosols |
| Dry cleaner, laboratory | Solvents |
| Warehouse, self-storage, furniture storage | High-piled or dense storage: heavy fire load, collapse of stacked goods |
| Gym, assembly hall, place of worship | High occupant load at peak times |
| Residential above commercial | Sleeping occupants above the fire floor, especially at night |

## Ranking hint

Flags that change how a crew approaches or which side they work from (toxic gas, re-ignition,
occupants above) usually outrank flags that only add fire load.
