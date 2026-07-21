FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROBOT_DOCTOR_CONTAINER=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

EXPOSE 8765
CMD ["robot-doctor-web", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
