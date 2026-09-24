# Spray passes notes

How sown hectares become sprayable acres, and why each number in
`seeds/crop_spray_passes.csv` is what it is. This is the weakest assumption in the
project. Every value is here so it can be challenged.

## What was looked for, and what exists

No national, crop-level count of how many times Indian farmers spray per season was
found. National pesticide statistics are in tonnes of technical grade by state, not
applications by crop. What exists is:

- **Small farmer surveys**, one district at a time. One Rajasthan survey of 500
  farmers (Alwar, 2016-18) covers cotton, wheat, mustard and bajra together, which
  makes their relative order comparable. One Andhra Pradesh survey of 41 paddy
  farmers gives rice.
- **Official recommended schedules** from ICAR institutes and the Directorate of
  Plant Protection (DPPQ&S / NIPHM IPM packages). These say what to spray and when,
  but most sprays are conditional on a pest threshold, so they give a range, not a
  count.

So every row carries a low, base and high value, a basis (`observed_survey` or
`recommended_schedule`), and a grade:

| Grade | Meaning |
|---|---|
| A | National or multi-state observed data. Rice, since 24 Sep 2026 (CSISA survey). |
| B | A survey of 100+ farmers, or an explicit count in an official ICAR / DPPQ&S schedule |
| C | A survey under 100 farmers, a partial figure, or a count that needed judgement |

`needs_check = Y` marks rows to review before M4, where the ranking is produced.

## Two kinds of pass, kept separate

1. **Plant-protection passes** (herbicide, insecticide, fungicide), per crop, from the
   seed.
2. **Nutrient passes.** The Namo Drone Didi scheme gives drones through Lead
   Fertiliser Companies "for application of liquid fertilizers and pesticides"
   (PIB, 1 Nov 2024, PRID 2070029). IFFCO's nano urea guide recommends **2 foliar
   sprays** per crop and says the number "can be increased or decreased depending
   upon crop" (https://nanourea.in/en/application). This is a manufacturer's
   recommendation, uniform across crops, and lives in `config/assumptions.yml`
   rather than the seed.

They are materialised as separate columns so the Power BI layer can switch nutrient
passes on and off. Adding them is what the scheme assumes; leaving them out is the
conservative case.

## Row by row, the ones that move the answer

**Rice (A). Re-based 24 Sep 2026 on the CSISA 2018 survey, with state-level figures.** The
CSISA Landscape Diagnostic Survey covers 8,355 farmers' largest rice plot in kharif 2018 across
eight states (Andhra Pradesh, Bihar, Chhattisgarh, Haryana, Odisha, Punjab, Uttar Pradesh, West
Bengal), which hold 65% of India's rice area. It counts herbicide applications and records
insecticide and fungicide use as yes or no. One rule turns that into passes, per plot:

| Value | Rule | Why |
|---|---|---|
| Low | herbicide count + 1 per yes | the observed floor |
| Base | herbicide count + 1.5 per yes | between one spray and a pair |
| High | herbicide count + 2 per yes | PAU's schedule gives fungicide sprays in pairs 15 days apart |

State results (low / base / high): Bihar 0.39 / 0.47 / 0.54, Uttar Pradesh 0.88 / 1.00 / 1.12,
Odisha 1.34 / 1.84 / 2.35, Haryana 1.58 / 1.96 / 2.35, West Bengal 1.77 / 2.38 / 2.99, Andhra
Pradesh 2.33 / 3.19 / 4.05, Chhattisgarh 2.58 / 3.37 / 4.15, Punjab 2.98 / 3.96 / 4.95. These are
in `seeds/crop_state_spray_passes.csv` and override the national row for rice in those states.

Two cross-checks agree with the rule. Andhra Pradesh's high (4.05) sits next to the Palnadu
survey's 4.32. Punjab's base (3.96) is what PAU's schedule gives when the herbicide, one
insecticide and a fungicide pair are all applied.

**Why state level.** The spread between states is bigger than the spread between most crops. A
single national figure would rank Bihar and Uttar Pradesh rice districts too high and Punjab and
Andhra Pradesh too low, and the finding is a district ranking.

**National row** (the other 35% of rice area): low 1.66 and base 2.17 are the state figures
weighted by 2018-19 rice area. High stays at 4 from the Palnadu survey, because the unsurveyed
states include heavy-spraying southern rice states (Telangana, Tamil Nadu, Karnataka).

The survey is kharif 2018 only; its state figures are applied to every rice season in that
state. The dataset carries no licence, so only these derived figures are used; the data is not
redistributed or committed.

**Cotton (B).** In the Alwar survey, 52% of cotton growers apply three times, 41% twice and
6% not at all: a mean of 2.40, which is the base (it was the modal 3 until 24 Sep 2026). Low
stays 2. The Karnataka Bt cotton study (Sagar et al. 2013) gives 1 to 3 in most
districts and up to 4 to 5 in Raichur and Yadgir, which sets the high.

**Wheat, bajra (B).** Same Alwar survey, base on the mean over all farmers since 24 Sep 2026.
Wheat: 37% once, 27% twice, 2% three times, 34% none, a mean of 0.97 (low and base; was 1 / 1).
Bajra: 39% once, 13% twice, 47% none, a mean of 0.66 (base; was 1, the modal answer among
farmers who spray). Low stays 0.

