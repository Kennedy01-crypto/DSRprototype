# Kenya DPA 2019 DSR Prototype

This repository is a Docker-first Django 5 prototype for handling data subject requests (DSRs) under Kenya's Data Protection Act, 2019. It models a privacy operations workflow for healthcare or public-sector organizations that need to process personal-data requests in a structured, auditable way.

## What this system is about

This project is a demonstration of a DSR management workflow rather than a full production privacy platform. It shows how an organization can:

- accept requests from patients or data subjects through a portal
- process requests through internal review stages
- track separate configurable internal targets and legal deadlines
- record overdue deadline events without automatically pausing or escalating cases
- log every state transition for audit purposes
- handle data collection and quarantine as part of the review process

In short, it is a prototype for managing the lifecycle of privacy rights requests such as access, correction, erasure, portability, objection, and restriction.

## Key workflow

The request lifecycle is modeled as a finite state machine. A DSR can move through states like:

- Submitted
- In Progress (Departmental Search, Privacy Review, Legal / Management Review,
  Waiting for Subject Information, or Response Preparation)
- Completed (Fulfilled, Partially Fulfilled, Rejected, Withdrawn, or Duplicate)

Patients see only the three major statuses; DPOs can see the processing substatus. These transitions are implemented in the Django model and enforced by the django-fsm package. Each state change records the actor and reason for traceability.

Refusals, partial outcomes, retention decisions, and cases explicitly flagged as sensitive or complex require independent review before a response can be published or the case closed. Review is bound to the exact response version and case evidence; the requester, response author, and assigned handler cannot approve it. A changed case requires a new response version and review. Duplicate cases are linked to a canonical case, then completed only after the duplicate outcome is recorded and published. Patients may withdraw only before any response has been published; eligible completed cases can be linked to a new reconsideration request.

## Main features

- Patient portal API for submitting and viewing personal requests
- Next.js patient portal and DPO, department contributor, and independent reviewer workspaces
- DPO queue filters, case assignment, review tasks, review decisions, and patient update history
- DPO overdue, due-soon, unassigned, and escalated work queues plus audited CSV reports
- Patient withdrawal and clarification-response flows, with reasoned escalation recovery
- Mock-only PARAS/CIMS/ERPS source-search checklist with status and evidence references
- Response-preparation draft assembled from workflow records and requiring staff review
- Immutable, numbered response versions with attributed DPO finalization and separate portal publication
- Patient portal visibility restricted to published responses for the authenticated patient's own cases
- Separate workflow audit and case-activity timelines
- Explicit case resolution outcomes when closing requests
- Independent review for sensitive/complex cases and refusal, partial, or retention outcomes
- Canonical duplicate-case linkage and patient-linked reconsideration submissions
- Simulated role-based bearer authentication for patient, DPO, and restricted admin roles
- DPO-configurable internal targets and optional legal deadlines by request type, with an audit history
- Celery background tasks for data collection and SLA monitoring
- Audit logging for every transition
- Data quarantine model for secure retention of collected data payloads
- Docker Compose setup for local development with PostgreSQL, Redis, workers, and Beat
- Synthetic acceptance scenarios for published access responses, erasure review, blocked searches, clarification, and escalation

## Architecture

- Django 5 + Django REST Framework for the API
- Next.js App Router frontend with Tailwind CSS, shadcn/ui, Lucide React, and Sonner toasts
- PostgreSQL as the database
- Redis for Celery broker/backend
- Celery worker and Celery Beat for async jobs and deadline checks
- Gunicorn as the web server
- Docker Compose for local orchestration

## API overview

### Patient-facing endpoints

- POST /api/v1/dsrs/submit/
  Submit a new DSR.
- GET /api/v1/dsrs/my-requests/
  View the authenticated patient's requests.

### DPO console endpoints

- GET /api/v1/dpo/dsrs/
  View all requests and a basic departmental heatmap.
