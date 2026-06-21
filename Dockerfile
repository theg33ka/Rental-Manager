FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN python -c "import importlib.metadata as md; import aiohttp, cron.scheduler_provider; print('hermes-agent', md.version('hermes-agent'), 'runtime deps ok')"

COPY . /app

EXPOSE 10000

CMD ["sh", "scripts/start-container.sh"]
