FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system rental && adduser --system --ingroup rental rental

COPY requirements.txt constraints.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/constraints.txt

COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY rental_manager /app/rental_manager
COPY static /app/static
COPY scripts/start-container.sh /app/scripts/

RUN mkdir -p /app/data && chown -R rental:rental /app

USER rental

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '10000') + '/healthz', timeout=3).read()" || exit 1

CMD ["sh", "scripts/start-container.sh"]
