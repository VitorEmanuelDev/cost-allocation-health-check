# Finance Data & Automation Challenge — Implementation

Dependency-free implementation of the `SPEC.md` solution: consolidate 27 billing files +
3 reference workbooks, validate them against the contract catalog / volumes / FX, and emit
evidence-backed findings, actual-vs-budget marts and an executive dashboard.

## Stack
- **Python 3.10+** (standard library only — no pip installs required)
- **SQL** via the built-in `sqlite3` (staging → marts, dbt-style layering)
- **AI layer** (`src/ask.py`): deterministic SQL-first Q&A with citations + refusals

## Data sources
- `01_Billing_Layouts/` — 27 monthly invoices (Entity A: USD, Entity B: EUR, Entity C: BRL; three layouts/languages)
- `02_Contract_Catalog.xlsx` — tabs `Contracts` and `Service_Pricing`
- `03_Budget_2026.xlsx` — tab `Budget_2026` (monthly budget by entity/service, USD)
- `04_Volumes_and_FX.xlsx` — tabs `Volumes` and `FX_Rates`

## How to run
```bash
cd Candidate_Pack
python pipeline.py          # ingest -> validate -> transform -> export
python -m unittest discover -s tests   # run regression tests
python -m src.ask "Which services were billed to Entity B in March without a mapped contract?"
```

## Outputs (written to `outputs/`)
| File | Content |
|---|---|
| `actuals.csv` | Consolidated USD actuals by entity/month/service/cluster |
| `budget_vs_actual.csv` | Actual vs budget + variance ($ and %) |
| `reconciliation.csv` | Per-file line-sum vs file TOTAL (local and USD) |
| `findings.csv` | Evidence-backed findings (rule, severity, file→sheet→row, USD impact) |
| `dashboard.html` | Self-contained executive view (open in a browser) |
| `finance_health.db` | SQLite database backing the marts and the AI tool |
| `run_metadata.json` | Version/run metadata (run id, file/line counts, finding counts) |

## Walkthrough video
`apresentacao_desafio_getnet.mp4` — presentation walkthrough of the solution. The file is ~45 MB
and stored with **Git LFS**, so after cloning run:

```bash
git lfs install
git lfs pull      # downloads the video; without it you only get a small pointer file
```

## Business rules (see `SPEC.md` §6)
V1 valid file · V2 completeness · V3 duplicates · V4 FX consistency · V5 price consistency ·
V6 quantity reconciliation · V7 amount computation · V8 file-total reconciliation ·
V9 contract coverage · V10 budget coverage.

## Findings (summary)
| Rule | Finding | USD impact |
|---|---|---|
| V4 | Entity B August uses FX 1.18 instead of 1.087 | +32,279 |
| V6 | Entity B July SVC-01 quantity wrong (55.9M vs 24.3M) | +101,070 |
| V8 | Entity A June TOTAL ≠ sum of lines | +12,400 |
| V3 | Entity A May SVC-07 billed twice | +38,000 |
| V9 | Entity A SVC-06 billed after CT-1006 expiry (Jul–Sep) | +30,640 |
| V9 | Entity B March unmapped SVC-99 | +9,274 |
| V2 | Entity C July missing SVC-10 | −10,000 |
| V9 | Entity C September SVC-03 with no Entity C contract | +860 |
| V5 | Entity C September SVC-05 unit price error (0.017889 vs 0.015556) | +1,506 |

Net correction ≈ **+216,000 USD** overstatement.

## Assumptions & limitations
See `SPEC.md` §3 and §14. In short: `Service_Pricing` is the price source of truth; the
Entity A April credit note is a legitimate adjustment; CT-1008 is assumed an intentional gap.

## AI transparency
This implementation uses a deterministic intent matcher (no external LLM) over the SQLite
marts. Every answer cites file → sheet → row → column and refuses on out-of-scope/ambiguous questions.
