# Postman Collection Build Guide for API & Integration Specialists

**Target Audience:** API & Integration Specialists (Team of 2)  
**Deliverable:** `docs/E_Clearance_Postman_Collection.json` & `docs/E_Clearance_Environment.json`  
**API Target:** Render Production (`https://e-clearance-system-api.onrender.com/api/v1/`)  

---

## 1. Overview and Mission

As the **API & Integration Specialists**, your mission is to deliver a **production-ready Postman Collection & Test Suite** that:
1. Tests all live Render API endpoints across all three portals (Student, Signatory, Admin).
2. Uses **automated JWT token capture** so nobody has to manually copy-paste auth tokens.
3. Contains **automated response tests** (status codes, response times, and payload structures) that pass cleanly in front of the Project Manager.
4. Can be exported as a JSON file and committed to our project repository.

> **Important Pro-Tip:** Do not build the collection from scratch by typing every endpoint one by one. Our backend already auto-generates a standardized OpenAPI schema that Postman can import in five seconds.

---

## 2. Step 1: One-Click OpenAPI Import (The Fast Track)

Instead of manually creating dozens of requests:

1. Open Postman desktop app.
2. Click the **Import** button in the top-left corner.
3. Choose the **Link** tab and paste our live schema URL:
   ```
   https://e-clearance-system-api.onrender.com/api/schema/
   ```
   *(Alternative: If offline, import the local file `docs/schema.yaml` from our repository).*
4. Under Import options:
   * Select **Postman Collection**.
   * Click **Import**.
5. Postman will automatically generate every endpoint, URL parameter, and request body schema.

---

## 3. Step 2: Set Up the Postman Environment

Create a dedicated Postman Environment so variables can be reused dynamically across all requests.

1. Click **Environments** on the left sidebar in Postman.
2. Click **+ Create Environment** and name it: `E-Clearance Production`.
3. Add the following initial variables:

| Variable Name | Initial Value | Current Value | Notes |
| :--- | :--- | :--- | :--- |
| `baseUrl` | `https://e-clearance-system-api.onrender.com/api/v1` | `https://e-clearance-system-api.onrender.com/api/v1` | API root prefix |
| `bearerToken` | *(leave blank)* | *(leave blank)* | Auto-populated by login script |
| `studentUsername` | `Student202400633` | `Student202400633` | Test team member account |
| `studentPassword` | `password123` | `password123` | Default seeded password |
| `adminUsername` | `superadmin` | `superadmin` | Platform admin account |
| `adminPassword` | `password123` | `password123` | Admin password |

4. In the top-right corner of Postman, make sure `E-Clearance Production` is selected in the active environment dropdown.

---

## 4. Step 3: Implement Automated JWT Token Capture

To prevent having to manually copy-paste tokens every 15 minutes:

### 4.1 Login Request Setup
1. In the imported collection, open the `POST /login/` request.
2. Set the URL to:
   ```
   {{baseUrl}}/login/
   ```
3. In the **Body** tab (raw JSON):
   ```json
   {
     "username": "{{studentUsername}}",
     "password": "{{studentPassword}}"
   }
   ```
4. Open the **Tests** tab of this `POST /login/` request and paste this script:

```javascript
// Check HTTP 200
pm.test("Status code is 200 OK", function () {
    pm.response.to.have.status(200);
});

// Auto-save the JWT Access Token into our environment variable
const responseData = pm.response.json();
if (responseData.access) {
    pm.environment.set("bearerToken", responseData.access);
    console.log("JWT Access Token successfully stored in {{bearerToken}}");
} else if (responseData.token) {
    pm.environment.set("bearerToken", responseData.token);
}
```

### 4.2 Collection-Level Authorization
1. Click the root folder of the collection (top-level collection name).
2. Go to the **Authorization** tab.
3. Set **Type** to: `Bearer Token`.
4. Set **Token** to: `{{bearerToken}}`.
5. Click **Save**.
6. For all other requests in the collection, make sure their **Authorization** is set to **Inherit auth from parent**.

Now, whenever anyone clicks **Send** on `POST /login/`, every other request in the collection is immediately authenticated automatically.

---

## 5. Step 4: Organize into 5 Workflow Folders

Organize the endpoints into 5 clean folders to match our system architecture:

### Folder 1: `01. Authentication & Session`
* `POST {{baseUrl}}/login/` — Authenticate and capture token
* `POST {{baseUrl}}/token/refresh/` — Refresh expired JWT token
* `GET {{baseUrl}}/me/` — Retrieve user profile and role groups
* `POST {{baseUrl}}/logout/` — End session / blacklist token

