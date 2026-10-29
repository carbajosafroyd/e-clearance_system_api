"""
The Excel builders' layout contract: which tabs exist, which totals rows sit
under the tables, and which cells carry conditional colour.

Built from synthetic report dicts rather than the database - the services'
own tests prove the numbers; these prove the workbook the numbers land in.
"""

from io import BytesIO

from django.test import SimpleTestCase
from openpyxl import load_workbook

from ..periods import ReportFilters
from ..services.excel import (
    BAD,
    BLUE,
    GOOD,
    GRAY,
    WARN,
    build_collection_report_workbook,
    build_detailed_fees_workbook,
    build_program_set_workbook,
    build_staff_workbook,
)

FILTERS = ReportFilters()


def sheet_of(builder, report, name):
    content = builder(report=report, filters=FILTERS)
    return load_workbook(BytesIO(content))[name]


def fill_of(cell) -> str:
    """The cell's solid fill as RRGGBB, or '' when unfilled."""
    rgb = cell.fill.start_color.rgb
    return (rgb or "")[-6:] if isinstance(rgb, str) else ""


def submission_block(**overrides):
    empty = {"count": 0, "amount": 0.0}
    block = {
        "total": dict(empty),
        "approved": dict(empty),
        "pending": dict(empty),
        "rejected": dict(empty),
        "cancelled": dict(empty),
        "withdrawn": dict(empty),
        "approval_rate": None,
    }
    block.update(overrides)
    return block


def collection_report():
    return {
        "generated_at": "2026-08-21T10:00:00",
        "kpi": {
            "total_collections": 150.0,
            "previous_total_collections": None,
            "total_outstanding": 50.0,
            "cash": 100.0,
            "gcash": 50.0,
            "top_category": "Locker",
            "top_category_rate": 100.0,
        },
        "status_breakdown": {"paid": 1, "pending": 1, "overdue": 0, "waived": 1},
        "categories": [
            {"category_name": "Locker", "collection_rate": 100.0,
             "expected": 100.0, "collected": 100.0, "outstanding": 0.0,
             "fee_count": 1, "paid_count": 1, "pending_count": 0,
             "overdue_count": 0, "waived_count": 0},
            {"category_name": "Kalibulong", "collection_rate": 50.0,
             "expected": 100.0, "collected": 50.0, "outstanding": 50.0,
             "fee_count": 2, "paid_count": 0, "pending_count": 1,
             "overdue_count": 0, "waived_count": 1},
        ],
        "losses": {
            "waived": {
                "count": 1, "billed": 500.0, "collected_before_waive": 200.0,
                "waived_amount": 300.0,
                "records": [
                    {"fee_id": 7, "category": "Kalibulong",
                     "student_number": "S-1", "student_name": "A Student",
                     "program": "BSIT", "year_level": 1, "set": "A",
                     "total_amount": 500.0, "paid_before_waive": 200.0,
                     "waived_amount": 300.0, "remarks": "Hardship"},
                ],
            },
            "discrepancies": {
                "count": 1, "difference": 60.0, "shortfall": 60.0,
                "records": [
                    {"fee_id": 9, "category": "Locker", "student_number": "S-2",
                     "student_name": "B Student", "status": "pending",
                     "total_amount": 100.0, "balance": 40.0, "fee_paid": 60.0,
                     "payments_total": 0.0, "approved_submission_total": 60.0,
                     "difference": 60.0},
                ],
            },
            "total_losses": 360.0,
        },
        "payment_submissions": submission_block(),
        "staff": [
            {"staff_username": "ana", "tx_count": 2, "collected": 100.0},
            {"staff_username": "ben", "tx_count": 1, "collected": 50.0},
        ],
        "payment_methods": [
            {"period": "2026-01", "start": "2026-01-01", "end": "2026-01-31",
             "cash": 100.0, "gcash": 0.0, "total": 100.0},
            {"period": "2026-02", "start": "2026-02-01", "end": "2026-02-28",
             "cash": 0.0, "gcash": 50.0, "total": 50.0},
        ],
        "trend": {
            "labels": ["2026-01", "2026-02"],
            "months": [],
            "series": [
                {"name": "Kalibulong", "points": [30.0, 20.0]},
                {"name": "Locker", "points": [70.0, 30.0]},
            ],
        },
        "program_sets": [
            {"program": "BSIT", "year_level": 1, "set": "A", "billed": 100.0,
             "total": 100.0, "balance": 0.0, "rate": 100.0},
            {"program": "BSIT", "year_level": 2, "set": "B", "billed": 100.0,
             "total": 50.0, "balance": 50.0, "rate": 50.0},
        ],
        "filter_options": {"programs": [], "staff": []},
    }


