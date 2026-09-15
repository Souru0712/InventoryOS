# One image, two roles: the web process (the default command) and the scheduler
# (`python -m app.cli scheduler`). Run many of the first and exactly one of the second.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8000 \
    WEB_CONCURRENCY=1

WORKDIR /app

# Dependencies before code, so a code change does not reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > requirements.txt \
    && pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN useradd --create-home --uid 10001 stockpulse \
    && mkdir -p /app/var/uploads \
    && chown -R stockpulse /app/var
USER stockpulse

EXPOSE 8000

# Behind a platform's TLS proxy, trust X-Forwarded-* so redirects and cookies see https.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers \"$WEB_CONCURRENCY\" --proxy-headers --forwarded-allow-ips '*'"]
