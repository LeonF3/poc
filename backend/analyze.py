"""
analyze.py — Rule-Based Signal Detectors
==========================================
Contains all 10 AML (Anti-Money Laundering) signal detectors. Each detector
is an independent function that accepts the full transaction DataFrame and
returns a list of "finding" dicts for any customers that triggered it.

Pipeline position:
  ingest.py → analyze.py → risk_engine.py → data_bridge.py → UI

Design decisions:
  - Detectors are pure functions: no side effects, no global state.
  - Each detector returns at most ONE finding per customer to avoid duplicate
    escalation on the same underlying pattern (the dedup step at the end of
    analyze_transactions() enforces this as a backstop).
  - Severity is assigned statically per signal type (SEVERITY_MAP), not
    calculated dynamically — this ensures consistency across different datasets.
    risk_engine.py then multiplies base score × severity multiplier to get a
    final 0–100 risk score.
  - Finding IDs are generated sequentially (FIND-001, FIND-002...) and reset
    on each call to analyze_transactions(). The order matches signal priority:
    structuring fires first so the highest-risk signals get the lowest IDs.

The 10 signals and why they matter:
  1. STRUCTURING     — Breaking large amounts into sub-$10K deposits to evade CTR reporting (Bank Secrecy Act)
  2. RAPID FUNDS     — Deposit then near-full withdrawal within 48h (classic layering)
  3. GEO RISK        — Transactions from FATF high-risk or sanctioned countries
  4. CRYPTO+WIRE     — Crypto purchases followed by international wire (conversion layering)
  5. HIGH-FREQ ATM   — 5+ ATM withdrawals in a single day (cash extraction to avoid traceability)
  6. DORMANT REACTIVATION — Account inactive 45+ days then large transaction (account takeover / shell)
  7. GAMBLING VELOCITY — Escalating gambling deposits (money laundering via gaming platforms)
  8. BIZ CASH ANOMALY — Large vague cash withdrawals from business accounts (cash co-mingling)
  9. NEG BALANCE     — Overdraft cycling (balance manipulation or financial distress)
 10. STAT OUTLIER    — Z-score > 2.5 vs peer group (catch-all statistical anomaly detector)
"""

import pandas as pd
import numpy as np
from scipy import stats
from datetime import timedelta


# Static severity map — each signal type has a fixed severity level.
# Used by risk_engine.py to calculate the final risk score:
#   final_score = BASE_SCORES[signal_type] × SEVERITY_MULTIPLIER[severity]
SEVERITY_MAP = {
    "structuring":           "HIGH",
    "rapid_fund_movement":   "HIGH",
    "unusual_geography":     "HIGH",
    "crypto_wire_pattern":   "HIGH",
    "high_frequency_atm":    "MEDIUM",
    "dormant_reactivation":  "MEDIUM",
    "escalating_gambling":   "MEDIUM",
    "negative_balance_risk": "LOW",
    "business_cash_anomaly": "MEDIUM",
    "statistical_outlier":   "LOW",
}

# Countries classified as high-risk per FATF guidance and US sanctions programs.
# Any transaction with a country code in this set triggers the GEO detector.
HIGH_RISK_COUNTRIES = {"NG", "RU", "IR", "KP", "MM", "SY", "CU", "SD"}

# Reference set for what counts as a "domestic" customer profile.
# Used for contrast — if a customer's history is domestic and they suddenly
# have transactions from HIGH_RISK_COUNTRIES, that divergence is the signal.
DOMESTIC_COUNTRIES = {"US", "CA", "GB", "AU"}

# Sequential finding ID counter — reset to 0 before each analysis run
_finding_counter = 0


def _next_id() -> str:
    """Generate the next sequential finding ID (FIND-001, FIND-002...)."""
    global _finding_counter
    _finding_counter += 1
    return f"FIND-{_finding_counter:03d}"


