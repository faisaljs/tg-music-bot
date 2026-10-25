FROM python:3.13

# Install ffmpeg, git, curl, ca-certificates
RUN apt-get update && \
    apt-get install -y ffmpeg git curl unzip ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Deno runtime
RUN curl -fsSL https://deno.land/install.sh | sh && \
    mv /root/.deno/bin/deno /usr/local/bin/deno

# Configure git with rebase true
RUN git config --global pull.rebase true

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

RUN chown -R root:root /root/.mozilla 2>/dev/null || true

# Run Python as PID 1 so Docker's stop signal reaches its shutdown handlers.
CMD ["python3", "main.py"]
