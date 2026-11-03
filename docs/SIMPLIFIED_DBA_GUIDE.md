# E-Clearance Database Architecture - Simplified DBA Guide

Welcome DBAs! This guide breaks down the database into simple, visual, real-world concepts so you can easily understand and explain all the table links to anyone.

---

## 💡 The 30-Second Big Picture

The entire database is made of just **3 simple stories**:
1. **The Campus Structure**: Where does the student study? (`School` ➡️ `Institute` ➡️ `Program` ➡️ `Student`)
2. **The Clearance Engine**: How does a student get cleared? (`ClearanceType` ➡️ `StudentClearance` ➡️ `StudentClearanceItems`)
3. **The User & Security System**: Who signs in and who approves? (`User` links to `Student` and signs `Items`)

```mermaid
flowchart TD
    subgraph S1["1. Campus Structure"]
        direction TB
        School["🏫 schools<br/>(Davao del Norte State College)"]
        Institute["🏢 institutes<br/>(Institute of Computing)"]
        Program["🎓 programs<br/>(BS in Information Technology)"]
        Student["👤 students<br/>(Juan Dela Cruz, 2026-0001)"]
        
        School -->|has many| Institute
        Institute -->|offers many| Program
        Program -->|enrolls many| Student
    end

    subgraph S2["2. The Clearance Engine"]
        direction TB
        ClearanceType["📋 clearance_types<br/>(1st Sem Semestral Clearance)"]
        Reqs["📌 clearance_requirements<br/>(1. Library, 2. Cashier, 3. Dean)"]
        StudentClearance["📄 student_clearances<br/>(Juan's Clearance Form)"]
        ClearanceItem["✅ student_clearance_items<br/>(Juan's Library Checkbox)"]

        ClearanceType -->|defines steps| Reqs
        ClearanceType -->|creates form for| StudentClearance
        StudentClearance -->|contains checkboxes| ClearanceItem
        Reqs -->|links to checkbox| ClearanceItem
    end

    subgraph S3["3. User & Authentication"]
        direction TB
        User["🔑 users<br/>(Login credentials & roles)"]
    end

    %% Key Links Between Stories
    Student -->|applies for| StudentClearance
    User -.->|student signs in as| Student
    User -.->|signatory signs off| ClearanceItem

    style S1 fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style S2 fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style S3 fill:#fff3e0,stroke:#e65100,stroke-width:2px
```

---

## 🧩 Story 1: The Campus Hierarchy (Parent ➡️ Child)
Think of a Russian nesting doll or an organizational chart:
- **`schools`** (e.g., DNSC)
  - └── **`institutes`** (e.g., Institute of Computing)
        - └── **`programs`** (e.g., BS Information Technology)
              - └── **`students`** (e.g., Juan Dela Cruz)

> **DBA Rule**: Every step cascades down (`on_delete=CASCADE`). If a program is deleted, its students belong to that program.

---

## 📋 Story 2: The Clearance Engine (The Core Feature!)

This is the most important part of the project. Here is how the tables interact in real life:

1. **`clearance_types` (The Template)**:
   - The school creates a template: *"1st Semester 2026-2027 Clearance"*.
2. **`clearance_requirements` (The Steps on the Template)**:
   - What must be cleared? 
     - Step 1: Library (Role: Librarian)
     - Step 2: Clinic (Role: Nurse)
     - Step 3: Cashier (Role: Cashier)
     - Step 4: Dean (Role: Dean)
