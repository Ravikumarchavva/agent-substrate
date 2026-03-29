---
name: api-testing
description: Systematic API testing skill for validating endpoints, checking status codes, and verifying response schemas.
version: "1.0"
license: MIT
allowed-tools: http_request code_interpreter
category: development/execution
tags: [api, test, endpoint, status, validation, contract, rest, http]
aliases: [test-api, endpoint-testing, api-validation]
metadata:
  author: agent-framework
---

# API Testing Skill

Use this skill when the user asks to test an API endpoint, validate a response, or verify an API contract.

## Testing Procedure

### Step 1 — Understand the API
- What is the base URL?
- What endpoints need testing?
- What authentication is required (API key, Bearer token, OAuth)?
- Is there API documentation or an OpenAPI spec?

### Step 2 — Plan Test Cases
For each endpoint, plan tests covering:
1. **Happy path** — valid request with expected parameters
2. **Missing required fields** — omit each required parameter
3. **Invalid types** — wrong data types for parameters
4. **Edge cases** — empty strings, very long inputs, special characters
5. **Auth failures** — missing or invalid credentials
6. **Rate limits** — if applicable

### Step 3 — Execute Tests
For each test case:
1. Build the request (method, URL, headers, body)
2. Call the endpoint using `http_request`
3. Record: status code, response body, response time

### Step 4 — Validate Responses
Check each response for:
- **Status code** matches expected (200, 201, 400, 401, 404, etc.)
- **Response body** structure matches expected schema
- **Required fields** are present and non-null
- **Data types** are correct (string, number, array, etc.)
- **Error messages** are descriptive for error responses

### Step 5 — Report Results
Present results as a table:
```
| Test Case | Method | Endpoint | Expected | Actual | Status |
|-----------|--------|----------|----------|--------|--------|
| Happy path | GET | /users | 200 | 200 | ✅ PASS |
| No auth   | GET | /users | 401 | 401 | ✅ PASS |
| Bad input  | POST | /users | 400 | 500 | ❌ FAIL |
```

Summarize:
- Total tests run
- Pass/fail counts
- Critical issues found
- Recommendations