- POST /api/v1/dpo/dsrs/<id>/transition/
  Move a request through workflow transitions such as `start_departmental_search`, `request_legal_review`, and `approve_and_close`.
- GET|PATCH /api/v1/dpo/policies/
  Review and update per-request-type deadline policy. Updates are reasoned and audited, and affect new requests only.
- GET|POST /api/v1/dpo/dsrs/<id>/response-versions/
  List finalized response versions or create a new immutable version after readiness checks.
- POST /api/v1/dpo/dsrs/<id>/response-versions/<version-id>/publish/
  Publish a finalized response into the patient's portal; no external communication is sent.
- GET /api/v1/dsrs/<id>/responses/
  List only published response content belonging to the authenticated patient.

## Authentication model

This is intentionally a prototype. It does not verify real JWTs or external identity provider tokens. Instead, it accepts simulated bearer tokens of the form:

- `Bearer patient:<patient_id>`
- `Bearer dpo:<dpo_id>`

These tokens stand in for an upstream identity system that has already verified the user. In production, this logic should be replaced with proper JWT validation or OAuth/OIDC integration.

## Local setup

1. Copy `.env.example` to `.env`.
2. Update environment values and secrets if needed.
3. Start the stack:

   `docker compose up --build`

4. Access the Next.js UI at:

   `http://localhost:3002`

5. The Django API (no browser pages) is available at:

   `http://localhost:8002`

The UI starts with a mock role/ID sign-in. Patients submit and follow requests; DPOs work the queue and run each case end to end (searches, decisions, draft, finalize, publish, review, close) on the case page; contributors and independent reviewers get their own queues. The frontend calls the Django API through same-origin Next.js route handlers; its signed HttpOnly demo session does not authenticate real users. Configure `NEXT_MOCK_SESSION_SECRET` in `.env` for local use. Keep `web` in `DJANGO_ALLOWED_HOSTS` so the frontend container can reach Django using its Compose service name. Internal target defaults are demonstration policy only; legal deadlines remain unset until configured by a DPO. See [API_README.md](API_README.md) for the API routes, request examples, and workflow actions. The web container runs database migrations automatically on startup.

To load the synthetic acceptance pack, run `docker compose exec web python manage.py seed_demo_data`. Use the DPO role with any mock staff ID to inspect the queue; use the patient role with `mock-patient-acceptance-access` to view the example published response. Other seeded patient IDs and expected scenarios are listed in [API_README.md](API_README.md). The erasure/retention example is explicitly hypothetical and is not legal guidance.

Run the backend tests with `docker compose exec web python manage.py test dsr`.

## Integrations

Searches of source systems and patient notifications go through adapters in `dsr/integrations/`, chosen in settings:

```python
DSR_SOURCE_CONNECTORS = {
    "PARAS": "dsr.integrations.mock.MockSourceConnector",
    "CIMS": "dsr.integrations.mock.MockSourceConnector",
    "ERPS": "dsr.integrations.mock.MockSourceConnector",
    "DYNAMICS": "dsr.integrations.mock.MockSourceConnector",
}
DSR_PATIENT_NOTIFIER = "dsr.integrations.mock.PortalOnlyNotifier"
```

To connect a real system, subclass `SourceConnector` in `dsr/integrations/base.py`, implement `search()`, and point the setting at the new class. The rules each connector must follow (counts and references only, which errors mean "retry" and which mean "stop") are written at the top of that file. The workflow code does not change.

Jobs retry on their own and end in the DPO's **Integrations** page when they give up. See [API_README.md](API_README.md#integrations) for the exact behaviour.

## Deadlines

