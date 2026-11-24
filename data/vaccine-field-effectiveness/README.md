# Vaccine field-effectiveness scenario

Review tier, scenario lane. Situational evidence, not clinical or vaccine advice.

For each INSP SitRep, one artifact answers two questions for the vaccination given (Ervebo, a Zaire ebolavirus vaccine). First, how much protection against Bundibugyo virus would follow for each assumed strain match S, the share of Zaire protection that carries over. Second, what share of infections that protection averts directly. S is not identified by any SitRep or published study, so results are given at S = 0, 0.25, 0.5, 0.75 and 1. A summary under a uniform prior on S is also given; it reflects that prior, not data.

The method is `lovs/vaccine_field_effectiveness.py`. The per-release build is `lovs/vaccine_scenario_release.py`.

## Files

| File | What it holds | Changes |
|---|---|---|
| `inputs/sr<N>.json` | Values as INSP prints them, each with its SitRep locator, plus labelled assumptions for what is not printed | Each release |
| `scenario-sr<N>.json` | The artifact, in the read-only model-artifact shape | Each release, generated |
| `ring-timing.json` | Declared calibration of ring-vaccination timing on Zaire data, carried to Bundibugyo virus as a bounded scenario | Only with a new method version |

## Each release

1. Copy the latest `inputs/sr<N>.json` to the new number.
2. Update `sitrep_number`, `data_as_of`, `source_id` and the printed values with their locators.
3. Define a group only where its coverage is a printed rate, a ratio of printed values (with an ASSUMPTION `denominator_assumption` and a `denominator_factor_range` around 1, so the assumed denominator carries a range), or a labelled ASSUMPTION range. Record rows without a defined group are listed under `not_computed`, never estimated.
4. Build the artifact:

   ```
   python3 -m lovs.vaccine_scenario_release --sitrep <N>
   ```

   If the reviewed promotion `data/sitrep_promotions/sitrep-<N>-*.json` carries a per-province vaccination record, the build uses it. Every field that file and the inputs file both hold must agree, or the build stops. A province the inputs file lists but the record does not hold is allowed only when all its values are empty (it says only that the province prints no count). It is listed as not computed unless the inputs file defines a group for it with its own labelled coverage, as SitRep 189 does for Ituri's contacts. If a later record holds a count for a province whose coverage is assumed, and nothing in that province's inputs uses the record or names the count, the build stops until the assumption is revisited or `record_acknowledged` names that count and says why the range still holds.
5. Run the tests: `python3 -m unittest tests.test_vaccine_scenario_release`. They rebuild every committed artifact and require an exact match.

## What is never computed

- A national total.
- A measured effectiveness against Bundibugyo virus. That would need the screening method on vaccination status of confirmed cases, registered in advance.
- A share of all cases averted in a province whose vaccinated are not split into a frontline-worker group and a contacts group. Each artifact's `share_of_all_cases_note` says, per province, whether it was computed and why.

## Rebuilding the ring-timing calibration

```
python3 tools/calibrate_vaccine_ring_timing.py --check
```

This takes about 16 minutes and is deterministic. Each parameter set is screened at 6,000 simulated cohort members and confirmed at 40,000 against the exact target windows before it is accepted, which removes most acceptances that rested on simulation noise; a set right at a window edge can still pass. Each parameter set draws a Zaire incubation mean across the 9.1 to 11.4 days printed by the WHO Ebola Response Team in 2014, and its onset-to-transmission mean is that study's serial interval of 15.3 days minus that incubation mean, so the two always add up to the serial interval. The carry to Bundibugyo virus reuses each set's value. The calibration fits Zaire data poorly: the trial's 0 late vaccinee cases are only partly reproduced, and the DRC 2018-19 early-case share of 380/434 = 0.876 (interval 0.84 to 0.905; Muyembe 2024) is not reached. Ring and mixed-timing rows are bounds, not estimates.
