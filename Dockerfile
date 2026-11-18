FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[embed,cv]"
COPY config ./config
COPY docs ./docs
ENTRYPOINT []
CMD ["job-radar", "--help"]
