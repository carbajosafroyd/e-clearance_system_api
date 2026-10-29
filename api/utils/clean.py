def clean_value(value):
    if value is None:
        return None

    value = value.strip()

    if value.upper() in ["NULL", "NONE", ""]:
        return None

    return value


def clean_row(row, remove_spaces_keys=None):
    """
    Clean a CSV row.
    
    - Converts 'NULL', 'None', or empty strings to Python None
    - Strips whitespace
    - Optionally removes ALL spaces from specified keys
    """
    if remove_spaces_keys is None:
        remove_spaces_keys = []

    cleaned = {}
    for key, value in row.items():
        if value is None:
            cleaned[key] = None
            continue

        value = value.strip()

        if value.upper() in ["NULL", "NONE", "", "NA", "N/A", "n/a"]:

            cleaned[key] = None
            continue

        # Remove all spaces for specified keys
        if key in remove_spaces_keys:
            value = value.replace(" ", "")

        cleaned[key] = value

    return cleaned