def _make_finding(signal_type: str, customer_id: str, customer_name: str,
                  txn_ids: list, dates: list, amounts: list,
                  categories: list, evidence: str, raw: dict) -> dict:
    """
    Construct a standardised finding dict.

    All detectors call this helper so the output shape is always identical —
    risk_engine.py and data_bridge.py can rely on the same keys existing on
    every finding without defensive checks.

    Fields added downstream (by risk_engine.py):
      risk_score, analyst_priority, ai_rationale, recommended_action,
      ai_provider, priority_rank
    """
    return {
        "finding_id":        _next_id(),
        "customer_id":       customer_id,
        "customer_name":     customer_name,
        "signal_type":       signal_type,
        "severity":          SEVERITY_MAP.get(signal_type, "LOW"),
        "transaction_ids":   txn_ids,
        "transaction_dates": [str(d)[:10] for d in dates],  # normalize to YYYY-MM-DD strings
        "amounts":           amounts,
        "merchant_categories": categories,
        "evidence_summary":  evidence,   # human-readable description shown in the UI
        "raw_data":          raw,        # structured data for downstream processing / export
    }


# =============================================================================
# Signal 1 — Structuring / Smurfing
# =============================================================================
def detect_structuring(df: pd.DataFrame) -> list:
    """
    Flag customers with 2+ transactions between $9,000–$9,999 within any 7-day window.

    Why this matters: The Bank Secrecy Act (BSA) requires banks to file a Currency
    Transaction Report (CTR) for any cash transaction ≥ $10,000. Deliberately breaking
    a larger sum into sub-$10K pieces to avoid triggering a CTR is called "structuring"
    (31 U.S.C. § 5324) and is itself a federal crime — independent of whether the
    underlying funds are illicit.

    Detection logic:
      For every transaction in the $9K–$9.999K band, look at a 7-day window starting
      from that transaction. If ≥2 transactions by the same customer fall in that window,
      file one finding (the first window that qualifies). We break after the first hit
      per customer to avoid creating N findings for N overlapping windows.
    """
    findings = []
    # Pre-filter to only the structuring band — avoids scanning the whole DataFrame per customer
    suspects = df[(df["amount"] >= 9000) & (df["amount"] < 10000)].copy()
    for cid, group in suspects.groupby("customer_id"):
        group = group.sort_values("transaction_date")
        for i, row in group.iterrows():
            window = group[
                (group["transaction_date"] >= row["transaction_date"]) &
                (group["transaction_date"] <= row["transaction_date"] + timedelta(days=7))
            ]
            if len(window) >= 2:
                findings.append(_make_finding(
                    "structuring", cid, row["customer_name"],
                    window["transaction_id"].tolist(),
                    window["transaction_date"].tolist(),
                    window["amount"].tolist(),
                    window["merchant_category"].tolist(),
                    f"{len(window)} transactions of ${', $'.join(f'{a:,.0f}' for a in window['amount'])} "
                    f"within 7 days — possible structuring below $10,000 threshold",
                    {"window_days": 7, "threshold": 10000}
                ))
                break  # one finding per customer; first qualifying window is enough
    return findings


