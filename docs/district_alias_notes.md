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

Result: 752 names become 627 stable units. Most states barely change. The exceptions
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
- Parents come from notifications, official district sites, RBI lead-bank circulars or
  named press reports. None are inferred from the data. `source_grade` says which kind.

## Trend window

The trend window is 2014-15 to 2023-24. 2013-14 stays in the fact table, flagged, but
does not feed trend measures. It would add one year at the cost of a known hole: the
Chhattisgarh gap, plus four boundary events that fall between 2013-14 and 2014-15
(Gujarat's seven new districts, Palghar, Alipurduar, and the Khammam transfer).
2024-25 is incomplete (five states missing), which is why 2023-24 is the latest
complete year.

## Open items (`needs_check = 'Y'`)

Only three change a stable unit if they turn out wrong:

- **East Godavari ← West Godavari** (Kovvur division, 2022). This edge joins AP's north
  coast with West Godavari and Krishna.
- **Sri Potti Sriramulu Nellore ← Prakasam** (Kandukur division, 2022). This edge joins
  the Tirupati/Chittoor/Kadapa group with Guntur/Prakasam.
- **Chennai ← Thiruvallur, Kancheepuram** (2018 expansion). Encyclopedia source.

These don't change any unit, because the parents are already linked another way:
Jayashankar Bhupalapally, Hanumakonda (Telangana), and Gaurela-Pendra-Marwahi
(single parent, encyclopedia source).

These only affect 2024-25, which is outside the trend window: the eight Rajasthan
districts (parents from the March 2023 announcement; Beawar may also have Pali and
Bhilwara tehsils) and Mauganj (date approximate).
