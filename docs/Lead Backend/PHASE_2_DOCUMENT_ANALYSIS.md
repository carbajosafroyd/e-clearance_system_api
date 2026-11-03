# Phase 2: Comprehensive Document Analysis & Requirements Extraction

**Document Code:** DOC-ANALYSIS-PHASE2  
**Target Document:** `Project Management Plan.pdf` (ClearanceDNSC Draft Version 1.0, dated October 28, 2026)  
**Supporting Documents:** `E_CLEARANCE_PRD_TDD_PLAN.md`, `E_CLEARANCE_POST_PURGE_ROADMAP.md`, `CLEAN_SLATE_PURGE_PLAN.md`, `README.md`  
**Execution Date:** October 31, 2026  
**Status:** Completed & Extracted  

---

## 1. Document Context & Metadata

* **Document Title:** Project Management Plan (PMP) with System Flow and Requirements  
* **Target System:** ClearanceDNSC (Proposed Web-Based Student Clearance Management System)  
* **Organization:** Davao del Norte State College (DNSC)  
* **Document Version:** Draft Version 1.0 (October 28, 2026)  
* **Project Manager:** Elyza M. Flores  
* **Project Sponsor / Owner:** Proposed Institutional Stakeholders: DNSC  
* **Status in Document:** In Progress — Planning and Requirements Definition  

---

## 2. Extraction of Documented Requirements

### 2.1 Functional Requirements (PMP Section 2.1)

| Requirement ID | Documented Statement | Documented Primary User | Documented Acceptance Measure |
| :--- | :--- | :--- | :--- |
| **FR-01** | The system shall allow authorized users to log in and log out. | All roles | Valid and invalid log in tests pass. |
| **FR-02** | The system shall display features according to the user’s assigned role. | All roles | Student, officer, and admin access tests pass. |
| **FR-03** | Students shall be able to view their profile and own clearance records. | Student | A student cannot access another student’s record. |
| **FR-04** | Students shall be able to initiate a clearance request when eligible. | Student | A valid request is saved and shown in the dashboard. |
| **FR-05** | The system shall show the status of each required office or clearance item. | Student | Status and remarks match the saved record. |
| **FR-06** | Officers shall view requests assigned to their office. | Officer | An officer cannot process another office’s items. |
| **FR-07** | Officers shall be able to approve or reject an assigned item and add remarks when needed. | Officer | Decision, actor, and timestamp are saved. |
| **FR-08** | The system shall update overall progress after an authorized decision. | System | Progress reflects all required items. |
| **FR-09** | The system shall not mark a clearance completed until all required approvals are obtained. | System | Incomplete clearance cannot become completed. |
| **FR-10** | Administrators shall manage user accounts, roles, offices, and clearance requirements. | Admin | Authorized changes save and unauthorized changes are blocked. |
| **FR-11** | The system shall maintain a history of important clearance actions. | System / Admin | Action history shows the user, action, and time. |
| **FR-12** | The system should provide notifications when clearance status changes. | System / Officer | Notification appears if this feature is included. |
| **FR-13** | The system should generate reports for pending, approved, rejected, and completed clearances. | Admin | Report totals match the test data. |
| **FR-14** | The system may allow authorized students to print or download a completed clearance. | Student | Output is available only after completion, if included. |

---

### 2.2 Non-Functional Requirements (PMP Section 2.2)

| Requirement ID | Category | Documented Specification |
| :--- | :--- | :--- |
| **NFR-01** | Security | Passwords must be stored using secure password hashing and access must be role-based. |
| **NFR-02** | Privacy | Student information must be visible only to authorized users for legitimate project purposes. |
| **NFR-03** | Usability | Labels, statuses, and actions must be clear to students and office personnel. |
| **NFR-04** | Responsiveness | Pages should work on common desktop and mobile screen sizes. |
| **NFR-05** | Compatibility | The system should support current versions of common web browsers used for testing. |
| **NFR-06** | Reliability | Saved records and decisions should remain consistent after refresh or re-login. |
| **NFR-07** | Auditability | Important decisions must record who acted and when. |
| **NFR-08** | Backup | The team should document a database backup and recovery procedure for the deployed version. |
| **NFR-09** | Maintainability | Code, database tables, and setup instructions should be organized and documented. |
| **NFR-10** | Data Integrity | The system should prevent duplicate clearance requests for the same student and clearance period. |