### Folder 2: `02. Student Self-Service`
* `GET {{baseUrl}}/student-clearances/my-clearances/` — View active clearance and 6-step checklist
* `POST {{baseUrl}}/student-clearance-items/{id}/upload-proof/` — Upload receipt image (`multipart/form-data`)
* `DELETE {{baseUrl}}/student-clearance-items/{id}/remove-proof/` — Remove uploaded proof
* `GET {{baseUrl}}/student-clearances/{id}/certificate/` — Download generated PDF certificate
* `GET {{baseUrl}}/student-clearances/verify/{token}/` — Public QR verification (no auth needed)

### Folder 3: `03. Signatory Queue & Sign-offs`
* `GET {{baseUrl}}/student-clearance-items/signatory-dashboard/` — Workload counters (Pending, Deficiencies, Cleared)
* `GET {{baseUrl}}/student-clearance-items/?status=pending` — Filtered student queue
* `PATCH {{baseUrl}}/student-clearance-items/{id}/` — Approve requirement (`{"status": "cleared"}`)
* `PATCH {{baseUrl}}/student-clearance-items/{id}/` — Flag deficiency (`{"status": "deficiency", "amount_due": 150.00, "remarks": "Overdue Book"}`)

### Folder 4: `04. Program Chair Special Actions`
* `GET {{baseUrl}}/student-clearances/program-chair-dashboard/` — Department cohort analytics
* `POST {{baseUrl}}/student-clearances/{id}/enroll/` — Validate cleared student for term enrollment

### Folder 5: `05. Clearance Administration`
* `GET {{baseUrl}}/clearance-types/admin-summary/` — Campus-wide completion metrics
* `GET {{baseUrl}}/clearance-types/` — List clearance templates
* `POST {{baseUrl}}/clearance-types/{id}/issue/` — Batch issue clearance to enrolled students
* `POST {{baseUrl}}/admin/student-bulk-invitations/` — Bulk Excel roster upload
* `GET {{baseUrl}}/audit-logs/` — Query audit trail

---

## 6. Step 5: Adding Standard Test Assertions (`pm.test`)

For the PM presentation, each request should demonstrate green test results. Add these standard assertions in the **Tests** tab of key requests:

### Common Assertion Template
```javascript
// 1. Verify Status Code
pm.test("Status code is 200 OK", function () {
    pm.response.to.have.status(200);
});

// 2. Verify Response Time (under 1.5 seconds)
pm.test("Response latency is within acceptable limit (<1500ms)", function () {
    pm.expect(pm.response.responseTime).to.be.below(1500);
});

// 3. Verify Content Type
pm.test("Content-Type is JSON", function () {
    pm.expect(pm.response.headers.get("Content-Type")).to.include("application/json");
});
```

### Specific Assertions by Request Type
* **For `GET /student-clearances/my-clearances/`:**
  ```javascript
  pm.test("Returns non-empty clearance array", function () {
      const data = pm.response.json();
      pm.expect(data).to.be.an("array");
      pm.expect(data.length).to.be.above(0);
      pm.expect(data[0]).to.have.property("overall_status");
  });
  ```
* **For `GET /student-clearance-items/signatory-dashboard/`:**
  ```javascript
  pm.test("Returns dashboard metric counters", function () {
      const data = pm.response.json();
      pm.expect(data).to.have.property("total_assigned");
      pm.expect(data).to.have.property("pending_count");
  });
  ```

---

## 7. Step 6: Exporting the Collection & Deliverables

Once the requests and test scripts are configured and verified:

1. In Postman, click the three dots (`...`) next to your collection name.
2. Select **Export**.
3. Choose **Collection v2.1 (recommended)** and click **Export**.
4. Save the exported file as:
   ```
   docs/API & Integration Specialists/E_Clearance_Postman_Collection.json
   ```
   *(Also copy to `docs/E_Clearance_Postman_Collection.json` for repository root consistency).*
5. In Postman, go to **Environments**, click the three dots (`...`) next to `E-Clearance Production`, and select **Export**.
6. Save the exported file as:
   ```
   docs/API & Integration Specialists/E_Clearance_Environment.json
   ```
7. Commit both files to Git.

---

## 8. Summary Checklist for the Specialists

Before presenting to the Project Manager, verify:

- [ ] All URLs reference `{{baseUrl}}` rather than hardcoded `http://localhost:8000`.
- [ ] Running `POST /login/` automatically sets `{{bearerToken}}`.
- [ ] Collection Authorization is set to Bearer Token `{{bearerToken}}`.
- [ ] Running Postman Collection Runner yields 100% passing tests (all green).
- [ ] `E_Clearance_Postman_Collection.json` and `E_Clearance_Environment.json` are exported into the `docs/` folder.
