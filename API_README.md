# DSR API Reference

This document describes the HTTP endpoints exposed by the Kenya DPA 2019 DSR prototype. The API is served at `http://localhost:8000` when the Docker Compose stack is running.

## Authentication

Except for the homepage, API routes require an `Authorization` header using the prototype's simulated bearer-token format:

```text
Authorization: Bearer patient:<patient_id>
Authorization: Bearer dpo:<dpo_id>
```

## Endpoints

### `GET /`

Returns the HTML homepage describing the API. Does not require authentication.

### `POST /api/v1/dsrs/submit/`

Submits a DSR for the authenticated patient. The patient ID is taken from the bearer token, not from the request body.

Required header:

```text
Authorization: Bearer patient:<patient_id>
Content-Type: application/json
```

Request body:

```json
{
  "request_type": "ACCESS",
  "description": "Please provide a copy of my personal data.",
  "department": "Medical Records"
}
```

`request_type` must be one of `ACCESS`, `PORTABILITY`, `RECTIFICATION`, `ERASURE`, `RESTRICTION`, or `OBJECTION`. `description` is required; `department` is optional and may be an empty string.

Success returns `201 Created` and a request-status object:

```json
{
  "id": 1,
  "request_type": "ACCESS",
  "description": "Please provide a copy of my personal data.",
  "department": "Medical Records",
  "state": "Accepted / Under Assessment",
  "submitted_at": "2026-10-07T09:00:00Z",
  "response_due_at": "2026-11-06T09:00:00Z",
  "time_remaining_seconds": 2592000,
  "closed_at": null
}
```

The prototype uses configurable response targets: 30 days for `ACCESS` and `PORTABILITY`, and 14 days for other request types by default. Configure `DSR_LONG_RESPONSE_TARGET_DAYS` and `DSR_DEFAULT_RESPONSE_TARGET_DAYS` in `.env`. These are demo defaults, not validated statutory periods or legal advice. Confirm the approved timeline policy with AAR legal/privacy owners before relying on it.

## Browser interfaces

The homepage links to the prototype browser workspaces:

- `GET /demo/login/?role=patient` — sign in to the patient portal with a mock ID
- `GET /demo/login/?role=dpo` — sign in to the DPO workspace with a mock ID
- `GET /patient/` — submit requests and review requests belonging to the current mock patient
- `GET /dpo/` — filter the DPO queue by status, request type, or assignee
- `GET /dpo/requests/<id>/` — assign a case, document mock source searches, preview a response draft, manage review tasks, record decisions and patient updates, perform workflow actions, and review activity/audit histories
- `POST /demo/logout/` — clear the demo session

The browser workspaces use Django sessions and CSRF-protected forms. Their sign-in only lets a user select a mock role and ID; it does not verify identity or authorize access to real patient data. The API continues to use its separate simulated bearer-token authentication.

Load the fictional demo cases and acceptance scenarios with:

```sh
docker compose exec web python manage.py seed_demo_data
```

The command is idempotent for seeded identities and does not overwrite existing
cases or connect to PARAS, CIMS, or ERPS. In addition to the basic access,
rectification, and further-review examples, it seeds these acceptance cases:

| Mock patient ID | Scenario | Starting state |
| --- | --- | --- |
| `mock-patient-acceptance-access` | Closed access request with a published, synthetic response; useful for checking patient ownership and published-only visibility | Closed |
| `mock-patient-acceptance-erasure` | Erasure request with a hypothetical `RETAINED` training decision, ready for DPO response review | Response Preparation |
| `mock-patient-acceptance-search` | Pending CIMS and blocked ERPS mock searches; final decisions and response preparation should remain blocked | Privacy Review |
| `mock-patient-acceptance-clarification` | Patient portal has a fictional clarification request waiting for a response | Waiting for Subject Information |
| `mock-patient-acceptance-escalated` | Overdue request already escalated; DPO can resume it without resetting its response target | Escalated |

For patient view, use the listed mock patient ID as the portal ID. For the DPO
workspace, any mock staff ID can inspect these cases. The erasure example is a
hypothetical exercise only: it does not state that retention is legally required
or that an erasure request should be refused. Do not treat its decision or
rationale as a legal determination or policy precedent.

Run the acceptance and core workflow regression tests with:

```sh
docker compose exec web python manage.py test dsr.tests.test_acceptance_pack dsr.tests.test_workflow
```

Example:

```sh
curl -X POST http://localhost:8000/api/v1/dsrs/submit/ \
  -H "Authorization: Bearer patient:alice" \
  -H "Content-Type: application/json" \
  -d '{"request_type":"ACCESS","description":"Please provide a copy of my personal data.","department":"Medical Records"}'
```

### `GET /api/v1/dsrs/my-requests/`

Returns the requests belonging to the authenticated patient. Requires a patient token. The response is an array of request-status objects, with the same fields as the submit response.

Example:

```sh
curl http://localhost:8000/api/v1/dsrs/my-requests/ \
  -H "Authorization: Bearer patient:alice"
```

### `GET /api/v1/dpo/dsrs/`

Returns all DSRs and a count of requests by department. Requires a DPO token.
Optional query parameters are `state`, `type`, `assigned_to`, and `queue`.
`queue` accepts `overdue`, `unassigned`, or `escalated`; filters can be combined.
Overdue includes active cases awaiting subject information. Asking for more
information does not pause or reset the configured response target.

Response shape:

```json
{
  "requests": [
    {
      "id": 1,
      "request_type": "ACCESS",
      "description": "Please provide a copy of my personal data.",
      "department": "Medical Records",
      "state": "Accepted / Under Assessment",
      "submitted_at": "2026-10-07T09:00:00Z",
      "response_due_at": "2026-11-06T09:00:00Z",
      "time_remaining_seconds": 2592000,
      "closed_at": null
    }
  ],
  "departmental_heatmap": {
    "Medical Records": 1
  }
}
```

Departments without a value are grouped under `Unassigned`. The response is a simple count by department, not a geographic or visual heatmap.

Example:

```sh
curl http://localhost:8000/api/v1/dpo/dsrs/ \
  -H "Authorization: Bearer dpo:caseworker1"
```

### `POST /api/v1/dpo/dsrs/<id>/transition/`

Runs a workflow action on the request identified by `<id>`. Requires a DPO token.

Request body:

```json
{
  "action": "start_departmental_search",
  "reason": "Begin the search with the relevant department."
}
```

`reason` is optional for routine progression actions and defaults to an empty string. A non-empty reason is required for `reject`, `escalate`, and `approve_and_close`. Closing also requires `resolution_status`, chosen from `FULFILLED`, `PARTIALLY_FULFILLED`, or `RETAINED`. Supported actions and their required current states are:

| Action | Required current state | Resulting state |
| --- | --- | --- |
| `start_departmental_search` | Accepted | Departmental Search |
| `send_to_privacy_review` | Departmental Search | Privacy Review |
| `request_information` | Accepted, Departmental Search, Privacy Review, Legal / Management Review, or Response Preparation | Waiting for Subject Information |
| `resume_escalated` | Escalated | Accepted, or returns to Waiting for Subject Information if that was the source state |
| `request_legal_review` | Privacy Review | Legal / Management Review |
| `approve_privacy_review` | Privacy Review | Response Preparation |
| `approve_legal_review` | Legal / Management Review | Response Preparation |
| `approve_and_close` | Response Preparation | Closed |
| `reject` | Accepted, Departmental Search, Privacy Review, or Legal / Management Review | Rejected |
| `escalate` | Accepted, Departmental Search, Privacy Review, Legal / Management Review, Response Preparation, or Waiting for Subject Information | Escalated |

Although `approve_privacy_review` is a supported action, it transitions directly from privacy review to response preparation without a legal review.

Success returns the updated request-status object. An action that is not valid for the request's current state returns `409 Conflict`; for example, `approve_and_close` only applies to a request in Response Preparation.
Approving privacy/legal review into Response Preparation also returns `409 Conflict`
until at least one source search exists and every recorded source search is marked
complete. Blocked or pending searches cannot be treated as complete; document an
applicable mock source/checklist entry explicitly before proceeding.

Example:

```sh
curl -X POST http://localhost:8000/api/v1/dpo/dsrs/1/transition/ \
  -H "Authorization: Bearer dpo:caseworker1" \
  -H "Content-Type: application/json" \
  -d '{"action":"start_departmental_search","reason":"Start the departmental search."}'
```

### DPO case-management APIs

All of the following require a DPO bearer token. They are synthetic case-management operations only.

