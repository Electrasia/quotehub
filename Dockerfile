FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    gosu \
    poppler-utils \
    ghostscript \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for the application
RUN addgroup --system --gid 1001 quodb && \
    adduser --system --uid 1001 --ingroup quodb --no-create-home quodb

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
RUN mkdir -p /app/data/temp /app/data/archive /app/data/images && \
    chown -R quodb:quodb /app/data

ARG GIT_COMMIT=unknown
RUN echo "${GIT_COMMIT}" > /app/GIT_COMMIT

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