---

### 2.3 Documented Scope Boundaries (PMP Section 3)

#### In-Scope Deliverables (Section 3.1)
1. Responsive web interface with login & role-based dashboards.
2. Student clearance request and progress-tracking pages (checklist of required items).
3. UI/UX wireframes, prototypes, and interface designs.
4. Office review & approval workflows with status remarks for pending/rejected items.
5. Admin functions for managing users, roles, offices, clearance requirements, and basic reports.
6. Backend functions for auth, routing, clearance processing, status updates, and RBAC.
7. Database structures for users, students, offices, clearances, items, and action history.
8. API specifications and integration functions for approved services / department communication (or mock services).
9. QR code generation and verification for approved master clearance pass / clearance reference.
10. Automated email notifications for approved clearance events.
11. Security measures (auth, sessions, validation, sensitive data, DB access).
12. Database backup and restoration procedures.
13. QA documents (test plan, test cases, defect log, test results).
14. Mobile responsiveness, functional, integration, security, and edge-case testing.
15. Cloud deployment of the web application.
16. Basic reports and printable/downloadable final clearance.
17. Mock accounts and sample data for demonstration.
18. Presentation slides, promotional graphics, and overview video.
19. User guide, technical setup notes, and final project report.

#### Out-of-Scope Boundaries (Section 3.2)
1. Direct integration with the official DNSC Student Information System (SIS) without official access.
2. Online payment processing, accounting integration, or automated financial clearance.
3. Biometric identification, native mobile applications, and SMS gateway integration.
4. Legally binding electronic signatures or digital certificates (X.509 PKI) unless required.
5. Automatic verification from external office databases not available to the team.
6. Production use of real student data without institutional authorization.
7. Replacement of the existing DNSC Student Information System.

---

### 2.4 Documented System Flow & Business Rules (PMP Section 4)

#### Documented Technical Coordination Steps (Section 4.1)
1. **Login and Role Identification:** Verifies credentials and assigns student, clearance officer, or admin access.
2. **Student Clearance Request:** Student initiates a clearance request and views assigned items.
3. **Office Review:** Authorized clearance officer reviews assigned items and records approval, rejection, or pending status with remarks.
4. **Backend Processing:** Backend validates actions, checks permissions, and records decisions in action history.
5. **Database Update:** Persists clearance decision with data consistency.
6. **Progress Tracking:** Student dashboard displays saved status and overall progress.
7. **API Integration:** Mock or real API integrations for departmental communication.
8. **QR Verification:** QR code generation, scanning, and verification confirming validity without exposing sensitive data.
9. **Email Notification:** Automated email notifications for clearance events, including clearance completion.
10. **Quality Assurance:** End-to-end testing across permissions, status updates, and layouts.
11. **Deployment & Demonstration:** Cloud hosting deployment and demonstration preparation.

#### Documented User Roles (Section 4.2)
* **Student:** Log in, view checklist, monitor progress, view remarks, access final clearance document.
* **Clearance Officer:** Log in, view requests assigned to their office, approve/reject items, record pending status, provide remarks.
* **Administrator:** Manage user accounts and roles, maintain office assignments and clearance requirements, monitor clearance records, manage system settings.