class CollectionWorkbookTests(SimpleTestCase):
    def build(self, name):
        return sheet_of(build_collection_report_workbook, collection_report(), name)

    def test_the_tabs_come_in_reading_order(self):
        content = build_collection_report_workbook(
            report=collection_report(), filters=FILTERS
        )
        self.assertEqual(
            load_workbook(BytesIO(content)).sheetnames,
            ["Summary", "Categories", "Programs & Sets", "Staff", "Monthly",
             "Trend", "Losses"],
        )

    def test_the_summary_losses_block_adds_up(self):
        sheet = self.build("Summary")

        rows = {
            row[0]: row
            for row in sheet.iter_rows(values_only=True)
            if row and row[0]
        }
        # The listed loss rows sum exactly to the total underneath them.
        self.assertEqual(rows["Waived fees (forgiven balance)"][2], 300.0)
        self.assertEqual(
            rows["Payment shortfall (drawn down with no payment)"][2], 60.0
        )
        self.assertEqual(rows["Total losses"][2], 360.0)
        # Kept money lives with the key figures, not inside the loss sum.
        self.assertEqual(
            rows["Collected on later-waived fees (excluded)"][1], 200.0
        )

    def test_categories_total_every_column(self):
        sheet = self.build("Categories")

        # Header + 2 categories -> totals on row 4.
        self.assertEqual(
            [cell.value for cell in sheet[4]],
            ["All categories", 75.0, 200.0, 150.0, 50.0, 3, 1, 1, 0, 1],
        )

    def test_the_rate_ramp_is_green_only_at_one_hundred(self):
        sheet = self.build("Programs & Sets")

        self.assertEqual(fill_of(sheet.cell(row=2, column=7)), GOOD[0])
        # 50% sits exactly at the amber midpoint of the ramp.
        self.assertEqual(fill_of(sheet.cell(row=3, column=7)), WARN[0])
        # The settled balance is green too.
        self.assertEqual(fill_of(sheet.cell(row=2, column=6)), GOOD[0])

    def test_staff_monthly_and_trend_carry_totals(self):
        staff = self.build("Staff")
        self.assertEqual(
            [cell.value for cell in staff[4]], ["All staff", 3, 150.0]
        )

        monthly = self.build("Monthly")
        self.assertEqual(
            [cell.value for cell in monthly[4]],
            ["Grand total", 100.0, 50.0, 150.0],
        )

        trend = self.build("Trend")
        self.assertEqual(
            [cell.value for cell in trend[4]], ["Total", 50.0, 100.0]
        )

    def test_the_losses_tab_lists_waived_fees_and_discrepancies(self):
        sheet = self.build("Losses")

        self.assertEqual(sheet["A1"].value, "Waived Fee")
        self.assertEqual(sheet["A2"].value, 7)
        # The forgiven amount reads red.
        self.assertEqual(fill_of(sheet.cell(row=2, column=10)), BAD[0])
        self.assertEqual(sheet["A3"].value, "Total (1 fees)")
        self.assertEqual(sheet.cell(row=3, column=10).value, 300.0)

        # Relief sits between the two: an empty table here, but it is
        # always written so the sheet reads the same whatever the period.
        self.assertEqual(sheet["A5"].value, "Discounted Fee")
        self.assertEqual(sheet["A6"].value, "Total (0 fees)")

        self.assertEqual(sheet["A8"].value, "Discrepancy Fee")
        self.assertEqual(sheet["A9"].value, 9)
        self.assertEqual(sheet.cell(row=9, column=11).value, 60.0)
        self.assertEqual(fill_of(sheet.cell(row=9, column=11)), BAD[0])


