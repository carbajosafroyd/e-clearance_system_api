# Phase 3: Comprehensive Gap Analysis & Reconciliation Matrix

**Document Code:** GAP-ANALYSIS-PHASE3  
**Technical Baseline:** `e-clearance_system_api` (Django 6.0 Backend Implementation)  
**Document Baseline:** `Project Management Plan.pdf` (ClearanceDNSC Draft v1.0) & Supporting Architecture Docs  
**Execution Date:** October 31, 2026  
**Status:** Completed  

---

## 1. Classification Methodology

To reconcile the documented specifications with the actual source code, requirements and architectural features are classified into four precise categories:

* **ALIGNED:** The feature/requirement is implemented in the backend code and matches the documented specification.
* **PARTIALLY IMPLEMENTED / DIVERGENT:** The feature exists in the codebase but differs in design, architectural workflow, or technical mechanism from what is written in the document.
* **DOCUMENTED BUT UNIMPLEMENTED / OUT-OF-SCOPE FOR BACKEND:** The requirement is described in the document but is either unimplemented in the backend or belongs to other project departments (e.g. Frontend UI/UX, Presentation, DevOps).
* **UNDOCUMENTED BACKEND FEATURE:** A fully working backend feature or architectural mechanism exists in the codebase but is completely omitted or unmentioned in the PMP document.

---

## 2. Requirements Reconciliation Matrix

### 2.1 Functional Requirements Comparison (PMP Section 2.1)

