# Financial Risk Signal Aggregator
**Firstsource STEM POC · Leon Franklin · 2026**

A financial compliance intelligence tool that ingests transaction data, runs 10 AML
(Anti-Money Laundering) detectors, scores every finding with AI-generated rationales,
and presents results in a tactical analyst dashboard.

---

## What This POC Demonstrates

Compliance teams at financial institutions manually review thousands of transaction alerts.
This tool automates the first-pass triage layer:

1. **Accept any transaction dataset** (CSV or JSON)
2. **Run 10 rule-based detectors** (structuring, geo risk, crypto layering, etc.)
3. **Score every finding 0–100** using signal severity × base risk weighting
4. **Generate AI rationales** via Groq (llama-3.3-70b) for the top 8 findings
5. **Present results** in a prioritised dashboard analysts can navigate immediately

**For the POC demo:** because Groq's free tier has a 30 RPM limit that makes
live analysis take 2–4 minutes, the app loads pre-built results from
`outputs/sample_risk_output.json` with a simulated 2-second analysis delay.
The AI rationales in that file were generated from real API calls during development.

---

## How to Run the App

### Prerequisites
```
Python 3.10+
pip install -r requirements.txt
```

### Start
```bash
cd poc
streamlit run app/streamlit_app.py --server.port 8512
```

Then open `http://localhost:8512` in your browser.

### To demo the full pipeline (optional — not needed for the UI demo)
```bash
# Generate fresh AI-enriched output from the sample dataset
cd poc
python -c "
from backend.ingest import load_transactions
from backend.analyze import analyze_transactions, get_analysis_summary
from backend.risk_engine import build_risk_output
import json

df = load_transactions('data/financial_transactions.csv')
findings = analyze_transactions(df)
summary = get_analysis_summary(df, findings)
output = build_risk_output(findings, summary)
with open('outputs/sample_risk_output.json', 'w') as f:
    json.dump(output, f, indent=2)
print('Done —', len(findings), 'findings generated')
"
```
Requires `GROQ_API_KEY` in `poc/.env`. Then restart the Streamlit app to pick up the new output.

---

## How to Use the App (User Guide)

### Upload Tab
The starting point. The app loads empty — no data is displayed until you run analysis.

**Two ways to load data:**

1. **Drop Intel File** — drag and drop any `.csv` or `.xlsx` file into the drop zone,
   or click it to open a file browser. Accepts any file matching the transaction schema
   (see `backend/ingest.py` for required columns).

2. **Load Sample** — click "Load Sample 500 TXN" to pre-load the included
   `data/financial_transactions.csv` dataset without uploading anything.

**Then click "Initiate Analysis."**

The analysis runs for ~2 seconds (simulated), then the entire dashboard populates:
- **Signal Types** (left panel) — shows how many findings each of the 10 detectors flagged.
  Click any signal type to expand a dropdown of the customers flagged under it.
  Click a customer name to jump directly to their case file.
- **Center feed** — shows Analysis Complete with total findings and suspects flagged.
- **Active Threats** (right panel) — shows the 5 highest-risk findings with scores.
  Click any threat card to jump directly to that case file.
- **Top Priority Case** (bottom right) — shows the #1 ranked case with AI rationale preview.

**Data persists across navigation.** Once analysis runs, you can switch to Case Files,
Intel Map, and back to Upload — all panels retain the data. The only time data clears
is if you close the browser tab entirely (localStorage is cleared) or upload a new file.

---

### Case Files Tab
A full filterable list of all flagged findings, sorted by risk score.

- **Filter buttons** at the top: ALL / CRITICAL / THREAT / CAUTION / MONITOR
  These correspond to the four threat level bands (≥90 / 70–89 / 45–69 / <45).

- **Click any case row** to expand the full detail panel:
  - **Suspect Profile** — name, ID, signal type
  - **Threat Level** — score bar + level label + mission badge (IMMEDIATE ACTION / INVESTIGATE / WATCH LIST)
  - **Evidence Briefing** — what the detector found, with transaction dates and merchant categories
  - **AI Field Assessment** — 2–3 sentence compliance rationale from Groq (llama-3.3-70b)
  - **Mission Directive** — one concrete next step for the analyst

- **Deep linking** — clicking a name on the Upload tab opens Case Files with that case
  already expanded and scrolled into view (URL param: `?case=FIND-001`).

