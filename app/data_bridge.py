"""
data_bridge.py — Backend-to-Frontend Data Transformer
=======================================================
Converts the Python dict produced by risk_engine.build_risk_output() into the
"RSA object" shape that the HTML/JS frontend expects via the window.RSA global.

Pipeline position:
  risk_engine.py → data_bridge.py → demo_data.json (static file) → JS frontend

Why a separate bridge module?
  The backend (analyze.py, risk_engine.py) uses snake_case Python dicts with
  field names that make sense in a data-processing context ("signal_type",
  "analyst_priority"). The frontend HTML was designed around a different shape
  ("signal", "key", "glyph", "color") that maps to CSS variables and UI labels.
  Rather than coupling either side to the other's conventions, this module owns
  the translation. If the frontend UI changes, only data_bridge.py needs updating.

Key mappings:
  signal_type (Python)  → SIGNAL_META key (JS)   e.g. "structuring" → "STRUCTURING"
  analyst_priority       → MISSION_MAP entry       e.g. "URGENT" → {cls, glyph, text, color}
  risk_score             → LEVEL_MAP entry         e.g. 90 → {key:"CRITICAL", glyph:"☠", color:"var(--red)"}

The RSA object shape (window.RSA in JS):
  {
    CASES:         [ { id, name, cust, signal, sig, score, rank, txns, evidence,
                       rationale, directive, level, mission } ],
    SIGNAL_COUNTS: [ { signal: {key, short, full}, count: N } ],
    STATS:         { total, flagged, immediate, rangeStart, rangeEnd, txnCount },
    analyst:       "LEON FRANKLIN"
  }
"""

import json
import re
import os

POC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mapping from Python snake_case signal type names → JS uppercase keys used in the HTML.
# The JS frontend uses these keys to: filter cases, match signal counts, look up metadata.
SIGNAL_KEY_MAP = {
    "structuring":           "STRUCTURING",
    "rapid_fund_movement":   "RAPID",
    "unusual_geography":     "GEO",
    "crypto_wire_pattern":   "CRYPTO",
    "high_frequency_atm":    "ATM",
    "dormant_reactivation":  "DORMANT",
    "escalating_gambling":   "GAMBLING",
    "business_cash_anomaly": "BIZCASH",
    "negative_balance_risk": "NEGBAL",
    "statistical_outlier":   "OUTLIER",
}

# Mission directives — map analyst_priority to the full UI display object.
# cls:   CSS class suffix for badge styling (badge--urgent, badge--review, badge--monitor)
# glyph: Icon shown on the mission badge
# text:  Short action label ("IMMEDIATE ACTION", "INVESTIGATE", "WATCH LIST")
# color: CSS variable reference for the theme colour
# hex:   Hex fallback for contexts where CSS vars aren't available (e.g. canvas rendering)
# bg:    Background tint for the mission badge
MISSION_MAP = {
    "URGENT":  {"key": "URGENT",  "cls": "urgent",  "glyph": "⚡", "text": "IMMEDIATE ACTION", "color": "var(--red)",    "hex": "#EF4444", "bg": "#2D0A0A"},
    "REVIEW":  {"key": "REVIEW",  "cls": "review",  "glyph": "🔍", "text": "INVESTIGATE",      "color": "var(--orange)", "hex": "#F97316", "bg": "#2D1A0A"},
    "MONITOR": {"key": "MONITOR", "cls": "monitor", "glyph": "👁",  "text": "WATCH LIST",       "color": "var(--green)",  "hex": "#22C55E", "bg": "#0A2D0A"},
}

# Threat level bands — score → label, colour, and glyph.
# Evaluated in order (highest first); first match wins.
# color: CSS variable (used in JS as c.level.color, referenced as --c in CSS)
# hex:   Raw hex for contexts that can't resolve CSS vars (unused in current UI but kept for future use)
# glyph: Icon shown next to the level label in the case card
LEVEL_MAP = [
    (90, 100, "CRITICAL", "#EF4444", "var(--red)",    "☠"),
    (70,  89, "THREAT",   "#F97316", "var(--orange)", "✖"),
    (45,  69, "CAUTION",  "#F59E0B", "var(--gold)",   "▲"),
    ( 0,  44, "MONITOR",  "#22C55E", "var(--green)",  "●"),
]

