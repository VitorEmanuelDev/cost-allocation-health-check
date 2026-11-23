# Spec — Finance Data & Automation: Cost Allocation Health-Check

> Spec-first solution document. It defines the goal, assumptions, data model, business/validation
> rules, the evidence-backed findings discovered in the source data, the service taxonomy, the
> re-runnable automation, the executive output, and the AI layer with citation/refusal behaviour.

---

## 1. Goal

Consolidate the 27 monthly billing files (Entities A, B, C; Jan–Sep 2026) into one versioned,
lineage-tracked, USD-denominated dataset, validate it against the contract catalog, volumes and FX
reference data, and produce:

1. A single "actuals" mart with full source lineage (file → tab → row/column).
2. A findings register where every inconsistency is evidenced and quantified in USD.
3. A two-level service taxonomy (cluster → service).
4. A re-runnable pipeline (Python ingestion/validation + SQL/dbt transformations) with clear failure logs.
5. An executive actual-vs-budget view with variance drivers.
6. A small AI tool whose answers always cite source (file, tab, column) and refuse/escalate when evidence is insufficient.

**Reporting currency:** USD.

---

## 2. Tech stack decision

- **Python (pandas + openpyxl):** file discovery, xlsx parsing, layout normalization (3 different
  layouts/languages), currency conversion, and the fixed-rule validation engine. Python is the right
  tool because the inputs are unstructured spreadsheets with 3 different schemas.
- **SQL + dbt:** versioned, tested, documented transformations (staging → validated → marts).
  dbt gives re-runnability, `unique`/`not_null` tests, `source` lineage and an audit trail.
- **DuckDB / SQLite** as the local query engine backing the AI layer (single-file, no server).
- **AI layer:** a grounded Q&A agent over the mart tables (SQL-first RAG). It returns data + a
  citation block; no unsupported narrative.

Everything below is described as the target implementation; the "Findings" section contains the
actual results obtained from reading the provided data.

---

## 3. Assumptions

1. The Platform bills each entity monthly; the billing file for a month is the complete invoice for that month.
2. Entity A reports in USD (no FX conversion), Entity B in EUR, Entity C in BRL. FX is applied at line level using the monthly rate in `04_Volumes_and_FX.xlsx`.
3. The USD unit price in `Service_Pricing` is the contractual source of truth. For EUR/BRL entities the local unit price is derived as `USD price / FX(month, currency)`.
4. A service is "supported by a contract" only if all of these hold: a `Service_Pricing` row exists for (service, entity), the linked contract lists that entity, and the billing month is within the contract's `[Start Date, End Date]`.
5. For per-unit services, the billed quantity should equal the corresponding operational volume metric in `04_Volumes_and_FX.xlsx`. Fixed-monthly services must have quantity = 1.
6. The expected service set per entity is derived from the catalog: Entity A = 14 services, Entity B = 14 services (SVC-06 only valid through Jun because CT-1006 ends 2026-06-30), Entity C = 12 services (no SVC-03 DCC, no SVC-06 Chargeback Alerts).
7. Missing/blank values, unknown service codes and unreconciled totals are flagged, never silently dropped.
8. The challenge brief's time-box is not used as a constraint for this spec.

## 4. Data inventory (what is actually in the files)

### 4.1 `02_Contract_Catalog.xlsx`
- Tab **Contracts** (13 rows): 12 contracts. Columns: Contract ID, Vendor, Description, Start Date, End Date, Entities Covered, Contract Link.
  - CT-1001 (Authorization gateway), CT-1001A (amendment: approval-rate incentive), CT-1002 (3DS),
    CT-1003 (DCC — **A, B only**), CT-1004 (Fraud), CT-1005 (Device intelligence), CT-1006
    (Chargeback alerts — **A, B only, ends 2026-06-30**), CT-1007 (Tokenization), CT-1009 (Cloud),
    CT-1010 (Network & security), CT-1011 (L2 support), CT-1012 (Merchant onboarding & KYC).
  - **Observation:** CT-1008 is missing from the sequence (CT-1007 → CT-1009). No service references it.
