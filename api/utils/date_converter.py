from datetime import datetime, date

def ensure_date(value):
    if isinstance(value, date):
        return value if not hasattr(value, "date") else value.date()
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return None