| Req ID | Documented Requirement | Implementation Status | Technical Reality in Backend Code | Reconciled Recommendation / Action |
| :--- | :--- | :--- | :--- | :--- |
| **FR-01** | User Log in and Log out for all roles. | **ALIGNED** | Implemented via SimpleJWT at `POST /api/v1/login/` (`CustomTokenObtainPairView`) and `POST /api/v1/logout/` (`LogoutView` token blacklisting). Google OAuth also available. | Maintain requirement; update technical details to specify JWT and refresh token blacklisting. |
| **FR-02** | Display features according to assigned role. | **ALIGNED** | Implemented via DRF permission classes and `/api/v1/me/` (`AuthViewSet`) returning held permissions and system access. | Maintain requirement; note that backend enforces permissions server-side while frontend renders role-specific UI. |
| **FR-03** | Students view profile and own clearance records. | **ALIGNED** | Implemented at `GET /api/v1/student-clearances/my-clearances/` and `GET /api/v1/students/`. Isolated via `IsStudent` & `object_student_id` scoping in `api/clearance/permissions.py`. | Maintain requirement; note endpoint URLs and strict student data isolation. |
| **FR-04** | Students shall be able to initiate a clearance request when eligible. | **DIVERGENT** | **Code Divergence:** In the actual backend, students **do not initiate** individual clearance requests. Instead, an administrator executes a **Batch Issuance** (`POST /api/v1/clearance-types/{id}/issue/`), which automatically instantiates `StudentClearance` and child `StudentClearanceItem` records for all currently enrolled students matching the institute and target year levels. | **Revise Requirement:** Update FR-04 in the PMP to document **Administrative Batch Issuance**, noting that clearance is initiated systematically for enrolled student cohorts rather than per-student self-request. |
| **FR-05** | System shows status of each required office/clearance item. | **ALIGNED** | Implemented via `StudentClearanceReadSerializer` and `StudentClearanceItemReadSerializer`, detailing each requirement status (`pending`, `cleared`, `has_issue`), sequence, remarks, and authorizing roles. | Maintain requirement; document item status choices (`pending`, `cleared`, `has_issue`). |
| **FR-06** | Officers view requests assigned to their office. | **ALIGNED / ENHANCED** | Implemented via `GET /api/v1/student-clearance-items/signatory-dashboard/`. Scoped by `authorizing_role` and `authorizing_roles` (M2M) matching the user's assigned group(s). Includes bottleneck queue optimization (`is_actionable`). | Update requirement to describe role/group assignment and actionable queue filtering. |
| **FR-07** | Officers approve or reject assigned items and add remarks. | **ALIGNED / ENHANCED** | Implemented via `PATCH /api/v1/student-clearance-items/{id}/`. Supports marking `cleared` or flagging `has_issue` with mandatory `remarks` and optional `amount_due`. Enforces sequential validation prior to approval. | Update requirement to specify `cleared` vs. `has_issue` statuses, `remarks`, and `amount_due`. |
| **FR-08** | System updates overall progress after authorized decision. | **ALIGNED** | Centralized in `api/clearance/services/status_service.py` via `evaluate_student_clearance_status`. Calculates cleared count vs. total items and updates overall status. | Maintain requirement; document state machine logic. |
| **FR-09** | Clearance not marked completed until all required approvals obtained. | **ALIGNED** | Enforced by `evaluate_student_clearance_status`: `StudentClearance.status` transitions to `completed` **strictly if and only if** $100\%$ of associated clearance items are `cleared`. | Maintain requirement; cite status evaluator service. |
| **FR-10** | Administrators manage user accounts, roles, offices, and clearance requirements. | **ALIGNED** | Full administrative ViewSets exist for `ClearanceTypeViewSet`, `ClearanceRequirementViewSet`, `UserViewSet`, `StudentViewSet`, and `AccessViewSet` (RBAC group assignment). | Maintain requirement; document specific admin endpoints. |
| **FR-11** | Maintain history of important clearance actions. | **ALIGNED** | Implemented via `api.audits.models.AuditLog` and `AuditLogService.log_action`. Logs clearance creation, status evaluations (`APPROVE`/`UPDATE`), and signatory decisions with old/new values. | Maintain requirement; cite audit log subsystem and export capabilities (PDF/Excel). |
| **FR-12** | System provides notifications when clearance status changes. | **ALIGNED** | In-app notifications implemented via `api.notifications` (`clearance.issue`, `clearance.proof_submitted`, `clearance.cleared`, `clearance.completed`). Automated SMTP email alerts implemented in `api.clearance.services.email_service.py`. | Upgrade requirement from "should provide" to fully supported in-app and email notification architecture. |
| **FR-13** | Generate reports for pending, approved, rejected, completed clearances. | **ALIGNED** | Implemented via `ClearanceTypeViewSet.admin_summary`, `ClearanceTypeViewSet.dashboard`, `program_chair_dashboard`, and audit log exports (`api/audits/report_views.py`). | Maintain requirement; document analytical dashboard endpoints. |
| **FR-14** | Print or download completed clearance. | **ALIGNED** | Implemented via `GET /api/v1/student-clearances/{id}/certificate/`, generating a ReportLab vector PDF with institutional branding, audit summary, and a Segno QR code (available only when `status == completed`). | Update requirement to state that Certificate PDF download is fully implemented. |

---

### 2.2 Non-Functional Requirements Comparison (PMP Section 2.2)

| NFR ID | Category | Implementation Status | Technical Verification in Codebase |
| :--- | :--- | :--- | :--- |
| **NFR-01** | Security | **ALIGNED / ADVANCED** | Exceeds basic password hashing: implements PBKDF2/Argon2 with versioned password pepper (`core.pepper.py`) and granular RBAC. |
| **NFR-02** | Privacy | **ALIGNED** | Scoping engine (`api/rbac/scoping.py`) strictly partitions data across schools, institutes, programs, and individual student users. |
| **NFR-03** | Usability | **FRONTEND RESPONSIBILITY** | Backend provides descriptive error payloads and standardized envelope responses via `CustomResponseRenderer`. |
| **NFR-04** | Responsiveness | **FRONTEND RESPONSIBILITY** | Backend provides lightweight paginated payloads (`CustomPagination`) suitable for mobile and desktop clients. |
| **NFR-05** | Compatibility | **ALIGNED** | Standard REST/JSON API compatible with all modern HTTP clients, web browsers, and platforms. |
| **NFR-06** | Reliability | **ALIGNED** | Relational transactions (`@transaction.atomic`), composite unique constraints, and ACID compliance across SQLite and MySQL. |
| **NFR-07** | Auditability | **ALIGNED** | Comprehensive audit trail via `AuditLog` model and `ActorStampedModel` metadata (`created_by`, `updated_by`). |
| **NFR-08** | Backup | **PARTIALLY DOCUMENTED** | Deployment runbooks exist, but dedicated automated backup script is an operational DevOps deliverable. |
| **NFR-09** | Maintainability | **ALIGNED** | Clean modular architecture, type hints, automated migrations, seeders (`seed_clearance`, `seed_rbac`), and OpenAPI schemas. |
| **NFR-10** | Data Integrity | **ALIGNED** | Enforced at the database level via `UniqueConstraint(fields=["student", "clearance_type"])` and `UniqueConstraint(fields=["student_clearance", "requirement"])`. |

