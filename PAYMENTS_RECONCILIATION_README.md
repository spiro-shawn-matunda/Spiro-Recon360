# Payments Reconciliation Dashboard

New submenu added to the Spiro reconciliation dashboard for Payment transaction reconciliation.

## What's New

### 📍 Navigation Structure

Added a **PAYMENTS** section in the sidebar with two submenu items:

1. **Wallet** - Reconcile payments with wallet transactions
2. **Swap** - Reconcile payments with swap transactions

### 📄 New Pages

#### 1. Payments vs Wallet (`payments-wallet.html`)
- **Purpose:** Identify payment transactions that exist in atlas but are missing from wallet
- **Shows:** 
  - Total payments in atlas
  - Total wallet transactions
  - Matched count
  - Missing in wallet count
  - Paginated list of missing transactions

#### 2. Payments vs Swap (`payments-swap.html`)
- **Purpose:** Identify payment transactions that exist in atlas but are missing from swap
- **Shows:**
  - Total payments in atlas
  - Total swap transactions
  - Matched count
  - Missing in swap count
  - Paginated list of missing transactions

### 🔧 Backend Modules

#### `payments_wallet_reconciliation.py`
```python
get_summary(settings)  # Get summary stats
get_missing_transactions(settings, limit=50, offset=0)  # Get paginated missing transactions
```

#### `payments_swap_reconciliation.py`
```python
get_summary(settings)  # Get summary stats
get_missing_transactions(settings, limit=50, offset=0)  # Get paginated missing transactions
```

### 🌐 API Endpoints

Added to `dashboard.py`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/payments-wallet-summary` | GET | Get payments vs wallet summary |
| `/api/payments-wallet-missing` | GET | Get missing wallet transactions (paginated) |
| `/api/payments-swap-summary` | GET | Get payments vs swap summary |
| `/api/payments-swap-missing` | GET | Get missing swap transactions (paginated) |

**Query Parameters:**
- `page` (optional): Page number, default 0. Results are 50 per page.

### 📊 Reconciliation Summary

Each page displays:
- **Atlas Total:** Total payment transactions in atlas_transactions table
- **Wallet/Swap Total:** Total transactions in wallet_transactions or swap_transactions
- **Matched:** Transactions found in both tables
- **Missing:** Transactions in atlas but missing from the second table

### 🎯 Transaction Details

Missing transaction tables show:
- **Transaction No** - Unique transaction identifier
- **Customer** - Customer name
- **Amount** - Transaction amount and currency
- **Status** - Payment status (Paid, Pending, Failed, etc.)
- **Operator** - Payment operator (MTN, M-Pesa, Airtel, etc.)
- **Date** - Transaction date

### 🔄 How Reconciliation Works

**Payments vs Wallet:**
```sql
SELECT atlas transactions
LEFT JOIN wallet transactions
WHERE wallet transaction is NULL
```

**Payments vs Swap:**
```sql
SELECT atlas transactions
LEFT JOIN swap transactions
WHERE swap transaction is NULL
```

Matching is done using the `"Payment Transaction No"` column as the key.

### 📱 UI Features

- **Pagination:** Browse through results 50 records per page
- **Summary Cards:** Quick overview of reconciliation status
- **Error Handling:** Clear error messages for connection issues
- **Responsive Design:** Works on desktop and mobile
- **Styled Tables:** Formatted transaction data with proper spacing

### 🚀 How to Use

1. **Open Dashboard:** Navigate to `http://localhost:8765`
2. **Access Payments Section:**
   - Click "Wallet" to see payments vs wallet reconciliation
   - Click "Swap" to see payments vs swap reconciliation
3. **Review Results:**
   - Check summary cards for overall statistics
   - Browse missing transactions in the table
   - Use pagination to navigate through results
4. **Investigate:**
   - Note transaction numbers of missing records
   - Follow up with source systems
   - Document resolution steps

### 🔧 Customization

To modify the matching key (currently "Payment Transaction No"):

1. Edit `payments_wallet_reconciliation.py`:
   ```python
   ON a."Payment Transaction No" = w."Payment Transaction No"
   # Change to:
   ON a."Your Column" = w."Your Column"
   ```

2. Edit `payments_swap_reconciliation.py` similarly

### 📊 Performance Notes

- Results are paginated (50 per page) for better performance
- Summary statistics are quick queries
- Large tables (100K+ rows) may take a few seconds
- Consider adding indexes if reconciliation is slow:
  ```sql
  CREATE INDEX idx_atlas_payment_no ON atlas_transactions("Payment Transaction No");
  CREATE INDEX idx_wallet_payment_no ON wallet_transactions("Payment Transaction No");
  CREATE INDEX idx_swap_payment_no ON swap_transactions("Payment Transaction No");
  ```

### 🐛 Troubleshooting

**"No data showing"**
- Ensure tables exist: `atlas_transactions`, `wallet_transactions`, `swap_transactions`
- Verify data is loaded in the tables
- Check database connection in `.env`

**"API errors"**
- Check browser console for error messages
- Verify the matching column name is correct
- Ensure database user has SELECT permissions

**"Missing swap table"**
- If `swap_transactions` table doesn't exist, the page will show all atlas payments as "missing"
- Load swap data first or create the table

### 📚 Files Modified/Created

**New Files:**
- `dashboard_ui/payments-wallet.html` - Wallet reconciliation page
- `dashboard_ui/payments-swap.html` - Swap reconciliation page
- `payments_wallet_reconciliation.py` - Wallet reconciliation logic
- `payments_swap_reconciliation.py` - Swap reconciliation logic

**Modified Files:**
- `dashboard_ui/index.html` - Added sidebar menu items
- `dashboard_ui/styles.css` - Added submenu styling
- `dashboard.py` - Added API endpoints and static routes

### 🔐 Security

- All API endpoints are local-only (127.0.0.1)
- Uses existing CSRF token validation
- Read-only database access
- No sensitive data in logs