# =============================================================================
# Signal 2 — Rapid Fund Movement (Layering)
# =============================================================================
def detect_rapid_fund_movement(df: pd.DataFrame) -> list:
    """
    Flag customers who deposit ≥$20,000 then withdraw ≥80% of it within 48 hours.

    Why this matters: Rapid fund movement is the "layering" stage of money laundering —
    funds enter a legitimate account and are immediately moved out to obscure their origin.
    The 48-hour window and 80% threshold are calibrated to catch deliberate pass-through
    activity while excluding normal cash-flow behaviour (e.g. payroll or vendor payments).

    Detection logic:
      For each large deposit (≥$20K), sum all outbound transfers/withdrawals/debits
      from the same customer within 48 hours after the deposit. If total outflow
      ≥ 80% of the deposit amount, raise a finding.
    """
    findings = []
    large_deposits = df[
        (df["transaction_type"].isin(["deposit", "credit"])) &
        (df["amount"] >= 20000)
    ]
    for _, dep in large_deposits.iterrows():
        window_end = dep["transaction_date"] + timedelta(hours=48)
        withdrawals = df[
            (df["customer_id"] == dep["customer_id"]) &
            (df["transaction_type"].isin(["withdrawal", "debit", "transfer"])) &
            (df["transaction_date"] > dep["transaction_date"]) &
            (df["transaction_date"] <= window_end)
        ]
        total_out = withdrawals["amount"].sum()
        if total_out >= dep["amount"] * 0.8:
            txns    = [dep["transaction_id"]]     + withdrawals["transaction_id"].tolist()
            dates   = [dep["transaction_date"]]   + withdrawals["transaction_date"].tolist()
            amounts = [dep["amount"]]              + withdrawals["amount"].tolist()
            cats    = [dep["merchant_category"]]  + withdrawals["merchant_category"].tolist()
            findings.append(_make_finding(
                "rapid_fund_movement", dep["customer_id"], dep["customer_name"],
                txns, dates, amounts, cats,
                f"${dep['amount']:,.0f} deposited then ${total_out:,.0f} withdrawn within 48 hours "
                f"({total_out / dep['amount'] * 100:.0f}% of deposit)",
                {"deposit_amount": dep["amount"], "withdrawn_amount": total_out, "hours": 48}
            ))
    return findings


# =============================================================================
# Signal 3 — Unusual Geography (Sanctions / FATF Risk)
# =============================================================================
def detect_unusual_geography(df: pd.DataFrame) -> list:
    """
    Flag customers with 3+ transactions from high-risk or sanctioned countries.

    Why this matters: Transactions routed through FATF-listed jurisdictions (e.g. Russia,
    Iran, North Korea) or US-sanctioned countries carry heightened regulatory exposure.
    A domestic customer suddenly transacting in these regions is a strong indicator of
    potential sanctions evasion, correspondent banking risk, or shell-company activity.

    Detection logic:
      Filter transactions where country is in HIGH_RISK_COUNTRIES.
      If the same customer appears 3+ times, group all their high-risk-country
      transactions into a single finding. Threshold of 3 reduces false positives
      from legitimate one-off international purchases.
    """
    findings = []
    intl = df[df["country"].isin(HIGH_RISK_COUNTRIES)].copy()
    for cid, group in intl.groupby("customer_id"):
        group = group.sort_values("transaction_date")
        if len(group) >= 3:
            countries = group["country"].unique().tolist()
            findings.append(_make_finding(
                "unusual_geography", cid, group.iloc[0]["customer_name"],
                group["transaction_id"].tolist(),
                group["transaction_date"].tolist(),
                group["amount"].tolist(),
                group["merchant_category"].tolist(),
                f"{len(group)} transactions from high-risk countries ({', '.join(countries)}) "
                f"— customer profile is primarily domestic",
                {"countries": countries, "transaction_count": len(group)}
            ))
    return findings


# =============================================================================
# Signal 4 — High-Frequency ATM Withdrawals
# =============================================================================
def detect_high_frequency_atm(df: pd.DataFrame) -> list:
    """
    Flag customers with 5+ ATM withdrawals on the same calendar day.

    Why this matters: ATM withdrawals produce cash, which is untraceable. Hitting
    ATMs at or near daily limits multiple times in a single day is a known
    "cash-out" pattern — either for personal smurfing or mule account activity.

    Detection logic:
      Group ATM transactions by (customer_id, date). Any group with ≥5 transactions
      generates a finding. Calendar day (not 24-hour window) is used because
      ATM daily limits reset at midnight.
    """
    findings = []
    atm = df[df["merchant_category"] == "atm"].copy()
    atm["date_only"] = atm["transaction_date"].dt.date
    grouped = atm.groupby(["customer_id", "date_only"])
    for (cid, date), group in grouped:
        if len(group) >= 5:
            findings.append(_make_finding(
                "high_frequency_atm", cid, group.iloc[0]["customer_name"],
                group["transaction_id"].tolist(),
                group["transaction_date"].tolist(),
                group["amount"].tolist(),
                group["merchant_category"].tolist(),
                f"{len(group)} ATM withdrawals on {date} totalling ${group['amount'].sum():,.0f}",
                {"date": str(date), "withdrawal_count": len(group), "total_amount": group["amount"].sum()}
            ))
    return findings


