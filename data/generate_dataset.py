import pandas as pd
import numpy as np
import random
import os
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

END_DATE = datetime(2026, 6, 4)
START_DATE = END_DATE - timedelta(days=90)

MERCHANT_NAMES = {
    "retail": ["Walmart", "Target", "Amazon", "Best Buy", "Costco", "Home Depot"],
    "utilities": ["AT&T", "Comcast", "Con Edison", "Duke Energy", "City Water Dept"],
    "gambling": ["DraftKings", "BetMGM", "FanDuel", "Caesars Online", "PokerStars"],
    "atm": ["Chase ATM", "Wells Fargo ATM", "BofA ATM", "Citibank ATM", "7-Eleven ATM"],
    "wire_transfer": ["International Wire", "Domestic Wire Transfer", "SWIFT Transfer"],
    "crypto": ["Coinbase", "Binance US", "Kraken", "Gemini Exchange", "Crypto.com"],
    "other": ["Starbucks", "McDonald's", "Shell Gas", "CVS Pharmacy", "Uber"],
}

CUSTOMER_NAMES = [
    "James Okafor", "Maria Santos", "David Chen", "Priya Nair", "Robert Mwangi",
    "Lisa Kowalski", "Ahmed Hassan", "Elena Petrov", "Carlos Reyes", "Susan Park",
    "Thomas Nguyen", "Angela Brooks", "Michael Osei", "Rachel Kim", "Kevin Torres",
    "Fatima Al-Rashid", "Brian O'Connor", "Yuki Tanaka", "Diana Mbeki", "Samuel Green",
    "Olga Ivanova", "Derek Williams", "Nadia Suleiman", "Paul Adeyemi", "Christine Lee",
]

ACCOUNT_TYPES = ["checking", "savings", "business", "investment"]
TRANSACTION_TYPES = ["debit", "credit", "transfer", "withdrawal", "deposit"]
COUNTRIES_DOMESTIC = ["US"] * 18
COUNTRIES_HIGH_RISK = ["NG", "RU", "IR", "KP", "MM"]

customers = []
for i, name in enumerate(CUSTOMER_NAMES):
    cid = f"CUST-{1001 + i}"
    if i < 5:
        risk = "HIGH"
        acct = random.choice(["business", "checking"])
    elif i < 13:
        risk = "MEDIUM"
        acct = random.choice(ACCOUNT_TYPES)
    else:
        risk = "LOW"
        acct = random.choice(["checking", "savings"])
    customers.append({
        "customer_id": cid,
        "customer_name": name,
        "account_type": acct,
        "risk_profile": risk,
        "balance": round(random.uniform(5000, 50000), 2),
    })

cust_map = {c["customer_id"]: c for c in customers}

rows = []
txn_counter = 100000

def make_txn(cust, date, txn_type, amount, merchant_cat, country="US", notes=""):
    global txn_counter
    txn_counter += 1
    cid = cust["customer_id"]
    balance_after = round(cust["balance"] + (amount if txn_type in ["credit", "deposit"] else -amount), 2)
    cust["balance"] = balance_after
    merchant = random.choice(MERCHANT_NAMES.get(merchant_cat, MERCHANT_NAMES["other"]))
    return {
        "transaction_id": f"TXN-{txn_counter}",
        "customer_id": cid,
        "customer_name": cust["customer_name"],
        "account_type": cust["account_type"],
        "transaction_date": date.strftime("%Y-%m-%d"),
        "transaction_type": txn_type,
        "amount": round(abs(amount), 2),
        "merchant_category": merchant_cat,
        "merchant_name": merchant,
        "country": country,
        "flagged_by_system": False,
        "account_balance_after": balance_after,
        "notes": notes,
    }

def rand_date(start=START_DATE, end=END_DATE):
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))

DORMANT_CUSTOMERS = {"CUST-1004", "CUST-1005"}
DORMANT_START = START_DATE + timedelta(days=65)

# --- LOW risk baseline transactions ---
for cust in customers:
    num_txns = 8 if cust["risk_profile"] == "LOW" else 5
    for _ in range(num_txns):
        cat = random.choice(["retail", "utilities", "other"])
        txn_type = random.choice(["debit", "credit"])
        amount = round(random.uniform(10, 500), 2)
        # Dormant customers get no baseline transactions before day 65
        date_start = DORMANT_START if cust["customer_id"] in DORMANT_CUSTOMERS else START_DATE
        rows.append(make_txn(cust, rand_date(date_start, END_DATE), txn_type, amount, cat))

# --- SIGNAL 1: Structuring / smurfing (CUST-1001, CUST-1002) ---
for cid in ["CUST-1001", "CUST-1002"]:
    cust = cust_map[cid]
    base_date = rand_date(START_DATE, END_DATE - timedelta(days=10))
    for i in range(5):
        amount = round(random.uniform(8500, 9800), 2)
        d = base_date + timedelta(days=i)
        rows.append(make_txn(cust, d, "withdrawal", amount, "atm", notes="cash withdrawal"))
        rows[-1]["flagged_by_system"] = False

