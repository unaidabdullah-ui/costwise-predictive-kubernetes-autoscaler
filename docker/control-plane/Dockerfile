# OPTIONAL: CostWise control plane as a container (kubernetes/optional/control-plane.yaml
# or docker-compose). The default demo runs it directly on the laptop instead.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/control-plane.txt

COPY common/ common/
COPY autoscaler/ autoscaler/
COPY costwise/ costwise/
COPY marketplace/ marketplace/
COPY workload_generator/ workload_generator/
COPY ml/__init__.py ml/
COPY ml/src/ ml/src/
COPY ml/models/ ml/models/

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin costwise && mkdir -p /app/.costwise \
    && chown -R 10001:10001 /app/.costwise /app/ml/models
USER 10001:10001
ENV DASHBOARD_HOST=0.0.0.0 COSTWISE_STATE_DIR=/app/.costwise
EXPOSE 8000
CMD ["python", "-m", "costwise"]