---

### 2.3 Scope & Work Breakdown Structure Gaps

1. **Frontend UI Deliverables (WBS 3.0 & 4.0):**
   * The repository `e-clearance_system_api` contains **only backend source code**. Frontend interfaces (wireframes, prototypes, student dashboard UI, officer portal UI) belong to a separate frontend client repository (`dnsc_systems_web` / React Vite client).
2. **Presentation & Marketing Deliverables (WBS 10.0):**
   * Presentation slides, promotional graphics, and overview videos are project management deliverables external to the API repository.
3. **Draft Placeholders in PMP:**
   * Section 6 & 6.1 (Cost Management Plan & Budget Control) is empty in the draft PMP.
   * Section 10.1 (Assumptions and Pending Decisions) is empty in the draft PMP.

---

### 2.4 Undocumented Backend Features in the PMP

The following critical features are fully implemented in the backend code but are **completely absent or inadequately described** in the PMP:

| Feature / Mechanism | Backend Implementation Details | Document Gap in PMP |
| :--- | :--- | :--- |
| **1. Strict Sequential Signing Pipeline** | `api/clearance/services/status_service.py` (`validate_sequential_clearance`). Enforces that Step $N$ cannot be signed until Step $N-1$ is `cleared`. Preceding uncleared items lock approval with HTTP 400. | **Completely Missing:** The PMP does not mention sequence ordering or prerequisites between signing checkpoints. |
| **2. Self-Contained Deficiency Tracking & Proof Upload** | `StudentClearanceItem` contains `amount_due`, `remarks`, `proof_attachment` (Cloudinary ID), `proof_notes`, and `upload_proof`/`remove_proof` endpoints. | **Inadequately Described:** The PMP only mentions "approve or reject with remarks"; it has no specification for compliance proof uploads or monetary liabilities. |
| **3. Post-Clearance Enrollment Gate** | `StudentClearance` contains `is_enrolled`, `enrolled_at`, `enrolled_by_user`, and `POST /api/v1/student-clearances/{id}/enroll/`. | **Completely Missing:** The PMP does not document the handoff between completed clearance and institutional enrollment unlocking. |
| **4. Multi-Role Signatory Authorization** | `ClearanceRequirement` supports both `authorizing_role` (primary) and `authorizing_roles` (M2M `Group`), allowing dynamic multi-person or alternate signers. | **Omitted:** The PMP assumes a static 1-to-1 mapping between an item and an "Office". |
| **5. Public QR Code Nonce Verification** | `StudentClearance.verification_token` (UUIDv4) is rendered into a Segno Level H QR code embedded in the certificate. The public route `/api/v1/student-clearances/verify/{token}/` validates authenticity without login. | **Partially Documented:** PMP mentions QR verification conceptually (AT-08, Business Rule 10) but lacks technical nonce and URL structure specifications. |
| **6. Multi-Tenant Scoping Hierarchy** | Multi-level tenant scoping (`School -> Institute -> Program -> Student`) implemented in `api/rbac/scoping.py` and `api/clearance/permissions.py`. | **Omitted:** The PMP treats DNSC as a flat organization rather than modeling institutes (e.g. Institute of Computing) and academic programs. |
| **7. Versioned Security Mechanisms** | Versioned password pepper (`core.pepper.py`) and versioned Cloudinary multi-account asset storage. | **Omitted:** Technical security details in the PMP only mention standard password hashing. |

