# Runs the Playwright e2e scripts inside the compose network (service `e2e`, profile `e2e`).
# Build context is the repo root (see .dockerignore): only e2e/ and the DOCX fixture builder are copied.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble
RUN pip install --no-cache-dir playwright==1.65.2 python-docx pillow pypdf lxml
WORKDIR /e2e
COPY backend/tests/__init__.py tests/__init__.py
COPY backend/tests/fixtures tests/fixtures
RUN python -m tests.fixtures.sample /e2e/sample.docx && python -m tests.fixtures.fidelity /e2e/fidelity.docx && python -m tests.fixtures.tables /e2e/tables.docx && python -m tests.fixtures.cover /e2e/cover.docx && python -m tests.fixtures.long /e2e/long.docx 600
COPY e2e/*.py ./
ENV E2E_SAMPLE_DOCX=/e2e/sample.docx E2E_FIDELITY_DOCX=/e2e/fidelity.docx E2E_FIDELITY_PAGES=2 E2E_TABLES_DOCX=/e2e/tables.docx E2E_COVER_DOCX=/e2e/cover.docx E2E_LONG_DOCX=/e2e/long.docx
