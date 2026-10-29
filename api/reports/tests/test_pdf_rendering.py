from django.test import SimpleTestCase
from api.reports.services.pdf import render_pdf


class PdfRendererSmokeTests(SimpleTestCase):
    def test_render_pdf_returns_bytes_or_buffer(self):
        context = {
            "school_name": "Test School",
            "school_location": "Test Location",
            "institute_name": "Test Institute",
            "clearance_type_name": "Graduation Clearance",
            "academic_year": "2025-2026",
            "semester": "1st Semester",
            "student_name": "John Doe",
            "student_number": "2022-0001",
            "program_name": "BSIT",
            "year_level": "4th Year",
            "date_completed_formatted": "October 29, 2026",
            "generated_at_formatted": "October 29, 2026 10:00 AM",
            "verification_token": "test-uuid",
            "verify_url": "http://localhost:3000/verify",
            "qr_code_data_uri": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
            "items": [],
        }
        output = render_pdf(template_name="clearance/certificate.html", context=context)
        self.assertIsInstance(output, bytes)
        self.assertTrue(output.startswith(b"%PDF"))