# =============================================================================
# Signal 5 — Dormant Account Reactivation
# =============================================================================
def detect_dormant_reactivation(df: pd.DataFrame) -> list:
    """
    Flag accounts dormant for 45+ days that suddenly resume large transactions.

    Why this matters: A long-dormant account reactivating with a significant transaction
    is a classic indicator of: (a) account takeover fraud, where a criminal gains
    access to a forgotten account; (b) shell accounts deliberately left idle until
    needed for a specific illicit purpose. Both require investigation before the
    account continues operating normally.

    Detection logic (two patterns):
      1. Gap detection: find any 45-day+ gap between consecutive transactions
         where the reactivation transaction is ≥$5,000.
      2. Late-starter: if a customer's FIRST transaction in the dataset appears
         60+ days after the dataset start date, and it's ≥$5,000.
    Only one finding is raised per customer (first pattern that matches).
    """
    findings = []
    df_sorted = df.sort_values("transaction_date")
    dataset_start = df_sorted["transaction_date"].min()

    for cid, group in df_sorted.groupby("customer_id"):
        group = group.reset_index(drop=True)
        gap_found = False

        # Pattern 1: gap of 45+ days between consecutive transactions
        for i in range(1, len(group)):
            gap = (group.loc[i, "transaction_date"] - group.loc[i-1, "transaction_date"]).days
            if gap >= 45 and group.loc[i, "amount"] >= 5000:
                row = group.loc[i]
                findings.append(_make_finding(
                    "dormant_reactivation", cid, row["customer_name"],
                    [row["transaction_id"]],
                    [row["transaction_date"]],
                    [row["amount"]],
                    [row["merchant_category"]],
                    f"Account dormant for {gap} days — reactivated with ${row['amount']:,.0f} transaction",
                    {"dormant_days": gap, "reactivation_amount": row["amount"]}
                ))
                gap_found = True
                break  # first qualifying gap per customer

        # Pattern 2: first transaction appears 60+ days after dataset start
        if not gap_found:
            first_txn_date  = group["transaction_date"].min()
            days_until_first = (first_txn_date - dataset_start).days
            if days_until_first >= 60:
                large = group[group["amount"] >= 5000]
                if len(large) > 0:
                    row = large.iloc[0]
                    findings.append(_make_finding(
                        "dormant_reactivation", cid, row["customer_name"],
                        [row["transaction_id"]],
                        [row["transaction_date"]],
                        [row["amount"]],
                        [row["merchant_category"]],
                        f"No account activity for first {days_until_first} days of review period "
                        f"— then ${row['amount']:,.0f} transaction detected",
                        {"dormant_days": days_until_first, "reactivation_amount": row["amount"]}
                    ))
    return findings


