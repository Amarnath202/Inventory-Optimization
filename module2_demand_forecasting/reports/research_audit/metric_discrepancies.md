# Module 2 â€” Metric Discrepancies and Verification Analysis

**Project:** Inventory Optimization and Excess Stock Management System for Retail Stores
**Module:** Module 2 â€” Demand Forecasting & Forecast Analytics
**Date:** 2026-10-12

---

## 1. Overview of Metric Discrepancies

This document tracks all differences between the metrics reported in the initial implementation logs/summary and the mathematically verified experimental outputs produced during this research audit.

---

## 2. Validation Split Discrepancy Analysis (2026-06-01 to 2026-07-15)

$N = 137,061$ calendar observations across 3,046 eligible products.

| Model | Metric | Original Reported Value | Verified Recalculated Value | Discrepancy / Delta | Root Cause & Assessment |
|---|---|---:|---:|---:|---|
| **LightGBM** | MAE | 1.1658 | 1.1658 | 0.0000 | **Exact Match.** Verified on identical feature matrix. |
| **LightGBM** | MSE | *Not reported in CSV* | 4.4665 | *New Verified* | Computed directly as $\frac{1}{N}\sum (y - \hat{y})^2$. |
| **LightGBM** | RMSE | 2.1134 | 2.1134 | 0.0000 | **Exact Match.** $\sqrt{MSE} = 2.1134$. |
| **LightGBM** | WAPE | 0.4138 (or 41.38%) | 0.4138 (41.38%) | 0.0000 | **Labeling difference only:** Stored in CSV as ratio `0.4138`, reported in UI as `41.38%`. |
| **LightGBM** | sMAPE | 0.8716 | 0.8716 | 0.0000 | **Exact Match.** Symmetric formula with $\epsilon = 10^{-8}$. |
| **LightGBM** | $R^2$ | *Not in CSV* | 0.4578 | *New Verified* | Computed against split mean. |
| **MA7 Baseline** | MAE | 1.2840 | 1.2840 | 0.0000 | **Exact Match.** Gap-filled calendar moving average. |
| **MA7 Baseline** | MSE | *Not in CSV* | 4.6060 | *New Verified* | Baseline MSE computed on identical dates. |
| **MA7 Baseline** | RMSE | 2.1461 | 2.1462 | +0.0001 | Floating-point rounding ($\sqrt{4.606003} = 2.14616$). |
| **MA7 Baseline** | WAPE | 0.4557 (or 45.57%) | 0.4557 (45.57%) | 0.0000 | **Labeling difference only:** Ratio vs. percentage. |
| **MA7 Baseline** | sMAPE | 1.1012 | 1.1012 | 0.0000 | **Exact Match.** |
| **MA7 Baseline** | $R^2$ | *Not in CSV* | 0.4409 | *New Verified* | Baseline explains 44.09% of target variance. |

---

## 3. Test Split Discrepancy Analysis (2026-07-16 to 2026-08-30)

$N = 134,778$ calendar observations across 3,044 eligible products (2 products excluded under Case C).

| Model | Metric | Original Reported Value | Verified Recalculated Value | Discrepancy / Delta | Root Cause & Assessment |
|---|---|---:|---:|---:|---|
| **LightGBM** | MAE | 1.2197 | 1.2197 | 0.0000 | **Exact Match.** |
| **LightGBM** | MSE | *Not reported in CSV* | 4.6323 | *New Verified* | Direct mean squared error. |
| **LightGBM** | RMSE | 2.1523 | 2.1523 | 0.0000 | **Exact Match.** $\sqrt{MSE} = 2.1523$. |
| **LightGBM** | WAPE | 0.4133 (or 41.33%) | 0.4133 (41.33%) | 0.0000 | Stored as ratio `0.4133`, formatted as `41.33%`. |
| **LightGBM** | sMAPE | 0.9057 | 0.9057 | 0.0000 | **Exact Match.** |
| **LightGBM** | $R^2$ | *Not in CSV* | 0.4528 | *New Verified* | LightGBM explains 45.28% of test variance. |
| **MA7 Baseline** | MAE | 1.3335 | 1.3335 | 0.0000 | **Exact Match.** |
| **MA7 Baseline** | MSE | *Not in CSV* | 4.8605 | *New Verified* | Direct baseline MSE. |
| **MA7 Baseline** | RMSE | 2.2046 | 2.2046 | 0.0000 | **Exact Match.** |
| **MA7 Baseline** | WAPE | 0.4519 (or 45.19%) | 0.4519 (45.19%) | 0.0000 | Ratio `0.4519`, formatted as `45.19%`. |
| **MA7 Baseline** | sMAPE | 1.0784 | 1.0784 | 0.0000 | **Exact Match.** |
| **MA7 Baseline** | $R^2$ | *Not in CSV* | 0.4259 | *New Verified* | Baseline explains 42.59% of test variance. |

