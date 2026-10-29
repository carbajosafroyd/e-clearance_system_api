from rest_framework.routers import DefaultRouter
from .views import SchoolViewSet

router = DefaultRouter()
router.register("schools", SchoolViewSet, basename="school")

urlpatterns = router.urls


"""
With this setup:

Create → SchoolCreateSerializer, CanCreateSchool, 5/min throttle

Read → SchoolReadSerializer, authenticated only

Update → SchoolUpdateSerializer, CanUpdateSchool, 20/hour throttle

Delete → SchoolDeleteSerializer, only superusers

Summary (custom) → SchoolSummarySerializer, any authenticated user
"""