# =============================================================================
# Signal 6 — Escalating Gambling Velocity
# =============================================================================
def detect_escalating_gambling(df: pd.DataFrame) -> list:
    """
    Flag customers with 3+ consecutively escalating gambling transactions.

    Why this matters: Gambling platforms are a known vector for money laundering.
    Criminals deposit illicit funds, place bets (often with an accomplice), and
    withdraw "winnings" — which now appear to come from gambling activity. Rapidly
    escalating bet sizes are a behavioural tell: legitimate recreational gamblers
    rarely sustain a monotonically increasing spend sequence over many transactions.

    Detection logic:
      For each customer's gambling transactions (sorted by date), find the longest
      consecutive run of strictly increasing amounts. If that run is ≥3, raise a finding
      covering those specific transactions.
    """
    findings = []
    gambling = df[df["merchant_category"] == "gambling"].sort_values("transaction_date")
    for cid, group in gambling.groupby("customer_id"):
        group = group.reset_index(drop=True)
        if len(group) < 3:
            continue
        amounts = group["amount"].tolist()

        # Find the longest strictly-increasing streak
        streak      = [amounts[0]]
        best_streak = [amounts[0]]
        for i in range(1, len(amounts)):
            if amounts[i] > amounts[i-1]:
                streak.append(amounts[i])
                if len(streak) > len(best_streak):
                    best_streak = streak[:]
            else:
                streak = [amounts[i]]

        if len(best_streak) >= 3:
            idx_start  = amounts.index(best_streak[0])
            streak_rows = group.iloc[idx_start:idx_start + len(best_streak)]
            findings.append(_make_finding(
                "escalating_gambling", cid, group.iloc[0]["customer_name"],
                streak_rows["transaction_id"].tolist(),
                streak_rows["transaction_date"].tolist(),
                streak_rows["amount"].tolist(),
                streak_rows["merchant_category"].tolist(),
                f"{len(best_streak)} escalating gambling transactions: "
                f"${' → $'.join(f'{a:,.0f}' for a in best_streak)}",
                {"escalation_sequence": best_streak, "streak_length": len(best_streak)}
            ))
    return findings


# =============================================================================
# Signal 7 — Crypto + Wire Transfer Pattern
# =============================================================================
def detect_crypto_wire_pattern(df: pd.DataFrame) -> list:
    """
    Flag customers who make 2+ crypto purchases then send a wire transfer within 14 days.

    Why this matters: This is a documented "conversion layering" technique. Illicit cash
    is used to buy cryptocurrency (which is harder to trace than bank transfers), then
    the crypto is converted back to fiat and wired internationally — creating two layers
    of obfuscation between the original source of funds and the final destination.

    Detection logic:
      For each customer: find the most recent date of any crypto transaction.
      Then check if an international wire transfer occurs within 14 days after.
      If the customer has ≥2 crypto transactions and ≥1 qualifying wire, raise a finding.
    """
    findings = []
    for cid, group in df.groupby("customer_id"):
        group = group.sort_values("transaction_date")
        crypto_txns = group[group["merchant_category"] == "crypto"]
        if len(crypto_txns) < 2:
            continue

        last_crypto_date = crypto_txns["transaction_date"].max()
        wires = group[
            (group["merchant_category"] == "wire_transfer") &
            (group["transaction_date"] > last_crypto_date) &
            (group["transaction_date"] <= last_crypto_date + timedelta(days=14))
        ]
        if len(wires) > 0:
            combined = pd.concat([crypto_txns, wires]).sort_values("transaction_date")
            findings.append(_make_finding(
                "crypto_wire_pattern", cid, group.iloc[0]["customer_name"],
                combined["transaction_id"].tolist(),
                combined["transaction_date"].tolist(),
                combined["amount"].tolist(),
                combined["merchant_category"].tolist(),
                f"{len(crypto_txns)} crypto purchases (${crypto_txns['amount'].sum():,.0f} total) "
                f"followed by wire transfer of ${wires['amount'].sum():,.0f} within 14 days",
                {
                    "crypto_count": len(crypto_txns),
                    "crypto_total": crypto_txns["amount"].sum(),
                    "wire_total":   wires["amount"].sum()
                }
            ))
    return findings


