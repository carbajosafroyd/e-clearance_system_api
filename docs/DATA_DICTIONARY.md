# E-Clearance System API - Complete Data Dictionary

This document details the database schema, primary/foreign keys, field types, constraints, and cascade behaviors for the E-Clearance System API.

---

## 1. schools (Schools & Campuses)
Represents educational institutions / university campuses.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `school_name` | Varchar(100) | No | **Unique** | Full legal name of the school |
| `short_name` | Varchar(10) | Yes | | Acronym / abbreviation (e.g., "DNSC") |
| `location` | Text | No | | Campus address / geographic location |
| `logo` | Text | Yes | | Cloudinary asset public ID for campus seal |
| `logo_cloudinary_version` | SmallInt | Yes | | Cloudinary storage account version |
| `background_image` | Text | Yes | | Login banner / portal cover image |
| `background_cloudinary_version` | SmallInt | Yes | | Cloudinary storage account version |
| `email_host_user` | Varchar(254) | Yes | | Campus-level default SMTP sender address |
| `email_host_password_encrypted` | Text | Yes | | AES/Fernet-encrypted app password |
| `default_from_email` | Varchar(254) | Yes | | Displayed outgoing email address |
| `created_at` | DateTime | No | | Timestamp of creation |
| `updated_at` | DateTime | No | | Timestamp of last modification |

---

## 2. school_buildings (Campus Buildings)
Buildings located inside a specific school campus.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `school_id` | BigInt | No | **FK (School)** | `on_delete=CASCADE` |
| `name` | Varchar(50) | No | | Name of the academic building / hall |
| `max_floor` | Integer (Unsigned) | No | | Number of floors |
| `created_at` | DateTime | No | | Timestamp of creation |
| `updated_at` | DateTime | No | | Timestamp of last modification |

*Unique Together: (`school_id`, `name`)*

---

## 3. institutes (Colleges / Institutes)
Academic divisions / colleges under a school (e.g., Institute of Computing).

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `school_id` | BigInt | No | **FK (School)** | `on_delete=CASCADE` |
| `institute_name` | Varchar(100) | No | **Unique** | Full name of the college / institute |
| `acronym` | Varchar(12) | No | **Indexed** | Short code (e.g., "IC", "ITE") |
| `logo` | Text | Yes | | Cloudinary public ID for college seal |
| `logo_cloudinary_version` | SmallInt | Yes | | Cloudinary account version |
| `background_image` | Text | Yes | | Optional custom banner |
| `background_cloudinary_version`| SmallInt | Yes | | Cloudinary account version |
| `email_host_user` | Varchar(254) | Yes | | Institute-level custom SMTP sender |
| `email_host_password_encrypted` | Text | Yes | | Encrypted email credentials |
| `default_from_email` | Varchar(254) | Yes | | Outgoing display email |
| `created_at` | DateTime | No | | Timestamp of creation |
| `updated_at` | DateTime | No | | Timestamp of last modification |

---

## 4. programs (Degree Programs)
Curricular degree programs offered by institutes (e.g., BSIT, BSCS).

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `institute_id` | BigInt | No | **FK (Institute)**| `on_delete=CASCADE` |
| `name` | Varchar(100) | No | | Full degree title |
| `acronym` | Varchar(10) | No | | Program code (e.g., "BSIT") |
| `status` | Varchar(20) | No | | Choice: `active`, `inactive` |
| `created_at` | DateTime | No | | Timestamp of creation |
| `updated_at` | DateTime | No | | Timestamp of last modification |

---

