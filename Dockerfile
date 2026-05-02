FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests beautifulsoup4 pandas google-cloud-storage lxml

COPY finn_mobility_packages_requests.py .
COPY blocket_mobility_packages_requests.py .
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
