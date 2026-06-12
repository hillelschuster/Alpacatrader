FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create data and log directories
RUN mkdir -p /app/data /app/logs

# Default command (override with docker run args)
ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "mock", "--once"]