---

## 3. Core Business Rules Reconciliation

| PMP Business Rule | Current Statement in PMP | Backend Technical Reality | Reconciliation Action |
| :--- | :--- | :--- | :--- |
| **Rule 3: Clearance Request** | *"A student must initiate a clearance request before the applicable clearance items can be processed..."* | **Conflicting:** Clearance is created through administrative batch issuance (`ClearanceType.issue`) for all enrolled students in the target year levels. Students do not create their own clearance requests. | **Revise Rule 3:** Clarify that clearance is batch-issued by administrators for eligible student cohorts; students access and track their automatically instantiated checklist. |
| **Rule 4: Office Assignment** | *"Each clearance item must be associated with the appropriate office or authorized clearance officer."* | Requirements are mapped to Django `Group` permissions (`authorizing_role` and `authorizing_roles`), allowing multiple staff members holding that role to clear the item within their institute. | **Refine Rule 4:** Specify role-based authorization groups (e.g. Program Chair, Librarian, Cashier, Dean) rather than generic physical offices. |
| **Rule 7: Remarks & Instructions** | *"Clearance officers may provide remarks or instructions when an item is rejected, remains pending, or requires further action..."* | When flagging deficiencies (`status = has_issue`), remarks are mandatory, and an optional `amount_due` can be assigned. Students can upload compliance proof attachments with notes. | **Expand Rule 7:** Incorporate deficiency tracking, amounts due, and student proof attachment workflow. |
| **NEW RULE: Sequential Progression** | *Not present in PMP.* | Requirements must be cleared in strict accordance with their `order_sequence`. Unlocking step $N$ requires all steps $< N$ to be `cleared`. | **Insert Rule 16 (Sequential Progression):** Formally document the sequential signing pipeline as a core business rule. |
| **NEW RULE: Enrollment Handoff** | *Not present in PMP.* | Once a student clearance reaches `completed`, an authorized Program Chair or Administrator may verify enrollment (`is_enrolled = True`). | **Insert Rule 17 (Enrollment Verification):** Formally document the post-clearance enrollment confirmation mechanism. |

---

## 4. Reconciliation Strategy & Action Plan for Phase 4

In **Phase 4 (Documentation Revision)**, the Project Management Plan will be updated in markdown format (`REVISED_PROJECT_MANAGEMENT_PLAN.md`) adhering to the following reconciliation principles:
1. **Preserve Original Structure:** Retain all 11 original sections, numbering, and formatting conventions of the PMP.
2. **Reconcile Divergences with Backend Reality:**
   * Update FR-04 and Business Rule 3 from "student-initiated requests" to "administrative cohort batch issuance".
   * Upgrade generic "Clearance Officer" and "Office" descriptions to reflect actual RBAC roles (Advisers, Program Chairs, Librarians, Cashiers, Deans, Governors).
3. **Incorporate Undocumented Technical Features:**
   * Add the Sequential Signing Workflow and validation rules (`order_sequence`).
   * Detail deficiency management (`amount_due`, remarks, and Cloudinary proof attachments).
   * Specify the tamper-evident PDF Certificate generation and public QR verification endpoints.
   * Add the Post-Clearance Enrollment Gate (`is_enrolled`).
4. **Complete Draft & Placeholder Sections:**
   * Provide a realistic, professional narrative for Section 6 & 6.1 (Cost Management Plan & Budget Control).
   * Formulate the foundational technical, operational, and institutional assumptions for Section 10.1 (Assumptions and Pending Decisions).
   * Fill out the approval sign-off metadata in Section 11.

---
*End of Phase 3 Gap Analysis. Ready for Phase 4 (Documentation Revision).*
