FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# pandoc: pinned official static binary (Debian's 3.1 writes no grid-table spans); sha256 checked, only the binary is kept
ARG PANDOC_VERSION=3.14
ARG PANDOC_SHA256=c5f4720cdefb3f7bdc5d07202ef579feef0d1370773c873804dd8fdaa8c4f1b8
RUN python -c "import urllib.request as u; u.urlretrieve('https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-amd64.tar.gz', '/tmp/p.tgz')" \
 && echo "${PANDOC_SHA256}  /tmp/p.tgz" | sha256sum -c - \
 && tar -xzf /tmp/p.tgz -C /usr/local --strip-components=1 pandoc-${PANDOC_VERSION}/bin/pandoc && rm /tmp/p.tgz
RUN useradd -m app && mkdir /repos /files && chown app /repos /files
COPY . .
USER app
CMD ["sh", "entrypoint.sh"]
