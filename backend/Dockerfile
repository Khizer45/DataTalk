FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home --uid 10001 datatalk
USER datatalk
EXPOSE 8000
# PORT is set by hosts such as Render. FORWARDED_ALLOW_IPS must be "*" behind a trusted proxy (Render)
# so rate limits see real client IPs; the default trusts only localhost.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