- Tab **Service_Pricing** (41 rows): 14 services × entity. Columns: Service Code, Service Name,
  Contract ID, Entity, Pricing Model (`Per unit` | `Fixed monthly`), Unit/Monthly Price (USD), Billing Unit.
  - Per-unit: SVC-01 (0.0032/txn), SVC-02 (0.011/3DS), SVC-03 (0.0021/DCC), SVC-04 (0.0046/txn),
    SVC-05 (0.0028/device), SVC-06 (1.45/alert), SVC-08 (0.0007/token), SVC-12 (36/onboarding),
    SVC-13 (1.9/KYC), SVC-14 (0.0009/approved txn).
  - Fixed-monthly: SVC-07 (A 38k / B 21k / C 12k), SVC-09 (64k/37k/21k), SVC-10 (29k/17k/10k), SVC-11 (22k/14k/9k).

### 4.2 `03_Budget_2026.xlsx`
- Tab **Budget_2026** (481 rows = 480 data rows): Entity, Month, Service Code, Service Name, Budget (USD).
  - Full year Jan–Dec 2026. Entities A & B have 14 services/month; Entity C has 12/month (no SVC-03, no SVC-06).

### 4.3 `04_Volumes_and_FX.xlsx`
- Tab **Volumes** (226 rows): Entity, Month, Metric, Volume. Metrics map 1:1 to per-unit services:
  - Transactions → SVC-01 **and** SVC-04; 3DS authentications → SVC-02; DCC transactions → SVC-03;
    Device checks → SVC-05; Chargeback alerts → SVC-06; Tokens provisioned → SVC-08;
    Merchant onboardings → SVC-12; KYC checks → SVC-13; Approved transactions → SVC-14.
  - Entity C has no DCC-transactions and no Chargeback-alerts metrics (consistent with catalog).
- Tab **FX_Rates** (19 rows): Month, Currency, Rate (USD per 1 local unit), Source.
  - EUR: 1.082/1.079/1.091/1.088/1.095/1.101/1.093/**1.087**/1.09 (Jan→Sep).
  - BRL: 0.172/0.17/0.174/0.176/0.179/0.181/0.178/0.177/0.18 (Jan→Sep).

### 4.4 `01_Billing_Layouts/` (27 files, 3 layouts)

| Entity | Language | Currency | File pattern | Sheet | Header row | Columns |
|---|---|---|---|---|---|---|
| A | English | USD | `EntityA_Billing_2026-MM.xlsx` | `Billing` | row 3 | Invoice No, Line, Service Code, Service Description, Quantity, Unit Price (USD), Amount (USD) |
| B | Spanish | EUR | `Facturacion_EntB_mmm26.xlsx` | `mmm26` | row 1 | Nº Factura, Línea, Cód. Servicio, Descripción, Cantidad, Precio Unitario (EUR), Importe (EUR), Tipo de Cambio, Importe USD |
| C | Portuguese | BRL | `Faturamento_EntC_2026_MM.xlsx` | `Detalhe` | row 2 | Nota Fiscal, Item, Cód. Serviço, Descrição, Quantidade, Valor Unitário (BRL), Valor Total (BRL), Taxa de Câmbio, Valor USD |

Each file ends with a TOTAL row (Entity A: `TOTAL`; Entity B: `Total`; Entity C: `Total geral`).

## 5. Target data model

**Layer 1 — Staging (Python, raw + lineage).** One table per source, every row carrying lineage columns:
`source_file`, `source_sheet`, `source_row` (and `source_column` where relevant).

- `stg_billing_raw`: `entity`, `billing_month`, `invoice_no`, `line_no`, `service_code`,
  `service_name_local`, `quantity`, `unit_price_local`, `amount_local`, `currency`,
  `fx_rate_in_file`, `amount_usd_in_file`, `raw_total_local`, `raw_total_usd`, + lineage columns.
- `stg_contracts`, `stg_service_pricing`, `stg_budget`, `stg_volumes`, `stg_fx`.

**Layer 2 — Validated (dbt intermediate).** `int_billing_validated` recomputes the authoritative USD
amount and adds one boolean/flag column per rule (see §6). Key recomputed fields:

- `expected_unit_price_local` = `catalog_usd_price / fx(month, currency)` (per-unit) or
  `catalog_monthly_usd / fx(...)` (fixed).
- `expected_quantity` = `volumes[entity, month, metric]` (per-unit) or `1` (fixed).
- `expected_amount_usd` = `catalog_usd_price * quantity` (per-unit) or `catalog_monthly_usd` (fixed).
- `amount_usd` = `amount_local * fx` (this is the value used for the actuals mart).

**Layer 3 — Marts (dbt).**

