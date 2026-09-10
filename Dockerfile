FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg, libopus)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot source code
COPY . .

# Environment defaults
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

CMD ["python", "-u", "ripple_bot_gateway.py"]
