# DNSC Systems API

Django REST API behind the Collection Management System and its sibling
systems (clearance, lockers, attendance).

- [Setup](#-setup)
- [One line to a working database](#-one-line-to-a-working-database)
- [Starting fresh](#-starting-fresh-formatting-the-database)
- [Seeding](#-seeding)
- [Roles and permissions](#-roles-and-permissions)
- [Contributions and points](#-student-contributions-and-points)
- [Running the server](#-running-the-server)
- [Endpoints](#%EF%B8%8F-available-endpoints)

---

## 📦 Setup

### 1. Clone

```bash
git clone https://github.com/Etriii/dnsc_systems_api.git
cd dnsc_systems_api
```

### 2. Virtual environment

**Windows (PowerShell/CMD):**

```bash
python -m venv venv
.\venv\Scripts\activate
```

**macOS/Linux:**

```bash
python -m venv venv
source venv/bin/activate
```

> `deactivate` when you are done.

### 3. Dependencies

```bash
pip install -r requirements.txt
```

### 4. Environment

```bash
cp .env.example .env
```

Fill in the database credentials. For a zero-setup local database, set
`USE_SQLITE=True` instead and skip MySQL entirely.

Every environment needs a `PASSWORD_PEPPER_1` — the server refuses to boot
without one. See [Password pepper](#-password-pepper-versioned-rotatable).

---

## ⚡ One line to a working database

```bash
python manage.py bootstrap
```

That is the whole thing. It runs, in order:

1. `makemigrations` — catches a model change nobody generated a migration for
2. `migrate` — applies **every** app's migrations
3. `seed_rbac` — creates the roles and their permissions

Step 3 is not optional. Every endpoint in this API is permission-gated, so a
migrated-but-unseeded database answers `403` to everything.

Migrated-and-roled is still not something you can *sign in to*, though —
there is no school, no institute and no account yet. That is `--essential`:

```bash
python manage.py bootstrap --essential
```

which adds the schools, their institutes, the degree programs, the admin
accounts to sign in with and the per-institute settings the frontend reads on
boot — and none of the sample fees, payments or complaints. It is the one to
use on a new database.

Want the whole sample dataset to click through as well?

```bash
python manage.py bootstrap --demo
```

Other flags:

| Flag | What it does |
| --- | --- |
| `--essential` | Also seed schools, institutes, programs, the admin accounts and the per-institute settings. |
| `--demo` | Also run every seeder (`seed_all`). Includes everything `--essential` does. |
| `--fresh` | **Format the database first** — drop every table, then migrate from nothing. See below. |
| `--allow-production` | Required alongside `--fresh` when `DEBUG` is off. |
| `--scale 10` | Ten times the demo volume. |
| `--reset` | With `--demo`, delete seeded rows before recreating them. |
| `--no-input` | Never prompt. Use in CI. |
| `--check-migrations` | Fail if a model change has no migration, instead of creating one. Use in CI. |
| `--skip-makemigrations` | Go straight to `migrate`. |

`bootstrap` is safe to re-run: applied migrations are skipped and every
seeder is idempotent. (`--fresh` is the exception — that one destroys data on
purpose.)

If you prefer the raw commands:

```bash
python manage.py migrate
python manage.py seed_rbac
python manage.py seed_all --essential
```

---

## 🧹 Starting fresh (formatting the database)

When the database is past saving — a migration that was rewritten, a table
that no longer matches its model, a half-seeded mess nobody trusts — format
it and build it back with one command:

```bash
python manage.py bootstrap --fresh --essential
```

It drops **every table** in the configured database, migrates from nothing,
and then seeds the six steps that make an empty deployment usable:

| # | Step | Why it is essential |
| --- | --- | --- |
| 1 | `seed_rbac` | Roles and their permissions. Without them every endpoint answers `403`. |
| 2 | `seed_schools` | The campuses, and the buildings lockers and attendance hang off. |
| 3 | `seed_institutes` | The institutes under each school. Nearly every row in the system is scoped to one. |
| 4 | `seed_programs` | The degree programs students are enrolled in. |
| 5 | `seed_users` | `superadmin`, a School Admin per school, an Institute Admin per institute — something to sign in as. |
| 6 | `seed_system_settings` | The per-institute settings the frontend reads on boot: school year, semester, feature toggles. |

Nothing else. No sample fees, payments, complaints or audit trail — and no
demo collection, clearance or locker staff either: `seed_users` is asked for
its `--essential` set, so you get the admins and nothing with a shared
password that you did not ask for. Every **role** still exists, because
`seed_rbac` always seeds the full catalogue — an admin creates the real staff
through the API.

Want the rest later? `python manage.py seed_users` fills in the remaining
role accounts, and `python manage.py seed_all` the whole sample dataset.

### What `--fresh` actually does

It drops the tables, all of them, and lets `migrate` build the schema again.
Django's `flush` only empties the tables it can still see; it cannot help
when the schema *itself* is what disagrees with the code, which is the case
this exists for.

There is no undo, so it asks before it moves:

- it prints how many tables it is about to drop, and from which database;
- it asks you to type the database name back — `--no-input` skips the
  question, and is meant for CI;
- it refuses outright when `DEBUG` is off, unless `--allow-production` is
  passed as well.

Take a dump first if anything in there matters:

```bash
mysqldump -u USER -p DBNAME > backup.sql     # MySQL
cp db.sqlite3 db.sqlite3.bak                 # SQLite
```

Prefer it in steps?

```bash
python manage.py bootstrap --fresh     # drop every table, migrate, seed the roles
python manage.py seed_all --essential  # the six steps above
python manage.py seed_all              # ...or the full demo dataset instead
```

---

## 🌱 Seeding

Assume the database is empty. `seed_all` fills it in dependency order —
school → institute → program → student → category → fee → payment → …

```bash
python manage.py seed_all                 # roles + a demo dataset
python manage.py seed_all --essential     # the minimum a new database needs
python manage.py seed_all --core-only     # roles only (safe in production)
python manage.py seed_all --scale 10      # ten times the volume
python manage.py seed_all --reset         # wipe seeded rows first
python manage.py seed_all --only seed_fees seed_payments
```

### Who exists in the dataset

Schools, institutes and programs are not three independent lists — they are
one wiring diagram, and it lives in **`api/seeding/catalog.py`**:

```
SCHOOLS      numbered 1, 2, 3 …                DNSC, UM, …
INSTITUTES   name their school by that number  school=1 → IC, ITE, IBA
PROGRAMS     name their institute by acronym   IC → BSCS, BSIT
```

The numbers are **not** primary keys; they are stable names for a row in that
file. Each seeder resolves a spec to a real row by its natural key — a school
by its acronym (`short_name`), an institute by its acronym — so an institute
lands under the school it was declared for whatever ids the database handed
out, and in whatever order the rows were created.

| School | Institutes | Programs |
| --- | --- | --- |
| 1 · `DNSC` Davao del Norte State College | `IC`, `ITE`, `IBA` | BSCS/BSIT, BEED/BSED, BSBA/BSA |
| 2 · `UM` University of Mindanao | `CCE`, `CTE`, `CBAE` | BSCS/BSIT, BEED/BSED, BSBA/BSA |

Adding a campus is three edits in that one file: give it the next number,
list its institutes against that number, list their programs against the
institute acronyms. `seed_institutes` reports a declared school it cannot
find rather than quietly leaving it empty, and re-running it re-attaches an
institute that ended up under the wrong school instead of shrugging.

Acronyms carry weight beyond display. `core/features.py` decides from
`Institute.acronym` which institute may run the contribution scheme, and
`seed_users` builds every username out of it — so changing one is a rename
with consequences, while adding one is free.

`--scale 2` and up add satellite campuses (`SAT1`, `SAT2`, …) with an
institute each. One campus is enough to click through; several are what
surface a cross-tenant leak.

### The seeders

Each API feature owns its own seeder, in that app's
`management/commands/`. Run any of them on its own:

| Command | Creates |
| --- | --- |
| `seed_rbac` | Roles and their permissions. **Required.** |
| `seed_schools` | The campuses and their buildings |
| `seed_institutes` | The institutes declared for each school, matched to it by acronym |
| `seed_programs` | The degree programs each institute offers, keyed on the institute acronym |
| `seed_users` | One account per role, per institute, named `<school>_<institute>_<role>`, plus a superuser |
| `seed_students` | Students and the accounts they sign in with |
| `seed_system_settings` | The per-institute settings the frontend reads on boot |
| `seed_collection_categories` | Categories, GCash accounts, fee exemptions |
| `seed_attendance` | Attendance events, day settings, student records |
| `seed_fees` | Fee batches and fees, through the real generation engine |
| `seed_payments` | Over-the-counter payments, some later cancelled |
| `seed_payment_submissions` | GCash submissions in every review state |
| `seed_lockers` | Lockers, requests, subscriptions, reports |
| `seed_clearance` | Clearance templates, requirements, student clearances |
| `seed_contributions` | Point policies, student reports, point redemptions |
| `seed_posts` | Announcement feed with comments and reactions |
| `seed_notifications` | Notifications and one broadcast per institute |
| `seed_audits` | A short audit trail |

Every one of them supports:

```bash
--scale N        volume multiplier (default 1)
--reset          delete this feature's rows first
--random-seed N  a different, still-reproducible dataset
```

### What you get

Data is **deterministic**: the random stream is seeded from a fixed value, so
the same command produces the same rows on every machine.

It is also **idempotent**: running a seeder twice does not double anything.
Reference data is created with `get_or_create`; activity data (payments,
submissions, locker requests, audit entries) is created once and skipped
afterwards — use `--reset` to redo it.

The fee, payment and submission seeders go through the **real services**
(`fees.services.generation.engine`, `payments.services.record_payment`,
`payment_submissions.services`), so balances, statuses and batches are
consistent with each other rather than plausible-looking but wrong.

### Demo accounts

Every seeded account uses the password `password123`. No username is written
down anywhere — each one is **derived from the rows seeded before it**: the
school's acronym, the institute's acronym, and the role.

```
<school>_<institute>_<role>     dnsc_ic_admin, um_cce_cmsadmin
<school>_<role>                 dnsc_admin, um_admin   (school-wide)
superadmin                      belongs to no school
```

So the roles below, where `*` is any school/institute pair the catalogue
declares:

| Username | Role |
| --- | --- |
| `superadmin` | Django superuser — bypasses every permission check |
| `<school>_admin` | School Admins — school-wide, and deliberately attached to no institute |
| `*_admin` | Institute Admins — runs one institute, tied to no system |
| `*_staff` | Institute Staffs — the institute's day-to-day records, tied to no system |
| `*_cmsadmin` / `*_cmsstaff` | Collection Management System |
| `*_clearanceadmin` | Clearance Management System Admins |
| `*_lockeradmin` / `*_lockerstaff` | Locker Management System |
| `*_librarian`, `*_dean`, `*_cashier`, `*_adviser`, `*_programchair`, `*_governor` | Clearance signatories |
| `student2026000001`, … | Students |

Which, for the dataset in `api/seeding/catalog.py`, comes out as
`dnsc_admin` and `um_admin` at school level, then the full set for each of
`dnsc_ic_`, `dnsc_ite_`, `dnsc_iba_`, `um_cce_`, `um_cte_` and `um_cbae_` —
`dnsc_ic_cashier`, `um_cbae_cmsadmin`, and so on. Every institute gets its
own set, because an institute with no collection staff is one the fee,
payment and clearance seeders quietly skip.

Carrying the school as well as the institute is what makes the name safe:
`IC` at one school and `IC` at another are different institutes, and a bare
`icadmin` would have to belong to one of them. An institute whose acronym is
blank falls back to `inst1`, `inst2`, … and says so as it seeds; so does a
school with no short name.

### Just the accounts a new deployment needs

```bash
python manage.py seed_users --essential
```

The superuser, a School Admin per school, an Institute Admin per institute —
and none of the collection, clearance, locker or signatory accounts, which
are sample data. Their **roles** still exist (that is `seed_rbac`'s job, and
it always seeds every role), so an admin creates the real staff through the
API instead of inheriting a pile of logins that share one password.
`seed_all --essential` and `bootstrap --essential` pass this through.

`seed_users` refuses to run with `DEBUG` off unless you pass
`--allow-production`, and `--password` overrides the default. `--reset`
deletes the accounts it would create, including the ones an older naming
scheme left behind.

### Writing a new seeder

Subclass `SeedCommand` from `api/seeding/support.py`:

```python
from api.seeding.support import SeedCommand

class Command(SeedCommand):
    seed_name = "widgets"
    help = "Create demo widgets."
    depends_on = ("seed_institutes",)

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")
        for institute in institutes:
            Widget.objects.get_or_create(institute=institute, name="Demo")
        self.created("widget", len(institutes))

    def reset(self, **options):
        Widget.objects.all().delete()
```

You get `--scale`, `--reset`, `--random-seed`, a seeded `self.rng`, and
`self.require(...)`, which fails with *"run `seed_institutes` first"* instead
of an `IntegrityError` six frames deep. Then add one line to `SEED_STEPS` in
`api/seeding/registry.py`, after whatever your seeder depends on — plus
`essential=True` if a deployment is unusable without it, and
`supports_essential=True` if your seeder takes an `--essential` of its own to
seed a smaller set (the way `seed_users` seeds only the admins).

Reference data that another seeder also has to agree with — a campus, an
institute, a program — belongs in `api/seeding/catalog.py` rather than in
your command, so the two cannot drift apart.

---

## 🔐 Roles and permissions

Every endpoint is gated on a permission, and permissions are handed out in
bundles called roles. The full guide lives with the code it describes —
**[api/rbac/README.md](api/rbac/README.md)**; the short version:

| To… | Edit… |
| --- | --- |
| add a permission | the owning model's `Meta.permissions`, then `makemigrations` |
| grant it to a role | `api/rbac/roles.py`, then `python manage.py seed_rbac` |
| grant it to every role | `BASELINE` in `api/rbac/roles.py`, then `seed_rbac` |
| require it on a view | `required_permissions` on the viewset |

```python
from api.rbac import ActionPermission, crud

class FeeViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud("fees", "fee", waive="fees.waive_fee")
```

Anything not named in that table is denied, so a new `@action` is closed
until somebody decides what it needs.

```bash
python manage.py seed_rbac --dry-run          # what would change
python manage.py seed_rbac --sync-staff-flag  # fix User.is_staff
python manage.py seed_rbac --prune            # drop groups roles.py omits
```

### Who may hand out which role

`DELEGATION` in `api/rbac/roles.py` is the chain, keyed by the role the
**actor** holds — the union over every role they have:

| Actor holds | May invite into, or assign |
| --- | --- |
| superuser | `School Admins`, naming the school |
| `School Admins` | `Institute Admins`, naming one of their school's institutes |
| `Institute Admins` | `Institute Staffs`, the collection / clearance / locker admins and staff, and `Students` — in their own institute |

`POST /admin/invitations/` refuses a `group_id` outside the caller's row, and
`/access/assign/` refuses *adding or removing* one. A role the caller cannot
hand out may stay on an account untouched — only the difference is judged, so
an institute administrator editing somebody's roles can never strip the
school-level one they cannot see past.

### Who sees which rows

`api/rbac/scoping.py` is the one place tenancy is decided, from the account's
role and where it is attached — deliberately **not** from `is_staff`, which
was off on real accounts and quietly emptied every list for them:

| Caller | Sees |
| --- | --- |
| superuser | everything |
| `School Admins` | every institute of their school (`user.school`, or their institute's) |
| anyone else | their own institute |
| attached to neither | nothing |

The users, programs, students, schools, buildings, system-settings and
institute-GCash viewsets read it (a school administrator sees *no* GCash
accounts — which wallet students pay into is the institute's own decision).
`POST /api/v1/users/{id}/set-scope/` places an account, since an account
attached to nothing is the usual reason for empty lists everywhere.

---

## 🎛️ Feature flags

Not every institute wants every module. Optional ones are switched off in
`.env`, and off means **gone**, not hidden:

| Variable | Covers | Default |
| --- | --- | --- |
| `FEATURE_CONTRIBUTIONS` | student reports and recommendations | `True` |
| `FEATURE_CONTRIBUTION_POINTS` | points, wallets, the ranking, fee discounts | `True` |
| `FEATURE_COMPLAINTS` | [student complaints](docs/COMPLAINTS.md) and the staff workflow | `True` |

Three layers act on the same flag, each failing closed:

1. **routing** — `api/urls.py` never registers the routes, so they `404`;
2. **permissions** — `api/rbac/roles.py` stops granting them, so `seed_rbac`
   creates nothing, and `/api/v1/me/` drops any grant an earlier seed left
   behind, superusers included;
3. **actions** — an action on a resource that stays (a fee's discount panel)
   is wrapped in `Feature(...)` and refuses.

`FEATURE_CONTRIBUTION_POINTS=False` with contributions still on is the
useful middle state: students keep filing reports and staff keep reviewing
them, approvals simply pay nothing and no ranking exists. A child flag can
never outlive its parent, so turning contributions off turns points off
whatever the env says.

Both `/api/v1/me/` and `/api/v1/access/features/` report the live flags, and
the frontend reads them from there — it has no feature variables of its own,
so switching a module on for an institute needs no fresh bundle. Adding a
flag is four steps, listed at the top of `core/features.py`.

---

## 🎓 Student contributions and points

Students file reports and recommendations against a specific system; an admin
who approves one awards points; points convert into a discount on a fee, and
enough of them waive it outright. Each system reviews and funds its own
reports, so points are earned and spent per system, and there is a ranking.

See **[docs/CONTRIBUTING_POINTS.md](docs/CONTRIBUTING_POINTS.md)** for how to
file something worth points, how reviewers score it, and how a coordinator
tunes the rates per institute and per system.

The whole module is optional — see [Feature flags](#️-feature-flags) if this
deployment should not run it.

---

## 📣 Student complaints

Separate from contributions, and separate on purpose. A contribution is an
offer the institute may pay for; a complaint is a claim it owes an answer to
— a fee that should not have been issued, a payment that never landed, a
record that does not match what happened.

Students file one against a system and, where it is about money, against the
fee itself. Staff acknowledge, resolve or close it, and **every one of those
notifies the student** — including a rejection, with its reason. Nothing
here earns points, and the module keeps working with the whole contributions
scheme switched off.

See **[docs/COMPLAINTS.md](docs/COMPLAINTS.md)**.

---

## ☁️ Cloudinary Multi-Account Setup

Images (logos, sign-in backgrounds, profile pictures, GCash QR codes, payment
screenshots, post media) live on Cloudinary, and a free-tier account fills up.
Rather than migrate everything when it does, the project supports **N accounts
at once**: new uploads go to the *current* account, and every image already
uploaded stays on the account that holds it — the same shape as a versioned
password pepper, where the current version signs new hashes and each record
remembers the version it was made with.

### How it works

- `config/settings/base.py` discovers every numbered block
  `CLOUDINARY_CLOUD_NAME_<N>` / `CLOUDINARY_API_KEY_<N>` / `CLOUDINARY_API_SECRET_<N>`
  into `CLOUDINARY_ACCOUNTS = {N: {...}}`, and `CLOUDINARY_CURRENT_VERSION`
  names the account new uploads use (default: the highest `N`). The original
  un-numbered trio is still read as version **1**, so nothing changes for a
  deployment that predates the numbering.
- Every model that stores an image also stores the version it was uploaded
  under: `cloudinary_version` on `User`, `Student`, `InstituteGcashAccount`,
  `PostMedia` and `PaymentSubmission` (one per submission — its screenshots are
  uploaded together), and `logo_cloudinary_version` /
  `background_cloudinary_version` on `Institute` and `School`, which carry two
  images that may live on different accounts.
- `api/utils/cloudinary/cloudinary_utils.py` names the account on **every**
  call instead of switching the SDK's global configuration:
  - `upload_image(...)` / `upload_image_versioned(...)` / `upload_files(...)`
    always use the **current** account and report the version they used;
  - `delete_image(public_id, version)`, `delete_file(...)`, `delete_files(...)`,
    `get_file_url(public_id, version=...)`, `resolve_image_url(value, version=...)`,
    `replace_image(old, new, folder, old_version=...)` and
    `remove_image(value, version=...)` all take the **stored** version.
- A delivery URL embeds the cloud name, so reads need the version as much as
  deletes do: an image on account 1 rendered with account 2's cloud name is a
  404, and a `destroy` sent to the wrong account quietly answers "not found"
  and leaves the file — and the bill — behind.

### Rows that do not know their version

The data migrations set version 1 on every existing image (only one account
ever existed before this). Should a row still be null — a code path that
forgot to set it, a manually inserted row — the helpers **look it up once**:
a full URL already names its cloud, and a bare public id is asked of each
account (current first) through the Admin API until one answers. Persist the
answer on the row when you have it; the Admin API is rate-limited on free
tiers, so a lookup should happen once per image, not once per request.

### Adding a new account (no code changes)

1. Create the account on Cloudinary and copy its cloud name, API key and secret.
2. Add the next numbered block to the environment, e.g. for a third account:
   ```
   CLOUDINARY_CLOUD_NAME_3=...
   CLOUDINARY_API_KEY_3=...
   CLOUDINARY_API_SECRET_3=...
   ```
3. Point new uploads at it: `CLOUDINARY_CURRENT_VERSION=3` (or leave it unset
   to use the highest number). A `CURRENT_VERSION` with no matching block
   refuses to start rather than uploading nowhere.
4. Restart the server. Everything already uploaded keeps resolving under the
   account it lives on; only new uploads land on the new account.

Never remove an older block while rows still reference it — the images on
that account would become unreadable and undeletable.

### Where uploads live - the folder tree

Every path is decided in **one module**, `api/utils/cloudinary/paths.py`
(its docstring is the long version of this section, with a worked example of
one upload's life). Serializers ask `folder_for(owner, kind)` and
`tags_for(owner)` and pass both to the upload helpers; nothing else names a
folder.

```
superadmin/profiles/                         superusers' avatars
schools/<school_id>/
  logos/  backgrounds/  profiles/            the school's images; School Admins
  institutes/<institute_id>/
    logos/  backgrounds/  profiles/          the institute's images; its staff
    gcash_accounts/                          GCash QR codes
    posts/<post_id>/                         post media, one folder per post
    students/<student_id>/
      profiles/  payments/                   the student's avatar and receipts
users/<kind>/                                an account attached to nothing
```

- **Ids, never names.** A public id is permanent (renaming is an API call per
  asset plus every row that references it); a short name or acronym is not.
- **Students by id, not by program / year / set.** Those change every term
  and a folder cannot follow. They go on **tags** instead.
- **Tags name the owner** on every upload: `school:1 institute:2 student:1234
  program:BSIT year:3 set:B user:7 post:9`. Filter by them in the Media
  Library, or `cloudinary.Search().expression("tags=student:1234")`.
- **Both accounts share the tree.** The row's `cloudinary_version` says which
  account has the file; the path says where in the tree it is.
- **Deleting an owner deletes its subtree.** Destroying a school or institute
  runs `delete_by_prefix(owner_root(instance))` on every account - logos, QR
  codes, posts, every student's uploads, in a handful of calls.
- **Old ids stay put.** `users/...`, `institutes/logos/...`, `gcash_qr/...`,
  `payment_submissions/...`, `posts/<id>/...` from before the tree keep
  working; `describe_path()` reports them as `legacy`. Nothing moves them.
- **Never move an asset in the Cloudinary Media Library.** Depending on the
  account's folder mode that changes the public id and orphans the row.

Debugging a public id:

```
python manage.py shell -c "from api.utils.cloudinary.paths import describe_path;   print(describe_path('schools/1/institutes/2/students/1234/payments/abc').owner)"
# -> student 1234 (school 1, institute 2)
```

### When you add a new image field

Add a `…cloudinary_version` column beside it, set it from the version the
upload helper returns at the same call site that stores the public id, and
pass it to every `get_file_url` / `resolve_image_url` / `delete_image` for that
field. **Always delete with the image's own stored version — never the
current one.**

## 📧 Email

Every message is branded with the **school** of the account it concerns —
the inviting staff member's school on an invitation, the recipient's on
everything else — never a name baked into a template. Accounts with no
school fall back to `EMAIL_BRAND_NAME`; the header logo follows the same
idea, institute → school → `EMAIL_BRAND_LOGO_URL`.

**Which mailbox sends** is decided per message too. A school and an
institute can each mail through their own Gmail account — an address and an
app password saved from the console, the password **encrypted** under
`EMAIL_SECRETS_KEY` and never returned by the API. A superuser sends as the
platform (`EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` in the environment), a
school administrator as their school, an institute account as its institute.

**Nothing set up means the default.** The sender fields are optional at
every level, and an empty one falls through to the next:

```
institute sender  ->  school sender  ->  platform (EMAIL_HOST_USER / DEFAULT_FROM_EMAIL)
```

So a fresh install with only the `.env` mailbox works exactly as before; a
school that sets one up takes over its own mail (and its institutes'); an
institute that sets one up takes over only its own. Every school and
institute row reports `effective_email_sender` — the mailbox its mail
*actually* goes out through right now, `{source, from_email, label}` — and
`GET /api/v1/email-sender/defaults/` reports the platform's, so the console
can show the real fallback ("leave this empty and mail is sent as …")
rather than describe it. The from-line and the brand name are all that is
ever exposed; the login never leaves the server.

Two flows are built out of email rather than merely announced by it:

- **Invitations** carry the activation link, the username the account will
  use, and a one-time password, so an invited user can sign in without a
  password-reset round trip. The password is stored hashed the moment it is
  generated; the plaintext exists only in the message that was sent.
- **Changing your own email** takes four steps: a code to the current
  address, then a confirmation link to the new one. `User.email` is not
  written until that link is opened — an address nobody can prove they own
  never becomes an account's address.

  ```
  POST /api/v1/email-change/request/   (auth)   code -> current address
  POST /api/v1/email-change/verify/    (auth)   code in, change_token out
  POST /api/v1/email-change/set-new/   (auth)   link -> new address
  GET  /api/v1/email-change/confirm/   (public) applies the change
  ```

- **Moving somebody else's email** is an administrator's version of the same
  flow, for the person whose old mailbox is gone — the case the four steps
  above cannot help, because the code would go somewhere nobody reads. The
  code step is skipped and the confirmation link goes straight to the new
  address, naming the administrator who asked; `User.email` still changes
  only when it is opened, so a typo simply expires.

  ```
  POST   /api/v1/users/{id}/change-email/  (users.change_user_email) link -> new address
  DELETE /api/v1/users/{id}/change-email/  cancels the pending change
  ```

- **Password resets an administrator starts**: `set-password` types one for
  the person and signs their sessions out everywhere, `send-password-reset`
  emails them the ordinary code and never shows it to the administrator.
  Both need `users.set_user_password`, both refuse a target more senior than
  the caller.

See **[docs/EMAIL.md](docs/EMAIL.md)** for the templates, the settings and
what guards each step.

---

## 🚀 Running the server

### Option A: HTTPS (for SSL/secure features)

```bash
python manage.py runserver_plus --cert-file local_cert
```

`https://127.0.0.1:8000/` — the browser will warn about the self-signed
certificate; **Advanced → Proceed**.

### Option B: HTTP

```bash
python manage.py runserver
```

`http://127.0.0.1:8000/`

---

## 🗺️ Available endpoints

| Service | URL |
| --- | --- |
| Django Admin | `/admin/` |
| API Root | `/api/v1/` |
| Current user (incl. roles + permissions) | `/api/v1/me/` |
| Role/permission catalog | `/api/v1/access/` |
| Change your own email | `/api/v1/email-change/` |
| Per-institute settings | `/api/v1/system-settings/` |
| Student workbook (export, import, template) | `/api/v1/students/export/` |
| Enabled feature flags | `/api/v1/access/features/` |
| Swagger UI | `/api/docs/swagger/` |
| Redoc | `/api/docs/redoc/` |
| OpenAPI schema (JSON) | `/api/schema/` |

---

## 🧪 Tests

```bash
python manage.py test
python manage.py test api.fees api.rbac      # one or more apps
```

`api/rbac/tests.py` includes a check that every permission named in
`roles.py` actually exists — a typo there grants nothing and is otherwise
silent.

---

## 🌶️ Password pepper (versioned, rotatable)

Every password is run through `HMAC-SHA256(pepper, password)` before Django's
normal hasher salts and stretches it. The pepper lives only in the environment,
so a database dump on its own gives an attacker nothing to crack against —
they would need the server's `.env` as well. `DJANGO_SECRET_KEY` is deliberately
**not** used for this: it signs sessions, tokens and reset links, and rotating
it must not re-key every password hash (nor the reverse).

### How it works

- `config/settings/base.py` discovers every `PASSWORD_PEPPER_<N>` into
  `PASSWORD_PEPPERS = {N: secret}`, and `PASSWORD_PEPPER_CURRENT_VERSION` names
  the one new hashes use (default: the highest `N`). `core/pepper.py` does the
  mixing.
- `User.pepper_version` records which pepper each hash was made with.
  **Version 0** means "no pepper" — every hash from before this feature — and
  needs no reset campaign.
- `User.check_password` (which the JWT login, the admin and every
  change-password flow go through) verifies the password under the **row's**
  version. If it verifies and that version is behind the current one, the
  account is re-hashed under the current pepper right there — the only moment
  the plaintext is in hand — and `pepper_version` is bumped in the same save.
  A wrong password changes nothing.
- `User.set_password` always uses the current version, and the project's
  `UserManager` routes `create_user` / `createsuperuser` / the seeders
  through it too (Django's own writes `make_password` directly and would skip
  the pepper).
- An invitation's one-time password is hashed at invite time and stores its
  version alongside (`UserRegistrationToken.initial_password_pepper_version`),
  so a rotation between sending and opening the invitation does not break it.
- The server refuses to boot with no pepper set, in every environment —
  version 0 is only ever a hash from before the feature, never a fresh one.

### Rotating

Say version 1 is in use and you want to move to 3:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # a new pepper
```

```env
PASSWORD_PEPPER_1=<keep the old one>
PASSWORD_PEPPER_3=<the new one>
PASSWORD_PEPPER_CURRENT_VERSION=3      # optional - the highest number is the default
```

Restart. From then on every new hash uses 3, and each existing user moves
from 1 to 3 the first time they sign in successfully. Check on the stragglers:

```bash
python manage.py pepper_status
```

```
Current pepper version: 3
Configured versions: [1, 3]

  version 1:     14 user(s)   old pepper still configured - upgrades on next sign-in
  version 3:    312 user(s)   current
```

Only when a version reports no rows may its `PASSWORD_PEPPER_<N>` be removed.
Remove it earlier and the accounts still on it can no longer be verified at
all — their only way back is a password reset (`pepper_status` flags them as
`PEPPER MISSING`).

---

## 🔒 Security note

`runserver_plus` writes `local_cert.crt` and `local_cert.key` into the
project root. They are personal to your machine and must never be committed —
make sure they stay in `.gitignore`.

The seeded accounts share a well-known password. They are for local and
staging use only; `seed_users` refuses to create them with `DEBUG` off unless
explicitly forced.
