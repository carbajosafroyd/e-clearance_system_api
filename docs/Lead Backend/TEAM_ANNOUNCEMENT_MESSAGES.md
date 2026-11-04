# Ready-to-Send Internal Team Task Assignments

Use these messages in your internal backend department chat. You (as Lead) will represent the team and report directly to the Project Manager (PM). Their job is to test, verify, document, and propose improvements to ensure what you hand over to the PM is bulletproof.

---

## 📢 Template 1: Master Team Announcement (Post in Backend Group Chat)

> **Backend Team Update: Verification, Testing & PM Handoff Preparation**
>
> Hey everyone! 👋
> 
> As you know, our core **E-Clearance Backend API** is fully developed, connected to cloud MySQL, and deployed live on Render:
> 🔗 Live API Docs: `https://e-clearance-system-api.onrender.com/api/docs/swagger/`
> 
> I will be representing our department and presenting the completed backend proposal directly to the **Project Manager (PM)** for project sign-off and frontend handoff. 
> 
> Before I present to the PM, **I need each group to stress-test our current implementation, verify that everything is working as intended in real-world scenarios, document their domain, and identify any improvements or edge-case gaps.**
> 
> If you find any bugs, confusing responses, or room for improvement, report them to me so we can fix them together.
> 
> Here is the breakdown by group:
> 
> 🗄️ **Database Administrators (Team of 3)**
> - **Mission**: Test database operations, audit schema relationships, polish our ERD visual, and document disaster recovery.
> 
> 🔌 **API & Integration Specialists (Team of 2)**
> - **Mission**: Test all endpoints via Postman, audit response schemas/status codes, and construct the complete Frontend Handoff Handbook.
> 
> 📱 **QR Code & Email Automation Engineers (Team of 2)**
> - **Mission**: Test real email delivery across Gmail/devices, test scanning the QR code on generated PDF certificates, and verify error edge-cases.
> 
> 🛡️ **Assistant Backend (1 Member)**
> - **Mission**: Run end-to-end user testing (Student -> Signatory -> Deficiency -> Clearance Approval), document the QA Test Matrix, and help assemble the final technical report for the PM.
> 
> 📅 **Timeline**:
> - **Days 1–3**: Hands-on testing & verification of your assigned domain
> - **Days 4–5**: Bug/improvement reporting & review session with me
> - **Days 6–7**: Finalizing reports and documentation for the PM proposal
> 
> Check your direct/group messages for your exact instructions and testing guidelines. Let’s make sure we deliver an airtight system to the PM! 💪🚀

---

## 👥 Template 2: Group Message for the 3 DBAs (Bundled Assignment)

*(Send this to the 3 DBAs)*

