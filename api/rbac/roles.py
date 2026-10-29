"""
The role catalog: the one file to edit when a role changes.

`ROLE_DEFINITIONS` maps a Django ``Group`` name to the permissions its
members hold. `python manage.py seed_rbac` reads this file and nothing else,
so a role change is a one-line edit here followed by one command - never a
hand-edit in the Django admin that the next environment will not have.

Permission strings
------------------
Three forms are accepted:

    "fees.view_fee"     one permission
    "fees.*"            every permission registered by the fees app
    "*"                 every permission in the project (use sparingly)

Every codename must exist in the database, which means it must come from a
model - either one of Django's four defaults (add/change/delete/view) or an
entry in a model's ``Meta.permissions``. Adding a new custom permission is
therefore: add it to the model's ``Meta.permissions``, run
``makemigrations`` + ``migrate``, then reference it here.

`BASELINE`
----------
A handful of permissions every account needs whatever it does. They are
folded in by `resolve_permissions`, so a new role picks them up without
listing them - and so nothing has to be repeated fifteen times.

`staff_flag`
------------
Several older code paths still branch on ``user.is_staff`` (institute
scoping in `FeeViewSet.get_queryset`, the submission review permission, and
others). A role that must satisfy those sets ``staff_flag=True``;
``seed_rbac --sync-staff-flag`` then applies it to the members of that group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core import features


class Roles:
    """Canonical group names.

    These strings are the contract with the frontend - the same values live
    in ``src/core/constants.ts`` as ``Roles``. Renaming one here means
    renaming it there and migrating the ``auth_group`` rows.
    """

    STUDENT = "Students"

    # Collection Management System (this repo's main system)
    CMS_ADMIN = "Collection Management System Admins"
    CMS_STAFF = "Collection Management System Staffs"

    # School-wide administration
    SCHOOL_ADMIN = "School Admins"
    # Institute-level administration, tied to no one system. The console's
    # four tiers (super admin > school admin > institute admin > institute
    # staff) need these two to exist: before them, "runs this institute"
    # could only be expressed as "owns the collection system", which handed
    # a registrar fee-waiving powers they never asked for.
    INSTITUTE_ADMIN = "Institute Admins"
    INSTITUTE_STAFF = "Institute Staffs"

    # Clearance Management System
    CLEARANCE_ADMIN = "Clearance Management System Admins"
    LIBRARIAN = "Librarians"
    DEAN = "Deans"
    CASHIER = "Cashiers"
    ADVISER = "Advisers"
    PROGRAM_CHAIR = "Program Chairs"
    GOVERNOR = "Governors"

    # Locker Management System
    LOCKER_ADMIN = "Locker Management System Admins"
    LOCKER_STAFF = "Locker Management System Staffs"


#: Roles whose members sign clearance requirements.
CLEARANCE_SIGNATORY_ROLES = (
    Roles.LIBRARIAN,
    Roles.DEAN,
    Roles.CASHIER,
    Roles.ADVISER,
    Roles.PROGRAM_CHAIR,
    Roles.GOVERNOR,
)


@dataclass(frozen=True)
class RoleSpec:
    description: str
    permissions: tuple[str, ...] = ()
    #: Other roles whose permissions are folded into this one.
    inherits: tuple[str, ...] = ()
    #: Give members ``is_staff`` (see the module docstring).
    staff_flag: bool = False


# ---------------------------------------------------------------------------
# Reusable permission bundles
#
# Bundles keep the role table below readable and stop the same list being
# copy-pasted into three roles that then drift apart.
# ---------------------------------------------------------------------------

#: Which deployment feature owns which permission.
#:
#: A permission listed here is only ever granted while its feature is on:
#: `resolve_permissions` drops it, so `seed_rbac` never writes the row, and
#: `user_permission_codenames` drops it too, so a database seeded *before*
#: the flag was turned off stops reporting it to the frontend on the next
#: request rather than the next reseed.
#:
#: `contributions` and `contribution_points` share an app label, so the split
#: is per codename rather than per app. Points come first because lookup
#: prefers an exact match and then falls back to an ``app.*`` wildcard - the
#: narrower feature must get its say first.
FEATURE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    features.CONTRIBUTION_POINTS: (
        "contributions.view_pointaccount",
        "contributions.add_pointaccount",
        "contributions.change_pointaccount",
        "contributions.delete_pointaccount",
        "contributions.view_pointtransaction",
        "contributions.add_pointtransaction",
        "contributions.change_pointtransaction",
        "contributions.delete_pointtransaction",
        "contributions.view_pointpolicy",
        "contributions.add_pointpolicy",
        "contributions.change_pointpolicy",
        "contributions.delete_pointpolicy",
        "contributions.view_feediscount",
        "contributions.add_feediscount",
        "contributions.change_feediscount",
        "contributions.delete_feediscount",
        "contributions.review_feediscount",
        "contributions.cancel_feediscount",
        "contributions.view_contribution_leaderboard",
        # The panel on the fee itself: nothing to show without payouts.
        "fees.view_fee_discount_history",
    ),
    features.CONTRIBUTIONS: (
        "contributions.*",
        "rbac.view_contribution_report",
    ),
    features.COMPLAINTS: ("complaints.*",),
    features.COUPONS: ("coupons.*",),
}


def feature_of_permission(permission: str) -> str | None:
    """The feature flag that owns `permission`, or None if it is always on."""
    for feature, owned in FEATURE_PERMISSIONS.items():
        if permission in owned:
            return feature

    app_label = permission.split(".", 1)[0]
    for feature, owned in FEATURE_PERMISSIONS.items():
        if f"{app_label}.*" in owned:
            return feature
    return None


def permission_enabled(permission: str, institute=None) -> bool:
    """Is this permission grantable, for this institute?

    With no institute the deployment answers alone - which is what
    `seed_rbac` wants, since a group grant is global and cannot be issued per
    institute. The per-institute filtering happens on the way out, in
    `/api/v1/me/`.
    """
    feature = feature_of_permission(permission)
    return feature is None or features.is_enabled(feature, institute)


def enabled_permissions(permissions: Iterable[str], institute=None) -> list[str]:
    """`permissions`, minus anything a switched-off feature owns."""
    return [
        permission
        for permission in permissions
        if permission_enabled(permission, institute)
    ]


#: Held by every role, without being listed in any of them.
#:
#: `resolve_permissions` folds this in, so a permission here reaches students,
#: desk staff and school administrators alike - including any role added
#: later, which is the point: a capability every account needs should not
#: depend on somebody remembering to repeat it in a new `RoleSpec`.
#:
#: Keep it small. Anything that is an *authority* rather than plumbing belongs
#: in a bundle a role opts into.
BASELINE = (
    # Jumping between the sibling systems without signing in again. Which
    # systems an account may actually enter is `UserSystem`'s answer, not
    # this permission's - it only says the account may mint the token.
    "rbac.use_transfer_token",
)

#: Read-only academic reference data every signed-in user needs.
ACADEMIC_READ = (
    "schools.view_school",
    "schools.view_schoolbuilding",
    "institutes.view_institute",
    "programs.view_program",
)

#: What a student may do with their own records.
STUDENT_SELF_SERVICE = (
    "students.view_student",
    "fees.view_fee",
    # Their own fee's discount history. Row scoping is get_queryset's job;
    # this only says a student may look at the list at all.
    "fees.view_fee_discount_history",
    "payments.view_payment",
    "payment_submissions.view_paymentsubmission",
    "payment_submissions.add_paymentsubmission",
    "payment_submissions.withdraw_paymentsubmission",
    "payment_submissions.view_paymentsubmissionitems",
    "collection_categories.view_collectioncategory",
    "collection_categories.view_institutegcashaccount",
    "notifications.view_notification",
    "notifications.change_notification",
    "system_settings.view_systemsetting",
    "rbac.view_student_dashboard",
    "rbac.upload_media",
    # Their own account row. `UserViewSet.get_queryset` narrows a student to
    # `id=user.id`, and `UserUpdateSerializer` exposes only name, username,
    # password and avatar - no groups, no institute, no is_staff - so
    # `change_user` here buys a profile page, not an escalation path.
    "users.view_user",
    "users.change_user",
)

#: Attendance, from a student's side: their own record of the institute's
#: events. Recording attendance is the event staff's job, so this is read-only.
STUDENT_ATTENDANCE = (
    "institutes_attendance_events.view_instituteattendanceevent",
    "attendance_records.view_attendancerecord",
)

#: Lockers, from a student's side: browse what exists and ask for one.
#: Approving the request belongs to the locker desk, not the asker.
STUDENT_LOCKERS = (
    "locker_system.view_locker",
    "locker_system.view_lockersubscription",
    "locker_system.view_lockersubscriptionrequest",
    "locker_system.add_lockersubscriptionrequest",
)

#: Contributions, from a student's side: file reports, spend the points.
STUDENT_CONTRIBUTIONS = (
    "contributions.view_contribution",
    "contributions.add_contribution",
    "contributions.change_contribution",
    "contributions.delete_contribution",
    "contributions.view_pointaccount",
    "contributions.view_pointtransaction",
    "contributions.view_pointpolicy",
    "contributions.view_feediscount",
    "contributions.add_feediscount",
    "contributions.cancel_feediscount",
    "contributions.view_contribution_leaderboard",
)

#: Coupons, from a student's side. Reading their own and spending them; the
#: verbs that matter (claim, apply) are ALLOW_ANY on the viewset, because a
#: student redeeming their own coupon is not an administrative act.
STUDENT_COUPONS = (
    "coupons.view_coupon",
    "coupons.view_couponredemption",
    "coupons.redeem_coupon",
)

#: Complaints, from a student's side. Deliberately separate from
#: STUDENT_CONTRIBUTIONS: a complaint is a claim on the institute, not an
#: offer to it, and a student keeps it where the points scheme is switched
#: off entirely.
STUDENT_COMPLAINTS = (
    "complaints.view_complaint",
    "complaints.add_complaint",
    "complaints.change_complaint",
    "complaints.withdraw_complaint",
)


#: Clearance, from a student's side.
STUDENT_CLEARANCE = (
    "clearance.view_clearancetype",
    "clearance.view_clearancerequirement",
    "clearance.view_studentclearance",
    "clearance.view_studentclearanceitem",
)

#: Day-to-day collection desk work: record money, review submissions.
COLLECTION_DESK = (
    "fees.view_fee",
    "fees.add_fee",
    "fees.change_fee",
    "fees.mark_fee_overdue",
    "fees.view_fee_discount_history",
    "fees.view_generatedfeebatch",
    "payments.view_payment",
    "payments.add_payment",
    "payments.bulk_add_payment",
    "payment_submissions.view_paymentsubmission",
    "payment_submissions.change_paymentsubmission",
    "payment_submissions.review_paymentsubmission",
    "payment_submissions.view_paymentsubmissionitems",
    "collection_categories.view_collectioncategory",
    "collection_categories.view_feeexemptedstudent",
    "collection_categories.view_institutegcashaccount",
    "students.view_student",
    "students.view_student_fee_summary",
)

#: Everything the collection desk does, plus the money-reversing actions and
#: the configuration behind them.
COLLECTION_MANAGEMENT = (
    "fees.delete_fee",
    "fees.waive_fee",
    "fees.unwaive_fee",
    "fees.add_generatedfeebatch",
    "fees.change_generatedfeebatch",
    "fees.delete_generatedfeebatch",
    "fees.generate_fees",
    "fees.generate_attendance_fees",
    "payments.change_payment",
    "payments.cancel_payment",
    "payment_submissions.cancel_paymentsubmission",
    # A *pending* submission is deletable; the viewset refuses to delete an
    # approved or cancelled one whatever permission you hold, because the
    # payments behind it are the trail.
    "payment_submissions.delete_paymentsubmission",
    "collection_categories.add_collectioncategory",
    "collection_categories.change_collectioncategory",
    "collection_categories.delete_collectioncategory",
    "collection_categories.bulk_add_collectioncategory",
    "collection_categories.add_feeexemptedstudent",
    "collection_categories.change_feeexemptedstudent",
    "collection_categories.delete_feeexemptedstudent",
    "collection_categories.bulk_add_feeexemptedstudent",
    "collection_categories.bulk_delete_feeexemptedstudent",
    "collection_categories.add_institutegcashaccount",
    "collection_categories.change_institutegcashaccount",
    "collection_categories.delete_institutegcashaccount",
    "students.add_student",
    "students.change_student",
    "students.import_students",
    "notifications.add_notificationbroadcast",
    "notifications.view_notificationbroadcast",
    "notifications.delete_notificationbroadcast",
    "notifications.send_notificationbroadcast",
)

#: Attendance capture and the fee rules driven from it.
ATTENDANCE_MANAGEMENT = (
    "attendance_events.view_attendanceevent",
    "attendance_events.add_attendanceevent",
    "attendance_events.change_attendanceevent",
    "attendance_records.view_attendancerecord",
    "attendance_records.add_attendancerecord",
    "attendance_records.change_attendancerecord",
    "attendance_records.delete_attendancerecord",
    "attendance_records.import_attendancerecord",
    "attendance_records.bulk_delete_attendancerecord",
    "institutes_attendance_events.view_instituteattendanceevent",
    "institutes_attendance_events.add_instituteattendanceevent",
    "institutes_attendance_events.change_instituteattendanceevent",
    "institutes_attendance_event_settings.view_instituteattendanceeventsetting",
    "institutes_attendance_event_settings.add_instituteattendanceeventsetting",
    "institutes_attendance_event_settings.change_instituteattendanceeventsetting",
)

#: Answering complaints. Held by every desk that fields them, because a
#: complaint about lockers is not the collection office's to settle.
COMPLAINT_MANAGEMENT = (
    "complaints.view_complaint",
    "complaints.change_complaint",
    "complaints.resolve_complaint",
    "complaints.view_all_complaints",
)


#: Reviewing contributions and paying them out in points.
#: Running the coupon scheme. Issuing is *not* in the desk-staff bundle:
#: creating instruments that reduce money owed is a different authority from
#: recording a payment, the same argument GeneratedFeeBatch makes for fee
#: generation.
COUPON_MANAGEMENT = (
    "coupons.view_couponbatch",
    "coupons.add_couponbatch",
    "coupons.change_couponbatch",
    "coupons.issue_couponbatch",
    "coupons.revoke_couponbatch",
    "coupons.export_couponbatch",
    "coupons.view_coupon",
    "coupons.revoke_coupon",
    "coupons.view_couponredemption",
    "coupons.reverse_couponredemption",
)

CONTRIBUTION_MANAGEMENT = (
    "contributions.view_contribution",
    "contributions.change_contribution",
    "contributions.review_contribution",
    "contributions.view_all_contributions",
    "contributions.view_pointaccount",
    "contributions.view_pointtransaction",
    "contributions.add_pointtransaction",
    "contributions.view_feediscount",
    "contributions.change_feediscount",
    "contributions.review_feediscount",
    "contributions.cancel_feediscount",
    "contributions.view_pointpolicy",
    "contributions.view_contribution_leaderboard",
    "rbac.view_contribution_report",
)

REPORTING = (
    "rbac.view_admin_dashboard",
    "rbac.view_collection_summary",
    "rbac.view_collection_report",
    "rbac.view_detailed_report",
    "rbac.export_report",
    "rbac.view_contribution_report",
)

AUDIT_READ = (
    "audits.view_auditlog",
    "audits.view_all_audit_logs",
    "audits.export_audit_logs",
    "rbac.view_audit_report",
)

#: The records an institute's staff maintain day to day: the student roster
#: and the reference data behind it. No money, no clearances, no lockers -
#: those belong to the system roles, which an account may hold as well.
INSTITUTE_RECORDS = (
    "students.view_student",
    "students.add_student",
    "students.change_student",
    "students.import_students",
    "notifications.view_notification",
    "notifications.change_notification",
    "system_settings.view_systemsetting",
    "rbac.view_admin_dashboard",
    "rbac.upload_media",
)

#: Everything institute staff do, plus what running the institute adds: its
#: programs, its GCash accounts, its own settings, its people (invite, roles
#: catalogue, password resets), and the audit trail. Deliberately not the
#: maintenance shutter - closing a system is the school's call.
INSTITUTE_ADMINISTRATION = (
    "students.delete_student",
    "programs.add_program",
    "programs.change_program",
    "programs.delete_program",
    # Its own logo and acronym. Row scoping (`InstituteViewSet.get_queryset`)
    # keeps it to the caller's school.
    "institutes.change_institute",
    "collection_categories.view_institutegcashaccount",
    "collection_categories.add_institutegcashaccount",
    "collection_categories.change_institutegcashaccount",
    "collection_categories.delete_institutegcashaccount",
    "system_settings.change_systemsetting",
    "system_settings.bulk_change_systemsetting",
    "system_settings.change_term_settings",
    "system_settings.change_feature_settings",
    "system_settings.change_student_maintenance",
    # Closing and reopening the online-payment lane - the desk's own call,
    # like holding students out while fee data is corrected.
    "system_settings.change_payment_settings",
    # Changing which roles an account in their institute holds. Bounded by
    # `DELEGATION` below: an institute administrator hands out the staff
    # and system roles of their own institute, never a peer's role or a
    # school administrator's - `AccessViewSet.assign` refuses the rest.
    "users.assign_user_role",
    "rbac.manage_roles",
) + AUDIT_READ

CLEARANCE_SIGNING = (
    "clearance.view_clearancetype",
    "clearance.view_clearancerequirement",
    "clearance.view_studentclearance",
    "clearance.view_studentclearanceitem",
    "clearance.change_studentclearanceitem",
    "clearance.sign_studentclearanceitem",
)

CLEARANCE_MANAGEMENT = CLEARANCE_SIGNING + (
    "clearance.add_clearancetype",
    "clearance.change_clearancetype",
    "clearance.delete_clearancetype",
    "clearance.issue_clearancetype",
    "clearance.add_clearancerequirement",
    "clearance.change_clearancerequirement",
    "clearance.delete_clearancerequirement",
    "clearance.add_studentclearance",
    "clearance.change_studentclearance",
    "clearance.delete_studentclearance",
    "clearance.add_studentclearanceitem",
    "clearance.delete_studentclearanceitem",
    # The clearance role picker lists auth Groups, so the grant is Django's
    # own group permission rather than a clearance-app one.
    "auth.view_group",
)

LOCKER_DESK = (
    "locker_system.view_locker",
    "locker_system.view_lockersubscription",
    "locker_system.view_lockersubscriptionrequest",
    "locker_system.approve_lockersubscriptionrequest",
    "locker_system.view_lockerreport",
    "locker_system.view_locker_summary",
)

LOCKER_MANAGEMENT = LOCKER_DESK + (
    "locker_system.add_locker",
    "locker_system.change_locker",
    "locker_system.delete_locker",
    "locker_system.bulk_add_locker",
    "locker_system.bulk_delete_locker",
    "locker_system.add_lockersubscription",
    "locker_system.change_lockersubscription",
    "locker_system.cancel_lockersubscription",
    "locker_system.add_lockersubscriptionrequest",
    "locker_system.change_lockersubscriptionrequest",
    "locker_system.delete_lockersubscriptionrequest",
    "locker_system.add_lockerreport",
    "locker_system.change_lockerreport",
)

USER_ADMINISTRATION = (
    "users.view_user",
    "users.add_user",
    "users.change_user",
    "users.invite_user",
    "users.bulk_invite_user",
    # Reset a forgotten password for an account in the administrator's own
    # institute (`UserViewSet.get_queryset` does the scoping; rank checks
    # in the action stop it pointing upward). Students never hold this.
    "users.set_user_password",
    # Move an account to a new email address by mailing that address a
    # confirmation link - the lost-mailbox case, where the self-service
    # flow's code to the old address can never arrive. Same scope and rank
    # rules as the password reset, in `UserViewSet.change_email`.
    "users.change_user_email",
    "rbac.view_role_catalog",
)

PLATFORM_ADMINISTRATION = USER_ADMINISTRATION + (
    "users.delete_user",
    "users.assign_user_role",
    "rbac.manage_roles",
    "schools.add_school",
    "schools.change_school",
    "schools.add_schoolbuilding",
    "schools.change_schoolbuilding",
    "schools.delete_schoolbuilding",
    "institutes.add_institute",
    "institutes.change_institute",
    "programs.add_program",
    "programs.change_program",
    "programs.delete_program",
    "system_settings.view_systemsetting",
    "system_settings.add_systemsetting",
    "system_settings.change_systemsetting",
    "system_settings.bulk_change_systemsetting",
    # Every settings bucket, maintenance included. Closing a system to the
    # staff who run it is a school-level act: the person running the institute
    # should not be able to shut it, and the person who can shut it is the one
    # they escalate to.
    "system_settings.change_term_settings",
    "system_settings.change_feature_settings",
    "system_settings.change_maintenance",
    "system_settings.change_student_maintenance",
    # And the exemption, so whoever closes a system can still reach the page
    # that reopens it. Separate from the authority to close it on purpose -
    # see `core.setting_buckets.BYPASS_MAINTENANCE`.
    "system_settings.bypass_maintenance",
    # Settings for every institute in their own school, rather than only the
    # one they happen to belong to. Bounded by `school`, so a school
    # administrator never reaches another school's institutes.
    "system_settings.manage_school_settings",
    "contributions.add_pointpolicy",
    "contributions.change_pointpolicy",
    "contributions.delete_pointpolicy",
)

SOCIAL_FEED = (
    "posts.view_post",
    "posts.view_postcomment",
    "posts.add_postcomment",
    "posts.change_postcomment",
    "posts.delete_postcomment",
)


# ---------------------------------------------------------------------------
# The role table
# ---------------------------------------------------------------------------

ROLE_DEFINITIONS: dict[str, RoleSpec] = {
    Roles.STUDENT: RoleSpec(
        description="A student. Sees only their own fees, payments and clearances.",
        permissions=(
            ACADEMIC_READ
            + STUDENT_SELF_SERVICE
            + STUDENT_ATTENDANCE
            + STUDENT_CLEARANCE
            + STUDENT_LOCKERS
            + STUDENT_CONTRIBUTIONS
            + STUDENT_COUPONS
            + STUDENT_COMPLAINTS
            + SOCIAL_FEED
        ),
    ),
    Roles.INSTITUTE_STAFF: RoleSpec(
        description=(
            "Works at one institute: keeps the student roster and the "
            "reference data behind it. Tied to no system; appoints nobody."
        ),
        staff_flag=True,
        permissions=ACADEMIC_READ + INSTITUTE_RECORDS,
    ),
    Roles.INSTITUTE_ADMIN: RoleSpec(
        description=(
            "Runs one institute: its students, programs, GCash accounts, "
            "settings and people. Appoints its staff. Tied to no system."
        ),
        staff_flag=True,
        inherits=(Roles.INSTITUTE_STAFF,),
        permissions=INSTITUTE_ADMINISTRATION + USER_ADMINISTRATION,
    ),
    Roles.CMS_STAFF: RoleSpec(
        description=(
            "Collection desk staff: records payments, reviews GCash "
            "submissions, keeps fees up to date. Institute-scoped."
        ),
        staff_flag=True,
        permissions=(
            ACADEMIC_READ
            + COLLECTION_DESK
            + ATTENDANCE_MANAGEMENT
            + COMPLAINT_MANAGEMENT
            + SOCIAL_FEED
            + (
                "notifications.view_notification",
                "notifications.change_notification",
                "system_settings.view_systemsetting",
                "rbac.view_admin_dashboard",
                "rbac.view_collection_summary",
                "rbac.view_collection_report",
                "rbac.view_detailed_report",
                "rbac.export_report",
                "rbac.upload_media",
                "contributions.view_contribution",
                "contributions.view_pointaccount",
                "contributions.view_contribution_leaderboard",
                "coupons.view_couponbatch",
                "coupons.view_coupon",
                "coupons.view_couponredemption",
            )
        ),
    ),
    Roles.CMS_ADMIN: RoleSpec(
        description=(
            "Owns the Collection Management System for an institute: fee "
            "generation, waivers, cancellations, exemptions, reports and "
            "contribution point payouts."
        ),
        staff_flag=True,
        inherits=(Roles.CMS_STAFF,),
        permissions=(
            COLLECTION_MANAGEMENT
            + CONTRIBUTION_MANAGEMENT
            + COUPON_MANAGEMENT
            + REPORTING
            + AUDIT_READ
            + USER_ADMINISTRATION
            + (
                "system_settings.change_systemsetting",
                "system_settings.bulk_change_systemsetting",
                # The term and the optional modules - the settings that
                # describe how this institute runs. This is the institute
                # administrator for the collection system; the desk staff
                # below hold `view_systemsetting` and nothing more, so an
                # officer reads these settings and never writes one.
                #
                # Deliberately *not* `change_maintenance`: closing the system
                # to its own staff is the school administrator's call, not the
                # institute's own.
                "system_settings.change_term_settings",
                "system_settings.change_feature_settings",
                # The narrow shutter: hold students out while fee data is
                # corrected, with staff still working. Not the wide one -
                # closing the staff side stays with the school.
                "system_settings.change_student_maintenance",
                # And the narrower one: the GCash lane alone.
                "system_settings.change_payment_settings",
                "posts.add_post",
                "posts.change_post",
                "posts.delete_post",
                "rbac.upload_media",
            )
        ),
    ),
    Roles.CLEARANCE_ADMIN: RoleSpec(
        description="Owns clearance templates, requirements and issuance.",
        staff_flag=True,
        permissions=(
            ACADEMIC_READ
            + CLEARANCE_MANAGEMENT
            + COMPLAINT_MANAGEMENT
            + (
                "students.view_student",
                "notifications.view_notification",
                "rbac.view_admin_dashboard",
            )
        ),
    ),
    Roles.LOCKER_STAFF: RoleSpec(
        description="Handles locker requests and hand-overs. Institute-scoped.",
        staff_flag=True,
        permissions=(
            ACADEMIC_READ
            + LOCKER_DESK
            + COMPLAINT_MANAGEMENT
            + ("students.view_student",)
        ),
    ),
    Roles.LOCKER_ADMIN: RoleSpec(
        description="Owns lockers, subscriptions and locker reports.",
        staff_flag=True,
        inherits=(Roles.LOCKER_STAFF,),
        permissions=(
            LOCKER_MANAGEMENT
            + (
                "rbac.view_admin_dashboard",
                # Locker admins review the reports filed against *their*
                # system; the row scoping is what keeps them out of the
                # collection queue.
                "contributions.view_contribution",
                "contributions.review_contribution",
                "contributions.view_pointaccount",
            )
        ),
    ),
    Roles.SCHOOL_ADMIN: RoleSpec(
        description=(
            "School-wide administrator: everything the system-level admins "
            "can do, plus users, institutes, programs and platform settings."
        ),
        staff_flag=True,
        # Everything an institute administrator can do, in any institute of
        # the school, plus the three system-admin roles.
        inherits=(
            Roles.INSTITUTE_ADMIN,
            Roles.CMS_ADMIN,
            Roles.CLEARANCE_ADMIN,
            Roles.LOCKER_ADMIN,
        ),
        permissions=PLATFORM_ADMINISTRATION,
    ),
}

# Clearance signatories all hold the same permission set; only the
# ``ClearanceRequirement.authorizing_role`` they are named on differs, and
# `CanManageStudentClearanceItem` checks that per object.
for _signatory in CLEARANCE_SIGNATORY_ROLES:
    ROLE_DEFINITIONS[_signatory] = RoleSpec(
        description=(
            f"Clearance signatory ({_signatory}). Signs off the requirements "
            "their group is named on."
        ),
        staff_flag=True,
        permissions=ACADEMIC_READ + CLEARANCE_SIGNING + ("students.view_student",),
    )


# ---------------------------------------------------------------------------
# Delegation: who may hand out which role
# ---------------------------------------------------------------------------

#: Which roles an account may invite somebody into, or add to / remove from
#: an existing account, keyed by the role the ACTOR holds. The union over
#: every role held is what applies.
#:
#: This is the delegation chain the console has always described - super
#: admin appoints school admins, school admins appoint institute admins,
#: institute admins appoint their institute's people - and until this table
#: existed it was enforced only in the console: `UserInviteSerializer` took
#: any group id, so anyone holding `users.invite_user` (an institute-level
#: grant) could mint a School Admin by calling the endpoint directly. The
#: serializer and `AccessViewSet.assign` now refuse anything not listed here.
#:
#: The console mirrors this table in `src/core/rbac/roles.ts`; this one is
#: the authority.
DELEGATION: dict[str, tuple[str, ...]] = {
    Roles.SCHOOL_ADMIN: (Roles.INSTITUTE_ADMIN,),
    # Everyone who works at the institute, whichever system they work in.
    # Placement is the institute administrator's own institute - the
    # invitation serializer and the assign endpoint both check it.
    Roles.INSTITUTE_ADMIN: (
        Roles.INSTITUTE_STAFF,
        Roles.CMS_ADMIN,
        Roles.CMS_STAFF,
        Roles.CLEARANCE_ADMIN,
        Roles.LOCKER_ADMIN,
        Roles.LOCKER_STAFF,
        Roles.STUDENT,
    ),
    # The collection system's own administrator, from before the generic
    # institute roles existed: still appoints their desk staff and their
    # students, so a deployment that has not moved its accounts over keeps
    # working.
    Roles.CMS_ADMIN: (Roles.CMS_STAFF, Roles.STUDENT),
}

#: A superuser appoints school administrators and nothing below them - the
#: point of the chain is that authority is delegated, not reached past.
#: (`AccessViewSet.assign` still lets a superuser set any role directly, as
#: the Django admin does; the chain governs invitations.)
SUPERUSER_DELEGATION: tuple[str, ...] = (Roles.SCHOOL_ADMIN,)


def delegable_roles(user) -> set[str]:
    """Role names `user` may hand out, per `DELEGATION`."""
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if user.is_superuser:
        return set(SUPERUSER_DELEGATION)

    held = set(user.groups.values_list("name", flat=True))
    return {target for role in held for target in DELEGATION.get(role, ())}


def role_names() -> tuple[str, ...]:
    return tuple(ROLE_DEFINITIONS)


def resolve_permissions(role: str, _seen: frozenset[str] | None = None) -> set[str]:
    """Every permission string a role holds, inherited roles folded in."""
    seen = _seen or frozenset()
    if role in seen:
        # A cycle in `inherits` would otherwise recurse forever. Roles are
        # hand-written, so this is a guard rail rather than a real case.
        return set()

    spec = ROLE_DEFINITIONS.get(role)
    if spec is None:
        return set()

    permissions = set(spec.permissions) | set(BASELINE)
    for parent in spec.inherits:
        permissions |= resolve_permissions(parent, seen | {role})

    # A role never holds a permission belonging to a feature this deployment
    # does not run, so `seed_rbac` cannot create the grant in the first place.
    return set(enabled_permissions(permissions))


def staff_roles() -> tuple[str, ...]:
    return tuple(name for name, spec in ROLE_DEFINITIONS.items() if spec.staff_flag)
