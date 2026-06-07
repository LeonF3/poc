"""
risk_engine.py — AI Enrichment & Risk Scoring
================================================
Takes raw findings from analyze.py, assigns numerical risk scores, and enriches
the top N findings with AI-generated compliance rationales via the Groq API
(primary) or Gemini API (fallback). Lower-ranked findings get pre-written
professional templates instead of live AI calls.

Pipeline position:
  analyze.py → risk_engine.py → data_bridge.py → UI

Why two AI providers?
  "Grok" (Groq / llama-3.3-70b) is the primary provider. It is genuinely free
  with no daily hard cap — only a 30 RPM (requests per minute) rate limit.
  "Giovanni" (Google Gemini gemini-2.0-flash-lite) is the fallback for when
  Groq returns a 429 rate-limit error. OpenAI ("Oliver") was considered but
  requires prepaid credits, so it is excluded from this POC.

Why AI_CAP = 8?
  At 30 RPM with a 2.1s gap between calls (safely under the limit), calling AI
  for all 27 findings would take ~57 seconds. Capping at 8 (the highest-risk
  cases that matter most to analysts) keeps enrichment under 20 seconds while
  ensuring the findings that actually drive decisions get genuine AI assessment.

POC note:
  In the demo deployment (poc/app/streamlit_app.py), AI enrichment is NOT called
  at runtime. The sample_risk_output.json was pre-generated with live AI calls
  and is served statically. This avoids API latency during the live demo while
  still showcasing real AI-generated rationales in the output.
"""

import os
import re
import time
import threading
from datetime import datetime
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=_ENV_PATH)

# --- Provider model identifiers ---
GROQ_MODEL   = "llama-3.3-70b-versatile"   # Groq's most capable free model
GEMINI_MODEL = "gemini-2.0-flash-lite"      # Google's lightweight fast model

# --- Base risk scores per signal type (before severity multiplier) ---
# These represent the maximum risk score for a perfect-severity instance of each signal.
# Structuring and geo-risk score highest because they are direct regulatory violations
# (BSA structuring is a federal crime; OFAC sanctions exposure triggers mandatory review).
BASE_SCORES = {
    "structuring":           90,
    "rapid_fund_movement":   85,
    "unusual_geography":     88,
    "crypto_wire_pattern":   82,
    "high_frequency_atm":    65,
    "dormant_reactivation":  60,
    "escalating_gambling":   70,
    "business_cash_anomaly": 72,
    "negative_balance_risk": 45,
    "statistical_outlier":   35,
}

# --- Severity multipliers ---
# HIGH severity findings hit the base score at full value.
# MEDIUM and LOW findings are discounted proportionally.
# This creates score separation within each signal type (e.g. a MEDIUM structuring
# finding scores lower than a HIGH one, even though structuring is always serious).
SEVERITY_MULTIPLIER = {
    "HIGH":   1.0,
    "MEDIUM": 0.75,
    "LOW":    0.5,
}

# --- AI system prompt ---
# Written specifically for a non-technical compliance audience.
# The structured output format (RATIONALE / ACTION / PRIORITY) allows reliable
# regex parsing by _parse_ai_response() without depending on JSON mode.
SYSTEM_PROMPT = """You are a senior financial compliance analyst reviewing flagged transaction data. Your job is to assess each risk signal and produce a clear, professional rationale that a compliance officer can act on.

For each finding, you will:
1. Confirm or challenge the risk classification based on the evidence provided
2. Explain the specific compliance concern in plain language (no jargon)
3. Assign a recommended priority: URGENT, REVIEW, or MONITOR
4. Suggest one concrete next step for the analyst

Be concise. Maximum 3 sentences per rationale. Do not repeat the raw data back. Write for a non-technical compliance audience.

Respond in this exact format:
RATIONALE: [2-3 sentence compliance assessment]
ACTION: [one specific next step for the analyst]
PRIORITY: [URGENT | REVIEW | MONITOR]"""


def score_finding(finding: dict) -> int:
    """
    Calculate the final 0–100 risk score for a finding.

    Formula: BASE_SCORES[signal_type] × SEVERITY_MULTIPLIER[severity], capped at 100.
    The score determines:
      - Priority ranking in the UI (higher score → lower rank number → appears first)
      - Threat level label (CRITICAL ≥90, THREAT 70–89, CAUTION 45–69, MONITOR <45)
      - Which findings get AI enrichment (top AI_CAP by score)
    """
    base       = BASE_SCORES.get(finding["signal_type"], 50)
    multiplier = SEVERITY_MULTIPLIER.get(finding["severity"], 0.75)
    return min(100, int(base * multiplier))


