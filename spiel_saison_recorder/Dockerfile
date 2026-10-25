ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}

RUN apk add --no-cache ffmpeg python3 py3-aiohttp
COPY app /app
WORKDIR /app
CMD ["python3", "-u", "/app/server.py"]