**Mustard (B, cleared 24 Sep 2026).** Earlier this row read the Alwar survey as partial.
It is not: Table 3 gives the whole distribution, 12.6% of 500 farmers sprayed once and
87.4% did not spray at all. The mean is 0.13 passes a season, and low and base are set to
that mean, the same rule as rice (a mean over all farmers, non-sprayers counted as zero).
High stays at 2, DRMR's threshold spray plus its 15-day repeat in a heavy aphid year. This
cuts mustard's base from 1 to 0.13. The risk is that it rests on one tehsil in 2016-18.

**Soybean (B).** Counted from ICAR-NSRI's 2023 bulletin: one post-emergence herbicide
(the 10-12 and 15-20 DAS options are alternatives, not two sprays), one insecticide
before flowering, one against defoliators at flowering. The two fungicide sprays for
rust are conditional and only in the high.

**Maize (B, left open 24 Sep 2026).** ICAR-IIMR caps chemical sprays at two for the whole crop.
Biological and neem sprays are threshold-triggered on top. No Indian survey with a spray
count was found. The base sits at the top of IIMR's cap, and one field study points lower:
Pal et al. 2024 (Scientific Reports; rabi maize, 10 districts of West Bengal and Bihar,
2021-22) found that 18-71% of farmers at a location applied any insecticide. Eastern maize
districts are probably overstated at base; the low case (1) is the better read there.

**Gram (B, left open 24 Sep 2026).** Pod borer sprays at flowering and podding, threshold
triggered. The NCIPM package recommends HaNPV as three weekly sprays. No crop-level survey
was found; the one Karnataka Helicoverpa survey (Honnakerappa & Udikeri 2018, 20 farmers per
district) does not split sprays by crop.

**Groundnut (B).** The DPPQ&S package gives an explicit "two spray ... at 15 days
interval" for leaf spot and rust. Insecticides are need-based and only in the high.
Herbicide is pre-emergence, so not a foliar pass.

**Sugarcane (C, cleared 24 Sep 2026).** Values unchanged (1 / 1 / 3); the reasoning is
corrected. The earlier note said the package's pest control is almost all soil, sett or
parasitoid based. That understated it: the package also lists foliar insecticide sprays for
shoot borers, pyrilla and stalk borers, all conditional. Base is one herbicide pass (the
package's herbicides are alternatives, at sowing or metsulfuron methyl at tillering). High
adds two of the conditional insecticide sprays. ICAR-SBI's weed schedule (pre-emergence
atrazine, then a directed spray at 90 DAP) adds no broadcast foliar pass. The source records
sugarcane under Kharif only although the crop stands 10 to 12 months; that matters for the
spray window in M4, not here.

**Moong (C, sourced 24 Sep 2026, left open).** From the NIPHM / DPPQ&S package for blackgram
and greengram (2014): low 1 is the pod borer spray at pod development; base 2 adds one
post-emergence grass herbicide at 15-20 DAS, which the package ranks behind mechanical weeding;
high 4 adds the conditional whitefly and powdery mildew sprays. No survey was found, and the
count needed judgement, so the row stays open.

**One rule for observed rows (agreed 24 Sep 2026).** Every `observed_survey` base is a mean over
all farmers, with non-sprayers counted as zero, because the model multiplies area by passes and
only a mean gives the right total. Rice, mustard, cotton, wheat and bajra all follow it. Until
then the Alwar rows used the modal answer, which overstated cotton by a quarter and bajra by half.

## Coverage

Checked against the 2023-24 rows of the raw export (field crops, summary rows
excluded, 183.4 million ha):

- The eleven in-scope crops cover **86.2%** of area.
- All eleven have a pass count since 24 Sep 2026, when moong was sourced, so **86.2%**
  (83.4% before).
- The other nineteen crops (jowar, tur, urad, guarseed and smaller) are
  `in_scope = N`. They are about 14% of area. Every crop in the data appears in the
  seed, so a new crop name in a future export fails a check instead of vanishing.

## Seasons

`seeds/season_calendar.csv` gives month spans from the Government of India's
Arthapedia: Kharif July to October, Rabi October to March, Summer March to June,
agricultural year July to June. Rabi wraps the calendar year. The spray window in
days, which is what caps a single drone's capacity, is an M4 question and is not
set here.

**The source relabels seasons, mostly in 2023-24 (found 24 Sep 2026).** Eastern winter rice,
sown in the monsoon and harvested around November, is labelled Rabi up to 2022-23 and Kharif
from 2023-24: Bihar, Odisha, West Bengal, Assam, Tripura, Manipur and Meghalaya, about 10 million
ha. Across all crops, 27 state-crop-years move more than half of a crop's area from one season
label to another; Jharkhand rice flips back and forth. District totals are unaffected. Season-level
trends are not: each relabel looks like a collapse in one season and a jump in another.

**Rule for M4 (agreed 24 Sep 2026):** year-on-year and moving-average measures run on all-season
totals only. Season is used only within the 2023-24 ranking year, where the labels match the
agronomy.

## What an interviewer will ask

"Where do the rice spray numbers come from?" A survey of 8,355 farmers in eight states,
with one stated rule for turning yes/no answers into sprays, applied state by state: from
under one a season in Bihar to about four in Punjab. The dashboard shows the ranking at the
low and high ends of that rule. The first version used four from a single district,
and checking it against the larger survey is why it changed. That is the honest answer and
it is on the assumptions page.