def build_finding_prompt(finding: dict, analysis_summary: dict) -> str:
    """
    Build the user-turn prompt sent to the AI for a specific finding.

    The dataset context block (total transactions, date range, customer count) is
    included so the AI can frame its assessment relative to the overall risk landscape.
    For example, 5 flagged transactions out of 50 total is very different from
    5 flagged out of 50,000 — the AI uses this context to calibrate urgency.
    """
    return f"""DATASET CONTEXT:
- Total transactions analyzed: {analysis_summary['total_transactions']}
- Date range: {analysis_summary['date_range']['start']} to {analysis_summary['date_range']['end']}
- Total customers: {analysis_summary['total_customers']}

FLAGGED FINDING:
- Customer: {finding['customer_name']} ({finding['customer_id']})
- Signal Type: {finding['signal_type'].replace('_', ' ').title()}
- Initial Severity: {finding['severity']}
- Evidence: {finding['evidence_summary']}
- Transactions involved: {len(finding['transaction_ids'])}
- Amounts: {finding['amounts']}
- Dates: {finding['transaction_dates']}
- Merchant Categories: {finding['merchant_categories']}

Provide your compliance assessment for this finding."""


def _parse_ai_response(text: str) -> tuple:
    """
    Extract RATIONALE, ACTION, and PRIORITY from the AI's structured text response.

    Uses regex with DOTALL so multi-line rationales are captured correctly.
    Falls back to the full response text if RATIONALE: isn't found — this handles
    cases where the model ignores the format instruction.
    Defaults to PRIORITY=REVIEW if the priority field is missing or unrecognised.
    """
    rationale = ""
    action    = ""
    priority  = "REVIEW"

    rationale_match = re.search(r"RATIONALE:\s*(.+?)(?=ACTION:|$)",  text, re.DOTALL | re.IGNORECASE)
    action_match    = re.search(r"ACTION:\s*(.+?)(?=PRIORITY:|$)",   text, re.DOTALL | re.IGNORECASE)
    priority_match  = re.search(r"PRIORITY:\s*(URGENT|REVIEW|MONITOR)", text, re.IGNORECASE)

    if rationale_match:
        rationale = rationale_match.group(1).strip()
    if action_match:
        action = action_match.group(1).strip()
    if priority_match:
        priority = priority_match.group(1).upper()

    if not rationale:
        rationale = text.strip()[:400]  # graceful degradation for malformed responses

    return rationale, action, priority


def _call_groq(prompt: str) -> str:
    """
    Call Groq (llama-3.3-70b) — primary AI provider.
    temperature=0.3 keeps responses consistent and professional without being robotic.
    max_tokens=600 is enough for 3-sentence rationales with room to spare.
    """
    from groq import Groq
    client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        max_tokens=600,
        temperature=0.3,
    )
    return response.choices[0].message.content


def _call_gemini(prompt: str) -> str:
    """
    Call Google Gemini (gemini-2.0-flash-lite) — fallback provider.
    Gemini's API doesn't support a separate system message in the same way as
    OpenAI-compatible APIs, so we prepend the system prompt to the user message.
    """
    import google.genai as genai
    client      = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
    full_prompt = SYSTEM_PROMPT + "\n\n" + prompt
    response    = client.models.generate_content(model=GEMINI_MODEL, contents=full_prompt)
    return response.text


def _call_ai(prompt: str, provider: str = "groq") -> str:
    """
    Try the primary provider; auto-fallback to Gemini on rate-limit (429) errors.
    Other error types (auth failures, network errors) are re-raised immediately —
    they won't be fixed by switching provider.
    """
    if provider == "groq":
        try:
            return _call_groq(prompt)
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                time.sleep(5)  # brief back-off before trying Gemini
                try:
                    return _call_gemini(prompt)
                except Exception:
                    raise e  # re-raise original Groq error if Gemini also fails
            raise
    else:
        return _call_gemini(prompt)


# --- Rate limiter state ---
# Groq's free tier allows 30 RPM. We enforce 2.1s between calls (~28.5 RPM),
# leaving a small buffer for network variance.
AI_CAP    = 8         # max number of findings that receive live AI enrichment
_rate_lock = threading.Lock()
_next_slot = [0.0]    # earliest Unix timestamp the next API call may start
_CALL_GAP  = 2.1      # seconds between consecutive AI calls


