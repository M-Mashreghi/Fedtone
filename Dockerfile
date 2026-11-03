# FedTone runtime image: API, dashboard, scheduler and CLI share one image.
# Configuration and secrets come from the environment at run time; nothing from .env is copied.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FEDTONE_DATABASE__URL=sqlite:////app/data/fedtone.db \
    FEDTONE_BACKUP__DIRECTORY=/app/data/backups \
    FEDTONE_MARKET_DATA__CSV_DIRECTORY=/app/data/market

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[dashboard]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/fedtone-entrypoint
RUN chmod 0755 /usr/local/bin/fedtone-entrypoint \
    && useradd --create-home --uid 10001 fedtone \
    && mkdir -p /app/data \
    && chown -R fedtone:fedtone /app/data

USER fedtone
VOLUME ["/app/data"]
EXPOSE 8000 8501

ENTRYPOINT ["fedtone-entrypoint"]
CMD ["api"]
