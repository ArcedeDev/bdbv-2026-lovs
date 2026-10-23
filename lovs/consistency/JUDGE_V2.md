# Judge: SitRep figure reconstruction (BDBV lane, first case), version 2

Status: APPROVED by the founder 2026-10-16. The founder chose two options in session a2711f69: "v2: grouped-field null" and "Add composite C13". The judge is founder-gated (standard:mechanism-reconstruction, Rules). A lane may change its candidates, never this file. Any change is a new version with a new hash.
Version: 2 (drafted 2026-10-16, after the judge v1 run and before any v2 run). Version 1 (JUDGE_V1.md, sha256 1cfc8a82d568c10d77103016da66d140ee50ae8d2e931fd8d542ff6b1a5329da) stays registered, and its run stays recorded.

## Why version 2 exists
The v1 run (receipt-judge-v1.json, 2026-10-16T22:36:45Z) could not return EXPLAINED for any edition, whatever the data. Its null compares a whole tuple across at most 63 editions, so the smallest p_hat it can report is 1/64. With the multiplicity factor of 88, that floor is above 1.3, and the bar is 0.01. This is a fault in the design, not a finding about the data. Version 2 changes only how p is estimated, and adds one candidate. The targets, the acceptance rule, the gold, the control and the 0.01 bar do not change.

## Disclosure of prior sight (selection watch, step 7)
Everything in v1's disclosure still applies: SR151, SR152 and SR153 are SEEN editions. They are reported, and they are excluded from every null pool. In addition, the v1 run results were seen before this version was drafted. Those results include the field-level match pattern of SR152, and they are what motivated candidate C13. C13 is therefore charged to the family (m_eff rises by one), and SR152 cannot count as support for it. Only C13 matches at unseen editions count as support.

## Targets
Unchanged from v1: KM(E) = (n_investigated, n_validated, pct_printed, n_not_investigated, province), where any element that is not printed is absent.

## Candidate family (m = 13)
C1 to C12 are exactly as in v1. One candidate is added:
- C13 composite update: the counts and percentage come from same-edition Table 3 (as in C1), and the remainder sentence (n_not_investigated, province) is carried from the key message of edition E-1 (as in C9 at lag 1). C13 gives counts wherever T(E) exists, and gives a remainder only when KM(E-1) prints one.
Effective hypothesis count: m_eff = 88 + 1 = 89.

## Acceptance rule
Unchanged from v1, rules 1 to 4. Counts must match exactly. The percentage must match after half-up rounding at printed precision. Every printed element must be reproduced, and a partial match is never accepted. The scope is every edition that prints the target.

## Coincidence bar
1. Field groups. The printed elements are split into groups of dependent fields:
   G1 = {n_investigated, n_validated, pct_printed} (the percentage is a function of the two counts);
   G2 = {n_not_investigated};
   G3 = {province}.
   If the target's n_not_investigated equals n_validated - n_investigated, then G2 is implied by G1, and G2 is merged into G1. A separate factor is used only when the printed remainder carries information that the counts do not.
2. Null per group. For each group g that is printed in the target, the null pool is the same as in v1: every edition j with |E - j| >= 4, j not seen, and the candidate producing output at j. The group rate is p_g = (matches_g + 1) / (pairs + 1). A match at j means that every printed field of g is reproduced by the candidate's output at j.
3. Combined p. The combined p is p_hat = product of p_g over the printed groups. This assumes that group matches are independent across unrelated editions. The receipt reports every p_g, and also the whole-tuple v1 p_hat as a cross-check.
4. Bonferroni: a match is accepted only if p_hat * m_eff < 0.01, with m_eff = 89.
5. Negative control: unchanged from v1. It applies to C7, C8, C9 and C13. A carry-forward candidate must not reproduce, at a rate above p_hat, the editions where the key message agrees with same-edition Table 3 and the candidate's output differs from it. Otherwise it is a universal fit and is rejected.

## Verdicts
Unchanged from v1. EXPLAINED means exactly one candidate passes. AMBIGUOUS means two or more pass, and they are named. UNEXPLAINED means none passes, and the figure goes to the residue ledger. A single-group target (for example a lone percentage) has a floor of 1/(pairs + 1), and that floor is reported. Such a target can only be UNEXPLAINED at this corpus size, and the receipt says so instead of hiding it.

## Gold
Unchanged from v1. The gold is the pinned PDF bytes, cross-checked against the reviewed LOVS promotions.
