#!/usr/bin/env python3
"""
Flask API for Atlas vs Wallet Reconciliation
Integrates with the reconciliation.html dashboard
"""

from flask import Blueprint, jsonify, request
from datetime import datetime
from atlas_wallet_reconciliation import (
    get_reconciliation_summary,
    get_missing_transactions,
    get_unmatched_wallet_transactions
)

atlas_wallet_bp = Blueprint('atlas_wallet', __name__, url_prefix='/api')


@atlas_wallet_bp.route('/atlas-wallet-summary', methods=['GET'])
def summary():
    """Get reconciliation summary"""
    try:
        data = get_reconciliation_summary()
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@atlas_wallet_bp.route('/atlas-wallet-missing', methods=['GET'])
def missing_transactions():
    """Get missing transactions with pagination"""
    try:
        transaction_type = request.args.get('type', 'wallet')  # 'wallet' or 'atlas'
        page = int(request.args.get('page', 0))
        limit = 50
        offset = page * limit

        if transaction_type == 'atlas':
            data = get_unmatched_wallet_transactions(limit=limit, offset=offset)
        else:
            data = get_missing_transactions(limit=limit, offset=offset)

        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def register_atlas_wallet_routes(app):
    """Register atlas wallet routes to Flask app"""
    app.register_blueprint(atlas_wallet_bp)