def detailed_report():
    fee = {
        "id": 1, "student_number": "S-1", "student_name": "A Student",
        "program": "BSIT", "year_level": 1, "set": "A", "total_amount": 100.0,
        "paid": 100.0, "balance": 0.0, "status": "paid", "due_date": None,
        "issued_at": "2026-01-05T08:00:00", "issued_by": None,
        "academic_year": "2025-2026", "semester": "1st", "remarks": None,
        "submissions": [], "payments": [],
    }
    waived_fee = {
        **fee, "id": 2, "total_amount": 500.0, "paid": 200.0, "balance": 300.0,
        "status": "waived", "remarks": "Hardship",
    }
    payment = {
        "id": 11, "fee_id": 1, "category": "Locker", "student_number": "S-1",
        "student_name": "A Student", "previous_balance": 100.0,
        "amount_paid": 100.0, "payment_method": "cash", "source": "direct",
        "payment_submission_id": None, "submission_reference": None,
        "received_by": "ana", "status": "active", "cancelled_by": None,
        "cancelled_at": None, "cancellation_reason": None,
        "created_at": "2026-01-06T08:00:00", "updated_at": "2026-01-06T08:00:00",
    }
    cancelled = {
        **payment, "id": 12, "fee_id": 2, "amount_paid": 50.0,
        "payment_method": "gcash", "status": "cancelled", "cancelled_by": "ana",
        "cancellation_reason": "Encoding error",
    }
    return {
        "generated_at": "2026-08-21T10:00:00",
        "summary": {
            "status_breakdown": {"paid": 1, "pending": 0, "overdue": 0,
                                 "waived": 1},
            "payments": {"total_collections": 100.0, "cash": 100.0,
                         "gcash": 0.0, "tx_count": 1},
            "payment_submissions": submission_block(),
        },
        "categories": [
            {"category_name": "Locker",
             "totals": {"billed": 100.0, "paid": 100.0, "balance": 0.0,
                        "collection_rate": 100.0},
             "statuses": [
                 {"status": "paid", "count": 1,
                  "totals": {"billed": 100.0, "paid": 100.0, "balance": 0.0,
                             "collection_rate": 100.0},
                  "fees": [fee]},
                 {"status": "waived", "count": 1,
                  "totals": {"billed": 0.0, "paid": 0.0, "balance": 0.0,
                             "collection_rate": 0.0},
                  "fees": [waived_fee]},
             ]},
        ],
        "totals": {"billed": 100.0, "paid": 100.0, "balance": 0.0,
                   "collection_rate": 100.0},
        "payments": {
            "records": [payment, cancelled],
            "totals": {
                "count": 2, "amount_paid": 150.0,
                "active": {"count": 1, "amount_paid": 100.0, "cash": 100.0,
                           "gcash": 0.0},
                "cancelled": {"count": 1, "amount_paid": 50.0},
            },
        },
        "payment_submissions": {
            "records": [
                {"id": 21, "submitted_at": "2026-01-06T08:00:00",
                 "student_number": "S-1", "student_name": "A Student",
                 "reference_number": "REF-1", "status": "approved",
                 "total_amount_paid": 100.0, "fee_count": 1,
                 "screenshot_urls": [], "reviewed_by": "ana",
                 "reviewed_at": None, "remarks": None,
                 "updated_at": "2026-01-06T08:00:00"},
            ],
            "totals": {"count": 1, "total_amount_paid": 100.0},
        },
        "payment_submission_items": {"records": [], "totals": {"count": 0,
                                                               "amount_paid": 0.0}},
        "losses": {
            "count": 1, "billed": 500.0, "collected_before_waive": 200.0,
            "waived_amount": 300.0,
            "records": [
                {"fee_id": 2, "category": "Locker", "student_number": "S-1",
                 "student_name": "A Student", "program": "BSIT",
                 "year_level": 1, "set": "A", "total_amount": 500.0,
                 "paid_before_waive": 200.0, "waived_amount": 300.0,
                 "remarks": "Hardship",
                 "payments": [
                     {"id": 13, "amount_paid": 200.0, "previous_balance": 500.0,
                      "payment_method": "gcash", "received_by": "ana",
                      "created_at": "2026-01-06T08:00:00", "status": "active",
                      "submission_reference": "REF-1"},
                 ],
                 "submission_items": [
                     {"item_id": 31, "payment_submission_id": 21,
                      "submission_reference": "REF-1",
                      "submission_status": "approved",
                      "previous_balance": 500.0, "amount_paid": 200.0,
                      "submitted_at": "2026-01-06T08:00:00"},
                 ]},
            ],
        },
    }


