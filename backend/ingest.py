"""
ingest.py — Transaction Data Loader
=====================================
Responsible for accepting raw financial transaction files (CSV or JSON) from the
user and converting them into a clean, validated pandas DataFrame.

This is the first stage of the pipeline:
  User uploads file → ingest.py → analyze.py → risk_engine.py → UI

Why a separate ingest module?
  All downstream code (analyze.py, risk_engine.py) assumes a clean, typed DataFrame
  with specific column names. Keeping validation here means neither the detector logic
  nor the AI layer ever has to handle malformed input — they can trust the data shape.
"""

import pandas as pd
import json
import os


def load_transactions(filepath: str) -> pd.DataFrame:
    """
    Load a CSV or JSON transaction file and return a validated DataFrame.

    Expected CSV/JSON schema (all columns required):
      transaction_id        — unique identifier for each transaction (string)
      customer_id           — account holder identifier (string, e.g. CUST-1001)
      customer_name         — full name of the account holder (string)
      account_type          — "personal" or "business" (affects cash anomaly detection)
      transaction_date      — ISO date string; parsed to datetime64
      transaction_type      — "deposit", "withdrawal", "credit", "debit", "transfer"
      amount                — transaction value in USD (float, always positive)
      merchant_category     — category label: "atm", "gambling", "crypto", "wire_transfer", etc.
      merchant_name         — name of the merchant or counterparty (string)
      country               — ISO 2-letter country code of the transaction origin
      flagged_by_system     — boolean: was this flagged by the bank's existing rules engine?
      account_balance_after — account balance immediately after the transaction (float)

    Optional columns (filled with defaults if missing):
      notes                 — free-text memo (defaults to "")
      country               — defaults to "US" if missing
      merchant_category     — defaults to "other" if missing

    Raises:
      FileNotFoundError if the file path does not exist.
      ValueError if the file format is not .csv or .json, or required columns are missing.

    Returns a DataFrame sorted ascending by transaction_date, with invalid rows dropped.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Transaction file not found: {filepath}")

    ext = os.path.splitext(filepath)[1].lower()

    # Support both flat JSON arrays and objects with a "transactions" key
    if ext == ".json":
        with open(filepath) as f:
            data = json.load(f)
        df = pd.DataFrame(data if isinstance(data, list) else data.get("transactions", data))
    elif ext == ".csv":
        df = pd.read_csv(filepath)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use .csv or .json")

    # Validate that every required column is present before proceeding.
    # Downstream detectors reference these columns by name — missing columns
    # would produce silent NaN propagation rather than clear errors.
    required = [
        "transaction_id", "customer_id", "customer_name", "account_type",
        "transaction_date", "transaction_type", "amount", "merchant_category",
        "merchant_name", "country", "flagged_by_system", "account_balance_after",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # --- Type coercion ---
    # errors="coerce" turns unparseable values into NaT/NaN rather than raising,
    # allowing us to drop bad rows cleanly below instead of crashing here.
    df["transaction_date"]      = pd.to_datetime(df["transaction_date"], errors="coerce")
    df["amount"]                = pd.to_numeric(df["amount"], errors="coerce")
    df["account_balance_after"] = pd.to_numeric(df["account_balance_after"], errors="coerce")
    df["flagged_by_system"]     = df["flagged_by_system"].astype(bool)

    # Fill optional columns with safe defaults so detectors don't need to null-check
    df["notes"]             = df["notes"].fillna("")         if "notes" in df.columns else ""
    df["country"]           = df["country"].fillna("US")
    df["merchant_category"] = df["merchant_category"].fillna("other")

    # Drop rows where date or amount couldn't be parsed — these are structurally broken
    # and cannot meaningfully participate in any time-window or threshold calculation.
    before  = len(df)
    df      = df.dropna(subset=["transaction_date", "amount"])
    dropped = before - len(df)
    if dropped > 0:
        print(f"[ingest] Dropped {dropped} rows with invalid date/amount")

    # Sort by date ascending — detectors that use sliding time windows rely on this ordering
    df = df.sort_values("transaction_date").reset_index(drop=True)
    return df
