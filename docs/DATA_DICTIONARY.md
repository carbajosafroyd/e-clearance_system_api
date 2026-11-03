# E-Clearance System API - Complete Data Dictionary

This document details the database schema for the 19 concrete tables in the E-Clearance System API, formatted with the standardized column structure:
`keys | fields | datatypes | others`

---

## 1. schools (Schools & Campuses)
Represents educational institutions and university campuses.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **UK** | `school_name` | Varchar(100) | Full legal name, Unique, Not Null |
| | `short_name` | Varchar(10) | Nullable, Campus acronym (e.g., "DNSC") |
| | `location` | Text | Not Null, Campus physical location |
| | `logo` | Text | Nullable, Cloudinary asset public ID |
| | `logo_cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `background_image` | Text | Nullable, Portal banner image |
| | `background_cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `email_host_user` | Varchar(254) | Nullable, Campus default SMTP username |
| | `email_host_password_encrypted` | Text | Nullable, Fernet-encrypted app password |
| | `default_from_email` | Varchar(254) | Nullable, Displayed sender email |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 2. school_buildings (Campus Buildings)
Buildings located inside a specific school campus.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `school_id` | BigInt | References `schools(id)`, `on_delete=CASCADE` |
| | `name` | Varchar(50) | Not Null, Building name |
| | `max_floor` | Integer (Unsigned) | Not Null, Number of floors |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Unique Together: (`school_id`, `name`)*

---

