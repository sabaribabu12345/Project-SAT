FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY apps /app/apps
COPY infra /app/infra
COPY cds_sql_query_registry.md /app/cds_sql_query_registry.md

RUN pip install --no-cache-dir -e .

EXPOSE 8010

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
