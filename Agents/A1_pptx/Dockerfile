FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress fonts-dejavu ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Agents/A1_pptx/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/artifact-core /tmp/artifact-core
RUN pip install --no-cache-dir /tmp/artifact-core
COPY Agents/A1_pptx/backend ./backend
COPY Agents/A1_pptx/ppt_video_agent/*.py ./ppt_video_agent/
ENV AGENT_STORAGE_DIR=/data PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8101
CMD ["uvicorn", "backend.integrated:app", "--host", "0.0.0.0", "--port", "8101", "--no-access-log"]