class DetailedFeesWorkbookTests(SimpleTestCase):
    def build(self, name):
        return sheet_of(build_detailed_fees_workbook, detailed_report(), name)

    def test_fee_statuses_and_settled_balances_are_coloured(self):
        sheet = self.build("Fees")

        # Columns: Status (3), Amount (9), Discount (10), Paid (11), Balance (12).
        self.assertEqual(fill_of(sheet.cell(row=2, column=3)), GOOD[0])  # paid
        self.assertEqual(fill_of(sheet.cell(row=2, column=12)), GOOD[0])  # 0.00
        self.assertEqual(fill_of(sheet.cell(row=3, column=3)), GRAY[0])  # waived
        # The waived fee still owes on paper - no green for its balance.
        self.assertNotEqual(fill_of(sheet.cell(row=3, column=12)), GOOD[0])

    def test_payments_split_totals_and_colour_method_and_status(self):
        sheet = self.build("Payments")

        self.assertEqual(fill_of(sheet.cell(row=2, column=9)), GOOD[0])  # cash
        self.assertEqual(fill_of(sheet.cell(row=3, column=9)), BLUE[0])  # gcash
        self.assertEqual(fill_of(sheet.cell(row=2, column=13)), GOOD[0])
        self.assertEqual(fill_of(sheet.cell(row=3, column=13)), BAD[0])

        self.assertEqual(sheet["A4"].value, "Active (1 payments)")
        self.assertEqual(sheet.cell(row=4, column=11).value, 100.0)
        self.assertEqual(sheet["A5"].value, "Cancelled (1 payments)")
        self.assertEqual(sheet.cell(row=5, column=11).value, 50.0)
        self.assertEqual(sheet["A6"].value, "Grand total (2 payments)")
        self.assertEqual(sheet.cell(row=6, column=11).value, 150.0)

    def test_the_losses_tab_traces_the_waived_fee_and_its_history(self):
        sheet = self.build("Losses")

        self.assertEqual(sheet["A2"].value, 2)
        self.assertEqual(fill_of(sheet.cell(row=2, column=10)), BAD[0])
        self.assertEqual(sheet["A3"].value, "Total (1 waived fees)")

        # Its payment and its submission item follow, each with a total.
        self.assertEqual(sheet["A5"].value, "Fee ID")
        self.assertEqual(sheet["B6"].value, 13)
        self.assertEqual(sheet["A7"].value, "Total (1 payments)")
        self.assertEqual(sheet["A9"].value, "Fee ID")
        self.assertEqual(sheet["B10"].value, 31)
        self.assertEqual(sheet["A11"].value, "Total (1 items)")


def staff_report():
    return {
        "generated_at": "2026-08-21T10:00:00",
        "staff": [
            {"staff_username": "ana", "full_name": "Ana A", "totals": {
                "collected": 100.0, "tx_count": 2, "cash": 60.0, "gcash": 40.0,
                "share_of_total": 100.0},
             "payments": [
                 {"id": 1, "created_at": "2026-01-06T08:00:00",
                  "amount_paid": 60.0, "previous_balance": 100.0,
                  "payment_method": "cash", "student_number": "S-1",
                  "student_name": "A Student", "category": "Locker",
                  "fee_id": 1, "submission_reference": None},
                 {"id": 2, "created_at": "2026-01-07T08:00:00",
                  "amount_paid": 40.0, "previous_balance": 40.0,
                  "payment_method": "gcash", "student_number": "S-1",
                  "student_name": "A Student", "category": "Locker",
                  "fee_id": 1, "submission_reference": "REF-1"},
             ]},
        ],
        "totals": {"collected": 100.0, "tx_count": 2, "cash": 60.0,
                   "gcash": 40.0},
    }


