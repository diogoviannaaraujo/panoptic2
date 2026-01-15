FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default environment variables
ENV RECORDINGS_DIR=/recordings
ENV VLLM_API_URL=http://vllm:8000/v1/chat/completions
ENV SERVER_PORT=8080

CMD ["python", "main.py"]

