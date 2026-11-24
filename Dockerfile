FROM python:3.12-slim AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --requirement /tmp/requirements.lock

FROM dependencies AS builder
WORKDIR /src
COPY pyproject.toml README.md agents.py utils.py pipeline.py ./
COPY agentguard/ agentguard/
COPY evaluation/ evaluation/
COPY scenarios/ scenarios/
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM dependencies AS runtime
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --no-deps /wheels/*.whl && \
    useradd --uid 10001 --no-create-home runner && mkdir results state && chown runner results state
USER 10001
EXPOSE 8000
CMD ["python", "-m", "evaluation.runner"]