class StaffWorkbookTests(SimpleTestCase):
    def test_the_summary_totals_split_cash_and_gcash(self):
        sheet = sheet_of(build_staff_workbook, staff_report(), "Summary")

        # Meta block ends at row 9; table headers land on row 10.
        rows = list(sheet.iter_rows(values_only=True))
        totals = next(row for row in rows if row and row[0] == "All staff")
        self.assertEqual(totals[2:6], (2, 60.0, 40.0, 100.0))

    def test_the_payments_tab_totals_the_amount_and_colours_methods(self):
        sheet = sheet_of(build_staff_workbook, staff_report(), "Payments")

        self.assertEqual(fill_of(sheet.cell(row=2, column=6)), GOOD[0])
        self.assertEqual(fill_of(sheet.cell(row=3, column=6)), BLUE[0])
        self.assertEqual(sheet["A4"].value, "Total (2 payments)")
        self.assertEqual(sheet.cell(row=4, column=9).value, 100.0)


def program_set_report():
    def totals(billed, paid, balance, rate, students=None):
        data = {"billed": billed, "paid": paid, "balance": balance,
                "collection_rate": rate}
        if students is not None:
            data["student_count"] = students
        return data

    return {
        "generated_at": "2026-08-21T10:00:00",
        "programs": [
            {"program": "BSIT", "totals": totals(300.0, 250.0, 50.0, 83.3, 2),
             "year_levels": [
                 {"year_level": 1, "totals": totals(300.0, 250.0, 50.0, 83.3, 2),
                  "sets": [
                      {"set": "A", "totals": totals(300.0, 250.0, 50.0, 83.3, 2),
                       "students": [
                           {"student_number": "S-1", "student_name": "A Student",
                            "totals": totals(100.0, 100.0, 0.0, 100.0),
                            "fees": [
                                {"id": 1, "category": "Locker",
                                 "total_amount": 100.0, "paid": 100.0,
                                 "balance": 0.0, "status": "paid",
                                 "due_date": None, "remarks": None},
                            ]},
                           {"student_number": "S-2", "student_name": "B Student",
                            "totals": totals(200.0, 150.0, 50.0, 75.0),
                            "fees": [
                                {"id": 2, "category": "Locker",
                                 "total_amount": 200.0, "paid": 150.0,
                                 "balance": 50.0, "status": "overdue",
                                 "due_date": None, "remarks": None},
                            ]},
                       ]},
                  ]},
             ]},
        ],
        "totals": totals(300.0, 250.0, 50.0, 83.3, 2),
    }


class ProgramSetWorkbookTests(SimpleTestCase):
    def test_students_are_coloured_and_totalled(self):
        sheet = sheet_of(build_program_set_workbook, program_set_report(),
                         "Students")

        # Columns: ..., Billed (6), Discounts (7), Paid (8), Balance (9),
        # Rate (10). A settled balance and a 100% rate both read green.
        self.assertEqual(fill_of(sheet.cell(row=2, column=9)), GOOD[0])
        self.assertEqual(fill_of(sheet.cell(row=2, column=10)), GOOD[0])
        self.assertNotEqual(fill_of(sheet.cell(row=3, column=10)), GOOD[0])
        self.assertEqual(sheet["A4"].value, "Total (2 students)")
        self.assertEqual(
            [sheet.cell(row=4, column=c).value for c in (6, 7, 8, 9, 10)],
            [300.0, 0, 250.0, 50.0, 83.3],
        )

    def test_fees_are_coloured_and_totalled(self):
        sheet = sheet_of(build_program_set_workbook, program_set_report(),
                         "Fees")

        # Columns: ..., Amount (7), Discount (8), Paid (9), Balance (10),
        # Status (11).
        self.assertEqual(fill_of(sheet.cell(row=2, column=10)), GOOD[0])
        self.assertEqual(fill_of(sheet.cell(row=3, column=11)), BAD[0])
        self.assertEqual(sheet["A4"].value, "Total (2 fees)")
        self.assertEqual(
            [sheet.cell(row=4, column=c).value for c in (7, 8, 9, 10)],
            [300.0, 0.0, 250.0, 50.0],
        )
