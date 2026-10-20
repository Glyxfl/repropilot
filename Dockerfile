FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8765
# Container-only binding; the host boundary still rejects non-local Host headers.
CMD ["python", "-m", "uvicorn", "repropilot.server:app", "--host", "0.0.0.0", "--port", "8765"]
