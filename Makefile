.PHONY: up down test test-backend test-frontend
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
# TODO(milestone 3+): Playwright e2e happy path.
test-frontend:
	docker build --target test -t docgen-frontend-test frontend