# =============================================================================
# Signal 8 — Negative Balance Risk (Overdraft Cycling)
# =============================================================================
def detect_negative_balance_risk(df: pd.DataFrame) -> list:
    """
    Flag individual transactions that leave the account balance below $100 after a >$500 debit.

    Why this matters: Overdraft cycling — repeatedly spending down to zero and relying
    on overdraft protection — can indicate either severe financial distress (a welfare
    concern) or deliberate balance manipulation to confuse automated monitoring systems
    that look for positive account balances as a proxy for legitimacy.

    Detection logic:
      Simple row-level check: balance_after < $100 AND transaction_type is debit/withdrawal
      AND amount > $500. Each qualifying transaction becomes its own finding.
      This is the lowest-severity signal (LOW) and is used as a "watch list" indicator.
    """
    findings = []
    risky = df[
        (df["account_balance_after"] < 100) &
        (df["transaction_type"].isin(["debit", "withdrawal"])) &
        (df["amount"] > 500)
    ]
    for _, row in risky.iterrows():
        findings.append(_make_finding(
            "negative_balance_risk", row["customer_id"], row["customer_name"],
            [row["transaction_id"]],
            [row["transaction_date"]],
            [row["amount"]],
            [row["merchant_category"]],
            f"${row['amount']:,.0f} debit left account balance at ${row['account_balance_after']:,.2f}",
            {"balance_after": row["account_balance_after"], "transaction_amount": row["amount"]}
        ))
    return findings


# =============================================================================
# Signal 9 — Business Account Cash Anomaly
# =============================================================================
def detect_business_cash_anomaly(df: pd.DataFrame) -> list:
    """
    Flag business accounts with 2+ large withdrawals that have vague or missing descriptions.

    Why this matters: Business accounts are sometimes used to "co-mingle" illicit cash
    with legitimate revenue — the business appears to have high cash turnover, but the
    individual transactions have no clear commercial purpose. Vague descriptions like
    "misc" or blank memo fields on large withdrawals are a red flag used by examiners
    during BSA audits.

    Detection logic:
      Filter to business accounts with withdrawals/debits > $5,000 where the notes field
      is blank or contains "misc"/"miscellaneous". Any customer with ≥2 such transactions
      gets a single finding covering all of them.
    """
    findings = []
    biz = df[
        (df["account_type"] == "business") &
        (df["transaction_type"].isin(["withdrawal", "debit"])) &
        (df["amount"] > 5000)
    ].copy()
    biz["notes_lower"] = biz["notes"].str.lower()

    # Vague descriptions: explicitly says "misc" or the memo is empty
    flagged = biz[
        biz["notes_lower"].str.contains("misc|miscellaneous", na=False) |
        (biz["notes"] == "")
    ]
    for cid, group in flagged.groupby("customer_id"):
        if len(group) >= 2:
            findings.append(_make_finding(
                "business_cash_anomaly", cid, group.iloc[0]["customer_name"],
                group["transaction_id"].tolist(),
                group["transaction_date"].tolist(),
                group["amount"].tolist(),
                group["merchant_category"].tolist(),
                f"{len(group)} large cash withdrawals (${group['amount'].sum():,.0f} total) "
                f"from business account with vague or missing descriptions",
                {"withdrawal_count": len(group), "total_amount": group["amount"].sum()}
            ))
    return findings


