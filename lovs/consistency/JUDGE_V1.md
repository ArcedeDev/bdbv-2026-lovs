# Judge: SitRep figure reconstruction (BDBV lane, first case)

Status: APPROVED by the founder 2026-10-16 (AskUserQuestion in session a2711f69: "Approve as drafted"), founder-gated (standard:mechanism-reconstruction, Rules). A lane may change its candidates, never this file. Any change is a new version with a new hash.
Version: 1 (drafted 2026-10-16, before the formal run).

## Disclosure of prior sight (selection watch, step 7)
Before this judge was drafted, an exploratory read was made of SR151, SR152 and SR153 (body text, key messages, Table 3 province rows) and of the investigation sentences in SR84 to SR153. It showed that SR152's key message keeps SR151's "26" and that SR153's key-message block repeats SR152's. SR151, SR152 and SR153 are therefore SEEN editions. They are reported, but they do not count toward any calibration of the bar, and the null below is built only from other editions. The carry-forward idea is charged to the candidate family like every other candidate.

## Targets
For every edition E whose key messages (MESSAGES CLES) print a suspect-investigation statement with numbers, the target tuple is
KM(E) = (n_investigated, n_validated, pct_printed, n_not_investigated, province), with any element that is not printed left absent.

## Candidate family (registered before the run; m = 12)
Each candidate is a formula over recorded fields. T(E) is Table 3 of edition E (national total row, or a province row), and B(E) is the edition's surveillance body prose.
- C1 same-edition Table 3 national: (inv, val, inv/val, val-inv).
- C2 same-edition Table 3, one province row (6 provinces counted as 6 hypotheses in the grid, reported as one candidate).
- C3 same-edition alert basis: verified/received, and validated/verified.
- C4 same-edition province subset: any subset S of reporting provinces whose summed (inv, val) match (2^p - 1 subsets counted).
- C5 cumulative: running sum of T over editions up to E.
- C6 live-only: validated living and its investigated share.
- C7 previous edition Table 3, lag k in {1,2,3}: T(E-k).
- C8 previous edition body prose, lag k in {1,2,3}: B(E-k).
- C9 previous edition key message, lag k in {1,2,3}: KM(E-k) (carry-forward of text).
- C10 timing cut-off: the sum T(E) + T(E-1) and the average of T(E) and T(E-1).
- C11 same-edition laboratory block (samples tested and positive), which tests single-number collisions.
- C12 same-edition PoE/PoC table (investigated in 4 h).
Effective hypothesis count for multiplicity: m_eff = 1 + 6 + 2 + 63 + 1 + 1 + 3 + 3 + 3 + 2 + 2 + 1 = 88.

## Acceptance rule (what counts as reproduction)
1. Counts: exact integer equality, after normalising French formatting (space or NBSP thousands, decimal comma). No tolerance.
2. Percentages: the candidate's num/den, rounded half-up to the printed number of decimals, must equal the printed value. A percentage is never matched without its counts when the counts are printed.
3. A candidate REPRODUCES KM(E) only when every printed element of KM(E) is matched (all four counts and the percentage). A partial match is recorded as partial and is never accepted.
4. Scope: every edition in the corpus that prints the target, not only SR153. Editions without the target are NOT_EVALUABLE with a reason code. A present but unparseable target is FAIL, never a skip.

## Coincidence bar
1. Null: for each accepted (candidate, edition) match, the probability that an unrelated edition's figures would match the target by chance. It is estimated empirically. Pair the target tuple of E with the same candidate fields from every edition j where |E - j| >= 4 and j is not a seen edition, and count exact tuple matches under the acceptance rule. p_hat = (matches + 1) / (pairs + 1).
2. Per-field rates are also reported, so a single-number collision (for example a lone 506) is visible.
3. Bonferroni: a match is accepted only if p_hat * m_eff < 0.01.
4. Negative control: a carry-forward candidate (C7, C8, C9) must not reproduce editions where the key message agrees with same-edition Table 3 and differs from the prior edition, at a rate above the null. Otherwise it is a universal fit and is rejected.

## Verdicts (per target edition, and overall)
- EXPLAINED: exactly one candidate reproduces the target and passes the bar. Report the mechanism, N/N and p.
- AMBIGUOUS: two or more candidates pass, and they are named.
- UNEXPLAINED: no candidate passes. Each figure goes into the residue ledger with the candidates tried.
- Overall verdict for a question: EXPLAINED only if every target edition in scope is EXPLAINED by mechanisms from the family. Otherwise report each edition's verdict and the residue.

## Gold
The printed PDF bytes pinned by sha256 (INRB mirror commit 7a4d08dde28562d68132b8adc10d1fdc26c3e6f4 for SR1 to SR150, LOVS private raw for SR151 to SR153), with Table 3 cross-checked against the reviewed LOVS promotions. Where they disagree, the conflict is recorded, and neither one silently wins.