- Each case has an **internal target** and, if the policy sets one, a **legal deadline**, both set at submission from the policy version in force.
- A policy counts either **calendar days** or **business days**. Business days skip weekends and the dates in `DSR_PUBLIC_HOLIDAYS` in `config/settings.py`. That list is an example; confirm it against the Kenya Gazette each year and add moveable holidays when announced.
- An **extension** (if the policy allows it) moves both deadlines by the same number of days, counted the way the policy counts.
- A **pause** (if the policy allows it, with an approved reason) stops both clocks. While paused the case is not overdue and appears in the "Deadline clock paused" queue. **Resuming** moves both deadlines forward by exactly the time spent paused.
- Nothing pauses a clock automatically: waiting for the patient, an escalation or a blocked search does not.

## Working safely with others

Every case has a `revision` number that goes up with each change. The case screen sends it back with each change (`If-Match`). If someone else changed the case in the meantime, the change is refused with `409` and `"code": "stale_case"`, the screen reloads, and nothing is overwritten. API clients that don't send `If-Match` are not checked.

## Operations

- `GET /healthz`: the process is up.
- `GET /readyz`: the database and the job queue are reachable (`503` with the failing check otherwise). Docker Compose uses it: the frontend, worker and scheduler start only after the API is ready, which also means migrations have run.
- **Logs** are one JSON object per line. Every HTTP request logs method, path, status, duration and the acting role and ID, tagged with a **request ID**. The Next.js proxy sends an `X-Request-ID` with each call and Django returns it, so one problem can be traced across both. For server errors the UI shows the first characters of the ID as a reference to quote.
- Set `DSR_LOG_LEVEL` (default `INFO`) to change verbosity.

## Tests

- Backend: `docker compose exec web python manage.py test dsr`
- Browser tests (Playwright): start the stack with a production frontend build, `docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --wait`, then `cd e2e && npm install && npx playwright install chromium && npm test`. Return to the dev server afterwards with `docker compose up -d frontend`. (The dev server compiles on demand and can stall for minutes, which makes browser tests unreliable.)
- CI (`.github/workflows/ci.yml`) runs backend tests with a missing-migration check, frontend type-check and lint, and the browser tests against the full Docker stack on every push.

## Celery and SLA monitoring

The project includes a Beat schedule that runs `monitor_sla_deadlines` every 15 minutes. It records overdue events for active internal targets and configured legal deadlines; it does not change request states or pause/reset clocks. Waiting for internal teams is an operational delay, not a reason to stop a deadline. Confirm the applicable jurisdiction's legal timeline with authorized privacy/legal owners before setting legal deadlines or using this prototype beyond mock data.

## Repository structure

- `config/` - Django settings, Celery configuration, and routing
- `dsr/models.py` - data model and the case state machine
- `dsr/services.py` - workflow rules and every write to a case; call these from views, tasks, or integrations
- `dsr/selectors.py` - read-only queries and screen data (queue filters, activity, available actions, response draft)
- `dsr/views.py` - thin API views: check role and input, call one service, serialize the result
- `dsr/exceptions.py` - workflow errors and their HTTP mapping
- `dsr/permissions.py` - role gate for every API view
- `dsr/integrations/` - connector and notifier interfaces, mock implementations, and the settings-driven registry
- `dsr/deadlines.py` - calendar and business-day deadline arithmetic
- `dsr/observability.py` - request IDs and JSON logs
- `e2e/` - Playwright browser tests
- `.github/workflows/ci.yml` - continuous integration
- `frontend/` - Next.js user interface
- `docker-compose.yml` - service orchestration for local development
- `Dockerfile` - container build definition
- `.env.example` - environment variable template
- `requirements.txt` - Python dependencies

## Important note

This is a prototype, not a full production compliance system. Authentication is simulated, outbound patient communications are not sent, source-system results are mocked, and response-target defaults are not legal advice. Do not enter real patient or clinical information. The application does not implement real identity verification, production encryption, regulator reporting, or integrations with PARAS, CIMS, or ERPS.

Response finalization and portal publication are distinct, audited steps. Only
published response content is exposed in a patient's portal. Finalized versions,
publication records, and independent-review records are application-level
append-only. This prototype's reviewer separation compares mock principal IDs
only; it is not a production identity or role-separation control.
