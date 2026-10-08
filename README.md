# Kenya DPA 2019 DSR Prototype

This repository is a Docker-first Django 5 prototype for handling data subject requests (DSRs) under Kenya's Data Protection Act, 2019. It models a privacy operations workflow for healthcare or public-sector organizations that need to process personal-data requests in a structured, auditable way.

## What this system is about

This project is a demonstration of a DSR management workflow rather than a full production privacy platform. It shows how an organization can:

- accept requests from patients or data subjects through a portal
- process requests through internal review stages
- track a configurable prototype response target for each request
- escalate overdue cases
- log every state transition for audit purposes
- handle data collection and quarantine as part of the review process

In short, it is a prototype for managing the lifecycle of privacy rights requests such as access, correction, erasure, portability, objection, and restriction.

## Key workflow

The request lifecycle is modeled as a finite state machine. A DSR can move through states like:

- Submitted
- Accepted / Under Assessment
- Departmental Search
- Privacy Review
- Legal / Management Review
- Response Preparation
- Closed
- Rejected
- Escalated

These transitions are implemented in the Django model and enforced by the django-fsm package. Each state change records the actor and reason for traceability.

## Main features

- Patient portal API for submitting and viewing personal requests
- Patient portal and DPO browser workspaces for the synthetic prototype workflow
- DPO queue filters, case assignment, review tasks, review decisions, and patient update history
- DPO overdue, unassigned, and escalated work queues plus audited CSV reports
- Patient withdrawal and clarification-response flows, with reasoned escalation recovery
- Mock-only PARAS/CIMS/ERPS source-search checklist with status and evidence references
- Response-preparation draft assembled from workflow records and requiring staff review
- Immutable, numbered response versions with attributed DPO finalization and separate portal publication
- Patient portal visibility restricted to published responses for the authenticated patient's own cases
- Separate workflow audit and case-activity timelines
- Explicit case resolution outcomes when closing requests
- Simulated role-based bearer authentication for patient and DPO roles
- Configurable prototype response-target calculation by request type
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
  Move a request through workflow transitions such as `start_departmental_search`, `request_legal_review`, `approve_and_close`, `reject`, and `escalate`.
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

   `http://localhost:3000`

5. The Django API and existing browser workspace remain available at:

   `http://localhost:8000`

The new UI starts with a mock role/ID sign-in, a patient request list and submission form, and a DPO queue with filters and workload indicators. The DPO case-detail workflows remain in the existing Django workspace during this first frontend phase. The frontend calls the Django API through same-origin Next.js route handlers; its signed HttpOnly demo session does not authenticate real users. Configure `NEXT_MOCK_SESSION_SECRET` in `.env` for local use. Keep `web` in `DJANGO_ALLOWED_HOSTS` so the frontend container can reach Django using its Compose service name. Response target defaults are not validated legal deadlines. See [API_README.md](API_README.md) for the backend browser and API routes, request examples, and workflow actions. The web container runs database migrations automatically on startup.

To load the synthetic acceptance pack, run `docker compose exec web python manage.py seed_demo_data`. Use the DPO role with any mock staff ID to inspect the queue; use the patient role with `mock-patient-acceptance-access` to view the example published response. Other seeded patient IDs and expected scenarios are listed in [API_README.md](API_README.md). The erasure/retention example is explicitly hypothetical and is not legal guidance.

Run its focused regression checks with `docker compose exec web python manage.py test dsr.tests.test_acceptance_pack dsr.tests.test_workflow`.

## Celery and SLA monitoring

The project includes a Beat schedule that runs `monitor_sla_deadlines` every 15 minutes. That task escalates active requests that pass their configured prototype response target. Confirm the appropriate legal and operational policy before using these targets outside the mock-data demo.

## Repository structure

- `config/` - Django settings, Celery configuration, and routing
- `dsr/` - models, serializers, views, authentication, task logic, and workflow logic
- `docker-compose.yml` - service orchestration for local development
- `Dockerfile` - container build definition
- `.env.example` - environment variable template
- `requirements.txt` - Python dependencies

## Important note

This is a prototype, not a full production compliance system. Browser and API authentication are simulated, outbound patient communications are not sent, source-system results are mocked, and response-target defaults are not legal advice. Do not enter real patient or clinical information. The application does not implement real identity verification, production encryption, regulator reporting, or integrations with PARAS, CIMS, or ERPS.

Response finalization and portal publication are distinct, audited steps. Only
published response content is exposed in a patient's portal. Finalized versions
and publication records are application-level append-only; no second-approver
requirement is assumed pending confirmation of AAR policy.
