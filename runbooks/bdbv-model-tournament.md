# BDBV Model Tournament Runbook

## Scope

Operate the generator-owned recurring 30-day model evaluation contract and its render-only website projection. The first round still requires an explicit freeze approval and at least two eligible models; publishing the scheduled contract does not freeze a round.

## Routine Cycle

1. Inspect lifecycle state:

   ```bash
   python3 -m lovs.model_tournament status --as-of YYYY-MM-DD
   ```

2. On or after the eligible review date, generate and review the candidate target universe, every model's readiness, source release receipt, and complete model-by-target matrix. The v3 freeze requires the build receipt and resolution policy embedded in the candidate; a standalone sidecar is insufficient. Review the embedded input hashes and source availability cutoff. The actual freeze must occur at or after that cutoff. Freeze verifies input bytes and deterministically rebuilds the full candidate to check declared roles, clocks and input completeness. Any input change requires regeneration and another review of the exact candidate.

   ```bash
   python3 -m lovs.tournament_candidates --help
   ```

   Commit the exact generated candidate under `data/model-tournament/candidates/`, merge its review PR, then freeze against that GitHub PR receipt:

   ```bash
   python3 -m lovs.model_tournament freeze \
     --candidate data/model-tournament/candidates/ROUND_ID.json \
     --source-snapshot data/live-bdbv-2026-output.json \
     --approval-pr-api https://api.github.com/repos/ArcedeDev/bdbv-2026-lovs/pulls/NUMBER
   ```

3. After the inclusive 30-day window closes, follow [the resolution draft workflow](../docs/tournament-resolution.md). Review pending chains against their primary sources before adding them to the evidence registry. Missing or surveillance-dark evidence must use an unscoreable state, never `resolved_no` by table omission. Negative outcomes require target-specific reviewed full-window coverage. Then use the existing authoritative commands:

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
| Source cutoff or input hash rejected | Any mismatch or one source availability bound after cutoff | Regenerate from available reviewed inputs and obtain exact candidate approval. A date-only review uses a conservative UTC bound; do not invent a timestamp. |
| Draft unresolved or rejected | Missing target coverage, conflicting evidence, invalid source or changed centroid hash | Review the cited sources; preserve unscoreable states. Repair proposals before finalization. |
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

## Daily registry health

Default daily snapshot preparation reads `/api/bdbv-2026/status` once and checks
`tournament_registry.evaluated_as_of` against the actual UTC date. It validates
the served cadence against the canonical schedule. The check runs even when
public dataset parity is not requested. The evaluation date describes the
deployed registry; do not change it just to clear an alert.

For rollout, merge the reviewed generator change, sync the website from that
exact main commit, and publish its metadata endpoint. Verify the live response
before activating the daily agent's updated instructions. Until deployment,
missing metadata is a hard health issue. Do not suppress it with a date exemption.

| Symptom | Diagnostic | Mitigation | Owner |
|---|---|---|---|
| `tournament_registry_stale` (yellow, age 1–30 days) | Read the report's URL, evaluation date and age. | Review current lifecycle inputs; regenerate and sync the current registry when due. | Snapshot Prep Manager |
| `tournament_registry_expired` (red, age >30 or <0) | Compare report UTC clock, served date and canonical schedule. | Repair clock faults or publish a reviewed current registry. Keep release readiness blocked. | Snapshot Prep Manager; website operator |
| `tournament_registry_invalid` (red) | Read the content-validation error; check website CI and source receipt. | Publish the valid two-field metadata from a verified generator commit. | Website operator |
| `tournament_registry_fetch_failed` (red) | Read transport error and check endpoint availability. | Repair network/service access and rerun health; never infer freshness from a failed fetch. | Website operator |

Health colours affect release readiness. They do not change the preparation
CLI's exit status. Existing cycle reports carry these issues for agent triage.
The first round measures benchmark performance; it cannot by itself establish
general superiority or validate a replacement for the failed corridor model.

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

Expected state before the first freeze: `ready_for_freeze_review` after the eligible date. The base-rate probability and distance rank benchmarks are eligible. The failed corridor model is ineligible for new rounds; historical forecasts remain intact. Eligibility alone does not freeze Round 001. Historical v2 rounds remain readable, while new freezes require v3 provenance and policy bindings.

## Current source timing

SR141's source review is recorded as the bare date `2026-10-04`, whose conservative availability bound is `2026-10-05T11:59:59Z`. The October 4 build was tested and rejected for that reason. Source PR91 merged at `2026-10-04T12:33:34Z`, providing a separate public availability witness; the current contract does not accept GitHub merge time as a substitute review clock. Preserve that distinction. Rebuild when the bound is satisfied and review any newer sources before freezing. No future-clock preview is an actual frozen round.