3. **`student_clearances` (The Student's Paper Form)**:
   - When Juan applies, the system creates **1 `student_clearances` row** for Juan.
   - It gets a unique `verification_token` (UUID) that gets printed as a **QR code** on Juan's final PDF certificate!
4. **`student_clearance_items` (The Checkboxes on the Form)**:
   - If there are 4 requirements, Juan gets **4 `student_clearance_items` rows**:
     - Row 1: Library (Status: Pending or Cleared)
     - Row 2: Clinic (Status: Cleared)
     - Row 3: Cashier (Status: Deficiency -> Amount Due: 150.00 -> Proof Uploaded)
     - Row 4: Dean (Status: Pending)

---

## 🔑 Story 3: Who is `users`?
`users` is the table where usernames and passwords live:
- When a **student** logs in, their `users` row links 1-to-1 to their `students` row.
- When a **signatory** (e.g. Librarian) logs in, they look at Juan's `student_clearance_items` and sign it (`cleared_by_user_id`).

---

## 📊 Clean 3-Column Mermaid Code (Lucidchart Compatible)

Structure: `datatype field [KEY]` (No quote clutter or extra descriptions).

```mermaid
erDiagram
    %% Campus Tree
    schools ||--o{ institutes : "has"
    institutes ||--o{ programs : "offers"
    programs ||--o{ students : "enrolls"

    %% User Link
    users ||--o| students : "identifies (1:1)"
    institutes ||--o{ users : "staff/admin members"

    %% Clearance Tree
    institutes ||--o{ clearance_types : "defines"
    clearance_types ||--o{ clearance_requirements : "contains"
    
    students ||--o{ student_clearances : "applies for"
    clearance_types ||--o{ student_clearances : "generates"

    student_clearances ||--o{ student_clearance_items : "tracks"
    clearance_requirements ||--o{ student_clearance_items : "fulfills"
    users ||--o{ student_clearance_items : "signs off"

    %% Settings & Notifications
    users ||--o{ notifications : "receives"
    institutes ||--o{ system_settings : "configures"

    schools {
        bigint id PK
        string school_name UK
        string short_name
        string location
    }

    institutes {
        bigint id PK
        bigint school_id FK
        string institute_name UK
        string acronym
    }

    programs {
        bigint id PK
        bigint institute_id FK
        string name
        string acronym
        string status
    }

    users {
        bigint id PK
        string username UK
        string email
        string password
        string first_name
        string last_name
        bigint institute_id FK
        boolean is_staff
        boolean is_active
    }

    students {
        bigint id PK
        string s_studentID UK
        bigint program_id FK
        bigint user_id FK
        string s_fname
        string s_lname
        int s_lvl
        string s_set
        string s_status
    }

    clearance_types {
        bigint id PK
        bigint institute_id FK
        string name
        string clearance_category
        string academic_year
        string semester
        boolean is_active
        boolean is_issued
    }

    clearance_requirements {
        bigint id PK
        bigint clearance_type_id FK
        int authorizing_role_id FK
        string name
        int order_sequence
    }

    student_clearances {
        bigint id PK
        bigint student_id FK
        bigint clearance_type_id FK
        string status
        string verification_token UK
        datetime date_initiated
        datetime date_completed
    }

    student_clearance_items {
        bigint id PK
        bigint student_clearance_id FK
        bigint requirement_id FK
        bigint cleared_by_user_id FK
        string status
        string amount_due
        string remarks
        string proof_attachment
        datetime cleared_at
    }

    system_settings {
        bigint id PK
        bigint institute_id FK
        string setting_key
        string setting_value
    }

    notifications {
        bigint id PK
        bigint user_id FK
        string title
        string message
        string type
        boolean is_read
    }
```

---

## 🎯 Cheat Sheet for DBAs (Questions You Can Answer)

1. **"How does the system know what requirements a student must clear?"**
   > *Answer*: `clearance_types` holds the template (e.g. 1st Sem), which has multiple `clearance_requirements` (Library, Cashier, Dean). When a student initiates clearance, the system generates a `student_clearance_items` row for every requirement.

2. **"How does the QR Code connect to the database?"**
   > *Answer*: The `student_clearances` table has a `verification_token` column (UUID). When the student clears everything, that token is turned into a QR code on their PDF. Scanning the QR code looks up that token in `student_clearances`.

3. **"What happens if a student has an unreturned library book?"**
   > *Answer*: The Librarian updates Juan's row in `student_clearance_items` to `status = 'deficiency'` with `remarks = 'Unreturned book'`. Juan uploads a photo of his return receipt (`proof_attachment`), and the Librarian changes it to `status = 'cleared'`.
