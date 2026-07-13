# BDBV Model Tournament Runbook

## Scope

Operate the generator-owned recurring 30-day model evaluation contract and its render-only website projection. The first round still requires an explicit freeze approval and at least two eligible models; publishing the scheduled contract does not freeze a round.

## Routine Cycle

1. Inspect lifecycle state:

   ```bash
   python3 -m lovs.model_tournament status --as-of YYYY-MM-DD
   ```

2. On or after the eligible review date, review the candidate target universe, every model's readiness, source release receipt, and complete model-by-target matrix. Commit the exact candidate under `data/model-tournament/candidates/`, merge its review PR, then freeze against that GitHub PR receipt:

   ```bash
   python3 -m lovs.model_tournament freeze \
     --candidate data/model-tournament/candidates/ROUND_ID.json \
     --source-snapshot data/live-bdbv-2026-output.json \
     --approval-pr-api https://api.github.com/repos/ArcedeDev/bdbv-2026-lovs/pulls/NUMBER
   ```

3. After the inclusive 30-day window closes, prepare complete evidence-backed outcomes. Missing or surveillance-dark evidence must use an unscoreable state, never `resolved_no` by inference:

   ```bash
   python3 -m lovs.model_tournament resolve \
     --round-id ROUND_ID --candidate /reviewed/resolution-candidate.json
   python3 -m lovs.model_tournament score --round-id ROUND_ID
   ```

4. Refresh the contract, regenerate public artifacts, sync the website, and run both repositories' verification suites. Never hand-edit the website snapshot.

## Alerts

| Symptom | Stop condition | Action |
|---|---|---|
| `status == invalid` | Any diagnostic | Halt publication; repair the named registry, schedule, control, or immutable round artifact. |
| Freeze rejected | Fewer than two eligible models, early date, incomplete matrix, missing receipt, or disabled control | Do not bypass. Complete review or wait for eligibility. |
| Resolution rejected | Early finalization, incomplete target universe, invalid evidence state, or hash mismatch | Repair the candidate from authoritative evidence; do not mutate the forecast. |
| Public schema/leak/terminology test fails | Any failure | Halt sync/deploy and fix the generator or publication projection. |
| Production health degrades | Error rate >0.5% or p99 >2,000 ms for 30 minutes | Disable the contract and redeploy, or roll Vercel back to the prior verified deployment. |

## Kill Switch

The rollback mechanism is explicit disablement, not omission. Post-activation public snapshots are required to carry the contract, and the website renders a disabled contract as an empty section.

```bash
python3 -m lovs.model_tournament control \
  --state disabled \
  --updated-by OWNER \
  --reason "INCIDENT_OR_ROLLBACK_REFERENCE"
python3 refresh_pipeline.py --contract-only
WEBSITE_ROOT=/path/to/website/apps/site
APPROVED_LOVS_COMMIT=FULL_COMMIT_FROM_REVIEWED_MERGE_RECEIPT
python3 "$WEBSITE_ROOT/lib/scripts/sync-bdbv-lovs.py" \
  --lovs-root /path/to/generator --website-root "$WEBSITE_ROOT" \
  --expected-lovs-commit "$APPROVED_LOVS_COMMIT"
```

Re-enable only after the incident is resolved and the same full verification passes:

```bash
python3 -m lovs.model_tournament control \
  --state enabled \
  --updated-by OWNER \
  --reason "RESOLUTION_REFERENCE"
```

Owner: BDBV Snapshot Prep Manager. Website rollback owner: website deployment operator. The control command is atomically written; immutable forecast, resolution, and evaluation artifacts are retained.

## Verification

Generator:

```bash
python3 -m pytest -q
python3 -m py_compile refresh_pipeline.py calibration_resolver.py \
  lovs/forecast_scoring.py lovs/model_tournament.py lovs/lovs_validation.py
python3 refresh_pipeline.py --contract-only
python3 -m lovs.public_exports
```

Website:

```bash
WEBSITE_ROOT=/path/to/website/apps/site
APPROVED_LOVS_COMMIT=FULL_COMMIT_FROM_REVIEWED_MERGE_RECEIPT
python3 "$WEBSITE_ROOT/lib/scripts/sync-bdbv-lovs.py" \
  --lovs-root /path/to/generator --website-root "$WEBSITE_ROOT" \
  --expected-lovs-commit "$APPROVED_LOVS_COMMIT"
npm --workspace @arcede/site test
npm --workspace @arcede/site run lint
npm --workspace @arcede/site run build
```

Expected public state before the first freeze: `scheduled` through August 4, then `ready_for_freeze_review` on August 5. With only one currently eligible model, freeze must remain blocked until a second benchmark passes production-readiness review.
