import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv


# ============================================================
# LOAD DATABASE CONFIGURATION
# ============================================================

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    try:
        connection = psycopg2.connect(**DB_CONFIG)

        print("Connected to PostgreSQL successfully.")

        return connection

    except Exception as e:
        print(f"Database connection failed: {e}")
        raise


# ============================================================
# GET PAYMENT TRANSACTIONS
# ============================================================

def get_payments(connection, start_date, end_date):

    query = """
        SELECT *
        FROM payments
        WHERE date >= %s
          AND date <= %s
    """

    return pd.read_sql(
        query,
        connection,
        params=(start_date, end_date)
    )


# ============================================================
# GET WALLET TRANSACTIONS
# ============================================================

def get_wallet_transactions(connection, start_date, end_date):

    query = """
        SELECT *
        FROM wallet
        WHERE date >= %s
          AND date <= %s
    """

    return pd.read_sql(
        query,
        connection,
        params=(start_date, end_date)
    )


# ============================================================
# RECONCILIATION
# ============================================================

def reconcile(payments, wallet):

    # --------------------------------------------------------
    # Check required column
    # --------------------------------------------------------

    if "payment_transaction" not in payments.columns:
        raise ValueError(
            "payments table does not contain "
            "'payment_transaction'"
        )

    if "payment_transaction" not in wallet.columns:
        raise ValueError(
            "wallet table does not contain "
            "'payment_transaction'"
        )

    # --------------------------------------------------------
    # Convert references to strings
    # --------------------------------------------------------

    payments["payment_transaction"] = (
        payments["payment_transaction"]
        .astype(str)
        .str.strip()
    )

    wallet["payment_transaction"] = (
        wallet["payment_transaction"]
        .astype(str)
        .str.strip()
    )

    # --------------------------------------------------------
    # Find common references
    # --------------------------------------------------------

    payment_references = set(
        payments["payment_transaction"]
    )

    wallet_references = set(
        wallet["payment_transaction"]
    )

    common_references = (
        payment_references
        & wallet_references
    )

    # --------------------------------------------------------
    # Payment references missing from wallet
    # --------------------------------------------------------

    payment_only = (
        payment_references
        - wallet_references
    )

    # --------------------------------------------------------
    # Wallet references missing from payments
    # --------------------------------------------------------

    wallet_only = (
        wallet_references
        - payment_references
    )

    # --------------------------------------------------------
    # Create result
    # --------------------------------------------------------

    results = []

    # --------------------------------------------------------
    # COMMON REFERENCES
    # --------------------------------------------------------

    for reference in common_references:

        payment_records = payments[
            payments["payment_transaction"]
            == reference
        ]

        wallet_records = wallet[
            wallet["payment_transaction"]
            == reference
        ]

        results.append({
            "payment_transaction": reference,
            "status": "COMMON",
            "payment_records": len(payment_records),
            "wallet_records": len(wallet_records)
        })

    # --------------------------------------------------------
    # PAYMENT ONLY
    # --------------------------------------------------------

    for reference in payment_only:

        results.append({
            "payment_transaction": reference,
            "status": "PAYMENT_ONLY",
            "payment_records": 1,
            "wallet_records": 0
        })

    # --------------------------------------------------------
    # WALLET ONLY
    # --------------------------------------------------------

    for reference in wallet_only:

        results.append({
            "payment_transaction": reference,
            "status": "WALLET_ONLY",
            "payment_records": 0,
            "wallet_records": 1
        })

    return pd.DataFrame(results)


# ============================================================
# MAIN RECONCILIATION
# ============================================================

def run_reconciliation(start_date, end_date):

    connection = get_connection()

    try:

        print(
            f"\nChecking transactions from "
            f"{start_date} to {end_date}"
        )

        # Get payment data
        payments = get_payments(
            connection,
            start_date,
            end_date
        )

        print(
            f"Payment records found: "
            f"{len(payments)}"
        )

        # Get wallet data
        wallet = get_wallet_transactions(
            connection,
            start_date,
            end_date
        )

        print(
            f"Wallet records found: "
            f"{len(wallet)}"
        )

        # Reconcile
        results = reconcile(
            payments,
            wallet
        )

        # ----------------------------------------------------
        # SUMMARY
        # ----------------------------------------------------

        common = len(
            results[
                results["status"] == "COMMON"
            ]
        )

        payment_only = len(
            results[
                results["status"] == "PAYMENT_ONLY"
            ]
        )

        wallet_only = len(
            results[
                results["status"] == "WALLET_ONLY"
            ]
        )

        print("\n===================================")
        print("RECONCILIATION SUMMARY")
        print("===================================")

        print(
            f"Common references : {common}"
        )

        print(
            f"Payment only      : {payment_only}"
        )

        print(
            f"Wallet only       : {wallet_only}"
        )

        print(
            f"Total references  : {len(results)}"
        )

        # ----------------------------------------------------
        # DISPLAY RESULTS
        # ----------------------------------------------------

        print("\nRECONCILIATION RESULTS")

        print(
            results.to_string(index=False)
        )

        return results

    finally:

        connection.close()

        print("\nDatabase connection closed.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    run_reconciliation(
        start_date="2026-10-01",
        end_date="2026-10-05"
    )