## 3. institutes (Colleges / Institutes)
Academic divisions / colleges under a school (e.g., Institute of Computing).

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `school_id` | BigInt | References `schools(id)`, `on_delete=CASCADE` |
| **UK** | `institute_name` | Varchar(100) | Unique, Indexed, Full college name |
| | `acronym` | Varchar(12) | Indexed, Code acronym (e.g., "IC", "ITE") |
| | `logo` | Text | Nullable, Cloudinary seal public ID |
| | `logo_cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `background_image` | Text | Nullable, Custom college banner |
| | `background_cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `email_host_user` | Varchar(254) | Nullable, Custom SMTP sender |
| | `email_host_password_encrypted` | Text | Nullable, Encrypted app password |
| | `default_from_email` | Varchar(254) | Nullable, Outgoing sender display |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 4. programs (Degree Programs)
Curricular degree programs offered by institutes (e.g., BSIT, BSCS).

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `institute_id` | BigInt | References `institutes(id)`, `on_delete=CASCADE` |
| | `name` | Varchar(100) | Not Null, Full degree program title |
| | `acronym` | Varchar(10) | Not Null, Program code (e.g., "BSIT") |
| | `status` | Varchar(20) | Choices: `active`, `inactive` (Default: "active") |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 5. users (Application Accounts)
Core user identity for students, signatories, and system administrators.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **UK** | `username` | Varchar(150) | Unique, Not Null, Institutional username / ID |
| | `email` | Varchar(254) | Not Null, Primary institutional contact email |
| | `password` | Varchar(128) | Not Null, HMAC pepper + PBKDF2 hash |
| | `first_name` | Varchar(150) | Not Null, First name |
| | `last_name` | Varchar(150) | Not Null, Surname |
| **FK** | `school_id` | BigInt | Nullable, References `schools(id)`, `on_delete=SET_NULL` |
| **FK** | `institute_id` | BigInt | Nullable, References `institutes(id)`, `on_delete=SET_NULL` |
| | `profile` | Varchar(100) | Nullable, Cloudinary avatar public ID |
| | `cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `pepper_version` | SmallInt | Not Null, Default: 0, HMAC rotation index |
| | `email_verified_at` | DateTime | Nullable, Email verification timestamp |
| | `is_staff` | Boolean | Not Null, Default: False, Admin access |
| | `is_active` | Boolean | Not Null, Default: True, Account status |
| | `is_superuser` | Boolean | Not Null, Default: False, Superuser permissions |
| | `last_login` | DateTime | Nullable, Timestamp of most recent login |
| | `date_joined` | DateTime | Not Null, Registration timestamp |

---

## 6. oauth_accounts (Third-Party OAuth)
External OAuth credentials (e.g., Google Sign-In) linked to user accounts.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `user_id` | BigInt | References `users(id)`, `on_delete=CASCADE` |
| | `provider` | Varchar(20) | Not Null, Provider name (e.g., "google") |
| | `provider_user_id` | Varchar(255) | Not Null, External subject ID |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |

*Unique Together: (`provider`, `provider_user_id`)*

---

## 7. user_registration_tokens (Invitations & Activation)
One-time registration tokens dispatched via email for user account activation.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **UK** | `token` | Varchar(64) | Unique, Not Null, 64-character secure token |
| | `email` | Varchar(254) | Not Null, Invitee email address |
| **FK** | `group_id` | Integer | References `auth_group(id)`, `on_delete=PROTECT` |
| **FK** | `institute_id` | BigInt | Nullable, References `institutes(id)`, `on_delete=CASCADE` |
| **FK** | `school_id` | BigInt | Nullable, References `schools(id)`, `on_delete=CASCADE` |
| **FK** | `student_id` | BigInt | Nullable, References `students(id)`, `on_delete=CASCADE` |
| | `username` | Varchar(150) | Pre-assigned username |
| | `initial_password` | Varchar(128) | Hashed temporary one-time password |
| | `initial_password_pepper_version` | SmallInt | Default: 0 |
| | `expires_at` | DateTime | Not Null, Token expiration threshold |
| | `used_at` | DateTime | Nullable, Timestamp of redemption |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |

---

## 8. students (Student Enrollment Records)
Official enrolled academic student records.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **UK** | `s_studentID` | Varchar(10) | Unique, Not Null, Institutional ID (e.g., "2026-0001") |
| **FK** | `program_id` | BigInt | References `programs(id)`, `on_delete=CASCADE` |
| **FK / UK** | `user_id` | BigInt | One-to-One, Nullable, References `users(id)`, `on_delete=SET_NULL` |
| **UK** | `s_rfid` | Varchar(10) | Unique, Nullable, Physical RFID card tag |
| | `s_fname` | Varchar(100) | Not Null, Student first name |
| | `s_mname` | Varchar(100) | Nullable, Student middle name |
| | `s_lname` | Varchar(100) | Not Null, Student last name |
| | `s_suffix` | Varchar(20) | Nullable, Suffix (Jr., III) |
| | `s_lvl` | Integer (Unsigned) | Not Null, Year level (1, 2, 3, 4) |
| | `s_set` | Varchar(50) | Nullable, Section / set code (e.g., "A") |
| | `s_status` | Varchar(15) | Choices: `enrolled`, `graduated`, `dropped` (Default: "enrolled") |
| | `s_image` | Varchar(255) | Nullable, Student ID photo path |
| | `cloudinary_version` | SmallInt | Nullable, Storage account version |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 9. clearance_types (Clearance Templates)
Clearance templates issued for a specific academic semester or graduation term.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `institute_id` | BigInt | Nullable, References `institutes(id)`, `on_delete=PROTECT` |
| | `name` | Varchar(100) | Not Null, Clearance title (e.g., "1st Sem Clearance") |
| | `clearance_category` | Varchar(30) | Choices: `semestral`, `graduation`, `transfer` (Default: "semestral") |
| | `academic_year` | Varchar(20) | Not Null, School year (e.g., "2026-2027") |
| | `semester` | Varchar(20) | Not Null, Semester (e.g., "1st Semester") |
| | `target_year_levels` | JSON | Default: [], List of eligible year levels [1, 2, 3, 4] |
| | `is_active` | Boolean | Not Null, Default: True, Template active status |
| | `is_issued` | Boolean | Not Null, Default: False, Batch issuance flag |
| | `issued_at` | DateTime | Nullable, Batch issuance timestamp |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Unique Together: (`institute_id`, `name`, `academic_year`, `semester`)*

---

## 10. clearance_requirements (Signatory Checklist Template)
Specific signatories and tasks required to clear a clearance template.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `clearance_type_id` | BigInt | References `clearance_types(id)`, `on_delete=CASCADE` |
| **FK** | `authorizing_role_id` | Integer | References `auth_group(id)`, `on_delete=PROTECT` (Signatory role) |
| | `name` | Varchar(100) | Not Null, Requirement name (e.g., "Library Clearance") |
| | `order_sequence` | Integer (Unsigned) | Not Null, Sequential approval step (1, 2, 3...) |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Constraints: Unique(`clearance_type_id`, `name`), Unique(`clearance_type_id`, `order_sequence`)*

---

## 11. student_clearances (Student Clearance Application)
Individual clearance record for an enrolled student.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `student_id` | BigInt | References `students(id)`, `on_delete=CASCADE` |
| **FK** | `clearance_type_id` | BigInt | References `clearance_types(id)`, `on_delete=CASCADE` |
| | `status` | Varchar(20) | Choices: `draft`, `pending`, `under_review`, `cleared`, `completed` |
| **UK** | `verification_token` | UUID | Unique, Cryptographic UUID embedded in QR code |
| | `is_enrolled` | Boolean | Default: False, Eligible for next term enrollment |
| | `enrolled_at` | DateTime | Nullable, Enrollment confirmation timestamp |
| **FK** | `enrolled_by_user_id`| BigInt | Nullable, References `users(id)`, `on_delete=SET_NULL` |
| | `date_initiated` | DateTime | Not Null, Auto Add Now, Application timestamp |
| | `date_completed` | DateTime | Nullable, Final clearance timestamp |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Unique Together: (`student_id`, `clearance_type_id`)*

---

## 12. student_clearance_items (Signatory Line Items)
Sign-off status for each specific requirement on a student's clearance.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `student_clearance_id`| BigInt | References `student_clearances(id)`, `on_delete=CASCADE` |
| **FK** | `requirement_id` | BigInt | References `clearance_requirements(id)`, `on_delete=CASCADE` |
| **FK** | `cleared_by_user_id` | BigInt | Nullable, References `users(id)`, `on_delete=SET_NULL` |
| | `status` | Varchar(20) | Choices: `pending`, `deficiency`, `under_review`, `cleared` |
| | `amount_due` | Decimal(10,2) | Nullable, Unpaid dues / monetary balance |
| | `remarks` | Text | Nullable, Signatory deficiency remarks |
| | `proof_attachment` | Varchar(255) | Nullable, Cloudinary receipt public ID |
| | `cloudinary_version` | SmallInt | Nullable, Cloudinary account index |
| | `proof_notes` | Text | Nullable, Student settlement notes |
| | `proof_uploaded_at` | DateTime | Nullable, Proof submission timestamp |
| | `cleared_at` | DateTime | Nullable, Approval timestamp |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Unique Together: (`student_clearance_id`, `requirement_id`)*

---

## 13. system_settings (Per-Institute Configuration)
Runtime system settings scoped by institute.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `institute_id` | BigInt | References `institutes(id)`, `on_delete=CASCADE` |
| | `setting_key` | Varchar(100) | Not Null, Configuration key |
| | `setting_value` | Text | Not Null, Configuration value |
| | `system` | Varchar(20) | Default: "platform", Choices: `platform`, `clearance` |
| | `description` | Text | Nullable, Description of setting purpose |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

*Unique Together: (`institute_id`, `setting_key`)*

---

## 14. notification_broadcast (Department Broadcasts)
Broadcast announcements distributed to whole institutes or programs.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `sender_id` | BigInt | Nullable, References `users(id)`, `on_delete=SET_NULL` |
| | `title` | Varchar(255) | Not Null, Broadcast subject title |
| | `message` | Text | Not Null, Announcement body text |
| **FK** | `target_institute_id`| BigInt | Nullable, References `institutes(id)`, `on_delete=SET_NULL` |
| **FK** | `target_program_id` | BigInt | Nullable, References `programs(id)`, `on_delete=SET_NULL` |
| | `target_year_level` | Integer | Nullable, Target academic year level |
| | `target_all_users` | Boolean | Nullable, Global broadcast flag |
| | `total_recipients` | Integer | Default: 0, Count of recipient users |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 15. notifications (User Notifications)
Direct user notifications regarding clearance updates, deficiencies, and alerts.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `user_id` | BigInt | References `users(id)`, `on_delete=CASCADE` (Recipient) |
| **FK** | `sender_id` | BigInt | Nullable, References `users(id)`, `on_delete=SET_NULL` |
| **FK** | `broadcast_id` | BigInt | Nullable, References `notification_broadcast(id)`, `on_delete=CASCADE` |
| | `title` | Varchar(255) | Not Null, Notification headline |
| | `message` | Text | Not Null, Notification text |
| | `type` | Varchar(50) | Not Null, Classification category |
| | `source` | Varchar(30) | Not Null, Originating subsystem |
| | `data` | JSON | Nullable, Contextual JSON payload |
| | `is_read` | Boolean | Not Null, Default: False, Read indicator |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 16. audits (Audit Trail Logs)
Immutable log capturing system-wide modifications, sign-offs, and administrative actions.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| | `system` | Varchar(20) | Choices: `clearance`, `auth`, `platform`, `schools` |
| | `entity_type` | Varchar(100) | Not Null, Model class name |
| | `entity_id` | Varchar(50) | Not Null, Target record primary key |
| | `action` | Varchar(20) | Choices: `CREATE`, `UPDATE`, `DELETE`, `LOGIN`, `SIGN` |
| | `actor_type` | Varchar(20) | Choices: `USER`, `SYSTEM`, `ANONYMOUS` |
| | `actor_id` | Integer | Nullable, Actor user ID |
| | `old_values` | JSON | Nullable, Pre-action field state |
| | `new_values` | JSON | Nullable, Post-action field state |
| | `created_at` | DateTime | Not Null, Immutable creation timestamp |

---

## 17. email_change_requests (Self-Service Email Change)
Multi-step, cryptographic workflow for verifying and updating account email addresses.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `user_id` | BigInt | References `users(id)`, `on_delete=CASCADE` |
| **FK** | `initiated_by_id` | BigInt | Nullable, References `users(id)`, `on_delete=SET_NULL` |
| | `stage` | Varchar(24) | Choices: `code_sent`, `code_verified`, `new_email_sent`, `confirmed` |
| | `current_email` | Varchar(254) | Not Null, Current address |
| | `new_email` | Varchar(254) | Target address |
| | `code_hash` | Varchar(128) | Hashed 6-digit OTP |
| | `code_expires_at` | DateTime | Nullable, OTP expiration threshold |
| | `attempts` | SmallInt | Default: 0, Failed attempt counter |
| | `max_attempts` | SmallInt | Default: 5, Lockout attempt ceiling |
| | `verified_at` | DateTime | Nullable, Verification timestamp |
| | `change_token_hash` | Varchar(128) | Indexed, Hashed authorization token |
| | `change_token_expires_at` | DateTime | Nullable |
| | `change_token_used_at` | DateTime | Nullable |
| | `confirm_token_hash`| Varchar(128) | Indexed, Hashed confirmation link token |
| | `confirm_expires_at` | DateTime | Nullable |
| | `confirm_sent_at` | DateTime | Nullable |
| | `confirm_resends` | SmallInt | Default: 0 |
| | `confirmed_at` | DateTime | Nullable, Application timestamp |
| | `cancelled_at` | DateTime | Nullable, Cancellation timestamp |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
| | `updated_at` | DateTime | Not Null, Auto-updating timestamp |

---

## 18. change_password_passwordresetrequest (Password Reset OTPs)
Tracks password reset OTP requests.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| | `email` | Varchar(254) | Indexed, Target email address |
| | `code_hash` | Varchar(128) | Not Null, Hashed 6-digit OTP code |
| | `attempts` | SmallInt | Default: 0, Failed attempts counter |
| | `max_attempts` | SmallInt | Default: 5, Lockout attempt ceiling |
| | `expires_at` | DateTime | Not Null, Expiration timestamp |
| | `validated_at` | DateTime | Nullable, Verification timestamp |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |

---

## 19. change_password_passwordchangetoken (Password Reset Tokens)
One-time authorization tokens generated after OTP verification.

| keys | fields | datatypes | others |
| :---: | :--- | :--- | :--- |
| **PK** | `id` | BigInt | Auto-increment |
| **FK** | `reset_request_id` | BigInt | References `change_password_passwordresetrequest(id)`, `on_delete=CASCADE` |
| | `email` | Varchar(254) | Indexed, Target user email address |
| **UK** | `token_hash` | Varchar(128) | Unique, Not Null, Hashed authorization token |
| | `expires_at` | DateTime | Not Null, Token expiration threshold |
| | `used_at` | DateTime | Nullable, Timestamp when password changed |
| | `created_at` | DateTime | Not Null, Auto-generated creation timestamp |
