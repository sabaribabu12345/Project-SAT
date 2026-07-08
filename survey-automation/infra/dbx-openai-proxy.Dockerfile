FROM python:3.12-slim

WORKDIR /app

COPY infra/scripts/databricks_openai_proxy.py /app/databricks_openai_proxy.py

EXPOSE 9000

CMD ["python", "/app/databricks_openai_proxy.py"]
