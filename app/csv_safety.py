"""Serialize CSV cells consistently without executing source text as formulas."""
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


def csv_value(field, value, *, numeric=False):
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        # Numeric JSON strings may remain numbers only after checking their value.
        if numeric:
            try:
                if Decimal(value).is_finite():
                    return value
            except InvalidOperation:
                pass
        if value.lstrip(' ').startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
            return "'" + value
    return value
