#!/usr/bin/env python3
"""
Flask API for Payments Reconciliation
- Payments vs Wallet
- Payments vs Swap
"""

from flask import Blueprint, jsonify, request
from payments_wallet_reconciliation import get_summary as get_wallet_summary
from payments_wallet_reconciliation import get_missing_transactions as get_wallet_missing
from payments_swap_reconciliation import get_summary as get_swap_summary
from payments_swap_reconciliation import get_missing_transactions as get_swap_missing

payments_bp = Blueprint('payments', __name__, url_prefix='/api')


@payments_bp.route('/payments-wallet-summary', methods=['GET'])
def payments_wallet_summary(settings=None):
    """Get payments vs wallet reconciliation summary"""
    try:
        if settings is None:
            from db_config import read_database_config
            settings = read_database_config()

        data = get_wallet_summary(settings)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@payments_bp.route('/payments-wallet-missing', methods=['GET'])
def payments_wallet_missing(settings=None):
    """Get payments missing in wallet with pagination"""
    try:
        if settings is None:
            from db_config import read_database_config
            settings = read_database_config()

        page = int(request.args.get('page', 0))
        limit = 50
        offset = page * limit

        data = get_wallet_missing(settings, limit=limit, offset=offset)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@payments_bp.route('/payments-swap-summary', methods=['GET'])
def payments_swap_summary(settings=None):
    """Get payments vs swap reconciliation summary"""
    try:
        if settings is None:
            from db_config import read_database_config
            settings = read_database_config()

        data = get_swap_summary(settings)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@payments_bp.route('/payments-swap-missing', methods=['GET'])
def payments_swap_missing(settings=None):
    """Get payments missing in swap with pagination"""
    try:
        if settings is None:
            from db_config import read_database_config
            settings = read_database_config()

        page = int(request.args.get('page', 0))
        limit = 50
        offset = page * limit

        data = get_swap_missing(settings, limit=limit, offset=offset, direction=request.args.get('direction', 'payments'))
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def register_payments_routes(app, settings=None):
    """Register payments routes to Flask app"""
    if settings:
        @payments_bp.route('/payments-wallet-summary', methods=['GET'])
        def pw_summary():
            return payments_wallet_summary(settings)

        @payments_bp.route('/payments-wallet-missing', methods=['GET'])
        def pw_missing():
            return payments_wallet_missing(settings)

        @payments_bp.route('/payments-swap-summary', methods=['GET'])
        def ps_summary():
            return payments_swap_summary(settings)

        @payments_bp.route('/payments-swap-missing', methods=['GET'])
        def ps_missing():
            return payments_swap_missing(settings)

    app.register_blueprint(payments_bp)