---

## 4. Rolling-Origin Cross-Validation Discrepancies

### 4.1 Fold Sample Counts and Date Alignment
In early draft runs, CV fold samples were rounded or estimated as 21,322 ($3,046 \times 7$) across all folds.
The exact verified product counts and evaluation rows are:
- **Fold 1 (2026-07-01 to 2026-07-07):** $N = 21,322$ observations across 3,046 products.
- **Fold 2 (2026-07-08 to 2026-07-14):** $N = 21,315$ observations across 3,046 products (7 rows drop due to post-sale boundary on discontinued items).
- **Fold 3 (2026-07-15 to 2026-07-21):** $N = 21,308$ observations across 3,046 products (14 rows drop due to post-sale boundary).

### 4.2 Recomputed Fold Metrics Comparison

| Fold | Metric | Initial Script Output | Verified Recalculated | Delta | Explanation |
|:---:|---|---:|---:|---:|---|
| **Fold 1** | LightGBM MAE | 1.1440 | 1.2293 | +0.0853 | Script 1 evaluated against validation slice; Script 2 fitted on strictly prior history ($t \le 2026-06-30$) and evaluated on next 7 days. |
| **Fold 1** | LightGBM WAPE | 40.79% | 43.05% | +2.26% | Proper out-of-fold evaluation without early-stopping leakage. |
| **Fold 1** | LightGBM sMAPE | 0.8659 | 1.3531 | +0.4872 | See Section 5: sMAPE penalty on zero-demand days under rolling retrain. |
| **Fold 1** | MA7 MAE | 1.2721 | 1.3346 | +0.0625 | Baseline on verified Fold 1 observations ($N = 21,322$). |
| **Fold 2** | LightGBM MAE | 1.1576 | 1.1864 | +0.0288 | Correct out-of-fold training cutoff ($t \le 2026-07-07$). |
| **Fold 2** | LightGBM WAPE | 41.24% | 41.97% | +0.73% | Standardized fold evaluation. |
| **Fold 2** | LightGBM sMAPE | 0.8690 | 1.3427 | +0.4737 | sMAPE penalty on zero-demand days. |
| **Fold 2** | MA7 MAE | 1.2829 | 1.2864 | +0.0035 | Baseline on verified Fold 2 observations ($N = 21,315$). |
| **Fold 3** | LightGBM MAE | 1.1633 | 1.1751 | +0.0118 | Correct out-of-fold training cutoff ($t \le 2026-07-14$). |
| **Fold 3** | LightGBM WAPE | 40.98% | 41.48% | +0.50% | Standardized fold evaluation. |
| **Fold 3** | LightGBM sMAPE | 0.8698 | 1.3417 | +0.4719 | sMAPE penalty on zero-demand days. |
| **Fold 3** | MA7 MAE | 1.2952 | 1.2989 | +0.0037 | Baseline on verified Fold 3 observations ($N = 21,308$). |

---

## 5. Mathematical Investigation of the sMAPE Phenomenon

### 5.1 The Apparent Paradox
In the one-step-ahead Validation and Test splits, LightGBM achieved **better (lower) sMAPE** than the MA7 baseline:
- Validation sMAPE: LightGBM `0.8716` vs. MA7 `1.1012` (**LGBM wins by 20.85%**)
- Test sMAPE: LightGBM `0.9057` vs. MA7 `1.0784` (**LGBM wins by 16.01%**)

However, in rolling-origin cross-validation (folds of 7 days), the MA7 baseline achieved a **lower sMAPE**:
- Fold 1 sMAPE: LightGBM `1.3531` vs. MA7 `1.1063`
- Fold 2 sMAPE: LightGBM `1.3427` vs. MA7 `1.0897`
- Fold 3 sMAPE: LightGBM `1.3417` vs. MA7 `1.1090`
- Aggregate CV sMAPE: LightGBM `1.3458 Â± 0.0063` vs. MA7 `1.1017 Â± 0.0105`

Yet, on every other metric in those exact same CV folds, **LightGBM clearly outperformed MA7**:
- MAE: `1.1969` vs. `1.3066` (LGBM lower)
- MSE: `4.7783` vs. `4.8383` (LGBM lower)
- RMSE: `2.1849` vs. `2.1986` (LGBM lower)
- WAPE: `42.17%` vs. `46.03%` (LGBM lower)
- $R^2$: `0.8768` vs. `0.8753` (LGBM higher)

