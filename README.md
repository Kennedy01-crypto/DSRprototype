# Kenya DPA 2019 DSR prototype

This repository is a Docker-first Django 5 project. Docker Compose provisions the API, PostgreSQL, Redis, Celery worker, and Celery Beat without requiring local Python, PostgreSQL, or Redis installation.

The prototype authentication format is `Authorization: Bearer patient:<patient_id>` for portal requests and `Authorization: Bearer dpo:<operator_id>` for DPO console requests. These tokens deliberately model an upstream-verified identity; replace the parser with the portal's real JWT verification before deployment.

Copy `.env.example` to `.env`, replace the development secrets, then run `docker compose up --build`. The API is available at `http://localhost:8000`.

The web container applies migrations on startup. Register `monitor_sla_deadlines` with Celery Beat in the host project's Celery schedule before production use.