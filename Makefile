.PHONY: up down test test-backend test-frontend test-e2e test-e2e-docker
.env:
	cp .env.example .env

up: .env
	docker compose up --build -d

down:
	docker compose down

# backend pytest + Vitest + Playwright happy paths, all in Docker (e2e needs the stack: brought up here)
test: test-backend test-frontend test-e2e-docker

test-backend: .env
	docker compose run --rm --build api pytest -q

# Vitest smoke test runs in the frontend image's `test` stage.
test-frontend:
	docker build --target test -t docgen-frontend-test frontend

# Playwright in the `e2e` compose service (profile e2e) against the compose network; starts the stack first.
test-e2e-docker: .env
	docker compose up --build -d --wait
	E2E_UID=$$(id -u) docker compose --profile e2e run --rm --build e2e

# Same scripts from the host: needs the stack running, host .venv + browsers (see e2e/smoke.py).
test-e2e:
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/smoke.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/docbuilder.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/import.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/invite.py