- `POST /api/v1/dpo/dsrs/<id>/assignment/` — assign or unassign an owner using `{"assigned_to":"reviewer-2"}` or an empty string to unassign.
- `GET|POST /api/v1/dpo/dsrs/<id>/tasks/` — list review tasks or create one with `title`, optional `details`, and optional `assigned_to`.
- `PATCH /api/v1/dpo/dsrs/<id>/tasks/<task_id>/` — update a task using one of `OPEN`, `IN_PROGRESS`, `COMPLETED`, or `BLOCKED`. Completing records the completion time.
- `GET|POST /api/v1/dpo/dsrs/<id>/decisions/` — list or record a decision. A decision has `category`, `outcome`, and `rationale`. Outcomes include `FULFILLED`, `PARTIALLY_FULFILLED`, `RETAINED`, `REJECTED`, `WITHDRAWN`, and `FURTHER_REVIEW`.
- `GET|POST /api/v1/dpo/dsrs/<id>/source-searches/` — list or add a mock source-search entry. POST fields: `source_system` (`PARAS`, `CIMS`, `ERPS`, or `OTHER`), optional `status` (`PENDING`, `COMPLETE`, `BLOCKED`), `summary`, and `evidence_reference`. One entry per source system per case.
- `PATCH /api/v1/dpo/dsrs/<id>/source-searches/<search_id>/` — update `status`, `summary`, and/or `evidence_reference`. Completed or blocked searches require a short summary. Do not put extracted clinical/source-system data in these fields.
- `GET /api/v1/dpo/dsrs/<id>/response-draft/` — return a review-only draft assembled from the request, recorded source-search summaries/references, decisions, updates, resolution, and configured response target. It contains no retrieved source data and is not sent to the patient.
- `GET|POST /api/v1/dpo/dsrs/<id>/response-versions/` — list immutable finalized versions or finalize a reviewed `{"content":"..."}` response. Finalization requires Response Preparation, a complete mock source-search checklist, and a latest decision other than `FURTHER_REVIEW`; otherwise POST returns `409 Conflict`. Each version stores its exact content and a case-management snapshot with DPO and timestamp attribution.
- `POST /api/v1/dpo/dsrs/<id>/response-versions/<version-id>/publish/` — publish a finalized version to the subject's portal. Publication creates an append-only actor/time record and an audit event; it does not send email or SMS. A response must meet the same readiness checks. Closing a case from Response Preparation now also requires at least one published response.
- `GET /api/v1/dsrs/<id>/responses/` — patient-only list of published versions for that patient's own case. Unpublished versions and internal case snapshots are never returned to patients; another patient's case returns `404 Not Found`.
- `GET|POST /api/v1/dpo/dsrs/<id>/communications/` — list or record a patient-facing update using `{"message":"Your request is under review."}`. This stores a message for the portal; it does not send email or SMS.

Except for `FURTHER_REVIEW`, a final decision is rejected with `409 Conflict`
until the source-search checklist is complete. Source-search records are mock
workflow evidence only: these endpoints never connect to PARAS, CIMS, ERPS, or
any other source system. Evidence references are labels/IDs only, not file uploads
or secure evidence storage.
- `GET /api/v1/dpo/dsrs/export/` — download a CSV report using the same optional `state`, `type`, `assigned_to`, and `queue` filters as the DPO queue. Each included case gets an activity event noting the export. Formula-like spreadsheet cells are escaped.

### `GET|POST /api/v1/dsrs/<id>/communications/`

`GET` returns updates for the specified request only when it belongs to the authenticated patient. A different patient receives `404 Not Found`. `POST` records a patient response and moves a case from Waiting for Subject Information back to Accepted / Under Assessment; posting when no response is requested returns `409 Conflict`.

### `POST /api/v1/dsrs/<id>/withdraw/`

Allows the authenticated patient to withdraw their own request before it is closed, rejected, or already withdrawn. An optional `reason` may be supplied. Withdrawal is recorded as a distinct terminal `WITHDRAWN` state and resolution; it does not delete the request or audit history. Attempts to withdraw another patient's request return `404`; terminal cases return `409`.

### `GET /dpo/export/`

Downloads the currently filtered browser DPO queue as CSV. Requires a DPO demo session; the API export instead requires a DPO bearer token.

Assignment, task, decision, and communication changes also create append-only case-activity events. Workflow state transitions create entries in the transition audit log. These application-level append-only protections are not tamper-proof against database administrators.

Finalized response versions are immutable in the application and revisions
create a higher numbered version. Publication is a separate append-only record,
so finalization alone never makes content visible to a patient. The browser DPO
workspace supports the same review/finalize/publish workflow. Publication
requires one DPO action; a second-approver rule has not been assumed because
AAR's approval policy is not yet confirmed.

The browser patient portal also supports withdrawal and, when a case is waiting for subject information, sending a response. When requesting information, DPOs should record the actual question as a patient update; the transition reason is internal audit context. Escalated cases can be resumed by a DPO with a required reason. Resuming does not reset the response target. Requests waiting for information retain their original target date; no legal deadline pause is implemented.

## Response fields

The request-status object contains:

- `id` — database identifier
- `request_type` — request type code
- `description` — request details supplied at submission
- `department` — assigned department, possibly empty
- `state` — human-readable workflow state
- `assigned_to` — mock staff ID assigned to the case, or an empty string
- `submitted_at` — submission timestamp
- `response_due_at` — target response date calculated from the configured demo policy
- `time_remaining_seconds` — non-negative seconds until the deadline
- `closed_at` — closure timestamp, or `null` if not closed
- `resolution_status` — displayed resolution, such as Open, Fulfilled, Partially fulfilled, Retained with rationale, or Rejected

## Other processing

Audit records and data-quarantine metadata are stored in PostgreSQL models, but this prototype does not expose separate HTTP endpoints to read them. Moving a DSR into Departmental Search triggers a Celery task that simulates a Fides source-system query and records quarantine metadata. Celery Beat checks active requests for expired deadlines every 15 minutes and escalates overdue requests.
