# E-Clearance System API - Clean ERD Specification

This ERD code is stripped of all quote descriptions and "others" comments.
Every attribute follows the clean 3-part structure:
```text
datatype field [KEY]
```
When imported into **Lucidchart** or **Mermaid Live**, it automatically renders as a clean 3-column table:
**`Keys` | `Fields` | `Datatypes`** (or `Datatypes | Fields | Keys`).

---

## 🛠️ Instructions for Lucidchart & Mermaid Live

1. Copy the code block below.
2. In **Lucidchart**: Go to `Insert` -> `Import Data` -> `Mermaid` -> Paste -> Click **Insert Diagram**.
3. In **[Mermaid Live Editor](https://mermaid.live)**: Paste directly.

---

## 📊 Clean 3-Column Mermaid Code

```mermaid
erDiagram
    %% =========================================
    %% 1. Campus Hierarchy
    %% =========================================
    schools ||--o{ institutes : "has"
    institutes ||--o{ programs : "offers"
    programs ||--o{ students : "enrolls"

    %% =========================================
    %% 2. User & Authentication
    %% =========================================
    users ||--o| students : "identifies (1:1)"
    institutes ||--o{ users : "staff/admin members"

    %% =========================================
    %% 3. Clearance Template & Requirements
    %% =========================================
    institutes ||--o{ clearance_types : "defines"
    clearance_types ||--o{ clearance_requirements : "contains"

    %% =========================================
    %% 4. Student Clearance Execution
    %% =========================================
    students ||--o{ student_clearances : "applies for"
    clearance_types ||--o{ student_clearances : "generates"

    student_clearances ||--o{ student_clearance_items : "tracks"
    clearance_requirements ||--o{ student_clearance_items : "fulfills"
    users ||--o{ student_clearance_items : "signs off"

    %% =========================================
    %% 5. Notifications & Settings
    %% =========================================
    users ||--o{ notifications : "receives"
    institutes ||--o{ system_settings : "configures"

    %% =========================================
    %% ENTITY ATTRIBUTES (datatype field [key])
    %% =========================================

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
