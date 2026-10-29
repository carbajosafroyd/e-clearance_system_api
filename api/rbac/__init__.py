"""Role Based Access Control: the role catalog and the view-level enforcement.

Import surface for the rest of the project::

    from api.rbac import ActionPermission, crud, ALLOW_ANY, AnyOf, DEFAULT

`roles.py` is where a role's permissions are declared, `permissions.py` is
where a view declares what it needs, and `services.py` turns the former into
database rows (`python manage.py seed_rbac`).
"""

from .permissions import (  # noqa: F401
    ALLOW_ANY,
    DEFAULT,
    DENY,
    ActionPermission,
    AnyOf,
    Feature,
    HasAnyRole,
    InstituteScoped,
    ReadOnly,
    crud,
    gate,
    user_permission_codenames,
    user_role_names,
)
from .roles import (  # noqa: F401
    ROLE_DEFINITIONS,
    Roles,
    feature_of_permission,
    resolve_permissions,
)

__all__ = [
    "ALLOW_ANY",
    "DEFAULT",
    "DENY",
    "ActionPermission",
    "AnyOf",
    "Feature",
    "HasAnyRole",
    "InstituteScoped",
    "ReadOnly",
    "ROLE_DEFINITIONS",
    "Roles",
    "crud",
    "feature_of_permission",
    "gate",
    "resolve_permissions",
    "user_permission_codenames",
    "user_role_names",
]
