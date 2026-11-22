ARG REELBENCH_DEPS_IMAGE=python:3.11-slim
FROM ${REELBENCH_DEPS_IMAGE} AS dependencies
USER root
WORKDIR /build/reelbench
COPY requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt && python -m pip check

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 REELBENCH_DATA=/var/lib/reelbench
WORKDIR /opt/reelbench
COPY --from=dependencies /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY reelbench ./reelbench
COPY static ./static
RUN useradd -U -u 11519 reelbench && install -d -o reelbench -g reelbench /var/lib/reelbench
USER reelbench
EXPOSE 8000
CMD ["python", "-m", "reelbench"]