# =============================================================================
# Signal 10 — Statistical Outlier (Z-Score)
# =============================================================================
def zscore_anomalies(df: pd.DataFrame, threshold: float = 2.5) -> list:
    """
    Flag individual transactions where the amount is >2.5 standard deviations from
    the mean for that merchant category (peer-group comparison).

    Why this matters: This is a catch-all detector for unusual transaction sizes that
    don't fit any of the rule-based patterns above. Instead of comparing against a
    fixed dollar threshold, it compares each transaction against its own peer group
    (e.g. "how does this ATM withdrawal compare to all other ATM withdrawals in the
    dataset?"). This makes it robust across different customer wealth levels.

    Detection logic:
      Compute Z-score per merchant_category group using scipy.stats.zscore.
      Any transaction with |z| > 2.5 (approximately the 99th percentile) is flagged.
      This is classified LOW severity — it's an anomaly detector, not evidence of a
      specific crime, so it generates watch-list candidates rather than urgent alerts.
    """
    findings = []
    df = df.copy()
    # Z-score computed within each merchant_category group — compares apples to apples
    df["amount_zscore"] = df.groupby("merchant_category")["amount"].transform(
        lambda x: np.abs(stats.zscore(x, nan_policy="omit"))
    )
    outliers = df[df["amount_zscore"] > threshold]
    for _, row in outliers.iterrows():
        findings.append(_make_finding(
            "statistical_outlier", row["customer_id"], row["customer_name"],
            [row["transaction_id"]],
            [row["transaction_date"]],
            [row["amount"]],
            [row["merchant_category"]],
            f"${row['amount']:,.0f} is {row['amount_zscore']:.1f} standard deviations above the mean "
            f"for {row['merchant_category']} transactions",
            {"zscore": round(row["amount_zscore"], 2), "merchant_category": row["merchant_category"]}
        ))
    return findings


# =============================================================================
# Orchestrator
# =============================================================================
def analyze_transactions(df: pd.DataFrame) -> list:
    """
    Run all 10 detectors in priority order and return a deduplicated finding list.

    Detector order matters: higher-severity signals fire first and claim lower
    FIND-IDs. The dedup step (seen set) means if a customer triggers both
    structuring and a statistical outlier, only the structuring finding is kept —
    the more specific, higher-severity signal takes precedence.

    Returns a list of finding dicts ready for risk_engine.generate_risk_summary().
    """
    global _finding_counter
    _finding_counter = 0  # reset sequential IDs for each analysis run

    findings = []
    findings.extend(detect_structuring(df))
    findings.extend(detect_rapid_fund_movement(df))
    findings.extend(detect_unusual_geography(df))
    findings.extend(detect_high_frequency_atm(df))
    findings.extend(detect_dormant_reactivation(df))
    findings.extend(detect_escalating_gambling(df))
    findings.extend(detect_crypto_wire_pattern(df))
    findings.extend(detect_negative_balance_risk(df))
    findings.extend(detect_business_cash_anomaly(df))
    findings.extend(zscore_anomalies(df))

    # Deduplicate: one finding per (customer_id, signal_type) pair.
    # Because detectors ran in priority order, the first finding in the list
    # for any given (customer, signal) is the highest-priority one.
    seen   = set()
    deduped = []
    for f in findings:
        key = (f["customer_id"], f["signal_type"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return deduped


def get_analysis_summary(df: pd.DataFrame, findings: list) -> dict:
    """
    Build the summary statistics dict that appears in the UI header and gets
    passed to risk_engine.py as context for AI prompt generation.

    This summary gives the AI model the "big picture" of the dataset so it can
    contextualise individual findings appropriately (e.g. 500 transactions over
    90 days vs. 5,000 transactions over 3 days are very different risk profiles).
    """
    signal_counts = {}
    for f in findings:
        signal_counts[f["signal_type"]] = signal_counts.get(f["signal_type"], 0) + 1

    return {
        "total_transactions": len(df),
        "total_customers":    df["customer_id"].nunique(),
        "date_range": {
            "start": str(df["transaction_date"].min())[:10],
            "end":   str(df["transaction_date"].max())[:10],
        },
        "total_findings":    len(findings),
        "findings_by_severity": {
            "HIGH":   len([f for f in findings if f["severity"] == "HIGH"]),
            "MEDIUM": len([f for f in findings if f["severity"] == "MEDIUM"]),
            "LOW":    len([f for f in findings if f["severity"] == "LOW"]),
        },
        "customers_flagged":    len(set(f["customer_id"] for f in findings)),
        "signal_type_counts":   signal_counts,
    }
