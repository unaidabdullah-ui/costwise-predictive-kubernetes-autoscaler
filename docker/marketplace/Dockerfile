# COSTWISE MARKETPLACE + traffic generator image (same image, different command).
# Built inside Minikube by start-demo:  minikube image build -t costwise-marketplace:2.1.0 -f docker/marketplace/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements/marketplace.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY common/__init__.py common/env.py common/logging_utils.py common/jsonutil.py common/promtext.py common/
COPY marketplace/ marketplace/
COPY workload_generator/__init__.py workload_generator/__main__.py workload_generator/app.py \
     workload_generator/engine.py workload_generator/plans.py workload_generator/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin costwise
USER 10001:10001
EXPOSE 8080 8089
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" || exit 1
CMD ["python", "-m", "marketplace"]
