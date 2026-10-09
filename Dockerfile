FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --only-binary=:all: -r requirements.txt
COPY threads_bot ./threads_bot
COPY static ./static
COPY data ./data
COPY setting.container.py ./setting.py
CMD exec uvicorn threads_bot.cloud_app:app --host 0.0.0.0 --port "$PORT" --workers 1
