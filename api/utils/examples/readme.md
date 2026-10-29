# Reference snippets

Nothing in this folder is imported or routed. It is a place to read a
complete, commented example of the patterns the real apps use, without
having to pick them out of a 400-line viewset.

- `views.py` - a viewset with an RBAC table, per-action throttles and
  tenancy scoping. Start here; the full explanation is in `api/rbac/README.md`.
- `models.py` - where a custom permission is declared (`Meta.permissions`),
  which is what makes it appear in the database after `makemigrations`.
- `serializers.py`, `throttles.py`, `urls.py`, `utils.py` - the smaller
  pieces the viewset leans on.

## Rules the real apps follow

- Never let one user CRUD another school's or institute's data. Scope the
  list in `get_queryset()` **and** re-check the object with a permission
  class - a custom `@action` that fetches its own object bypasses the
  queryset.
- Every `@action` needs its own entry in `required_permissions`. Anything
  not listed is denied.
- A new permission is born on a model, granted in `api/rbac/roles.py`, and
  required in the view. Never granted anywhere else.