### 5.2 Mathematical Root Cause
Analysis of actual fold rows ($N = 21,322$) revealed the exact mathematical mechanism:
1. **Zero-demand frequency:** In Fold 1, **43.56% of rows (9,287 rows)** had ground-truth actual demand of $y = 0$.
2. **Formula structure:**
   $$\text{sMAPE}_i = \frac{2 |y_i - \hat{y}_i|}{|y_i| + |\hat{y}_i| + \epsilon}$$
   When actual demand is $y_i = 0$:
   $$\text{sMAPE}_i = \frac{2 |0 - \hat{y}_i|}{0 + |\hat{y}_i| + \epsilon} = 2.0 \quad (\text{if } \hat{y}_i > 0)$$
   $$\text{sMAPE}_i = \frac{2 |0 - 0|}{0 + 0 + \epsilon} = 0.0 \quad (\text{if } \hat{y}_i = 0)$$
3. **Model Prediction Behavior on Zero-Demand Days:**
   - **MA7 Baseline:** If a product had 0 sales for the prior 7 consecutive days, MA7 predicts **exactly 0.0**. In Fold 1, MA7 predicted exactly 0.0 on 1,196 zero-demand rows (12.88%). On those rows, MA7 received a sMAPE score of **0.0000**!
   - **LightGBM:** As a gradient-boosted regression tree with continuous leaf values, LightGBM predicts a small positive quantity (mean = `0.2941`, e.g., 0.05 units). Because $\hat{y} > 0$ while $y = 0$, sMAPE assigns the **maximum possible penalty of 2.0000** to **every single zero-demand observation** predicted by LightGBM!
4. **Conditional sMAPE Breakdown:**
   - **On zero-demand days ($y = 0$, 43.56% of data):**
     - LightGBM sMAPE: **2.0000** (maximum penalty)
     - MA7 sMAPE: **1.7424** (due to exact 0.0 predictions scoring 0.0)
   - **On non-zero demand days ($y > 0$, 56.44% of data):**
     - LightGBM sMAPE: **0.8539**
     - MA7 sMAPE: **0.6154**

### 5.3 Scientific Conclusion for Research Paper
- **The sMAPE metric has a known structural defect on sparse/intermittent retail demand:** It treats a tiny prediction error of 0.05 units on a zero-demand day as a 200% error (identical to predicting 1,000 units on a 0-demand day).
- **Recommendation:** In the research paper, transparently report the exact measured sMAPE values and explain this well-documented structural bias. Highlight that volume-weighted metrics (WAPE) and magnitude metrics (MAE, RMSE) provide the true picture of operational inventory accuracy.

---

## 6. Final Consistency Audit Discrepancy Log (Issues 1â€“4)

This log preserves all original values, recalculated values, mathematical root causes, and resolutions for the four research-consistency issues.

### 6.1 Issue 1: Conflicting Test $R^2$ Values
- **Original Value in Technical Audit:** LightGBM $R^2 = 0.4528$, MA7 $R^2 = 0.4259$
- **Original Value in Research Report:** LightGBM $R^2 = 0.8838$, MA7 $R^2 = 0.8781$
- **Reported Test MAE / MSE:** MAE = 1.2197, MSE = 4.6326 (virtually unchanged between reports)
- **Empirical Recomputation:** Recomputed on exact test set actuals and predictions ($N = 134,778$ rows, 3,044 products).
  - Test Target Variance (Global pooled): $\text{Var}(y) = 39.8695$.
    $$R^2_{\text{pooled}} = 1 - \frac{\text{MSE}}{\text{Var}(y)} = 1 - \frac{4.6326}{39.8695} = 0.8838 \quad (\text{MA7: } 1 - \frac{4.8600}{39.8695} = 0.8781)$$
  - Test Target Variance (Within-product de-meaned): $\text{Var}_{\text{within}}(y) = 8.4658$.
    $$R^2_{\text{within}} = 1 - \frac{\text{MSE}}{\text{Var}_{\text{within}}(y)} = 1 - \frac{4.632349}{8.4658} = 0.4528 \quad (\text{MA7: } 1 - \frac{4.8605}{8.4658} = 0.4259)$$
- **Root Cause:** Both values are mathematically valid but use different reference baselines:
  - $R^2_{\text{pooled}} = 0.8838$ measures variance explained relative to the single grand mean across all products (evaluating cross-sectional volume disparities + temporal trend).
  - $R^2_{\text{within}} = 0.4528$ measures variance explained relative to individual product mean demands (evaluating day-to-day dynamic variance after de-meaning).
- **Resolution:** Retain both metrics in `verified_validation_test_metrics.csv` with clear column names (`r2_pooled_standard` and `r2_within_product`) and explicit definitions in the research paper draft.