## 5. users (Application Accounts)
Core user identity for students, signatories, and system administrators.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `username` | Varchar(150) | No | **Unique** | Institutional username / ID |
| `email` | Varchar(254) | No | | Primary institutional contact email |
| `password` | Varchar(128) | No | | HMAC-peppering + PBKDF2 hash |
| `first_name` | Varchar(150) | No | | First name |
| `last_name` | Varchar(150) | No | | Surname |
| `school_id` | BigInt | Yes | **FK (School)** | `on_delete=SET_NULL` |
| `institute_id` | BigInt | Yes | **FK (Institute)**| `on_delete=SET_NULL` |
| `profile` | Varchar(100) | Yes | | Cloudinary avatar public ID |
| `cloudinary_version` | SmallInt | Yes | | Cloudinary version |
| `pepper_version` | SmallInt | No | Default: 0 | Current pepper rotation version |
| `email_verified_at` | DateTime | Yes | | Email verification timestamp |
| `is_staff` | Boolean | No | Default: False | Access to Django administration |
| `is_active` | Boolean | No | Default: True | Account status flag |
| `is_superuser` | Boolean | No | Default: False | Superuser flag |
| `last_login` | DateTime | Yes | | Timestamp of most recent login |
| `date_joined` | DateTime | No | | Registration timestamp |

---

## 6. students (Student Enrollment Records)
Official enrolled academic student records.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `user_id` | BigInt | Yes | **OneToOne (User)** | `on_delete=SET_NULL` (links to account) |
| `program_id` | BigInt | No | **FK (Program)** | `on_delete=CASCADE` |
| `s_studentID` | Varchar(10) | No | **Unique** | Student institutional ID (e.g., 2026-0001) |
| `s_rfid` | Varchar(10) | Yes | **Unique** | Physical RFID card tag (optional) |
| `s_fname` | Varchar(100) | No | | First name |
| `s_mname` | Varchar(100) | Yes | | Middle name |
| `s_lname` | Varchar(100) | No | | Last name |
| `s_suffix` | Varchar(20) | Yes | | Name suffix (Jr., III) |
| `s_lvl` | Integer (Unsigned) | No | | Year level (1, 2, 3, 4) |
| `s_set` | Varchar(50) | Yes | | Academic section / set (e.g., "A") |
| `s_status` | Varchar(15) | No | Default: enrolled | Choice: `enrolled`, `graduated`, `dropped` |
| `s_image` | Varchar(255) | Yes | | Student ID photo path |
| `cloudinary_version`| SmallInt | Yes | | Media storage account index |

---

## 7. clearance_types (Clearance Templates)
Clearance templates issued for an academic term or graduation.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `institute_id` | BigInt | Yes | **FK (Institute)**| `on_delete=PROTECT` |
| `name` | Varchar(100) | No | | Clearance title (e.g., "1st Sem Semestral Clearance") |
| `clearance_category`| Varchar(30)| No | Default: semestral | Choice: `semestral`, `graduation`, `transfer` |
| `academic_year` | Varchar(20) | No | | Academic year (e.g., "2026-2027") |
| `semester` | Varchar(20) | No | | Semester (e.g., "1st Semester") |
| `target_year_levels`| JSON | No | Default: [] | List of eligible year levels [1, 2, 3, 4] |
| `is_active` | Boolean | No | Default: True | Template active status |
| `is_issued` | Boolean | No | Default: False | Batch issuance flag to students |
| `issued_at` | DateTime | Yes | | Timestamp of batch issuance |

*Unique Together: (`institute_id`, `name`, `academic_year`, `semester`)*

---

## 8. clearance_requirements (Signatory Checklist Template)
Specific signatories and tasks required to clear a clearance template.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `clearance_type_id` | BigInt | No | **FK (ClearanceType)** | `on_delete=CASCADE` |
| `authorizing_role_id` | Integer | No | **FK (auth.Group)** | `on_delete=PROTECT` (Designated signatory role) |
| `name` | Varchar(100) | No | | Requirement name (e.g., "Library Clearance") |
| `order_sequence` | Integer (Unsigned) | No | | Step in sequential workflow (1, 2, 3...) |

*Constraints: Unique(`clearance_type_id`, `name`), Unique(`clearance_type_id`, `order_sequence`)*

---

