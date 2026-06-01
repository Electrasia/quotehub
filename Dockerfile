FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    poppler-utils \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY VERSION ./VERSION
COPY config.json .

# Note: data/ is not copied from the build context because it's gitignored
# (the directory would not exist on a fresh clone). The required subdirectories
# are created by the mkdir line below, and all real data lives in the
# `quodb_data` Docker volume at runtime.
RUN mkdir -p /app/data/temp /app/data/archive /app/data/images

ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/GIT_COMMIT

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
