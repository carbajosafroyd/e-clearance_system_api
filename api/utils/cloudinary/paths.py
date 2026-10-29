"""
Where every upload lives on Cloudinary - the one place a path is decided.

Nothing outside this module writes a folder name. A serializer asks
`folder_for(owner, kind)` and `tags_for(owner)` and hands both to the upload
helpers in `cloudinary_utils`; a debugger hands a public id to
`describe_path` and gets the owner back. Change the layout here, and the
tests in `tests.py` say exactly what moved.

The tree
--------

Roots are the two kinds of thing that own uploads and belong to nobody:
superadmins and schools. Everything else hangs off a school.

    superadmin/
    └── profiles/                       superusers' avatars

    schools/<school_id>/
    ├── logos/                          the school's own images -
    ├── backgrounds/                    School.logo / .background_image
    ├── profiles/                       school-wide accounts (School Admins)
    └── institutes/<institute_id>/
        ├── logos/                      Institute.logo / .background_image
        ├── backgrounds/
        ├── profiles/                   institute staff
        ├── gcash_accounts/             InstituteGcashAccount.gcash_qr
        ├── posts/<post_id>/            PostMedia, one folder per post
        └── students/<student_id>/
            ├── profiles/               the student's avatar (User.profile)
            └── payments/               PaymentSubmission.screenshot

    users/<kind>/                       an account attached to nothing - a
                                        seed row, a half-invited user; real
                                        accounts never land here

Ids, not names. A public id is permanent (renaming one is an API call per
asset plus every row that references it), so nothing in it may change:
`schools/1`, never `schools/DNSC` - a short name is display text, it can be
edited, and one institute's is blank today. Students are filed by *their*
id and not by program / year / set for the same reason: a year level moves
every June, and the folder would be wrong for most of the students in it
within a year of being made.

Tags carry what moves. Every upload is tagged with its owners
(`school:1`, `institute:2`, `student:1234`, `user:7`, `post:9`) and, for a
student, the classification a browse would want (`program:BSIT`, `year:3`,
`set:B`). Tags are searchable in the Media Library and the Search API and
can be rewritten in bulk without touching a public id, which is exactly
the property the path lacks.

Two accounts, one tree. `CLOUDINARY_ACCOUNTS` (see cloudinary_utils) means
the same folder exists on every account; the row's `cloudinary_version`
says which one holds the file, the path says where in the tree it is. A
browse of account 1 shows what was uploaded before the switch, account 2
what came after.

The life of an upload
---------------------

1. A student PATCHes `/me/update/` with a new avatar. `UserUpdateSerializer`
   asks `folder_for(user, Kind.PROFILES)`: the user is not a superuser, has
   a `student` row, so the answer is
   `schools/1/institutes/2/students/1234/profiles`, with tags
   `school:1 institute:2 student:1234 program:BSIT year:3 set:B user:7`.
2. `upload_image` sends it to the *current* account; Cloudinary appends a
   random name, and the public id
   `schools/1/institutes/2/students/1234/profiles/k3j2h1...` plus the
   account version are stored on the row. Only then is the old avatar
   destroyed, on *its* account.
3. A year later the student is in 4th year, set A. The path still says
   `students/1234` - true forever. The tags say `year:3 set:B` - the year
   the picture was taken, which is also true; re-tag if you want them
   current.
4. The institute is deleted. `delete_by_prefix("schools/1/institutes/2/")`
   on every account takes every asset under it - logos, QR codes, posts,
   every student's uploads - in a handful of calls, and nothing is orphaned.

What was there before
---------------------

Rows written before this module hold flat ids: `users/...`,
`institutes/logos/...`, `schools/backgrounds/...`, `gcash_qr/...`,
`payment_submissions/...`, `posts/<id>/...`. They keep working - the id is
the address, and it is still valid - and `describe_path` reports them as
`legacy`. Nothing moves them; a relocation would be a one-off management
command, run once, if it is ever wanted.

Never move an asset in the Cloudinary Media Library. Depending on the
account's folder mode, dragging it to another folder changes its public id
and the row pointing at it breaks. The tree is for looking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from django.core.exceptions import ObjectDoesNotExist

from api.rbac.scoping import is_school_wide


class Kind(str, Enum):
    """The leaf folders. A serializer names one; a typo is a ValueError."""

    PROFILES = "profiles"
    LOGOS = "logos"
    BACKGROUNDS = "backgrounds"
    GCASH_ACCOUNTS = "gcash_accounts"
    PAYMENTS = "payments"
    POSTS = "posts"

ROOT = "codex_systems/"

SUPERADMIN_ROOT = f"{ROOT}superadmin"
SCHOOLS_ROOT = f"{ROOT}schools"
INSTITUTES_SEGMENT = "institutes"
STUDENTS_SEGMENT = "students"
#: An account attached to no school or institute. Real accounts never land
#: here; it exists so an odd row still uploads somewhere describable.
UNFILED_ROOT = "users"

_TAG_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def _pk(value) -> int:
    """An id from a model instance or a bare integer."""
    pk = getattr(value, "pk", value)
    if pk is None:
        raise ValueError("An owner must be saved (have a pk) before it can own an upload.")
    return int(pk)


def superadmin_root() -> str:
    return SUPERADMIN_ROOT


def school_root(school) -> str:
    """`schools/<id>` - accepts a School or its id."""
    return f"{SCHOOLS_ROOT}/{_pk(school)}"


def institute_root(institute) -> str:
    """`schools/<school_id>/institutes/<id>`."""
    return f"{school_root(institute.school_id)}/{INSTITUTES_SEGMENT}/{_pk(institute)}"


def student_root(student) -> str:
    """`.../institutes/<id>/students/<student_pk>` - the institute is the program's."""
    return f"{institute_root(student.program.institute)}/{STUDENTS_SEGMENT}/{_pk(student)}"


