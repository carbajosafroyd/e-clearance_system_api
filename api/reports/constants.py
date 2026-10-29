"""
The knobs of the reports app - the values you are most likely to want to
change without reading any of the surrounding code.
"""

# --------------------------------------------------------------------------
# The monthly axis
# --------------------------------------------------------------------------
# Month labels on the trend and payment-method series. Both series are built
# against the same month axis, so a given label means the same calendar month
# in either of them.
#
# The label names its month outright - "2026-02" - so a point on the chart can
# be read without counting along the axis, and it sorts correctly as a string.
# Every row also carries `start` and `end` (the first and last day of the
# month) if you want to print or format them some other way.
MONTH_LABEL = "{start:%Y-%m}"

# How many months an axis may span before it stops being drawn continuously.
# Under this, empty months inside the reporting window are filled in with
# zeroes so the chart does not silently skip a quiet quarter. Over it - a
# multi-year date range - only months that actually saw a payment are
# returned, because a hundred mostly-empty points is not a chart.
MAX_TREND_MONTHS = 36

# --------------------------------------------------------------------------
# Export output
# --------------------------------------------------------------------------
# Every report export is named the same way, PDF and Excel alike - the
# prefix comes from the view (its `filename_prefix`), so
# `collection-report-20260816-0233.pdf` / `...xlsx`.
EXPORT_FILENAME = "{prefix}-{stamp}.{extension}"
EXPORT_STAMP_FORMAT = "%Y%m%d-%H%M"