- `mrt_service_taxonomy` (cluster → service mapping, §7).
- `mrt_actuals` — one row per (entity, month, service, cluster) in USD, with `amount_usd`, quantity, unit price.
- `mrt_budget_vs_actual` — actual vs budget, variance (USD and %), YTD, by entity/cluster/month.
- `mrt_findings` — one row per detected issue (rule id, severity, evidence, USD impact).
- `mrt_reconciliation` — per-file line-sum vs TOTAL (local and USD).

**Versioning/lineage:** every mart row carries `source_file`, `source_sheet`, `source_row`; dbt
provides model versioning and `dbt source freshness`/tests. Ingest appends a `load_id` (run timestamp
+ git SHA) so any historical run is reproducible.

## 6. Business rules & validation rules

Each rule produces a pass/fail flag and, on fail, a `mrt_findings` row with severity and evidence.
Tolerance for floating-point money is ±0.01 USD (or ±0.01 local).

| ID | Rule | Logic |
|---|---|---|
| V1 | Valid file | File opens; expected sheet exists; header row matches the entity's layout; period & currency parse from the title/period row and match the filename. |
| V2 | Completeness | Exactly 9 files per entity (Jan–Sep). Within a file, the expected service set is present: A = 14; B = 14 for Jan–Jun then 13 (SVC-06 ends with CT-1006 on 2026-06-30); C = 12. Flag missing/extra services. |
| V3 | Duplicates | A (entity, month, service) pair must appear once. Duplicate lines are flagged with both source rows. |
| V4 | FX consistency | Billing-file FX must equal `FX_Rates[month, currency]` (EUR/BRL). For Entity A, FX = 1. |
| V5 | Price consistency | `unit_price_local` must equal `catalog_usd_price / fx` (per-unit) or `catalog_monthly_usd / fx` (fixed). |
| V6 | Quantity reconciliation | Per-unit `quantity` must equal `Volumes[entity, month, metric]`. Fixed services must have `quantity = 1`. |
| V7 | Amount computation | `amount_local ≈ quantity × unit_price_local` and `amount_usd ≈ amount_local × fx`. |
| V8 | File-total reconciliation | `SUM(amount_local)` must equal the file's TOTAL row (local); same for USD. |
| V9 | Contract coverage | Service must have a `Service_Pricing` row for the entity; linked contract must cover the entity; billing month must be within `[Start Date, End Date]`. |
| V10 | Budget coverage | Service must exist in `Budget_2026` for (entity, month) — informational only (unbudgeted ≠ invalid). |

Severity: **Critical** = wrong money in USD; **High** = missing/unsupported charge; **Medium** = format/locale risk or catalog gaps; **Info** = adjustments/notes.

## 7. Findings (evidence-backed, quantified in USD)

These are the actual inconsistencies found in the provided files. Each cites file → tab → row.

