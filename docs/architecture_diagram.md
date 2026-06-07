# Architecture Diagram — Financial Risk Signal Aggregator

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER INPUT                               │
│         CSV Upload  ·  JSON Upload  ·  Sample Dataset           │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    backend/ingest.py                            │
│  • Load CSV or JSON (auto-detect by extension)                  │
│  • Parse and validate transaction_date                          │
│  • Cast amount, account_balance_after to float                  │
│  • Fill missing notes, country, merchant_category               │
│  • Drop rows with unparseable dates or amounts                  │
│  • Return clean pd.DataFrame (sorted by date)                   │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   backend/analyze.py                            │
│                                                                 │
│  Rule-Based Signal Detectors (9):                               │
│  ├─ detect_structuring()          → HIGH                        │
│  ├─ detect_rapid_fund_movement()  → HIGH                        │
│  ├─ detect_unusual_geography()    → HIGH                        │
│  ├─ detect_crypto_wire_pattern()  → HIGH                        │
│  ├─ detect_high_frequency_atm()   → MEDIUM                      │
│  ├─ detect_dormant_reactivation() → MEDIUM                      │
│  ├─ detect_escalating_gambling()  → MEDIUM                      │
│  ├─ detect_business_cash_anomaly()→ MEDIUM                      │
│  └─ detect_negative_balance_risk()→ LOW                         │
│                                                                 │
│  Statistical Detector:                                          │
│  └─ zscore_anomalies()            → LOW                         │
│     (SciPy Z-score per merchant_category group, threshold 2.5)  │
│                                                                 │
│  Output: list[dict] — one finding dict per flagged signal       │
│  Each dict: finding_id, customer_id, signal_type, severity,     │
│             transaction_ids, amounts, evidence_summary, raw_data│
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  backend/risk_engine.py                         │
│                                                                 │
│  Score each finding (deterministic):                            │
│  └─ score_finding() — BASE_SCORES × SEVERITY_MULTIPLIER        │
│     Range: 1–100                                                │
│                                                                 │
│  AI Reasoning (per finding):                                    │
│  ├─ Primary:  Grok (Groq / llama-3.3-70b)                      │
│  └─ Fallback: Giovanni (Gemini 2.0 Flash Lite)                  │
│                                                                 │
│  Per finding → AI returns:                                      │
│  ├─ ai_rationale      (2–3 sentence compliance assessment)      │
│  ├─ recommended_action (one specific next step)                 │
│  └─ analyst_priority  (URGENT · REVIEW · MONITOR)              │
│                                                                 │
│  Output: sorted by risk_score DESC, priority_rank assigned      │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  app/streamlit_app.py                           │
│                                                                 │
│  ┌─ COMMAND CENTER (sidebar) ──────────────────────────────┐   │
│  │  File upload · Sample data · Initiate Analysis · Stats  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─ UPLOAD TAB ─────────────────────────────────────────────┐  │
│  │  Hero video background (threat.mp4 loop)                 │  │
│  │  HUD Stats Banner: Threats · Suspects · Critical · Signals│  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ CASE FILES TAB ─────────────────────────────────────────┐  │
│  │  Filter pills: ALL · CRITICAL · THREAT · CAUTION · MONITOR│ │
│  │  Priority Threat Table (sortable dataframe)              │  │
│  │  Case File Cards (collapsible dossier per finding):      │  │
│  │    Suspect Profile → Threat Meter → Evidence Briefing    │  │
│  │    → AI Field Assessment → Mission Directive             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ INTEL MAP TAB ───────────────────────────────────────────┐  │
│  │  Plotly horizontal bar: findings by signal type × severity│  │
│  │  Signal breakdown table                                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─ ABOUT TAB ───────────────────────────────────────────────┐  │
│  │  Tech stack · Risk signals · Built by                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```
poc/
├── app/
│   ├── streamlit_app.py          ← UI (this skill's output)
│   └── assets/
│       └── background.mp4        ← Looping hero video
├── backend/
│   ├── ingest.py                 ← Data loading + validation
│   ├── analyze.py                ← 9 signal detectors + Z-score
│   └── risk_engine.py            ← AI reasoning layer (Grok/Gemini)
├── data/
│   ├── financial_transactions.csv← 500-row synthetic dataset
│   └── data_dictionary.md        ← Column docs + risk signal index
├── outputs/
│   └── sample_risk_output.json   ← Sample full analysis output
├── docs/
│   ├── README.md                 ← This file
│   └── architecture_diagram.md   ← Architecture (this file)
├── design/
│   └── design_spec.md            ← UI design contract
├── config/
│   └── build_config.json         ← Build configuration
├── .env                          ← API keys (not committed)
├── .env.example                  ← Key template (committed)
└── requirements.txt              ← Python dependencies
```

## Component Dependencies

```
streamlit_app.py
    └── imports → ingest.py
    └── imports → analyze.py
    └── imports → risk_engine.py
                      └── calls → Grok API (primary)
                      └── calls → Gemini API (fallback)
```
