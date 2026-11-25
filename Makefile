.PHONY: up down test test-backend test-frontend test-e2e test-e2e-docker
.env:
	cp .env.example .env

up: .env
	docker compose up --build -d

down:
	docker compose down

# backend pytest + Vitest + Playwright happy paths, all in Docker (e2e needs the stack: brought up here)
test: test-backend test-frontend test-e2e-docker test-e2e-oidc

test-backend: .env
	docker compose run --rm --build api pytest -q

# Vitest smoke test runs in the frontend image's `test` stage.
test-frontend:
	docker build --target test -t docgen-frontend-test frontend

# Playwright in the `e2e` compose service (profile e2e) against the compose network; starts the stack first.
test-e2e-docker: .env
	docker compose up --build -d --wait
	export E2E_TAG=$$(date +%s) E2E_PASSWORD=$$(head -c 18 /dev/urandom | base64 | tr -d '/+=') E2E_UID=$$(id -u); \
	scripts/e2e-users.sh && docker compose --profile e2e run --rm --build e2e

# OIDC e2e: throwaway stack (project docgen-oidc, own ports/volumes) with AUTH_MODE=oidc and the mock IdP; removed afterwards.
OIDC_ENV = AUTH_MODE=oidc OIDC_ISSUER=http://idp:9000 OIDC_CLIENT_ID=docgen OIDC_CLIENT_SECRET=mock-secret \
	OIDC_PROVIDER_NAME="Mock IdP" PUBLIC_URL=http://web WEB_PORT=18094 MAIL_UI_PORT=18026 SEED_DEMO=0 \
	OIDC_ROLE_MAP=docgen-admins=admin OIDC_GROUPS_CLAIM=groups
test-e2e-oidc: .env
	$(OIDC_ENV) docker compose -p docgen-oidc --profile oidc-test up --build -d --wait idp web
	$(OIDC_ENV) E2E_UID=$$(id -u) docker compose -p docgen-oidc --profile oidc-test --profile e2e run --rm --build e2e python oidc.py; \
	rc=$$?; $(OIDC_ENV) docker compose -p docgen-oidc --profile oidc-test --profile e2e down -v >/dev/null 2>&1; exit $$rc

# Same scripts from the host: needs the stack running, host .venv + browsers (see e2e/smoke.py).
test-e2e:  # needs E2E_TAG and E2E_PASSWORD, users via: E2E_TAG=.. E2E_PASSWORD=.. scripts/e2e-users.sh
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/smoke.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/docbuilder.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/import.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/invite.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/fidelity.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/basedoc.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/editor.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/round7.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/tables.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/round9.py
	PLAYWRIGHT_BROWSERS_PATH=$(CURDIR)/.playwright .venv/bin/python e2e/round15.py
