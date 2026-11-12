<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Tournament rounds

Each round is frozen before its window opens. It is resolved under the policy it
froze, and its artifacts are never edited: `data/model-tournament/rounds/`,
`resolutions/` and `scores/`. The method is in
[tournament-resolution.md](tournament-resolution.md).

## Round 001: resolved on 2026-11-12

**What was frozen.** On 2026-10-05, from SitRep 141 (data 2026-10-02):
- 103 target health zones: every GRID3 v8.0 zone in the seven provinces with a
  confirmed case at that date that had none itself;
- two models: the base-rate benchmark, which gives every zone 0.0818, and the
  distance-only frontier rank;
- the window 2026-10-06 to 2026-11-04, under resolution policy v1.

**One target resolves.** Ango resolves yes. SitRep 147 (data 2026-10-08, published
2026-10-11T14:30:13Z) adds a Dakwa health zone in Bas-Uele with one confirmed case.
- The frozen layer holds no Dakwa zone.
- GRID3 places the Dakwa health area in Ango. Its centroid, its referral health
  centre and 99.9 percent of its surface lie inside the frozen Ango polygon.
- The authority names Dakwa as a separate zone and has published no boundary for
  it. The attribution states that limitation:
  `data/model-tournament/zone-attributions.json`.

**The other 102 targets are unscoreable (surveillance dark).**
- Policy v1 accepts a negative only with reviewed evidence of surveillance in the
  target zone itself.
- The authority publishes alerts and laboratory activity by province, not by zone,
  so no target could show that evidence.
- No publication in the window lists any of these zones with a confirmed case.

**What the round can say.** One target resolved, so there is one outcome class.
- ROC AUC and Brier skill are undefined.
- A Brier value over a single positive target does not describe calibration.
- The round cannot compare the two models, and nothing here is a verdict on either.

**What changed for later rounds.** In round 001, positives came from the zone table,
while negatives needed evidence the reports do not hold. The two outcomes were
measured with different instruments. From round 002, rounds freeze policy v2, which
reads both outcomes from the same table. Round 001 stays resolved under v1.

**The universe.** Three health zones reported their first confirmed case in the
window. Two of them, Wapinda and Yakoma, are in Nord-Ubangi, a province with no
confirmed case when the round froze, so they were outside its universe. The universe
rule is unchanged. Round 002's universe includes Nord-Ubangi.