---

### Intel Map Tab
Signal distribution and geographic intelligence view.

- **Bar chart** — shows finding counts per signal type across the full dataset,
  so analysts can see which risk categories dominate this upload.
- **Case list** — filterable by signal type; same expand/collapse interaction as Case Files.
- Data loads from localStorage on page load, so it reflects the most recent analysis.

---

### About Tab
POC context — explains the brief, the AI stack, and the evaluation criteria.

---

## Architecture

```
User Browser
     │
     │  localhost:8512 (Streamlit)
     ▼
┌─────────────────────────────────┐
│  streamlit_app.py               │
│  • Renders a single <iframe>    │
│  • Starts _ThreadedHTTPServer   │
│    on port 8600 (static files)  │
│  • Copies HTML + demo_data.json │
│    to static/serve/ at startup  │
└─────────────┬───────────────────┘
              │ iframe src="http://localhost:8600/upload.html"
              ▼
┌─────────────────────────────────┐
│  _ThreadedHTTPServer (port 8600)│
│  Serves static files only:      │
│  • upload.html                  │
│  • case_files.html              │
│  • intel_map.html               │
│  • about.html                   │
│  • demo_data.json               │
│  • threat.mp4                   │
└─────────────────────────────────┘
```

### Why an iframe + separate file server?

Streamlit renders Python-controlled UI components. To build a custom tactical
dark-mode HTML/CSS/JS interface (with animations, custom fonts, complex layouts),
we need full control over the HTML. Streamlit's `st.markdown(unsafe_allow_html=True)`
only injects HTML fragments — it can't render a full document with its own `<head>`.

The solution: run a second HTTP server (port 8600) that serves static HTML files,
embed it via `<iframe>`. The iframe gets the full document context it needs;
Streamlit just provides the outer shell and keeps the server alive.

**Same-origin localStorage:** Because all HTML pages are served from the same
`localhost:8600` origin, they share `localStorage`. This is how analysis results
persist across tab navigation — `upload.html` writes to `localStorage["rsa_data"]`
after analysis completes, and `case_files.html` / `intel_map.html` read from it on load.

---

## Data Flow (Full Pipeline)

```
1. financial_transactions.csv
        │
        ▼
2. ingest.py → load_transactions()
   Validates schema, casts types, drops bad rows, sorts by date.
        │
        ▼
3. analyze.py → analyze_transactions()
   Runs 10 detectors in priority order. Each returns finding dicts.
   Deduplicates by (customer_id, signal_type).
        │
        ▼
4. risk_engine.py → build_risk_output()
   Scores every finding (BASE_SCORE × SEVERITY_MULTIPLIER).
   Calls Groq API for top 8 findings; uses templates for the rest.
   Assigns priority_rank 1–N.
   Saves to outputs/sample_risk_output.json.
        │
        ▼
5. data_bridge.py → build_rsa_data()
   Translates Python field names → JS RSA object shape.
   Adds level metadata (glyph, color, key) and mission metadata.
   Output is serialised to static/serve/demo_data.json at startup.
        │
        ▼
6. upload.html
   User presses "Initiate Analysis".
   JS fetches /demo_data.json (2-second simulated delay).
   Calls rerender(data) → populates Signal Types, Active Threats, Priority Case.
   Saves data to localStorage["rsa_data"].
        │
        ▼
7. case_files.html / intel_map.html
   On load: reads localStorage["rsa_data"].
   Populates UI from the same data.
   Supports ?case=FIND-001 deep-link param for direct case navigation.
```

---

## File Structure

```
poc/
├── app/
│   ├── streamlit_app.py      # Entry point — starts servers, renders iframe
│   ├── data_bridge.py        # Backend→frontend data transformer
│   ├── upload.html           # Upload tab (main dashboard)
│   ├── case_files.html       # Case Files tab (full case list)
│   ├── intel_map.html        # Intel Map tab (signal distribution)
│   ├── about.html            # About tab
│   ├── threat.mp4            # Background video for center feed
│   └── static/
│       └── serve/            # Runtime copy of HTML + demo_data.json
│           ├── upload.html   # ← copied from app/ at startup
│           ├── case_files.html
│           ├── intel_map.html
│           ├── about.html
│           ├── demo_data.json  ← generated from sample_risk_output.json
│           └── threat.mp4
│
├── backend/
│   ├── ingest.py             # Data loader + validator
│   ├── analyze.py            # 10 AML signal detectors
│   └── risk_engine.py        # Scoring + AI enrichment via Groq/Gemini
│
├── data/
│   └── financial_transactions.csv   # 500-row synthetic dataset
│
├── outputs/
│   └── sample_risk_output.json      # Pre-built AI-enriched analysis output
│
├── .env                      # API keys (not committed to git)
├── requirements.txt
└── README.md
```

