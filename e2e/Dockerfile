# Runs the Playwright e2e scripts inside the compose network (service `e2e`, profile `e2e`).
# Build context is the repo root (see .dockerignore): only e2e/ and the DOCX fixture builder are copied.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble
RUN pip install --no-cache-dir playwright==1.65.2 python-docx pillow
WORKDIR /e2e
COPY backend/tests/__init__.py tests/__init__.py
COPY backend/tests/fixtures tests/fixtures
RUN python -m tests.fixtures.sample /e2e/sample.docx
COPY e2e/*.py ./
ENV E2E_SAMPLE_DOCX=/e2e/sample.docx
