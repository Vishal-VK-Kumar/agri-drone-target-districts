# District alias notes

How district names in the source become stable geography, and why each non-obvious
decision was made. The mapping itself is `seeds/district_lineage.csv`: one row per
child–parent pair, with the event, the effective date, the scale of the transfer,
and a source for every row.

## What the data actually does

The UPAg district series (2013-14 to 2024-25) has 752 State–District names. 113 of
them appear after 2013-14. Three things are going on, and they need different fixes.

**1. New districts carved out of old ones (101 names).** The parent keeps its name and
shrinks; the child appears next to it. A naive `GROUP BY district` does not
double-count here. What it does is create a fake collapse in the parent at the split
year. Old Warangal, for example, falls from about 409k ha to about 102k ha on the main
field crops between 2015-16 and 2016-17. A `LAG` or moving-average query would report
that as a real decline.

**2. Coverage gaps, not new districts (12 names).** Nine Chhattisgarh districts that
have existed since 2000 or earlier (Raipur, Korba, Raigarh, Rajnandgaon, Mahasamund,
Jashpur, Kabeerdham, Korea, Uttar Bastar Kanker) are simply missing from the 2013-14
export. Kupwara, Mumbai Suburban and Mahe have gaps too. These map to themselves.
(Chennai also first appears in 2018-19, but that one is a real boundary change: the
district was enlarged in January 2018 with taluks from Thiruvallur and Kancheepuram.) Giving them a parent would be wrong. **Chhattisgarh's 2013-14 total is
short by 9 of its 27 districts**, so any year-on-year change from 2013-14 in that
state is an artefact.

**3. Current names applied to earlier years.** The source already uses today's names
for every year: Gangtok and Gyalshing for 2013-14 Sikkim (renamed 2021), Narmadapuram
(renamed 2022), Ahilyanagar, Chhatrapati Sambhajinagar and Dharashiv (2023), Sribhumi
(2024), Bengaluru South (2025). Pure renames are therefore already handled by the
source and need no alias rows. Boundaries are not handled: the name is current, but
the area behind it is whatever the district covered that year.

The awkward case is Warangal. Hanumakonda shows up from 2016-17, although the name
dates from 12 Aug 2021 (Telangana G.O.Ms.No.74). It is the 2016 Warangal Urban
district under its later name. "Warangal" in the data is really three different
territories over time: the undivided district up to 2015-16, then the former Warangal
Rural (renamed Warangal in 2021), with some mandals moving between the two in 2021.

## Why a lineage table and not a one-to-one alias

Many children have more than one parent. Siddipet was carved from Medak, Karimnagar and
Warangal. Jangaon came from Warangal and Nalgonda, Mahabubabad from Warangal and
Khammam, Vikarabad from Ranga Reddy and Mahabubnagar. In Andhra Pradesh, Tirupati came
from Chittoor and Nellore, Annamayya from Kadapa and Chittoor, and Eluru from West
Godavari and Krishna. A single-parent mapping would put all of Siddipet's area into
Medak and leave Karimnagar and Warangal with fake drops. The source reports area by
district, not by mandal, so a child's area cannot be split back across its parents.

## Stable units

Districts joined by a split, directly or through a chain (Leparada ← Lower Siang ←
East Siang + West Siang), are grouped into one **stable unit** (connected components
over `cluster_edge = 'Y'`). Time-series measures (LAG, moving average, year-on-year
change) run on stable units. Ranking on the latest complete year (2023-24) uses current
district names, because the boundaries within a single year are consistent.

Result: 752 names become 629 stable units (627 before the 23 Sep 2026 review set the two Chennai edges to N; see Edge rules). Most states barely change. The exceptions
are the cost of being correct:

- Telangana: 32 names become 4 units. The 2016 reorganisation cut across old
  boundaries so much that most of the state is one unit.
- Andhra Pradesh: 26 names become 5 units. The 2022 reorganisation moved whole revenue
  divisions between the old districts.

At this resolution, district-level trends in those two states can't be measured
across the reorganisation years. The analysis says so rather than hide it.

## Edge rules

- `cluster_edge = 'Y'` for splits, split-and-rename, and transfers between two
  districts that both still exist (for example Kovvur division moving from West
  Godavari to East Godavari in 2022).
- `cross_state_transfer` rows are recorded but not clustered. The 2014 transfer of the
  Polavaram mandals from Khammam (Telangana) to East and West Godavari (AP) is real,
  but joining two states into one unit would break every state-level rollup. The
  effect is a small step in Khammam between 2013-14 and 2014-15.
- `coverage_gap` rows map a district to itself and never create an edge.
- **Materiality (added 23 Sep 2026).** An edge is set to N only when the area that moved can be
  bounded from the data and the bound is under 1% of every district it touches. The row stays in the
  seed with its source, so the lineage check still passes. Only the Chennai rows meet this: Chennai
  reports at most 254 ha in any year, so at most about 0.2% of Thiruvallur or Kancheepuram moved.
  When the size cannot be bounded, the edge stays Y. A wrongly kept edge costs resolution; a wrongly
  dropped one puts a fake step into the tre## Open items (`needs_check = 'Y'`)

Reviewed 23 Sep 2026. Cleared with a citation:

- **East Godavari ← West Godavari** (Kovvur division, 2022). The official district portal lists
  the eight Kovvuru mandals in East Godavari. The transfer is also visible in size: in 2022-23 AP
  fell 11% statewide, while without this edge the East Godavari group is flat and the West Godavari
  group falls 19%, roughly 70-80k ha moving. Edge kept.
- **Sri Potti Sriramulu Nellore ← Prakasam** (Kandukur division, 2022). Confirmed (Census 2011
  handbook for Prakasam, Sakshi Post). Its size is lost in normal year-to-year swings of 10-16%, so
  under the materiality rule the edge stays. **Kandukur reverted to Prakasam from 31 Dec 2025**, and
  the same notification creates new districts (Polavaram, Markapuram) and moves the Gudur mandals from
  Tirupati to Nellore. None of this is in the current data, which ends at 2024-25. A 2025-26 export
  will add names and the lineage check will fail on them by design.
- **Chennai ← Thiruvallur, Kancheepuram** (2018). Parents confirmed (DT Next, 4 Jan 2018: 67
  villages from Tiruvallur, 55 from Kancheepuram). Edge set to N under the materiality rule. Tamil
  Nadu goes from 30 to 32 units and Thiruvallur can now be measured on its own.
- **Gaurela-Pendra-Marwahi ← Bilaspur** (10 Feb 2020). PTI: "carved out of Bilaspur".

Still open, and why they can wait:

- **Jayashankar Bhupalapally, Hanumakonda** (Telangana, 5 rows). No effect on units: the districts
  are already joined through other edges. Documentation only.
- **The eight Rajasthan districts and Mauganj** (10 rows). All first report in 2024-25, so they
  touch neither the ranking year (2023-24) nor the trend window. Beawar may also have Pali and
  Bhilwara tehsils. Resolve before any analysis uses 2024-25.