| # | Severity | Rule | Finding | Evidence | USD impact |
|---|---|---|---|---|---|
| F1 | Critical | V4 | Entity B **August** uses FX **1.18** instead of the Treasury rate **1.087**. Every line's `Importe USD` is overstated. | `01_Billing_Layouts/Entity_B/Facturacion_EntB_ago26.xlsx` → tab `ago26` → col "Tipo de Cambio" (rows 2–14) vs `04_Volumes_and_FX.xlsx` → `FX_Rates` (2026-08 EUR = 1.087). | **+32,279 USD** (409,560.90 vs correct ≈ 377,282). |
| F2 | Critical | V6 | Entity B **July** SVC-01 quantity is 55,874,990; the operational volume is 24,293,474. The authorization charge is ~2.3× too large. | `Facturacion_EntB_jul26.xlsx` → tab `jul26` → row 2 (SVC-01) vs `04_Volumes_and_FX.xlsx` → `Volumes` (Entity B, 2026-07, Transactions = 24,293,474). | **+101,070 USD** (extra 31,581,516 units × 0.002928 EUR × 1.093). |
| F3 | Critical | V8 | Entity A **June** TOTAL (831,052.90) does **not** equal the sum of its 14 lines (818,652.90). | `EntityA_Billing_2026-06.xlsx` → tab `Billing` → row 18 (TOTAL) vs rows 4–17. | **+12,400 USD** discrepancy. |
| F4 | High | V3 | Entity A **May** bills SVC-07 (Token Vault Fee) **twice**. | `EntityA_Billing_2026-05.xlsx` → tab `Billing` → row 10 (line 7) and row 18 (line 15), both SVC-07 at 38,000. | **+38,000 USD** duplicate. |
| F5 | High | V9 | Entity A bills SVC-06 (Chargeback Alerts) in **Jul/Aug/Sep** although contract CT-1006 ended **2026-06-30**. | `EntityA_Billing_2026-07/08/09.xlsx` → tab `Billing` → SVC-06 rows (9,831.00 / 10,318.20 / 10,490.75) vs `02_Contract_Catalog.xlsx` → `Contracts` (CT-1006 End Date 2026-06-30). | **+30,640 USD** unsupported. |
| F6 | High | V9 | Entity B **March** bills **SVC-99 "Analytics Premium Add-on"** with no contract, no pricing and no budget. | `Facturacion_EntB_mar26.xlsx` → tab `mar26` → row 16 (SVC-99, 8,500 EUR). Absent from `Service_Pricing` and `Budget_2026`. | **+9,274 USD** unsupported. |
| F7 | High | V2 | Entity C **July** is missing **SVC-10 (Network & Security)**. | `Faturamento_EntC_2026_07.xlsx` → tab `Detalhe` → rows 3–13 skip from SVC-09 (row 9) to SVC-11 (row 10). | **−10,000 USD** under-billed. |
| F8 | High | V9 | Entity C **September** bills **SVC-03 (DCC Service)** although CT-1003 covers **A, B only** (no Entity C pricing/budget/volume). | `Faturamento_EntC_2026_09.xlsx` → tab `Detalhe` → row 15 (SVC-03) vs `Service_Pricing` (SVC-03 has no row for C). | **+860 USD** unsupported. |
| F9 | High | V5 | Entity C **September** bills SVC-05 (Device Fingerprint) at **0.017889 BRL/unit** instead of the catalog/FX derived **0.015556** (0.0028 USD / 0.18). | `Faturamento_EntC_2026_09.xlsx` → tab `Detalhe` → row 6 (SVC-05) vs `Service_Pricing` (SVC-05 = 0.0028 USD) and `FX_Rates` (2026-09 BRL = 0.18). | **+1,506 USD** overcharge. |
| F10 | Medium | V1 | Entity C **March** switches to Brazilian number format (comma decimals, dot thousands) and rounds unit prices to 2 decimals — a locale/precision hazard for naive parsing. | `Faturamento_EntC_2026_03.xlsx` → tab `Detalhe` (e.g., "0,02", "169.329,74") vs all other months ("0.018824", dot decimals). | Parsing risk; minor unit-price rounding. |
| F11 | Info | — | Entity A **April** carries a credit note (`SVC-02`, qty −1, −1,250 USD) referencing INV-A-202601 line 2. Legitimate adjustment that reduces the total; must be preserved and tracked, not treated as a duplicate. | `EntityA_Billing_2026-04.xlsx` → tab `Billing` → row 18 (line 15). | −1,250 USD (correct adjustment). |
| F12 | Info | — | Contract ID **CT-1008** is absent from the catalog (sequence jumps CT-1007 → CT-1009). No service references it, but it should be confirmed with the vendor owner. | `02_Contract_Catalog.xlsx` → `Contracts`. | No billing impact identified. |

**Net headline impact (directional):** the data over-states total Platform cost by roughly
**+226,029 USD gross**, offset by the Entity C July missing SVC-10 (**−10,000 USD**), for a **net
overstatement of ≈ +216,000 USD** across Jan–Sep 2026 vs. what the rules support. (The Entity A April
−1,250 USD credit note is a legitimate adjustment already reflected in the totals and is excluded from
this correction figure; the final figure is produced by `mrt_findings` at run time.)


## 8. Service clustering (two-level taxonomy)

Cluster = business capability; Service = the billed SKU. Justification: clusters mirror how the
Platform's services are actually consumed (payment flow, risk, data/tokenization, infra, support,
compliance, commercial levers) and map cleanly to the "Fraud cluster" the business already asks about.

