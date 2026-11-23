.PHONY: up down test test-backend test-frontend test-e2e
.env:
	cp .env.example .env

up: .env
	docker compose up --build -d

down:
	docker compose down

test: test-backend test-frontend

test-backend: .env
	docker compose run --rm --build api pytest -q

# Vitest smoke test runs in the frontend image's `test` stage.
test-frontend:
	docker build --target test -t docgen-frontend-test frontend

# Needs the stack running; host venv + browsers (see e2e/smoke.py). Not part of `make test`.
test-e2e:
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/smoke.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/docbuilder.py
