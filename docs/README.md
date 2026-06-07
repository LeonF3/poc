# 🛡 Financial Risk Signal Aggregator
### AI-Powered Financial Threat Intelligence · Firstsource STEM POC

---

## What It Does

Ingests financial transaction data (CSV or JSON), runs 9 rule-based risk signal detectors
plus Z-score statistical anomaly detection, then uses AI (Grok via Groq) to generate a
prioritised compliance summary — so analysts focus on the highest-risk findings first,
not everything at once.

**Built for:** Compliance and risk teams at financial services firms who manually review
large volumes of transaction alerts and need AI to surface what matters most.

---

## How to Run

### Requirements
- Python 3.11+
- Groq API key (free — no credit card: [console.groq.com](https://console.groq.com))

### Setup
```bash
git clone <repo>
cd poc
pip install -r requirements.txt
cp .env.example .env
# Add your GROQ_API_KEY to .env
streamlit run app/streamlit_app.py
```

### With Sample Data
Click **🎯 LOAD SAMPLE DATASET** in the Command Center sidebar — no upload needed.
The app ships with a 500-row synthetic financial transaction dataset covering 25 customers
and 9 embedded risk signal types.

---

## Architecture

See [architecture_diagram.md](architecture_diagram.md) for the full data flow.

```
CSV / JSON Upload
      ↓
  ingest.py          — load, validate, clean DataFrame
      ↓
  analyze.py         — 9 signal detectors + Z-score anomaly detection
      ↓
  risk_engine.py     — Grok AI → rationale + priority per finding
      ↓
  streamlit_app.py   — gamified COD-MW-style compliance dashboard
```

---

## Tools Used

| Layer | Tool | Purpose |
|-------|------|---------|
| Language | Python 3.11 | Core logic |
| Data handling | Pandas + NumPy | Ingestion, analysis, anomaly detection |
| Anomaly detection | Z-Score (SciPy) | Statistical outlier identification |
| AI reasoning | Grok (Groq / llama-3.3-70b) | Risk rationale + priority assignment |
| AI fallback | Google Gemini (gemini-2.0-flash-lite) | Fallback if Groq quota exceeded |
| UI | Streamlit | Dashboard interface |
| Visualisation | Plotly Express | Threat intelligence chart |
| Env management | python-dotenv | API key handling |

---

## Risk Signals Detected (9 rule-based + 1 statistical)

| Signal | Severity | Description |
|--------|----------|-------------|
| Structuring / Smurfing | HIGH | Multiple transactions just below $10,000 threshold within 7 days |
| Rapid Fund Movement | HIGH | Large deposit followed by 80%+ withdrawal within 48 hours |
| Unusual Geography | HIGH | 3+ transactions from high-risk countries (FATF/OFAC list) |
| Crypto + Wire Pattern | HIGH | Multiple crypto purchases followed by wire transfer within 14 days |
| High-Frequency ATM | MEDIUM | 5+ ATM withdrawals by same customer in one calendar day |
| Dormant Account Reactivation | MEDIUM | No activity for 45+ days, then large transaction |
| Escalating Gambling | MEDIUM | 3+ gambling transactions with increasing amounts |
| Business Cash Anomaly | MEDIUM | Large business account cash withdrawals labeled "miscellaneous" |
| Negative Balance Risk | LOW | Debit transaction leaves account balance below $100 |
| Statistical Outlier | LOW | Z-score > 2.5 standard deviations from merchant category mean |

---

## Data Assumptions

- Dataset: 500 synthetic financial transactions across 25 customers (90-day window)
- Generated deterministically (`random.seed(42)`) — fully reproducible
- All data is synthetic — no real customer information used
- HIGH risk: 5 customers (3+ signal types each)
- MEDIUM risk: 8 customers (1–2 signal types each)
- LOW risk: 12 customers (clean transaction patterns)
- High-risk countries: NG, RU, IR, KP, MM (per FATF/OFAC guidance, illustrative only)
- `flagged_by_system` is False across all rows — simulating a legacy system with low
  detection rates, allowing the AI layer to demonstrate value by surfacing missed signals

---

## Sample Input → Output

### Input (CSV row)
```
TXN-100162,CUST-1001,James Okafor,checking,2026-05-08,withdrawal,9069.09,atm,Chase ATM,US,False,41823.45,cash withdrawal
```

### Output (risk finding — abridged)
```json
{
  "finding_id": "FIND-001",
  "customer_name": "James Okafor",
  "signal_type": "structuring",
  "severity": "HIGH",
  "risk_score": 90,
  "analyst_priority": "URGENT",
  "evidence_summary": "2 transactions of $9,069, $9,455 within 7 days — possible structuring below $10,000 threshold",
  "ai_rationale": "The flagged transactions suggest potential structuring activity by James Okafor, where he may be intentionally breaking up transactions to avoid $10,000 reporting thresholds.",
  "recommended_action": "Review James Okafor's transaction history for the past 6 months to identify any other similar patterns of activity."
}
```

---

## Submission

- **Email:** Ricardo.castillo@na.firstsource.com
- **Subject:** STEM_POC_Leon Franklin_Financial Risk Signal Aggregator
- **Built by:** Leon Franklin