| Cluster | Services | Rationale |
|---|---|---|
| Payment Processing | SVC-01 Authorization Gateway, SVC-02 3DS Authentication, SVC-03 DCC Service | Core transaction-flow fees, all per-transaction/3DS/DCC. |
| Fraud & Risk | SVC-04 Fraud Engine, SVC-05 Device Fingerprint, SVC-06 Chargeback Alerts | Prevention & risk-scoring services (the "Fraud" cluster). |
| Tokenization | SVC-07 Token Vault Fee, SVC-08 Token Provisioning | Vault + token lifecycle (CT-1007). |
| Infrastructure | SVC-09 Cloud Hosting, SVC-10 Network & Security | Fixed platform hosting/network. |
| Operations & Support | SVC-11 L2 Support | Application support (fixed). |
| Onboarding & Compliance | SVC-12 Merchant Onboarding, SVC-13 KYC Check | Merchant lifecycle + KYC (CT-1012). |
| Commercial Incentives | SVC-14 Approval Rate Incentive | Vendor amendment incentive (CT-1001A). |

This is stored in `mrt_service_taxonomy` and is the aggregation key for the executive view and the
AI tool (e.g., "Fraud cluster" = SVC-04 + SVC-05 + SVC-06).

## 9. Automation (re-runnable pipeline)

```
01_Billing_Layouts/ + 02..04 xlsx
        │  (1) Python ingest.py
        ▼
  stg_* raw tables (parquet/sqlite)  ──►  load_id + lineage (file, sheet, row)
        │  (2) Python validate.py  ──►  fixed-rule checks V1–V10 + anomaly checks
        ▼
  int_billing_validated (+ flags)
        │  (3) dbt run / dbt test  ──►  staging→validated→marts
        ▼
  mrt_actuals, mrt_budget_vs_actual, mrt_findings, mrt_reconciliation
        │  (4) export dashboard + findings.csv  +  notify
        ▼
  Executive dashboard / workbook
```

1. **Ingest (`ingest.py`)** — discovers the 27 files, detects the layout by entity (A/B/C), parses
   each with `openpyxl`, normalizes columns to the `stg_billing_raw` schema, and records lineage.
   Locale handling: strip currency symbols and thousand separators; a safe decimal parser handles the
   Entity C March comma-decimal variant.
2. **Validate (`validate.py`)** — applies V1–V10, writes `mrt_findings`, and raises a non-zero exit
   code if any **Critical** finding is present (so a scheduler can alert).
3. **Transform (dbt)** — SQL models + `dbt test` (`unique` on (entity, month, service), `not_null` on
   key/money columns, `accepted_values`, and a custom reconciliation test). `dbt build` refreshes marts.
4. **Output** — write the dashboard and a `findings.csv`; email/slack summary on failure.

**Re-runnability:** the pipeline is idempotent (a run key is `entity + billing_month`); when next
month's files arrive, the operator drops the new files into `01_Billing_Layouts/` and re-runs one
command (e.g., `make run` / `python pipeline.py`). Nothing is hard-coded to the 9-month window.

**Failure handling / logging:** structured logs (JSON) with run id, file, sheet, row, rule id, and
message. Failures are written to `logs/findings_<run_id>.log` and surfaced in the dashboard and the
AI tool. A run only "succeeds" when V1/V2/V4/V7/V8 pass; other rules produce warnings, not hard failure.

## 10. Executive output (actual vs. budget)

A single-page dashboard (workbook or Streamlit/Power BI) with four blocks:

1. **Actual vs Budget** — monthly and YTD totals by entity (A/B/C) and by cluster, with $ and %
   variance, colored by threshold (green within ±5%, amber ±5–15%, red >±15%).
2. **Variance drivers** — top contributors to variance by (entity, cluster, month), highlighting the
   known drivers (e.g., Entity B July authorization quantity, Entity B August FX, Entity A June total).
3. **Findings panel** — the `mrt_findings` table with severity, rule, evidence (file/tab/row) and USD impact.
4. **Data-quality scorecard** — file count, line count, reconciliation pass/fail, duplicate count, FX pass/fail.

The view is filtered to the reporting currency (USD) and supports drill-down to service and source row.

## 11. AI layer (grounded Q&A with citations & refusals)

A small SQL-first RAG agent over the validated marts (DuckDB/SQLite). The agent:

1. Maps the natural-language question to a structured query against `mrt_actuals`,
   `mrt_budget_vs_actual`, `mrt_findings` and the lineage columns.
2. Returns the answer **plus a citation block**: source file, tab, column, and row range for every
   number it used. Example citation: `Facturacion_EntB_mar26.xlsx → tab mar26 → row 16 (SVC-99)`.