# --- SIGNAL 2: Rapid fund movement (CUST-1001, CUST-1003) ---
for cid in ["CUST-1001", "CUST-1003"]:
    cust = cust_map[cid]
    d = rand_date(START_DATE, END_DATE - timedelta(days=5))
    rows.append(make_txn(cust, d, "deposit", 45000, "wire_transfer", notes="large incoming wire"))
    rows.append(make_txn(cust, d + timedelta(days=1), "withdrawal", 44500, "wire_transfer", notes="full funds withdrawn"))

# --- SIGNAL 3: Unusual geography (CUST-1002, CUST-1004) ---
for cid in ["CUST-1002", "CUST-1004"]:
    cust = cust_map[cid]
    date_start = DORMANT_START if cid in DORMANT_CUSTOMERS else START_DATE
    for country in random.sample(COUNTRIES_HIGH_RISK, 3):
        d = rand_date(date_start, END_DATE)
        rows.append(make_txn(cust, d, "debit", round(random.uniform(500, 3000), 2), "retail", country=country))

# --- SIGNAL 4: High-frequency ATM withdrawals (CUST-1003) ---
cust = cust_map["CUST-1003"]
d = rand_date(START_DATE, END_DATE - timedelta(days=2))
for _ in range(12):
    rows.append(make_txn(cust, d, "withdrawal", round(random.uniform(100, 400), 2), "atm", notes="atm withdrawal"))

# --- SIGNAL 5: Dormant account activity (CUST-1004, CUST-1005) ---
for cid in ["CUST-1004", "CUST-1005"]:
    cust = cust_map[cid]
    # no activity for 65 days, then large debit
    dormant_date = START_DATE + timedelta(days=65)
    rows.append(make_txn(cust, dormant_date, "debit", round(random.uniform(15000, 30000), 2), "wire_transfer", notes="first activity in 2+ months"))

# --- SIGNAL 6: Gambling escalation (CUST-1001, CUST-1006) ---
for cid in ["CUST-1001", "CUST-1006"]:
    cust = cust_map[cid]
    base = rand_date(START_DATE, END_DATE - timedelta(days=20))
    amounts = [200, 500, 1200, 2800, 5500]
    for i, amt in enumerate(amounts):
        rows.append(make_txn(cust, base + timedelta(days=i * 3), "debit", amt, "gambling"))

# --- SIGNAL 7: Crypto + wire transfer (CUST-1002, CUST-1005) ---
for cid in ["CUST-1002", "CUST-1005"]:
    cust = cust_map[cid]
    base = rand_date(START_DATE, END_DATE - timedelta(days=15))
    for i in range(4):
        rows.append(make_txn(cust, base + timedelta(days=i * 2), "debit", round(random.uniform(2000, 8000), 2), "crypto"))
    rows.append(make_txn(cust, base + timedelta(days=10), "transfer", round(random.uniform(15000, 25000), 2), "wire_transfer", notes="international wire post crypto"))

# --- SIGNAL 8: Negative balance risk (CUST-1007, CUST-1008) ---
for cid in ["CUST-1007", "CUST-1008"]:
    cust = cust_map[cid]
    cust["balance"] = round(random.uniform(1000, 2000), 2)
    rows.append(make_txn(cust, rand_date(), "debit", cust["balance"] + round(random.uniform(500, 1500), 2), "retail", notes="overdraft risk"))

# --- SIGNAL 9: Insider risk — business cash withdrawals (CUST-1003, CUST-1004) ---
for cid in ["CUST-1003", "CUST-1004"]:
    cust = cust_map[cid]
    cust["account_type"] = "business"
    date_start = DORMANT_START if cid in DORMANT_CUSTOMERS else START_DATE
    for _ in range(5):
        rows.append(make_txn(cust, rand_date(date_start, END_DATE), "withdrawal", round(random.uniform(3000, 9000), 2), "atm", notes="miscellaneous"))

# --- MEDIUM risk — 1-2 signals each ---
medium_signals = [
    ("CUST-1006", "gambling", 3),
    ("CUST-1007", "crypto", 2),
    ("CUST-1008", "atm", 6),
    ("CUST-1009", "wire_transfer", 2),
    ("CUST-1010", "gambling", 2),
    ("CUST-1011", "atm", 4),
    ("CUST-1012", "crypto", 2),
    ("CUST-1013", "wire_transfer", 1),
]
for cid, cat, n in medium_signals:
    cust = cust_map[cid]
    for _ in range(n):
        rows.append(make_txn(cust, rand_date(), "debit", round(random.uniform(500, 5000), 2), cat))

# --- Fill to 500 rows with clean LOW-risk transactions ---
while len(rows) < 500:
    cust = random.choice([c for c in customers if c["risk_profile"] == "LOW"])
    cat = random.choice(["retail", "utilities", "other"])
    txn_type = random.choice(["debit", "credit"])
    amount = round(random.uniform(5, 300), 2)
    rows.append(make_txn(cust, rand_date(), txn_type, amount, cat))

df = pd.DataFrame(rows[:500])
df = df.sort_values("transaction_date").reset_index(drop=True)

os.makedirs("D:/poc/data", exist_ok=True)
df.to_csv("D:/poc/data/financial_transactions.csv", index=False)
print(f"Saved {len(df)} rows to D:/poc/data/financial_transactions.csv")
print(f"Customers: {df['customer_id'].nunique()}")
print(f"Columns: {list(df.columns)}")