def _rate_limited_call(prompt: str) -> str:
    """
    Token-bucket rate limiter.

    The critical design: we hold the lock only long enough to *record* our
    reservation (next slot time), then release it and sleep outside the lock.
    If we slept inside the lock, all worker threads would queue up waiting for
    the lock itself, not for the rate limit — serializing execution even though
    ThreadPoolExecutor was supposed to run them concurrently.

    With sleep outside the lock, multiple threads can reserve future slots
    simultaneously and sleep in parallel, so the actual throughput is limited
    only by the rate, not by lock contention.
    """
    with _rate_lock:
        now      = time.time()
        start_at = max(now, _next_slot[0])
        _next_slot[0] = start_at + _CALL_GAP  # reserve this slot
        sleep_for = start_at - now
    if sleep_for > 0:
        time.sleep(sleep_for)   # sleep OUTSIDE the lock
    return _call_ai(prompt, provider="groq")


def _enrich_one(finding: dict, analysis_summary: dict) -> dict:
    """
    Enrich a single finding with AI rationale, action, and priority.

    Uses a per-call ThreadPoolExecutor with shutdown(wait=False) to implement
    a hard 10-second timeout. The standard approach of future.result(timeout=10)
    alone doesn't work because the default ThreadPoolExecutor context manager
    calls shutdown(wait=True) on exit, which blocks until the thread finishes —
    defeating the timeout. shutdown(wait=False) abandons the thread immediately
    when the timeout fires, at the cost of a small thread leak (acceptable here
    since each pool has only 1 worker and Python's GC will clean it up).

    On any failure (timeout, API error, parse error), falls back to template.
    """
    import concurrent.futures as cf

    prompt = build_finding_prompt(finding, analysis_summary)

    # Reserve rate-limit slot before starting the timeout clock
    with _rate_lock:
        now      = time.time()
        start_at = max(now, _next_slot[0])
        _next_slot[0] = start_at + _CALL_GAP
        sleep_for = start_at - now
    if sleep_for > 0:
        time.sleep(sleep_for)

    pool = cf.ThreadPoolExecutor(max_workers=1)
    fut  = pool.submit(_call_ai, prompt, "groq")
    try:
        ai_text  = fut.result(timeout=10)
        rationale, action, priority = _parse_ai_response(ai_text)
        finding["ai_rationale"]       = rationale
        finding["recommended_action"] = action
        finding["analyst_priority"]   = priority
        finding["ai_provider"]        = "Grok (Groq / llama-3.3-70b)"
    except Exception:
        _apply_signal_fallback(finding)  # template fallback on any failure
    finally:
        pool.shutdown(wait=False)  # abandon thread immediately — don't block
    return finding


# --- Per-signal professional rationale templates ---
# Used for two purposes:
#   1. Findings beyond AI_CAP (ranks 9+) always get templates — no API call.
#   2. Any AI call that fails (timeout, parse error) falls back to the template.
# Templates are written at the same quality level as AI output so the UI
# experience is consistent regardless of which path generated the rationale.
_SIGNAL_TEMPLATES = {
    "structuring": (
        "Transaction pattern shows structured deposits designed to evade BSA reporting thresholds. "
        "Multiple sub-$10,000 transactions across a short window are a hallmark of layering activity.",
        "File Suspicious Activity Report (SAR) within 30 days. Freeze incremental deposits pending review.",
        "URGENT",
    ),
    "rapid_fund_movement": (
        "Funds are entering and exiting the account within hours, consistent with layering or pass-through "
        "activity. Legitimate velocity at this level is uncommon for the customer's stated profile.",
        "Request source-of-funds documentation and freeze outbound transfers pending KYC refresh.",
        "URGENT",
    ),
    "unusual_geography": (
        "Transactions routed through or to high-risk jurisdictions without a clear business rationale. "
        "FATF-listed or sanctioned-country exposure elevates the risk profile significantly.",
        "Cross-reference against OFAC sanctions list and escalate to MLRO for correspondent bank review.",
        "URGENT",
    ),
    "crypto_wire_pattern": (
        "Combined crypto exchange activity and international wire transfers indicate potential conversion "
        "layering — a common technique to obscure the origin of illicit funds.",
        "Obtain blockchain analytics report and compare wallet addresses against known risk databases.",
        "URGENT",
    ),
    "high_frequency_atm": (
        "Repeated ATM withdrawals at or near daily limits suggest deliberate cash extraction to avoid "
        "electronic traceability, consistent with smurfing or cash-out schemes.",
        "Interview account holder on cash usage purpose. Flag for enhanced transaction monitoring.",
        "REVIEW",
    ),
    "dormant_reactivation": (
        "A previously inactive account has resumed significant transaction activity, often indicating "
        "account takeover or use of a shell account activated for a specific illicit purpose.",
        "Verify current beneficial owner identity. Request updated KYC documentation before processing further transactions.",
        "REVIEW",
    ),
    "escalating_gambling": (
        "Rapid escalation in gambling platform deposits and withdrawals may indicate money laundering "
        "through gaming — a known technique exploiting casino-style platforms for fund cleansing.",
        "Review gambling platform regulatory status and request documented winnings/loss statements.",
        "REVIEW",
    ),
    "business_cash_anomaly": (
        "Cash receipts are disproportionate to the business type and revenue profile on record. "
        "This discrepancy may indicate co-mingling of illicit cash with business revenues.",
        "Request audited financial statements and POS transaction logs for the past 12 months.",
        "REVIEW",
    ),
    "negative_balance_risk": (
        "Overdraft cycling and negative-balance transactions suggest either financial distress or "
        "deliberate manipulation to obscure account balances from detection systems.",
        "Place account on enhanced monitoring. Require prepaid balance before processing outbound transfers.",
        "MONITOR",
    ),
    "statistical_outlier": (
        "Z-score analysis flags this account as a statistical outlier relative to peer-group behavior. "
        "The deviation may indicate anomalous activity warranting manual verification.",
        "Conduct peer-comparison review and request a 90-day account activity explanation from the customer.",
        "MONITOR",
    ),
}


