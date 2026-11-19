from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class HealthCheckEndpointTests(SimpleTestCase):
    def test_liveness_endpoints(self):
        for path in ["/healthz", "/healthz/", "/health/live/"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "healthy")
                self.assertIn("uptime_seconds", data)
                self.assertIn("timestamp", data)


class CorsHeadersTests(SimpleTestCase):
    def test_allowed_origins_receive_cors_header(self):
        allowed_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://e-clearance-dnsc.vercel.app",
            "https://preview-branch-test.vercel.app",
        ]
        for origin in allowed_origins:
            with self.subTest(origin=origin):
                response = self.client.get("/healthz", HTTP_ORIGIN=origin)
                self.assertEqual(
                    response.headers.get("Access-Control-Allow-Origin"),
                    origin,
                    f"Origin {origin} should be granted Access-Control-Allow-Origin",
                )

    def test_untrusted_origin_denied_cors_header(self):
        response = self.client.get("/healthz", HTTP_ORIGIN="https://malicious-site.com")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_preflight_options_request_allowed(self):
        response = self.client.options(
            "/api/v1/login/",
            HTTP_ORIGIN="https://e-clearance-dnsc.vercel.app",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,authorization",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://e-clearance-dnsc.vercel.app",
        )
        self.assertEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")

