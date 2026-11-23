FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.txt
RUN useradd --uid 10001 --no-create-home runner && mkdir results && chown runner results
COPY pipeline.py ./
COPY agentguard/ agentguard/
COPY evaluation/ evaluation/
COPY scenarios/ scenarios/
USER 10001
CMD ["python", "-m", "evaluation.runner"]
