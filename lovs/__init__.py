"""LOVS (Latent Outbreak Visibility System) methodology package.

Stdlib-only Python implementation of a descriptive (not predictive) outbreak
visibility methodology, applied to the 2026 Bundibugyo virus outbreak in DRC
Ituri Province and Uganda Kampala.

Three-zone framing held throughout:
 - Mode A: retrospective backtest on the West Africa 2014 substrate
   (calibrated; the academically validated zone)
 - Mode B: live shadow forecasting (pre-registered with explicit resolution
   criteria; calibration accumulates over time)
 - Zone 3: worked example on the current outbreak (illustrative; no skill claim)

No predictive-skill claim is made for live application. The methodology is
calibrated against WA 2014; its application to active outbreaks is shadow-mode
only.

Citations: Wamala 2010 EID, MacNeil 2010 EID, Albariño 2013 Virology,
Backer & Wallinga 2016 PLOS Comp Bio, Bracher 2021 PLOS Comp Bio.
"""
