# SPDX-License-Identifier: Apache-2.0
"""The weekly pre-registered forecasting loop (spec v1). See docs/weekly-forecast-loop.md.

Weekly rows live in their own store (data/weekly-forecasts/) under their own ids
(bdbv-2026-wk-...). Nothing here writes to, or is read by, the calibration ledgers, the
pooled record, recalibration, the public exports or the snapshot pipeline.
"""
