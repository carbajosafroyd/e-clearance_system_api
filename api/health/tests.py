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

