# Chat API for the Global Sustainability Explorer.
# Built for Hugging Face Spaces (Docker SDK), which serves on port 7860.
FROM python:3.12-slim

RUN useradd -m -u 1000 app
WORKDIR /home/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gse_api ./gse_api

USER app
ENV DATA_DIR=/tmp \
    PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn gse_api.app:app --host 0.0.0.0 --port ${PORT}"]
