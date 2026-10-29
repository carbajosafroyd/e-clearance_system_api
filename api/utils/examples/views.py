"""
Reference viewset. Not wired to any router - copy from it, do not import it.

The point of this file is the `required_permissions` table: it is the only
place a view says who may reach what, and adding an action is one line in it.
See `api/rbac/README.md` for the whole picture.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.rbac import ALLOW_ANY, DENY, ActionPermission, AnyOf, InstituteScoped, crud

from ..models import School
from .serializers import (
    SchoolCreateSerializer,
    SchoolDeleteSerializer,
    SchoolReadSerializer,
    SchoolSummarySerializer,
    SchoolUpdateSerializer,
)
from .throttles import CreateSchoolThrottle, UpdateSchoolThrottle
from .utils import CustomPagination


"""
| Action name (`self.action`) | HTTP Method(s) | URL pattern (router prefix = `schools`) | What it does                        |
| --------------------------- | -------------- | --------------------------------------- | ----------------------------------- |
| **`list`**                  | GET            | `/schools/`                             | Get a paginated list of all objects |
| **`retrieve`**              | GET            | `/schools/{pk}/`                        | Get a single object by ID           |
| **`create`**                | POST           | `/schools/`                             | Create a new object                 |
| **`update`**                | PUT            | `/schools/{pk}/`                        | Replace an object (all fields)      |
| **`partial_update`**        | PATCH          | `/schools/{pk}/`                        | Update only some fields             |
| **`destroy`**               | DELETE         | `/schools/{pk}/`                        | Delete an object                    |

Those six are the built-ins. Everything else you add with @action - and every
one of those needs its own line in `required_permissions`, or it is denied.
"""


class SchoolViewSet(viewsets.ModelViewSet):
    queryset = School.objects.all()
    pagination_class = CustomPagination

    # ---------------------------------------------------------------- RBAC --
    #
    # `ActionPermission` reads the table below: what kind of user may reach
    # which action.
    #
    # `InstituteScoped` answers the other half - is this particular row
    # theirs? Both are needed: holding `change_school` must not let you edit
    # a school in someone else's institute.
    permission_classes = [IsAuthenticated, ActionPermission, InstituteScoped]

    # `crud(app_label, model)` writes the five standard entries plus a
    # `default`; keyword arguments name @action methods.
    required_permissions = crud(
        "schools",
        "school",
        # A custom action gets a custom permission - declared in the model's
        # Meta.permissions, so `makemigrations` puts it in the database.
        approve="schools.approve_school",
        # No permission needed beyond being signed in.
        summary=ALLOW_ANY,
        # Either grant is enough.
        archive=AnyOf("schools.archive_school", "schools.change_school"),
        # Switch a route off outright.
        destroy=DENY,
    )

    def get_serializer_class(self):
        if self.action == "create":
            return SchoolCreateSerializer
        elif self.action == "retrieve":
            return SchoolReadSerializer
        elif self.action in ["update", "partial_update"]:
            return SchoolUpdateSerializer
        elif self.action == "destroy":
            return SchoolDeleteSerializer
        elif self.action == "summary":
            return SchoolSummarySerializer
        return SchoolReadSerializer

    def get_throttles(self):
        # The global throttle usually covers this; per-action throttles are
        # for the expensive writes.
        if self.action == "create":
            return [CreateSchoolThrottle()]
        elif self.action in ["update", "partial_update"]:
            return [UpdateSchoolThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        # Tenancy, part one: never *list* rows outside the caller's scope.
        # Part two is `InstituteScoped`, which re-checks any object a custom
        # action fetches by itself.
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        return self.queryset.filter(id=getattr(user, "school_id", None))

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        school = self.get_object()
        return Response(self.get_serializer(school).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        school = self.get_object()
        # ... the actual work
        return Response({"detail": f"{school} approved."}, status=status.HTTP_200_OK)