def _apply_signal_fallback(finding: dict) -> None:
    """Apply the pre-written template for this finding's signal type."""
    sig = finding.get("signal_type", "statistical_outlier")
    tpl = _SIGNAL_TEMPLATES.get(sig, _SIGNAL_TEMPLATES["statistical_outlier"])
    finding["ai_rationale"]       = tpl[0]
    finding["recommended_action"] = tpl[1]
    finding["analyst_priority"]   = tpl[2]
    finding["ai_provider"]        = "rule-engine"


def _apply_fallback(finding: dict, reason: str = "") -> None:
    """Alias used by the parallel executor's error handler."""
    _apply_signal_fallback(finding)


def generate_risk_summary(findings: list, analysis_summary: dict) -> list:
    """
    Score, rank, and enrich all findings. Returns the complete enriched list
    sorted by risk score descending.

    Processing steps:
      1. Score every finding (deterministic formula, no API calls).
      2. Sort descending by score — this determines priority rank.
      3. Split into ai_batch (top AI_CAP) and rule_batch (the rest).
      4. Apply templates to rule_batch immediately (no waiting).
      5. Run AI enrichment on ai_batch in parallel (3 workers, rate-limited).
      6. Re-sort (AI may have changed some scores) and assign priority_rank.
    """
    import concurrent.futures

    # Step 1-2: score and sort
    for f in findings:
        f["risk_score"] = score_finding(f)
    findings.sort(key=lambda x: x["risk_score"], reverse=True)

    # Step 3: split
    ai_batch   = findings[:AI_CAP]
    rule_batch = findings[AI_CAP:]

    # Step 4: apply templates to lower-priority findings (instant, no API call)
    for f in rule_batch:
        _apply_fallback(f)

    # Step 5: parallel AI enrichment with rate limiting
    # max_workers=3 means up to 3 threads can sleep concurrently while waiting
    # for their rate-limit slot — but only one API call runs at a time (enforced
    # by the 2.1s gap, not by a mutex on the API call itself).
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_enrich_one, f, analysis_summary): f for f in ai_batch}
        for fut in concurrent.futures.as_completed(futs):
            try:
                fut.result()
            except Exception as e:
                _apply_fallback(futs[fut], str(e))

    # Step 6: final sort and rank assignment
    enriched = findings  # all dicts mutated in-place by _enrich_one
    enriched.sort(key=lambda x: x["risk_score"], reverse=True)
    for i, f in enumerate(enriched):
        f["priority_rank"] = i + 1  # 1-indexed; rank #1 = highest risk

    return enriched


def build_risk_output(findings: list, analysis_summary: dict) -> dict:
    """
    Top-level function: takes raw findings + summary, enriches everything,
    and returns the complete JSON-serialisable output object.

    This is the object saved to outputs/sample_risk_output.json.
    data_bridge.py reads this object and transforms it into the RSA shape
    that the HTML frontend expects.
    """
    enriched = generate_risk_summary(findings, analysis_summary)
    return {
        "generated_at":    datetime.now().isoformat(),
        "analysis_summary": analysis_summary,
        "risk_findings":    enriched,
    }
