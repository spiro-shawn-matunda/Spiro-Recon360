# Atlas vs Wallet Reconciliation

This module provides a reconciliation dashboard that compares transactions between `atlas_transactions` and `wallet_transactions` tables, identifying missing and unmatched records.

## Files

1. **atlas_wallet_reconciliation.py** - Backend Python module that queries the database
2. **atlas_wallet_api.py** - Flask API endpoints for the reconciliation data
3. **dashboard_ui/reconciliation.html** - Frontend HTML/JavaScript dashboard

## Installation

### 1. Add the Python dependencies (if not already installed)

```bash
pip install flask psycopg2-binary python-dotenv
```

### 2. Integrate with Dashboard

If you have a Flask dashboard running, add this to your `app.py` or `dashboard.py`:

```python
from atlas_wallet_api import register_atlas_wallet_routes

# Register the atlas wallet reconciliation routes
register_atlas_wallet_routes(app)
```

### 3. Add Menu Item to Dashboard

In your `dashboard_ui/index.html`, add a link to the reconciliation page:

```html
<nav>
    <!-- Existing menu items -->
    <a href="reconciliation.html">Atlas vs Wallet</a>
</nav>
```

## Usage

### Standalone Python Script

Run the reconciliation comparison directly:

```bash
python atlas_wallet_reconciliation.py
```

This will output a summary and show sample missing transactions.

### Via Dashboard

1. Open your dashboard in a browser
2. Click "Atlas vs Wallet" menu item
3. View the reconciliation summary and missing transactions

### API Endpoints

If using the Flask API:

#### Get Summary
```bash
curl http://localhost:5000/api/atlas-wallet-summary
```

Response:
```json
{
  "atlas_total": 172168,
  "wallet_total": 150000,
  "matched": 150000,
  "missing_in_wallet": 22168,
  "missing_in_atlas": 0,
  "match_percentage": 87.1,
  "generated_at": "2026-10-06T12:00:00"
}
```

#### Get Missing Transactions
```bash
curl "http://localhost:5000/api/atlas-wallet-missing?type=wallet&page=0"
```

Response:
```json
{
  "records": [
    {
      "Payment Transaction No": "48869801",
      "Customer Name": "John Doe",
      "Amount": "500.00",
      "Currency": "RWF",
      "Payment Status": "Paid",
      "Payment Operator": "MTN",
      "Transaction Date": "2026-10-05T01:00:00"
    }
  ],
  "total": 22168,
  "limit": 50,
  "offset": 0,
  "has_more": true
}
```

## Features

- **Summary Card**: Shows total transactions, matched count, match percentage
- **Missing in Wallet**: Transactions in atlas_transactions but missing from wallet_transactions
- **Missing in Atlas**: Transactions in wallet_transactions but missing from atlas_transactions
- **Pagination**: Browse through results 50 records per page
- **Sortable Columns**: See transaction details like date, customer, amount, operator
- **Export Ready**: Data structure prepared for CSV export

## Database Queries

The reconciliation uses these key queries:

### Count Total Transactions
```sql
SELECT COUNT(*) FROM atlas_transactions
SELECT COUNT(*) FROM wallet_transactions
```

### Find Missing in Wallet
```sql
SELECT a.* 
FROM atlas_transactions a
LEFT JOIN wallet_transactions w 
    ON a."Payment Transaction No" = w."Payment Transaction No"
WHERE w."Payment Transaction No" IS NULL
```

### Find Missing in Atlas
```sql
SELECT w.* 
FROM wallet_transactions w
LEFT JOIN atlas_transactions a 
    ON w."Payment Transaction No" = a."Payment Transaction No"
WHERE a."Payment Transaction No" IS NULL
```

## Customization

### Change Matching Key

If your tables use a different field for matching (not "Payment Transaction No"), edit this line in `atlas_wallet_reconciliation.py`:

```python
ON a."Payment Transaction No" = w."Payment Transaction No"
```

Replace with your matching column.

### Change Table Names

If your tables have different names:

```python
cursor.execute('SELECT COUNT(*) FROM your_atlas_table_name')
cursor.execute('SELECT COUNT(*) FROM your_wallet_table_name')
```

### Change Limit Per Page

In `atlas_wallet_reconciliation.py`, change this line:

```python
def get_missing_transactions(limit=50, offset=0):  # Change 50 to your preferred number
```

## Troubleshooting

### "Table not found" error

Make sure both `atlas_transactions` and `wallet_transactions` tables exist in the database:

```sql
SELECT table_name FROM information_schema.tables 
WHERE table_name IN ('atlas_transactions', 'wallet_transactions');
```

### No data showing

1. Ensure the database connection is configured correctly in `.env`
2. Check that both tables have data
3. Verify the matching column name is correct

### Performance Issues

For large tables, add indexes on the matching column:

```sql
CREATE INDEX idx_atlas_payment_no ON atlas_transactions("Payment Transaction No");
CREATE INDEX idx_wallet_payment_no ON wallet_transactions("Payment Transaction No");
```

## Integration with Existing Dashboard

This reconciliation module is designed to work alongside the existing Spiro-Recon360 dashboard. You can:

1. Add it as a submenu item
2. Display reconciliation results alongside existing payment data
3. Export missing transactions for further analysis
4. Schedule daily reconciliation reports

## Next Steps

1. Test with your data
2. Add to your dashboard navigation
3. Set up automated reporting if needed
4. Archive reconciliation results for audit trail