#### Documented Core Business Rules (Section 4.3)
* **Rule 1 (Authentication):** Users must authenticate before accessing protected features.
* **Rule 2 (Role-Based Access):** Access restricted based on assigned roles.
* **Rule 3 (Clearance Request):** Student must initiate a clearance request before items can be processed.
* **Rule 4 (Office Assignment):** Each clearance item must be associated with the appropriate office/officer.
* **Rule 5 (Clearance Decision):** Only authorized clearance officer can approve or reject assigned items.
* **Rule 6 (Status Recording):** Each decision must be saved in the database.
* **Rule 7 (Remarks & Instructions):** Officers may provide remarks when an item is rejected or pending.
* **Rule 8 (Overall Clearance Status):** Status becomes Completed only when all required items are approved.
* **Rule 9 (Data Integrity):** Consistent relationships between students, requests, items, and offices.
* **Rule 10 (QR Code Security):** Scanning QR code must not bypass server auth or directly grant approval.
* **Rule 11 (Email Notifications):** Triggered only by specified events.
* **Rule 12 (Data Privacy):** Real data used only with authorization; mock data otherwise.
* **Rule 13 (Integration Limitations):** External APIs integrated only with access; mock services used otherwise.
* **Rule 14 (Auditability):** Clearance actions must record sufficient info for accountability.
* **Rule 15 (Final Clearance Document):** Printable/downloadable final document available only when complete.

---

### 2.5 Documented Project Milestones & Acceptance Tests (PMP Sections 5 & 7)

#### Milestones (Section 5.1)
* **M1 (Week 1):** Project Initiation and Planning (requirements, clearance workflow, database planning).
* **M2 (Week 2):** Interface and Core Development (dashboards, officer portal, core modules).
* **M3 (Week 3):** System Integration and Feature Completion (QR generation/verification, email alerts, cloud prep).
* **M4 (Week 4):** Testing and Corrections (functional, security, usability testing, defect logging).
* **M5 (Week 4):** Deployment, Demonstration, and Handover (cloud deployment, documentation submission).

#### Acceptance Tests (Section 7.1)
* **AT-01:** User Authentication (login/logout).
* **AT-02:** Role-Based Access Control.
* **AT-03:** Student Dashboard (progress & item statuses).
* **AT-04:** Officer Approval Workflow (assigned item review, approval/flagging, remarks).
* **AT-05:** Clearance Status Updates (accurate database state).
* **AT-06:** Overall Clearance Completion (complete only when all items approved).
* **AT-07:** Administrator Functions (manage accounts, roles, requirements).
* **AT-08:** QR Code Verification (generate QR, verify reference against server record).
* **AT-09:** Email Notifications (automated completion email via test accounts).
* **AT-10:** Data Security and Integrity (access controls & persistence).
* **AT-11:** Responsiveness and Usability.
* **AT-12:** Cloud Deployment.
* **AT-13:** Documentation and Handover.

---

### 2.6 Document Gaps & Incomplete Sections Identified in PMP Draft 1.0

1. **Uncompleted Cost Management Section (Section 6 & 6.1):** Heading exists with no narrative, budget allocation, or operational cost breakdown.
2. **Blank Assumptions and Decisions Section (Section 10.1):** Heading exists under Section 10 with no content before jumping directly to Section 11 (Approval).
3. **Approval Status Table (Section 11):** Sign-off status fields for Project Manager (Elyza M. Flores) and Project Instructor are currently blank placeholders.
4. **Generic "Clearance Officer" Abstraction:** The PMP lumps all signing authorities into a single generic "Clearance Officer" role with an "Office" attribute, failing to specify the real-world institutional roles (Adviser, Program Chair, Librarian, Cashier, Dean).
5. **Absence of Sequential Signing Model:** The PMP does not describe sequential dependencies between signing checkpoints.
6. **Absence of Deficiency Settlement Lifecycle:** The PMP only mentions "approve or reject with remarks"; it lacks the concepts of monetary deficiencies (`amount_due`), compliance proof uploads, and signatory review.
7. **Initiation Mechanism Conflict:** The PMP describes a model where students initiate clearance requests (FR-04, Business Rule 3), whereas the implemented system operates via administrative batch issuance.

---
*End of Phase 2 Document Analysis. Proceeding to Phase 3 (Gap Analysis).*