# Signal metadata — displayed in the Signal Types panel and case cards.
# key:   Must match the JS filter logic (c.signal === sc.signal.key) in case_files.html and intel_map.html
# short: Compact label for narrow UI columns
# full:  Full descriptive name shown in case detail panels
SIGNAL_META = {
    "STRUCTURING": {"key": "STRUCTURING", "short": "STRUCTURING",   "full": "Structuring / Smurfing"},
    "RAPID":       {"key": "RAPID",       "short": "RAPID FUNDS",   "full": "Rapid Fund Movement"},
    "GEO":         {"key": "GEO",         "short": "GEO RISK",      "full": "Geographic Risk"},
    "CRYPTO":      {"key": "CRYPTO",      "short": "CRYPTO+WIRE",   "full": "Crypto + Wire Layering"},
    "ATM":         {"key": "ATM",         "short": "HIGH-FREQ ATM", "full": "High-Frequency ATM"},
    "DORMANT":     {"key": "DORMANT",     "short": "DORMANT",       "full": "Dormant Reactivation"},
    "GAMBLING":    {"key": "GAMBLING",    "short": "GAMBLING",      "full": "Gambling Velocity"},
    "BIZCASH":     {"key": "BIZCASH",     "short": "BIZ CASH",      "full": "Business Cash Intensity"},
    "NEGBAL":      {"key": "NEGBAL",      "short": "NEG BALANCE",   "full": "Negative Balance Cycling"},
    "OUTLIER":     {"key": "OUTLIER",     "short": "STAT OUTLIER",  "full": "Statistical Outlier (Z-Score)"},
}


def _get_level(score: int) -> dict:
    """
    Map a 0–100 risk score to its threat level metadata.

    Returns a dict with key, label, color (CSS var), hex, and glyph.
    The JS frontend accesses c.level.key, c.level.color, c.level.glyph, c.level.label.
    All four must be present — the UI will render "UNDEFINED" if any are missing.
    """
    for lo, hi, label, hex_c, css_c, glyph in LEVEL_MAP:
        if lo <= score <= hi:
            return {"key": label, "label": label, "color": css_c, "hex": hex_c, "glyph": glyph}
    # Fallback for any score outside 0–100 (shouldn't happen but defensive)
    return {"key": "MONITOR", "label": "MONITOR", "color": "var(--green)", "hex": "#22C55E", "glyph": "●"}


def build_rsa_data(risk_output: dict, analyst: str = "LEON FRANKLIN") -> dict:
    """
    Convert the risk_engine output dict into the RSA object the HTML frontend expects.

    Called at two points:
      1. During pre-build (streamlit_app.py startup) to generate demo_data.json
      2. Potentially called live if real analysis is ever re-enabled

    The CASES array is the most complex transformation — each finding dict from
    Python gets merged with signal metadata, level metadata, and mission metadata
    to produce a self-contained case object the JS renderer can display without
    any further lookups.

    The SIGNAL_COUNTS array preserves the canonical signal order defined in sig_order
    rather than whatever order the findings happened to come in — this keeps the
    Signal Types panel consistent regardless of which signals fired.
    """
    summary  = risk_output["analysis_summary"]
    findings = risk_output["risk_findings"]

    # --- Build CASES array ---
    cases = []
    for f in findings:
        sig_key  = SIGNAL_KEY_MAP.get(f["signal_type"], "OUTLIER")
        sig_meta = SIGNAL_META.get(sig_key, {"key": sig_key, "short": sig_key, "full": sig_key})
        score    = f["risk_score"]
        level    = _get_level(score)
        priority = f.get("analyst_priority", "REVIEW")
        mission  = MISSION_MAP.get(priority, MISSION_MAP["REVIEW"])

        # Build the transaction summary string shown in the Evidence Briefing
        dates    = f.get("transaction_dates", [])
        date_str = f"{dates[0]} – {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "N/A")
        cats     = ", ".join(sorted(set(f.get("merchant_categories", []))))

        cases.append({
            "id":        f["finding_id"],              # e.g. "FIND-001" — used as the deep-link key
            "name":      f["customer_name"],
            "cust":      f["customer_id"],             # e.g. "CUST-1001"
            "signal":    sig_key,                      # e.g. "STRUCTURING" — used for filtering
            "sig":       sig_meta,                     # {key, short, full} — used for display
            "score":     score,                        # 0-100 numeric score
            "rank":      f.get("priority_rank", 0),   # 1-indexed priority rank
            "txns":      f"{len(f.get('transaction_ids', []))} txns · {date_str} · {cats}",
            "evidence":  f.get("evidence_summary", ""),
            "rationale": f.get("ai_rationale", ""),   # AI or template text
            "directive": f.get("recommended_action", "Manual review required."),
            "level":     level,                        # {key, label, color, hex, glyph}
            "mission":   mission,                      # {key, cls, glyph, text, color, hex, bg}
        })

    # --- Build SIGNAL_COUNTS array ---
    # Canonical signal order matches the Signal Types panel in upload.html.
    # Counts are computed from findings (not pre-aggregated) so they reflect
    # whatever came out of the detectors for this specific dataset.
    sig_order = ["STRUCTURING","RAPID","GEO","CRYPTO","ATM","DORMANT","GAMBLING","BIZCASH","NEGBAL","OUTLIER"]
    raw_counts = {}
    for f in findings:
        k = SIGNAL_KEY_MAP.get(f["signal_type"], "OUTLIER")
        raw_counts[k] = raw_counts.get(k, 0) + 1

    signal_counts = [
        {"signal": SIGNAL_META[k], "count": raw_counts.get(k, 0)}
        for k in sig_order
    ]

    # --- Build STATS object ---
    urgent_count = len([f for f in findings if f.get("analyst_priority") == "URGENT"])
    stats = {
        "total":      summary["total_findings"],       # total number of flagged findings
        "flagged":    summary["customers_flagged"],    # number of unique customers flagged
        "immediate":  urgent_count,                   # URGENT priority count → status bar
        "rangeStart": summary["date_range"]["start"], # dataset date range for the footer
        "rangeEnd":   summary["date_range"]["end"],
        "txnCount":   summary["total_transactions"],  # shown in "X TXN Loaded" footer
    }

    return {
        "CASES":         cases,
        "SIGNAL_COUNTS": signal_counts,
        "STATS":         stats,
        "analyst":       analyst,  # displayed in the nav badge (top-right avatar)
    }


