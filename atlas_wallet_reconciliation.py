#!/usr/bin/env python3
"""
Reconciliation between Atlas Transactions and Wallet Transactions
Finds transactions in atlas_transactions that are missing in wallet_transactions
"""

import os
import json
from datetime import datetime
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT")),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def get_reconciliation_summary():
    """Get summary of reconciliation between atlas and wallet transactions"""
    connection = get_connection()
    cursor = connection.cursor()

    # Count total transactions in each table
    cursor.execute('SELECT COUNT(*) FROM atlas_transactions')
    atlas_total = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM wallet_transactions')
    wallet_total = cursor.fetchone()[0]

    # Find transactions in atlas but not in wallet (using Payment Transaction No as key)
    cursor.execute('''
        SELECT COUNT(DISTINCT a."Payment Transaction No")
        FROM atlas_transactions a
        LEFT JOIN wallet_transactions w
            ON a."Payment Transaction No" = w."Payment Transaction No"
        WHERE w."Payment Transaction No" IS NULL
    ''')
    missing_in_wallet = cursor.fetchone()[0]

    # Find transactions in wallet but not in atlas
    cursor.execute('''
        SELECT COUNT(DISTINCT w."Payment Transaction No")
        FROM wallet_transactions w
        LEFT JOIN atlas_transactions a
            ON w."Payment Transaction No" = a."Payment Transaction No"
        WHERE a."Payment Transaction No" IS NULL
    ''')
    missing_in_atlas = cursor.fetchone()[0]

    # Matched transactions
    matched = atlas_total - missing_in_wallet

    cursor.close()
    connection.close()

    return {
        'atlas_total': atlas_total,
        'wallet_total': wallet_total,
        'matched': matched,
        'missing_in_wallet': missing_in_wallet,
        'missing_in_atlas': missing_in_atlas,
        'match_percentage': round((matched / atlas_total * 100), 2) if atlas_total > 0 else 0,
        'generated_at': datetime.now().isoformat()
    }


def get_missing_transactions(limit=50, offset=0):
    """Get transactions in atlas but missing in wallet"""
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute('''
        SELECT DISTINCT
            a."Payment Transaction No",
            a."Customer Name",
            a."Amount",
            a."Currency",
            a."Payment Status",
            a."Payment Operator",
            a."Transaction Date",
            a."Atlas Transaction ID"
        FROM atlas_transactions a
        LEFT JOIN wallet_transactions w
            ON a."Payment Transaction No" = w."Payment Transaction No"
        WHERE w."Payment Transaction No" IS NULL
        ORDER BY a."Transaction Date" DESC
        LIMIT %s OFFSET %s
    ''', (limit, offset))

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    cursor.execute('''
        SELECT COUNT(DISTINCT a."Payment Transaction No")
        FROM atlas_transactions a
        LEFT JOIN wallet_transactions w
            ON a."Payment Transaction No" = w."Payment Transaction No"
        WHERE w."Payment Transaction No" IS NULL
    ''')
    total_missing = cursor.fetchone()[0]

    cursor.close()
    connection.close()

    records = []
    for row in rows:
        record = {}
        for col, val in zip(columns, row):
            if isinstance(val, datetime):
                record[col] = val.isoformat()
            else:
                record[col] = val
        records.append(record)

    return {
        'records': records,
        'total': total_missing,
        'limit': limit,
        'offset': offset,
        'has_more': (offset + limit) < total_missing
    }


def get_unmatched_wallet_transactions(limit=50, offset=0):
    """Get transactions in wallet but missing in atlas"""
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute('''
        SELECT DISTINCT
            w."Payment Transaction No",
            w."Customer Name",
            w."Amount",
            w."Currency",
            w."Payment Status",
            w."Payment Operator",
            w."Transaction Date"
        FROM wallet_transactions w
        LEFT JOIN atlas_transactions a
            ON w."Payment Transaction No" = a."Payment Transaction No"
        WHERE a."Payment Transaction No" IS NULL
        ORDER BY w."Transaction Date" DESC
        LIMIT %s OFFSET %s
    ''', (limit, offset))

    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    cursor.execute('''
        SELECT COUNT(DISTINCT w."Payment Transaction No")
        FROM wallet_transactions w
        LEFT JOIN atlas_transactions a
            ON w."Payment Transaction No" = a."Payment Transaction No"
        WHERE a."Payment Transaction No" IS NULL
    ''')
    total_missing = cursor.fetchone()[0]

    cursor.close()
    connection.close()

    records = []
    for row in rows:
        record = {}
        for col, val in zip(columns, row):
            if isinstance(val, datetime):
                record[col] = val.isoformat()
            else:
                record[col] = val
        records.append(record)

    return {
        'records': records,
        'total': total_missing,
        'limit': limit,
        'offset': offset,
        'has_more': (offset + limit) < total_missing
    }


if __name__ == "__main__":
    # Test the functions
    print("\n" + "="*60)
    print("ATLAS VS WALLET RECONCILIATION")
    print("="*60)

    summary = get_reconciliation_summary()
    print(f"\nSummary:")
    print(f"  Atlas Total: {summary['atlas_total']:,}")
    print(f"  Wallet Total: {summary['wallet_total']:,}")
    print(f"  Matched: {summary['matched']:,}")
    print(f"  Missing in Wallet: {summary['missing_in_wallet']:,}")
    print(f"  Missing in Atlas: {summary['missing_in_atlas']:,}")
    print(f"  Match Rate: {summary['match_percentage']}%")

    print(f"\n\nMissing in Wallet (first 5):")
    missing = get_missing_transactions(limit=5)
    for record in missing['records']:
        print(f"  - {record['Payment Transaction No']}: {record['Customer Name']} ({record['Amount']} {record['Currency']})")

    print(f"\n\nUnmatched in Wallet (first 5):")
    unmatched = get_unmatched_wallet_transactions(limit=5)
    for record in unmatched['records']:
        print(f"  - {record['Payment Transaction No']}: {record['Customer Name']} ({record['Amount']} {record['Currency']})")

    print("\n" + "="*60 + "\n")
