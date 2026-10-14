"""Uganda Due payment rule, independent of the payment source table."""
RENT_REMARKS = 'Settlement of Rent'
UGANDA_SCOPE_SQL = "country = 'Uganda' AND BTRIM(remarks) = 'Settlement of Rent'"
MISSING_DUE_ID_SQL = "COALESCE(union_due_id_new, '') ~ '^[[:space:]]*$'"
NO_DUE_SQL = f"({UGANDA_SCOPE_SQL}) AND ({MISSING_DUE_ID_SQL})"


def classify_payment(country, remarks, union_due_id_new):
    """ID presence only; with_due_id does not prove a matching due exists."""
    if country != 'Uganda' or (remarks or '').strip(' ') != RENT_REMARKS:
        return 'out_of_scope'
    return 'no_due' if not (union_due_id_new or '').strip() else 'with_due_id'
