# Data Dictionary — financial_transactions.csv

## Schema

| Column | Type | Description |
|--------|------|-------------|
| transaction_id | string | Unique transaction ID — format TXN-XXXXXX |
| customer_id | string | Unique customer identifier — format CUST-XXXX |
| customer_name | string | Full name of account holder |
| account_type | string | One of: checking / savings / business / investment |
| transaction_date | date | YYYY-MM-DD — 90-day window (2026-03-06 to 2026-06-04) |
| transaction_type | string | One of: debit / credit / transfer / withdrawal / deposit |
| amount | float | Transaction amount in USD (absolute value) |
| merchant_category | string | One of: retail / utilities / gambling / atm / wire_transfer / crypto / other |
| merchant_name | string | Name of merchant or institution |
| country | string | ISO 2-letter country code — US (domestic) or high-risk country |
| flagged_by_system | boolean | Whether the legacy rule-based system flagged the transaction |
| account_balance_after | float | Running account balance after transaction |
| notes | string | Free-text field — may be blank; contains analyst-relevant context |

## Valid Values

| Column | Valid Values |
|--------|-------------|
| account_type | checking, savings, business, investment |
| transaction_type | debit, credit, transfer, withdrawal, deposit |
| merchant_category | retail, utilities, gambling, atm, wire_transfer, crypto, other |
| country | US, NG (Nigeria), RU (Russia), IR (Iran), KP (North Korea), MM (Myanmar) |

## Risk Signal Index
*(For QA and evaluation use only — not displayed in the application)*

| Signal Type | Customer IDs | Description |
|-------------|-------------|-------------|
| Structuring / smurfing | CUST-1001, CUST-1002 | 5x ATM withdrawals $8,500–$9,800 within 5 days |
| Rapid fund movement | CUST-1001, CUST-1003 | $45,000 deposit followed by $44,500 withdrawal within 24–48 hrs |
| Unusual geography | CUST-1002, CUST-1004 | 3+ transactions from NG, RU, IR, KP, or MM |
| High-frequency ATM | CUST-1003 | 12 ATM withdrawals in a single day |
| Dormant account activity | CUST-1004, CUST-1005 | No activity for 65+ days, then large debit ($15,000–$30,000) |
| Gambling escalation | CUST-1001, CUST-1006 | 5 gambling transactions escalating: $200 → $500 → $1,200 → $2,800 → $5,500 |
| Crypto + wire transfer | CUST-1002, CUST-1005 | 4 crypto purchases followed by international wire transfer |
| Negative balance risk | CUST-1007, CUST-1008 | Debit transaction exceeds account balance |
| Insider risk (business) | CUST-1003, CUST-1004 | Business account, 5x large cash withdrawals noted "miscellaneous" |

## Customer Risk Profile Distribution

### HIGH Risk (5 customers — exhibit 3+ signal types)
| Customer ID | Name | Signals Present |
|-------------|------|-----------------|
| CUST-1001 | James Okafor | Structuring, Rapid fund movement, Gambling escalation |
| CUST-1002 | Maria Santos | Structuring, Unusual geography, Crypto + wire |
| CUST-1003 | David Chen | Rapid fund movement, High-frequency ATM, Insider risk |
| CUST-1004 | Priya Nair | Unusual geography, Dormant account, Insider risk |
| CUST-1005 | Robert Mwangi | Dormant account, Crypto + wire |

### MEDIUM Risk (8 customers — exhibit 1–2 signal types)
| Customer ID | Name | Signals Present |
|-------------|------|-----------------|
| CUST-1006 | Lisa Kowalski | Gambling (3 transactions) |
| CUST-1007 | Ahmed Hassan | Crypto (2 transactions), Negative balance |
| CUST-1008 | Elena Petrov | High-frequency ATM (6), Negative balance |
| CUST-1009 | Carlos Reyes | Wire transfer activity |
| CUST-1010 | Susan Park | Gambling (2 transactions) |
| CUST-1011 | Thomas Nguyen | ATM frequency (4 withdrawals) |
| CUST-1012 | Angela Brooks | Crypto (2 transactions) |
| CUST-1013 | Michael Osei | Wire transfer activity |

### LOW Risk (12 customers — clean transaction patterns)
| Customer ID | Name |
|-------------|------|
| CUST-1014 | Rachel Kim |
| CUST-1015 | Kevin Torres |
| CUST-1016 | Fatima Al-Rashid |
| CUST-1017 | Brian O'Connor |
| CUST-1018 | Yuki Tanaka |
| CUST-1019 | Diana Mbeki |
| CUST-1020 | Samuel Green |
| CUST-1021 | Olga Ivanova |
| CUST-1022 | Derek Williams |
| CUST-1023 | Nadia Suleiman |
| CUST-1024 | Paul Adeyemi |
| CUST-1025 | Christine Lee |

## Assumptions

- Dataset is fully synthetic — no real customer data used.
- Generated deterministically with `random.seed(42)` and `np.random.seed(42)` for reproducibility.
- Transaction amounts are in USD. No currency conversion applied.
- `account_balance_after` is a running simulation — not derived from a real ledger system.
- `flagged_by_system` is set to `False` across all rows to simulate a legacy system with low detection rates, allowing the AI layer to demonstrate value by surfacing signals the rule engine missed.
- High-risk countries (NG, RU, IR, KP, MM) are used per FATF and OFAC guidance for illustrative purposes only.
- Date range: 90-day window ending 2026-06-04.