def inject_data_into_html(html_content: str, rsa_data: dict, video_b64: str = "") -> str:
    """
    Replace the hardcoded demo data block in an HTML file with a live data injection script.

    This was the original live-injection approach — it searched for the IIFE comment
    block at the top of the data script and replaced the entire block with a script
    that sets window.RSA directly.

    Note: In the current POC architecture, this function is no longer called at
    request time. Instead, build_rsa_data() runs at startup, the result is serialised
    to demo_data.json, and the frontend fetches it via XHR. This function is retained
    for reference and potential future use if the live-injection approach is restored.

    The pattern match looks for the data IIFE that starts with a comment block
    containing "RISK SIGNAL AGGREGATOR". If not found, falls back to injecting
    before </head>.
    """
    injection = f"""<script>
/* === LIVE DATA INJECTION — generated by data_bridge.py === */
(function(global){{
  "use strict";
  var d = {json.dumps(rsa_data, ensure_ascii=False)};

  // Rebuild level/mission helpers to match HTML's expected shape
  function level(score){{
    var L=[
      [90,100,"CRITICAL","#EF4444"],[70,89,"THREAT","#F97316"],
      [45,69,"CAUTION","#F59E0B"],[0,44,"MONITOR","#22C55E"]
    ];
    for(var i=0;i<L.length;i++){{ if(score>=L[i][0]&&score<=L[i][1]) return {{label:L[i][2],color:L[i][3]}}; }}
    return {{label:"MONITOR",color:"#22C55E"}};
  }}
  function mission(p){{
    var M={{URGENT:{{cls:"urgent",glyph:"\\u26a1",text:"IMMEDIATE ACTION"}},
            REVIEW:{{cls:"review",glyph:"\U0001F50D",text:"INVESTIGATE"}},
            MONITOR:{{cls:"monitor",glyph:"\U0001F441",text:"WATCH LIST"}}}};
    return M[p]||M.REVIEW;
  }}

  global.RSA = global.RSA || {{}};
  global.RSA.CASES         = d.CASES;
  global.RSA.SIGNAL_COUNTS = d.SIGNAL_COUNTS;
  global.RSA.STATS         = d.STATS;
  global.RSA.analyst       = d.analyst;

  global.RSA.CASES.forEach(function(c){{
    c.level   = c.level   || level(c.score);
    c.mission = c.mission || mission("REVIEW");
  }});
}})(typeof window !== "undefined" ? window : this);
</script>"""

    pattern = r'<script>\s*/\*\s*={3,}\s*\n\s*RISK SIGNAL AGGREGATOR.*?</script>'
    match   = re.search(pattern, html_content, re.DOTALL)
    if match:
        html_content = html_content[:match.start()] + injection + html_content[match.end():]
    else:
        html_content = html_content.replace("</head>", injection + "\n</head>", 1)

    if video_b64:
        html_content = re.sub(
            r'(<video[^>]*><source src=")[^"]*(")',
            rf'\1data:video/mp4;base64,{video_b64}\2',
            html_content
        )

    return html_content


def load_html_with_data(filename: str, risk_output: dict, video_b64: str = "") -> str:
    """
    Load an HTML file from the app directory and inject live risk data into it.
    Convenience wrapper around inject_data_into_html — retained for future use.
    """
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    rsa_data = build_rsa_data(risk_output)
    return inject_data_into_html(html, rsa_data, video_b64)
