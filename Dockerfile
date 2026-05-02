FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY finn_mobility_packages_requests.py .

ENV PYTHONUNBUFFERED=1

CMD ["python", "finn_mobility_packages_requests.py"]