def post_root(post) -> str:
    """`.../institutes/<id>/posts/<post_id>`; a post with no institute keeps the old flat shape."""
    if post.institute_id is None:
        return f"{Kind.POSTS.value}/{_pk(post)}"
    return f"{institute_root(post.institute)}/{Kind.POSTS.value}/{_pk(post)}"


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


def student_of(user):
    """The Student row behind an account, or None."""
    try:
        return user.student
    except (AttributeError, ValueError, ObjectDoesNotExist):
        return None


def _models():
    # Resolved at call time: this module is imported by serializers of the
    # very apps whose models it dispatches on.
    from django.apps import apps
    from django.contrib.auth import get_user_model

    return (
        apps.get_model("schools", "School"),
        apps.get_model("institutes", "Institute"),
        apps.get_model("students", "Student"),
        apps.get_model("posts", "Post"),
        get_user_model(),
    )


def _user_scope(user):
    """
    (root, scope owner) for an account: which folder its own uploads go
    under, and the school / institute / student that folder belongs to
    (None for a superadmin or an unattached account).

    Precedence, first match wins:
      superuser        -> superadmin/          the account outranks any attachment
      has a Student    -> the student's folder even for an officer finishing
                          a degree: a person's own uploads are theirs
      school-wide      -> the school's folder   `user.school` with no institute,
                          or the School Admins role over both
      has an institute -> the institute's folder
      nothing          -> users/
    """
    if getattr(user, "is_superuser", False):
        return SUPERADMIN_ROOT, None
    student = student_of(user)
    if student is not None:
        return student_root(student), student
    school_id = getattr(user, "school_id", None)
    institute_id = getattr(user, "institute_id", None)
    if school_id is not None and (institute_id is None or is_school_wide(user)):
        return school_root(school_id), user.school
    if institute_id is not None:
        return institute_root(user.institute), user.institute
    return UNFILED_ROOT, None


def user_root(user) -> str:
    return _user_scope(user)[0]


def owner_root(owner) -> str:
    """The folder everything `owner` owns sits under - also the prefix to delete it all by."""
    School, Institute, Student, Post, User = _models()
    if isinstance(owner, School):
        return school_root(owner)
    if isinstance(owner, Institute):
        return institute_root(owner)
    if isinstance(owner, Student):
        return student_root(owner)
    if isinstance(owner, Post):
        return post_root(owner)
    if isinstance(owner, User):
        return user_root(owner)
    raise TypeError(f"{type(owner).__name__} does not own Cloudinary uploads.")


def folder_for(owner, kind) -> str:
    """
    The folder an upload of `kind` that belongs to `owner` goes in.

    `owner` is a School, Institute, Student, Post or User. A Post *is* its
    folder (`.../posts/<id>`), so `kind` must be `Kind.POSTS` there.
    """
    kind = Kind(kind)
    School, Institute, Student, Post, User = _models()
    if isinstance(owner, Post):
        if kind is not Kind.POSTS:
            raise ValueError("A post's media can only be of kind POSTS.")
        return post_root(owner)
    return f"{owner_root(owner)}/{kind.value}"


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def _tag(key: str, value) -> Optional[str]:
    clean = _TAG_UNSAFE.sub("", str(value if value is not None else "")).strip("_.-")
    return f"{key}:{clean}" if clean else None


def _present(*tags) -> list:
    return [t for t in tags if t]


def _school_tags(school_id) -> list:
    return _present(_tag("school", school_id))


