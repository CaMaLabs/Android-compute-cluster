FROM python:3.12-slim

WORKDIR /app
COPY coordinator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY coordinator/*.py ./

ENV SWARM_DB=/data/swarm.db \
    SWARM_ARTIFACT_DIR=/data/artifacts
VOLUME ["/data"]
EXPOSE 8765

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers"]
