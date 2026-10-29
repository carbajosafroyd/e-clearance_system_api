from rest_framework.throttling import UserRateThrottle

class CreateSchoolThrottle(UserRateThrottle):
    scope = "create_school"


class UpdateSchoolThrottle(UserRateThrottle):
    scope = "update_school"


"""
IN SETTINGS.PY

REST_FRAMEWORK = {
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "1000/day",
        "anon": "10/min",
        "create_school": "5/min",
        "update_school": "20/hour",
    }
}
"""