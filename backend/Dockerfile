FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -m app && mkdir /repos /files && chown app /repos /files
COPY . .
USER app
CMD ["sh", "entrypoint.sh"]
