# Frnkfurter ECB Rating — pipeline runtime image
#
# One image, several entry points: it can run ingestion (extract.py /
# load.py), dbt (dbt build), or the quality checks (quality/*.py).
# GitHub Actions runs these steps directly on the runner (see
# .github/workflows/), NOT via this image — this Dockerfile exists so
# you can run the exact same pipeline locally or on any other
# scheduler without reinstalling dependencies by hand.
#
# Usage:
#   docker build -t pdo .
#   docker run --rm -v ~/.config/gcloud:/root/.config/gcloud:ro \
#     pdo python ingestion/extract.py --mode latest
#   docker run --rm -v ~/.config/gcloud:/root/.config/gcloud:ro \
#     pdo dbt build --project-dir dbt --profiles-dir dbt
#   docker run --rm -v ~/.config/gcloud:/root/.config/gcloud:ro \
#     pdo python quality/freshness.py --project my-gcp-project

FROM python:3.12-slim

# git is needed by `dbt deps` to fetch packages (dbt_utils) from GitHub.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestion/ ingestion/
COPY quality/ quality/
COPY dbt/ dbt/
COPY tests/ tests/

ENV PYTHONUNBUFFERED=1 \
    DBT_PROFILES_DIR=/app/dbt

# No default CMD that assumes GCP credentials are present — this
# image is meant to be invoked with an explicit command (see Usage
# above), not run standalone.
CMD ["bash"]