def _institute_tags(institute) -> list:
    return _school_tags(institute.school_id) + _present(_tag("institute", institute.pk))


def _student_tags(student) -> list:
    program = student.program
    return _institute_tags(program.institute) + _present(
        _tag("student", student.pk),
        _tag("program", getattr(program, "acronym", None) or getattr(program, "name", None)),
        _tag("year", getattr(student, "s_lvl", None)),
        _tag("set", getattr(student, "s_set", None)),
    )


def tags_for(owner) -> list:
    """
    `key:value` tags naming everything that owns the upload, so the Media
    Library and the Search API can find it by school, institute, student,
    user or post - and, for a student, by program / year / set at the time
    of upload. Safe characters only; an empty value drops the tag.
    """
    School, Institute, Student, Post, User = _models()
    if isinstance(owner, School):
        return _school_tags(owner.pk)
    if isinstance(owner, Institute):
        return _institute_tags(owner)
    if isinstance(owner, Student):
        return _student_tags(owner)
    if isinstance(owner, Post):
        scope = _institute_tags(owner.institute) if owner.institute_id is not None else [] # type: ignore
        return scope + _present(_tag("post", owner.pk))
    if isinstance(owner, User):
        root, scope = _user_scope(owner)
        own = _present(_tag("user", owner.pk))
        if root == SUPERADMIN_ROOT:
            return [SUPERADMIN_ROOT] + own
        if scope is None:
            return own
        return tags_for(scope) + own
    raise TypeError(f"{type(owner).__name__} does not own Cloudinary uploads.")


# ---------------------------------------------------------------------------
# Reading a path back
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathInfo:
    """What a public id says about its owner. `legacy` = written before the tree."""

    public_id: str
    legacy: bool = False
    root: Optional[str] = None
    school: Optional[int] = None
    institute: Optional[int] = None
    student: Optional[int] = None
    post: Optional[int] = None
    kind: Optional[Kind] = None
    file: Optional[str] = None

    @property
    def owner(self) -> str:
        """One line for a log message: `student 1234 (school 1, institute 2)`."""
        if self.legacy:
            return "legacy (pre-tree id)"
        if self.root == SUPERADMIN_ROOT:
            return "superadmin"
        if self.root == UNFILED_ROOT:
            return "unattached account"
        if self.post is not None:
            return f"post {self.post} (school {self.school}, institute {self.institute})"
        if self.student is not None:
            return f"student {self.student} (school {self.school}, institute {self.institute})"
        if self.institute is not None:
            return f"institute {self.institute} (school {self.school})"
        return f"school {self.school}"


_KINDS = {k.value for k in Kind}


def describe_path(public_id: str) -> PathInfo:
    """
    Parse a public id written by this module. Anything else - the flat ids
    from before the tree, a URL, garbage - comes back as `legacy` rather
    than raising, because this is what you call while debugging.
    """
    if not public_id or not isinstance(public_id, str):
        return PathInfo(public_id=str(public_id), legacy=True)
    parts = public_id.strip("/").split("/")
    legacy = PathInfo(public_id=public_id, legacy=True)

    def after_root(root):
        root_parts = root.strip("/").split("/")
        if parts[:len(root_parts)] == root_parts:
            return parts[len(root_parts):]
        return None

    for root in (SUPERADMIN_ROOT, UNFILED_ROOT):
        rest = after_root(root)
        if rest is not None:
            if len(rest) == 2 and rest[0] in _KINDS:
                return PathInfo(public_id, root=root, kind=Kind(rest[0]), file=rest[1])
            return legacy

    rest = after_root(SCHOOLS_ROOT)
    if rest is None or len(rest) < 3 or not rest[0].isdigit():
        return legacy
    school, rest = int(rest[0]), rest[1:]
    institute = student = None
    if rest[0] == INSTITUTES_SEGMENT:
        if len(rest) < 4 or not rest[1].isdigit():
            return legacy
        institute, rest = int(rest[1]), rest[2:]
        if rest[0] == STUDENTS_SEGMENT:
            if len(rest) < 4 or not rest[1].isdigit():
                return legacy
            student, rest = int(rest[1]), rest[2:]
    if institute is not None and student is None and rest[0] == Kind.POSTS.value:
        if len(rest) == 3 and rest[1].isdigit():
            return PathInfo(
                public_id, root=SCHOOLS_ROOT, school=school, institute=institute,
                post=int(rest[1]), kind=Kind.POSTS, file=rest[2],
            )
        return legacy
    if len(rest) == 2 and rest[0] in _KINDS:
        return PathInfo(
            public_id, root=SCHOOLS_ROOT, school=school, institute=institute,
            student=student, kind=Kind(rest[0]), file=rest[1],
        )
    return legacy