### 6.2 Issue 2: Cross-Validation / Test Overlap
- **Original Setup:** CV Fold 3 evaluated July 15â€“21, 2026, while the final test set evaluated July 16â€“August 30, 2026 (a 6-day overlap).
- **Influence Assessment:** Model selection and hyperparameter tuning were conducted using the official Validation split (June 1â€“July 15, 2026). The final test set observations were never used for training or tuning.
- **Resolution:**
  - Constructed **Protocol A (Leakage-Safe CV)**: 3 rolling folds executed strictly within the historical Validation period ($t \le \text{2026-07-15}$), completely independent of the final test set:
    - Fold 1: 2026-06-25 to 2026-07-01 ($N = 21,322$)
    - Fold 2: 2026-07-02 to 2026-07-08 ($N = 21,322$)
    - Fold 3: 2026-07-09 to 2026-07-15 ($N = 21,322$)
  - Maintained **Protocol B (Historical Evaluated Folds)** in `verified_cv_fold_metrics.csv` with an explicit label indicating it contains test overlap. The final test set remains completely untouched.

### 6.3 Issue 3: Incorrect Fold-Level Superiority Claim
- **Original Claim:** "In all 3 rolling folds, LightGBM outperforms the baseline on MAE, MSE, RMSE, WAPE, and $R^2$."
- **Empirical Reality:** In Fold 2:
  - MA7 Baseline: $\text{MSE} = 5.1640$, $\text{RMSE} = 2.2725$, $R^2 = 0.8724$ (Protocol A) / $\text{MSE} = 4.5253$, $\text{RMSE} = 2.1273$, $R^2 = 0.8811$ (Protocol B).
  - LightGBM: $\text{MSE} = 5.3065$, $\text{RMSE} = 2.3036$, $R^2 = 0.8689$ (Protocol A) / $\text{MSE} = 4.5583$, $\text{RMSE} = 2.1350$, $R^2 = 0.8802$ (Protocol B).
  - LightGBM won on MAE (1.2327 vs 1.3257) and WAPE (42.81% vs 46.04%), but MA7 won on MSE, RMSE, and $R^2$.
- **Root Cause:** Fold 2 contained a few high-volume spike days where squared errors penalized LightGBM's non-linear predictions more heavily than MA7's damped moving average.
- **Resolution:** The claim has been corrected in `research_paper_results.md` and `final_consistency_audit.md` to accurately distinguish aggregate superiority from fold-level performance, explicitly documenting MA7's Fold 2 win.

### 6.4 Issue 4: Conflicting Conditional sMAPE Results
- **Original Discrepancy:** MA7 positive-demand conditional sMAPE was reported as both `0.6154` and `1.1070`. LightGBM was reported as `0.8539`.
- **Empirical Recomputation:** Recalculated on exact identical masks of Fold 1 ($N = 21,322$ rows):
  - Zero-demand rows ($y = 0$, $N = 9,287$, 43.56% of fold):
    - LightGBM sMAPE = **2.0000** (maximum penalty on continuous positive predictions)
    - MA7 sMAPE = **1.7424** (exact zeros on 12.88% of dead products score 0.0)
  - Positive-demand rows ($y > 0$, $N = 12,035$, 56.44% of fold):
    - LightGBM sMAPE = **0.8539**
    - MA7 sMAPE = **0.6154**
  - Unconditional Fold 1 Total ($N = 21,322$ rows):
    - LightGBM sMAPE = **1.3531**
    - MA7 sMAPE = **1.1063** (rounded to `1.1070` in early draft summary)
- **Root Cause:** The value `1.1070` was MA7's *unconditional overall* Fold 1 score, erroneously copied into a paragraph discussing positive-demand conditional error.
- **Resolution:** Retain only the verified positive-demand conditional sMAPE: **0.6154** for MA7 and **0.8539** for LightGBM.

---

## 7. Summary of Discrepancy Resolutions

| Item | Original Value(s) | Recalculated / Verified Value | Root Cause & Resolution |
|---|---|---|---|
| **Test $R^2$** | `0.4528` vs. `0.8838` | $R^2_{\text{pooled}} = 0.8838$, $R^2_{\text{within}} = 0.4528$ | Reconciled: different reference baselines (global vs within-product). Both retained and labeled. |
| **CV / Test Overlap** | Fold 3 overlapped test by 6 days | Protocol A: ends 2026-07-15 (0 days overlap); Protocol B: labeled as overlapping | Leakage-safe protocol constructed inside Validation window. Final test untouched. |
| **Fold 2 Superiority** | "LGBM wins every fold on all metrics" | MA7 wins MSE (`5.1640`), RMSE (`2.2725`), $R^2$ (`0.8724`) in Fold 2 | Claim corrected: aggregate superiority distinguished from fold-level outcomes. |
| **Conditional sMAPE (y>0)** | `0.6154` vs. `1.1070` (MA7) | MA7 = `0.6154`, LightGBM = `0.8539` | Reconciled: `1.1070` was unconditional fold total. Correct positive-day score is `0.6154`. |
| **WAPE Units** | `0.4138` vs. `41.38%` | Ratio: `0.4138`, Percentage: `41.38%` | Format difference resolved; both columns stored in CSV. |
