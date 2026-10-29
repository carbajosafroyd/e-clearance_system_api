# Roles and permissions

Every endpoint in this API is closed until a permission opens it. This is the
guide to that layer: what the files are, how to read them, and the exact steps
for the changes people actually make — most often *"role X should be able to
do Y"*.

- [The mental model](#the-mental-model)
- [The files](#the-files)
- [How to read `roles.py`](#how-to-read-rolespy)
- [Recipe: grant an existing permission to a role](#recipe-grant-an-existing-permission-to-a-role)
- [Recipe: grant something to *everyone*](#recipe-grant-something-to-everyone)
- [Recipe: add a brand-new permission](#recipe-add-a-brand-new-permission)
- [Recipe: add a new role](#recipe-add-a-new-role)
- [Recipe: gate a view](#recipe-gate-a-view)
- [Who may hand out which role](#who-may-hand-out-which-role)
- [Feature flags](#feature-flags)
- [Permissions vs. row scoping](#permissions-vs-row-scoping)
- [Checking your work](#checking-your-work)
- [Gotchas](#gotchas)

---

## The mental model

Four things, in one direction:

```
model Meta.permissions      a permission exists         "fees.waive_fee"
        |                   (a row in auth_permission, written by migrate)
        v
api/rbac/roles.py           a role holds it             CMS_ADMIN gets it
        |                   (declared in Python, nowhere else)
        v
manage.py seed_rbac         the database agrees         auth_group_permissions
        |                   (groups created, permissions attached)
        v
required_permissions        an endpoint demands it      "waive": "fees.waive_fee"
```

A **permission** is one capability, written `app_label.codename`. A **role** is
a named bundle of them, stored as a Django `Group`. A user belongs to groups,
and `user.has_perm("fees.waive_fee")` is the question every endpoint asks.

The rule that matters: **`api/rbac/roles.py` is the only source of truth for
what a role grants.** `seed_rbac` *replaces* a group's permission set with what
that file says, so a box you tick by hand in the Django admin survives until
the next seed and then vanishes — and never existed in anybody else's
environment. Edit the file, run the command.

---

## The files

| File | What lives there |
| --- | --- |
| `api/rbac/roles.py` | **The catalog.** Role names, permission bundles, which role holds which bundle, who may hand out which role. The file you edit. |
| `api/rbac/permissions.py` | The DRF enforcement: `ActionPermission`, `crud()`, `AnyOf`, `Feature`, `ALLOW_ANY`, `DENY`. What a *view* uses. |
| `api/rbac/services.py` | Turns the catalog into database rows. Idempotent; `seed_rbac` calls it. |
| `api/rbac/models.py` | Two unmanaged models that own the permissions no real model can (dashboards, reports, media upload). |
| `api/rbac/scoping.py` | Row-level tenancy — *which rows*, as opposed to *which verbs*. |
| `api/rbac/views.py` | `/api/v1/access/` — read the catalog over HTTP, and assign roles. |
| `api/rbac/management/commands/seed_rbac.py` | The command. |
| `api/rbac/tests.py` | Catches the silent failure: a permission named in `roles.py` that does not exist. |

---

## How to read `roles.py`

The file is five sections, top to bottom.

### 1. `class Roles` — the names

Canonical group names as constants. These strings are the contract with the
frontend (`src/core/constants.ts`), so renaming one means renaming it there and
migrating the `auth_group` rows.

```python
class Roles:
    STUDENT = "Students"
    CMS_ADMIN = "Collection Management System Admins"
    INSTITUTE_ADMIN = "Institute Admins"
```

### 2. `BASELINE` — what every role gets

A short tuple folded into every role by `resolve_permissions`, so a capability
every account needs is written once instead of fifteen times. Keep it small:
anything that is an *authority* rather than plumbing belongs in a bundle a role
opts into.

### 3. Permission bundles

Plain tuples of permission strings, named for the job they describe. They exist
so the role table stays readable and so the same list is not copy-pasted into
three roles that then drift apart.

```python
COLLECTION_DESK = (
    "fees.view_fee",
    "fees.add_fee",
    ...
)
```

Three spellings are accepted anywhere a permission string goes:

| Written | Means |
| --- | --- |
| `"fees.view_fee"` | exactly that permission |
| `"fees.*"` | every permission the `fees` app registers |
| `"*"` | every permission in the project — use sparingly |

### 4. `ROLE_DEFINITIONS` — the table

One entry per role:

```python
Roles.CMS_ADMIN: RoleSpec(
    description="Owns the Collection Management System for an institute: ...",
    staff_flag=True,
    inherits=(Roles.CMS_STAFF,),
    permissions=COLLECTION_MANAGEMENT + REPORTING + (
        "system_settings.change_term_settings",
    ),
),
```

- `description` — shown in the console's role catalog. Write it for the person
  choosing a role for a colleague, not for a developer.
- `permissions` — the bundles and one-off strings this role adds.
- `inherits` — other roles whose permissions are folded in. `CMS_ADMIN`
  inherits `CMS_STAFF`, so the desk permissions are not repeated. Recursive,
  and guarded against cycles.
- `staff_flag` — give members `user.is_staff`. Several older code paths still
  branch on it, so set it on any role that works behind the counter;
  `seed_rbac --sync-staff-flag` then applies it to existing members.

### 5. `DELEGATION` — who may appoint whom

See [Who may hand out which role](#who-may-hand-out-which-role).

### Answering "what can this role actually do?"

```bash
python manage.py shell -c "from api.rbac.roles import resolve_permissions, Roles; print('\n'.join(sorted(resolve_permissions(Roles.CMS_STAFF))))"
```

Or, signed in as somebody holding `rbac.view_role_catalog`, `GET
/api/v1/access/roles/` — every role with its resolved permission list, member
count and group id.

---

## Recipe: grant an existing permission to a role

The common case, and a two-step change.

**1. Find where it belongs in `api/rbac/roles.py`.**

If the permission fits an existing bundle — every role holding that bundle
should get it — add it to the bundle:

```diff
 COLLECTION_DESK = (
     "fees.view_fee",
     "fees.add_fee",
+    "fees.mark_fee_overdue",
 )
```

If only *one* role should get it, add it to that role's own `permissions`
tuple instead, so the bundle stays honest:

```diff
 Roles.LOCKER_ADMIN: RoleSpec(
     permissions=LOCKER_MANAGEMENT + (
         "rbac.view_admin_dashboard",
+        "locker_system.export_lockerreport",
     ),
 ),
```

Two questions worth asking before you type it:

- *Does a role that inherits this one now get something it should not?*
  `SCHOOL_ADMIN` inherits `INSTITUTE_ADMIN`, `CMS_ADMIN`, `CLEARANCE_ADMIN` and
  `LOCKER_ADMIN`, so anything added to any of those reaches it too.
- *Is this the same authority as its neighbours?* "Record a payment" and
  "cancel a payment" look alike and are not — the bundles are split along that
  line deliberately (`COLLECTION_DESK` vs. `COLLECTION_MANAGEMENT`).

**2. Seed.**

```bash
python manage.py seed_rbac --dry-run   # look first
python manage.py seed_rbac
```

That is the whole change. No migration — the permission row already exists;
you are only changing which group points at it.

Affected users pick it up on their next request; nothing is cached between
requests, so nobody has to sign out. The frontend reads its permission list
from `/api/v1/access/me/` at sign-in, though, so a *button* may not appear
until the page is reloaded.

---

## Recipe: grant something to *everyone*

"Every account, whatever its role" is `BASELINE`, near the top of `roles.py`:

```python
BASELINE = (
    "rbac.use_transfer_token",
)
```

`resolve_permissions` folds it into every role, so it reaches students, desk
staff and school administrators alike — and any role added later, which is the
point: a capability every account needs should not depend on somebody
remembering to repeat it in a new `RoleSpec`.

Add a line, run `seed_rbac`, done. Two cautions:

- **Keep it small.** `BASELINE` is invisible from the role table — somebody
  reading `ROLE_DEFINITIONS` will not see it. That is a fair trade for
  plumbing (a transfer token, a media upload) and a bad one for an authority.
  If the honest sentence is "everyone *happens* to need this today", it belongs
  in a bundle instead.
- **Permission, not scope.** Baseline says the *verb* is open to all. Which
  rows an account may touch is still `get_queryset`'s answer — see
  [Permissions vs. row scoping](#permissions-vs-row-scoping).

Note that "every role" is not literally every user row: an account in no group
holds no permissions at all. It is roles that carry the baseline.

---

## Recipe: add a brand-new permission

When no permission expresses what you need — usually because you added an
`@action` that is its own authority.

**1. Declare it on the model that owns the data.**

```python
class Fee(models.Model):
    class Meta:
        db_table = "fees"
        permissions = [
            ("waive_fee", "Can waive the remaining balance of a fee"),
        ]
```

The codename is the bare verb (`waive_fee`); the app label comes from the app
the model lives in, so the string you write everywhere else is
`fees.waive_fee`. The second element is the label shown in the Django admin —
write it as a sentence starting "Can ...".

If the endpoint is backed by no model at all — a dashboard, a report, an export
that reads across five tables — put it on one of the two holders in
`api/rbac/models.py` instead:

```python
class ReportAccess(models.Model):
    class Meta:
        managed = False          # no table is ever created
        default_permissions = ()
        permissions = [
            ("view_collection_report", "Can view the collection report"),
        ]
```

Those give you the `rbac.` prefix: `rbac.view_collection_report`.

**2. Migrate, so the permission row exists.**

```bash
python manage.py makemigrations fees   # or: rbac
python manage.py migrate
```

A `Meta.permissions` change produces a real migration, and Django's
`post_migrate` hook writes the `auth_permission` row. Until this runs the
permission does not exist, and `seed_rbac` will warn that `roles.py` names
something it cannot find.

**3. Grant it**, as in [the recipe above](#recipe-grant-an-existing-permission-to-a-role).

**4. Require it on a view** — see below. A permission nothing checks is
decoration.

---

## Recipe: add a new role

```diff
 class Roles:
+    REGISTRAR = "Registrars"
```

```diff
 ROLE_DEFINITIONS: dict[str, RoleSpec] = {
+    Roles.REGISTRAR: RoleSpec(
+        description="Keeps the student roster for one institute.",
+        staff_flag=True,
+        permissions=ACADEMIC_READ + INSTITUTE_RECORDS,
+    ),
 }
```

Then decide **who may appoint one**. A role nobody can hand out can only be set
by a superuser:

```diff
 DELEGATION = {
     Roles.INSTITUTE_ADMIN: (
         Roles.INSTITUTE_STAFF,
+        Roles.REGISTRAR,
     ),
 }
```

Run `python manage.py seed_rbac` to create the group, and mirror the name in
the frontend's `src/core/constants.ts` and its delegation table in
`src/core/rbac/roles.ts`.

Removing a role is the same edit in reverse, plus `seed_rbac --prune` to drop
the now-undeclared group. `--prune` deletes *every* group `roles.py` does not
declare, so check with `--dry-run` first if the deployment has hand-made ones.

---

## Recipe: gate a view

A viewset declares, in one table, which permission each action needs:

```python
from api.rbac import ActionPermission, crud

class FeeViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "fees", "fee",
        waive="fees.waive_fee",
        unwaive="fees.unwaive_fee",
    )
```

`crud("fees", "fee")` expands to the five CRUD entries plus a `default` of
`fees.view_fee`; every keyword after it is an `@action` name.

Lookup order is the exact action name, then `DEFAULT`, then **deny**. The
fall-through denies rather than allows, so a new `@action` added without a
table entry is closed until somebody decides what it needs. If you add an
action and get an unexpected `403`, you have found the feature, not a bug.

What a table entry can hold:

| Value | Means |
| --- | --- |
| `"app.codename"` | the caller must hold it |
| `["a", "b"]` | must hold **all** of them |
| `AnyOf("a", "b")` | must hold **at least one** |
| `ALLOW_ANY` | no permission beyond the view's other permission classes |
| `DENY` | always refused — switches an inherited route off |
| `Feature("x", req)` | refused unless the institute runs feature `x`; then `req` decides |

`gate(feature, table)` wraps a whole table in one `Feature`, for a viewset that
exists only because a module is enabled.

---

## Who may hand out which role

Permissions say what an account can *do*; `DELEGATION` says whom it can
*appoint*. Holding `users.assign_user_role` is not enough — the role being
added or removed must appear under a role the caller holds:

```python
DELEGATION: dict[str, tuple[str, ...]] = {
    Roles.SCHOOL_ADMIN: (Roles.INSTITUTE_ADMIN,),
    Roles.INSTITUTE_ADMIN: (
        Roles.INSTITUTE_STAFF, Roles.CMS_ADMIN, Roles.CMS_STAFF, ...
    ),
    Roles.CMS_ADMIN: (Roles.CMS_STAFF, Roles.STUDENT),
}
```

The union over every role the actor holds is what applies.
`AccessViewSet.assign` and the invitation serializer both enforce it, and only
the *difference* between an account's current roles and the submitted list is
judged — so the console can send an account's whole role set back untouched
even when it includes roles the caller may not grant.

A superuser appoints school administrators (`SUPERUSER_DELEGATION`) and,
through the assign endpoint, may set any role directly, as the Django admin
can.

The console mirrors this table in `src/core/rbac/roles.ts`; this one is the
authority.

---

## Feature flags

A permission can belong to an optional module. `FEATURE_PERMISSIONS` near the
top of `roles.py` records which:

```python
FEATURE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    features.COUPONS: ("coupons.*",),
}
```

A permission listed there is only ever granted while its feature is on:
`seed_rbac` never writes the row, and `/api/v1/access/me/` drops it on the way
out — so turning a flag off takes effect on the next request rather than the
next reseed. When you add a permission to a module that can be switched off,
list its codename here too, or an institute that does not run the module will
be told it holds a permission the API refuses.

Full details in `core/features.py`.

---

## Permissions vs. row scoping

Different questions, different files.

- **Permission** — *may this account do this kind of thing at all?*
  `fees.view_fee`, checked by `ActionPermission`.
- **Scope** — *which rows?* `api/rbac/scoping.py`, called from `get_queryset`.

```python
def get_queryset(self):
    return scope_queryset(
        self.queryset, self.request.user,
        institute_path="student__program__institute",
        school_paths=("student__program__institute__school",),
    )
```

Three kinds of caller: a superuser sees everything, a School Admin their whole
school, everybody else their own institute — and an account with no institute
sees nothing, which is the right fail-closed answer.

This is why a student can hold `users.change_user`: `UserViewSet.get_queryset`
narrows them to their own row, so the grant buys a profile page rather than an
escalation path. Before deciding a broad-looking permission is too much, read
what the viewset's `get_queryset` does with it.

---

## Checking your work

```bash
python manage.py seed_rbac --dry-run          # per role: how many permissions, what is missing
python manage.py seed_rbac                    # apply
python manage.py seed_rbac --sync-staff-flag  # also fix User.is_staff on members
python manage.py seed_rbac --prune            # delete groups roles.py does not declare
python manage.py test api.rbac                # the catalog tests
```

`--dry-run` is the one to reach for after editing `roles.py`. A line like

```
! fees.waive_fees
```

means `roles.py` names a permission the database does not have — nearly always
a typo (`waive_fees` for `waive_fee`), or a migration that has not been
applied. Django grants nothing for an unknown string and says nothing about it
at runtime, which is exactly why this check exists; `api/rbac/tests.py` asserts
the same thing in CI.

Run `seed_rbac` after **every** migration that adds a permission. It is
idempotent and safe on a live database.

---

## Gotchas

**A permission removed from `roles.py` is removed from the database.** A
group's permission set is replaced, not merged. That is deliberate — the file
is the source of truth — but it means the Django admin is not a place to
configure anything permanently.

**A typo grants nothing, silently.** `--dry-run` and the tests are how you
catch it.

**`inherits` is not free.** `SCHOOL_ADMIN` folds in four other roles, so a
permission added to `LOCKER_STAFF` reaches `LOCKER_ADMIN` and `SCHOOL_ADMIN`
as well.

**Wildcards grow.** `"coupons.*"` picks up every permission a future model in
that app declares, including ones nobody meant to hand out. Prefer explicit
codenames outside the bundles that deliberately take a whole app.

**Role names are a contract.** They are the strings in `auth_group.name`, in
the frontend's constants, and in `ClearanceRequirement.authorizing_role`.
Renaming one is a data migration, not an edit.

**`is_staff` is bookkeeping, not authority.** Scoping deliberately ignores it —
it has been wrong on real accounts — and it exists for the older code paths
that still read it. Decide access with permissions and roles.

**Superusers bypass permission checks but not feature flags.** `has_perm`
short-circuits to `True` for them, so a superuser reaches everything a
permission guards. An action wrapped in `Feature(...)` is still refused for
everybody when the module is off: a feature the deployment does not run is not
a permission anybody can be granted.