## 9. student_clearances (Student Clearance Application)
Individual clearance record for an enrolled student.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `student_id` | BigInt | No | **FK (Student)** | `on_delete=CASCADE` |
| `clearance_type_id` | BigInt | No | **FK (ClearanceType)** | `on_delete=CASCADE` |
| `status` | Varchar(20) | No | Default: pending | `draft`, `pending`, `under_review`, `cleared`, `completed` |
| `verification_token`| UUID | Yes | **Unique** | Cryptographic token embedded in QR code |
| `date_initiated` | DateTime | No | Auto Add Now | Timestamp when student applied |
| `date_completed` | DateTime | Yes | | Timestamp when final signatory cleared |
| `is_enrolled` | Boolean | No | Default: False | Enrolled state flag for next term |
| `enrolled_at` | DateTime | Yes | | Enrollment validation timestamp |
| `enrolled_by_user_id` | BigInt | Yes | **FK (User)** | `on_delete=SET_NULL` |

*Unique Together: (`student_id`, `clearance_type_id`)*

---

## 10. student_clearance_items (Signatory Line Items)
Sign-off status for each specific requirement on a student's clearance.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `student_clearance_id`| BigInt | No | **FK (StudentClearance)** | `on_delete=CASCADE` |
| `requirement_id` | BigInt | No | **FK (ClearanceRequirement)** | `on_delete=CASCADE` |
| `cleared_by_user_id`| BigInt | Yes | **FK (User)** | `on_delete=SET_NULL` (Signatory who signed) |
| `status` | Varchar(20) | No | Default: pending | `pending`, `cleared`, `deficiency`, `under_review` |
| `amount_due` | Decimal(10,2) | Yes | | Unpaid dues or penalties |
| `remarks` | Text | Yes | | Signatory deficiency remarks (e.g. unreturned book) |
| `proof_attachment` | Varchar(255) | Yes | | Cloudinary public ID for proof of payment/settlement |
| `cloudinary_version`| SmallInt | Yes | | Cloudinary account version |
| `proof_notes` | Text | Yes | | Student settlement notes |
| `proof_uploaded_at`| DateTime | Yes | | Proof submission timestamp |
| `cleared_at` | DateTime | Yes | | Timestamp when approved by signatory |

*Unique Together: (`student_clearance_id`, `requirement_id`)*

---

## 11. notifications (In-App Alerts)
Direct user notifications regarding clearance progress, approvals, and remarks.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `user_id` | BigInt | No | **FK (User)** | `on_delete=CASCADE` (Recipient) |
| `sender_id` | BigInt | Yes | **FK (User)** | `on_delete=SET_NULL` |
| `title` | Varchar(255) | No | | Notification headline |
| `message` | Text | No | | Alert body text |
| `type` | Varchar(50) | No | | Category / classification |
| `source` | Varchar(30) | No | | System origin |
| `is_read` | Boolean | No | Default: False | Read status flag |
| `created_at` | DateTime | No | | Timestamp sent |

---

## 12. audits (Security Audit Trail)
Tamper-resistant audit log capturing system-wide changes, approvals, and logins.

| Column | Type | Nullable | Key / Constraint | Description |
| :--- | :--- | :---: | :---: | :--- |
| `id` | BigInt | No | **PK** | Auto-increment primary key |
| `system` | Varchar(20) | No | | Subsystem identifier |
| `entity_type` | Varchar(100) | No | | Model class name |
| `entity_id` | Varchar(50) | No | | Record primary key |
| `action` | Varchar(20) | No | | `CREATE`, `UPDATE`, `DELETE`, `SIGN` |
| `actor_type` | Varchar(20) | No | | `USER`, `SYSTEM`, `ANONYMOUS` |
| `actor_id` | Integer | Yes | | Actor user primary key |
| `old_values` | JSON | Yes | | Previous state snapshot |
| `new_values` | JSON | Yes | | Modified state snapshot |
| `created_at` | DateTime | No | | Immutable audit timestamp |