3. States facts only from the data; when it is a derived/assumed value it labels it as such.

**Guaranteed answers (wired to the findings):**

| Question | Answer + citation |
|---|---|
| "Which services were billed to Entity B in March without a mapped contract?" | SVC-99 "Complemento Analítico Premium" — `Facturacion_EntB_mar26.xlsx → mar26 → row 16`, 8,500 EUR / 9,273.50 USD; no `Service_Pricing` row. |
| "Why does the total for Entity A in June differ from the file total?" | Sum of lines = 818,652.90 vs TOTAL = 831,052.90 (+12,400) — `EntityA_Billing_2026-06.xlsx → Billing → row 18 vs rows 4–17`. |
| "What is the budget variance for the Fraud cluster YTD, and what drives it?" | Fraud = SVC-04+SVC-05+SVC-06; the driver includes SVC-06 still billed Jul–Sep after CT-1006 expiry (+30,640 USD). |
| "Which charge appears more than once in the same month?" | SVC-07 Token Vault Fee in Entity A May — `EntityA_Billing_2026-05.xlsx → Billing → row 10 and row 18`. |
| "What was billed to Entity C in October?" | **Refuse/insufficient** — no October data exists (inputs cover Jan–Sep 2026); the agent says so instead of guessing. |

**Refusal / escalation policy (when the agent must not answer):**
- **No evidence / out of range:** months or entities not in the dataset (e.g., October 2026, Entity D).
- **Ambiguous identity:** a service/metric name that can't be uniquely mapped (e.g., "3DS" vs "3DS Authentication").
- **Judgment/prediction:** "is this a material misstatement?", "should we renegotiate?" — escalate to a human with the relevant findings.
- **PII / confidential requests** and anything requiring external data not present in the marts.

Every refusal returns the reason and the closest available, cited data (if any). The agent logs every
question/answer/refusal for later accuracy checks (§12).

## 12. Bonus items

- **Anomaly detection beyond fixed rules:** after fixed rules, run a monthly spend model per
  (entity, service) with Z-score / IQR on `amount_usd` and on unit-price/FX. This would surface the
  Entity B July quantity spike and the Entity B August FX spike automatically (not just via V4/V6).
- **Regression tests:** unit tests for each validation rule (V1–V10) on small fixtures, plus a golden
  dataset where every expected finding is asserted (prevents rule regressions).
- **Scenario / sensitivity:** an FX +10% toggle in the dashboard that re-computes EUR/BRL actuals and
  variance (useful because Entity B August is already an FX-sensitive outlier).
- **Monitoring plan:**
  - *Pipeline:* run the job on a schedule; alert (email/Slack) on non-zero exit, on any new Critical
    finding, or on a volume/FX source file going stale (dbt `source freshness`).
  - *AI tool:* log every Q/A/refusal; weekly spot-check of citations against source files; alert if
    refusal rate spikes (model confusion) or if an answer lacks a citation.

## 13. README outline (deliverable)

1. Data sources (the 5 inputs and their tabs/columns).
2. Business rules & assumptions (this spec §3, §6).
3. How to run (one command: `make run` / `python pipeline.py`; `dbt build`; `streamlit run dashboard.py`).
4. Outputs (marts, dashboard, `findings.csv`, logs).
5. Findings summary and how to action them.
6. Known limitations (below).
7. AI transparency: which AI tools were used, for what, and how outputs were validated.

## 14. Known limitations

- The catalog's `Service_Pricing` is the single source of truth for price; if the real contract
  contains volume discounts/tiers not in this file, they are out of scope here.
- The credit note (F10) is treated as a legitimate adjustment; its business rationale (why 1,250 USD)
  is not in the data and should be confirmed.
- CT-1008 is assumed to be an intentional gap; it should be confirmed with the contract owner.
- Budget exists for Jan–Dec but billing only Jan–Sep; comparisons are scoped to Jan–Sep.
- Floating-point artifacts (e.g., `8477.280000000001`) are normalized with a ±0.01 tolerance.

---

### Summary of the solution

Consolidate with lineage (Python) → validate with 10 explicit rules (V1–V10) → transform with
dbt/SQL into actuals, budget-vs-actual, findings and reconciliation marts → surface 11 evidence-backed
findings (net ≈ +182k USD overstatement) → expose an actual-vs-budget dashboard and a cited,
refusal-aware AI tool — all re-runnable from one command with clear failure logs.