> **To our Database Administration Team (@Name1, @Name2, @Name3):** 🗄️
>
> Hey team! As our DBAs, I need the three of you to thoroughly audit and test our database layer before I present our architecture to the PM.
>
> You will work together as one group on this **Database Verification & Documentation Package**:
>
> 1. **Visual Schema & Relationship Audit**
>    - I have generated the complete Mermaid ERD of all tables in `docs/TEAM_DELEGATION_PLAN.md`.
>    - Import it into [Mermaid Live](https://mermaid.live) or Draw.io, format it cleanly with color coding, and verify that foreign keys, many-to-many relationships, and table linkages make sense. Save the clean visual as `docs/DATABASE_ERD.png`.
>
> 2. **Data Integrity & Constraints Review**
>    - Review our data dictionary in `docs/DATA_DICTIONARY.md`.
>    - Check our table constraints: Are our `on_delete` behaviors (`CASCADE`, `PROTECT`, `SET_NULL`) safe? Identify if any indexes or unique constraints are missing.
>
> 3. **Hands-on Backup, Disaster Recovery & Seeding Test**
>    - Test creating a database backup and restoring it locally via XAMPP phpMyAdmin / `mysqldump`.
>    - Test running our automated database seed command: `python manage.py seed_all`.
>    - Write a short runbook (`docs/DB_BACKUP_AND_RESTORE_MANUAL.md`) with screenshots proving that our database can be recovered from zero in under 3 minutes.
>
> Divide the tasks among yourselves, test the live database, and let me know if you find any data issues or improvements!

---

## 👥 Template 3: Group Message for the 2 API & Integration Specialists (Bundled Assignment)

*(Send this to your 2 API Specialists)*

> **To our API & Integration Specialists (@Name1, @Name2):** 🔌
>
> Hey guys! As our API & Integration team, the two of you will work as **one collaborative unit** to audit our live endpoints and build our **Frontend Integration & Testing Package** for the PM.
>
> The PM will ask: *"Are our APIs thoroughly tested, and can the frontend devs plug in immediately without confusion?"* 
>
> Here are the two connected parts for your group (collaborate and divide as you see fit):
>
> 1. **Live Postman Test Suite & Endpoint Audit**
>    - Build an organized Postman Collection testing all key live endpoints on Render (`https://e-clearance-system-api.onrender.com/api/v1`):
>      - Auth: `POST /login/` (auto-save token), `POST /token/refresh/`, `GET /me/`
>      - Clearance: `GET /clearance-types/`, `POST /student-clearances/`, `PATCH /student-clearance-items/{id}/`, `GET /student-clearances/{id}/certificate/`
>    - Test HTTP status codes: verify that invalid logins return `401`, missing fields return `400`, and unauthorized actions return `403`.
>    - Export the final verified collection and environment file as `docs/E_Clearance_Postman_Collection.json`.
>
> 2. **Frontend Developer Handoff Handbook**
>    - Review our live Swagger documentation: `https://e-clearance-system-api.onrender.com/api/docs/swagger/`.
>    - Create `docs/FRONTEND_API_GUIDE.md` for the frontend/mobile engineers:
>      - Document the Base URLs (Local `http://127.0.0.1:8000/api/v1` vs Render production URL).
>      - Document authentication headers (`Authorization: Bearer <token>`).
>      - Document how to upload proof receipts via `multipart/form-data`.
>      - Document our standard error response structure so frontend devs know what fields to expect.
>    - Identify if any endpoint responses are missing fields that the frontend UI might need (e.g. student full name, signatory department titles).
>
> Work together to test and document, and report any bugs or response improvements directly to me!

---

## 📩 Template 4: Messages for Remaining Roles

### 1. To QR Code Automation Engineer
> *Hey [Name]! As our QR Automation Engineer, your objective is to stress-test the QR verification feature before my PM report:*
> 
> 1. *Test the PDF certificate generation endpoint and inspect the generated QR code.*
> 2. *Use real smartphone cameras (Android and iOS) to scan the QR code on the certificate: Does it direct cleanly to the verification URL? Does the verification token load properly?*
> 3. *Identify edge cases or improvements: What happens if an invalid or altered token is checked?*
> 4. *Compile your findings and phone scan screenshots into `docs/QR_VERIFICATION_SPECIFICATION.md`.*
> 
> *Let me know as soon as you complete your phone scan tests!*

### 2. To Email Automation Engineer
> *Hey [Name]! As our Email Automation Engineer, your objective is to test our automated email notifications:*
> 
> 1. *Trigger test clearance events: (a) when a student has deficiency remarks, and (b) when all items are cleared and the completion certificate is issued.*
> 2. *Verify email delivery in real inboxes (check spam folder, check mobile view, check dark mode, check if the PDF attachment opens).*
> 3. *Verify performance: Ensure the API doesn't hang or freeze while sending email (thanks to our async background threads).*
> 4. *Document your test results and email screenshots into `docs/EMAIL_NOTIFICATION_MANUAL.md`.*
> 
> *Report any delivery formatting issues to me so we can refine them!*

### 3. To Assistant Backend
> *Hey [Name]! As Assistant Backend, you will be my right-hand partner in verifying the whole system:*
> 
> 1. *Perform an end-to-end user journey test: Sign in as Student -> Apply for Clearance -> Sign in as Signatory (Librarian/Dean) -> Add Deficiency -> Upload Proof -> Approve Item -> Generate Final Certificate.*
> 2. *Build our **QA Test Execution Matrix** (`docs/QA_TEST_EXECUTION_MATRIX.md`), documenting 20+ test scenarios with inputs, expected behavior, actual results, and status (PASS/FAIL).*
> 3. *Help me assemble everyone's findings, screenshots, and diagrams into our master technical report for the PM.*
> 
> *Let's coordinate closely so we have a rock-solid report ready for the PM!*
