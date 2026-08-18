FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 127.0.0.1 (the app's own default) isn't reachable from outside the
# container, so bind to all interfaces here. Set DRONE_API_KEY when running
# this image anywhere it's reachable beyond your own machine.
ENV DRONE_HOST=0.0.0.0
ENV DRONE_PORT=8000

EXPOSE 8000

VOLUME ["/app/data"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
