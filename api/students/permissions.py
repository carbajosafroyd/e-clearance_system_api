from rest_framework.permissions import BasePermission

class IsStudent(BasePermission):
    message = "Only students"

    def has_permission(self, request, view):
        return hasattr(request.user, "student")