---

## The 10 Detectors

| # | Signal | Severity | What It Detects |
|---|--------|----------|-----------------|
| 1 | Structuring | HIGH | 2+ transactions $9K–$9.999K within 7 days (CTR evasion) |
| 2 | Rapid Fund Movement | HIGH | Deposit ≥$20K then 80%+ withdrawn within 48h (layering) |
| 3 | Geo Risk | HIGH | 3+ transactions from FATF/sanctioned countries |
| 4 | Crypto + Wire | HIGH | 2+ crypto purchases → wire transfer within 14 days |
| 5 | High-Freq ATM | MEDIUM | 5+ ATM withdrawals in one calendar day |
| 6 | Dormant Reactivation | MEDIUM | 45+ day gap then ≥$5K transaction |
| 7 | Gambling Velocity | MEDIUM | 3+ consecutively escalating gambling deposits |
| 8 | Biz Cash Anomaly | MEDIUM | 2+ large vague withdrawals from business accounts |
| 9 | Neg Balance | LOW | Debit >$500 leaving balance <$100 |
| 10 | Stat Outlier | LOW | Z-score >2.5 vs peer category |

---

## Risk Scoring

```
risk_score = min(100, BASE_SCORE[signal_type] × SEVERITY_MULTIPLIER[severity])

Threat level bands:
  CRITICAL  ≥ 90   (☠  red)
  THREAT    70–89  (✖  orange)
  CAUTION   45–69  (▲  gold)
  MONITOR    0–44  (●  green)
```

---

## AI Stack

**Primary:** Groq API (llama-3.3-70b-versatile) — free, 30 RPM limit
**Fallback:** Google Gemini (gemini-2.0-flash-lite) — activates on Groq 429 errors

Only the top 8 findings by score receive live AI enrichment. Findings 9+ receive
professional pre-written compliance templates. This keeps total enrichment time
under 20 seconds when running the pipeline live.

**API keys required** (add to `poc/.env`):
```
GROQ_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here   # optional fallback
```

Get Groq key free at: https://console.groq.com
Get Gemini key free at: https://aistudio.google.com

---

## Design Decisions

**Why COD-style dark UI?**
The brief specified a tool for compliance analysts who work in monitoring contexts —
high-contrast dark interfaces reduce eye strain during extended review sessions and
visually communicate urgency through colour (red/orange/green threat levels) without
relying on text labels alone. The tactical aesthetic also made the POC visually
distinctive in the evaluation context.

**Why demo mode instead of live analysis?**
Groq's 30 RPM free-tier limit makes live enrichment of 27 findings take ~57 seconds.
For a POC demo, a 57-second wait defeats the purpose. Pre-building the output from
`sample_risk_output.json` lets us show genuinely AI-generated rationales (they were
produced by real Groq API calls) while delivering a 2-second demo experience.

**Why localStorage for cross-tab persistence?**
All HTML pages are served from the same origin (`localhost:8600`), so they share
a localStorage namespace. This is simpler and more reliable than alternatives like
URL params (limited size), postMessage (requires coordinated frames), or a server-side
session (adds complexity). The tradeoff is that data doesn't survive a browser restart —
acceptable for a POC demo environment.

**Why separate HTTP server instead of Streamlit components?**
Full-page custom HTML with custom fonts, CSS animations, and complex JavaScript
cannot be rendered inside Streamlit's component model. The iframe + static file server
pattern gives complete control over the HTML document while keeping Streamlit as the
outer shell that manages the Python process lifecycle.

---

## Submission Details

- **Email:** Ricardo.castillo@na.firstsource.com
- **Subject:** `STEM_POC_Leon Franklin_Financial Risk Signal Aggregator`
- **Deliverables:** Demo link or screen recording (max 3 min) + 5-slide deck + this repo
