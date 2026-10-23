FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 JEVDESK_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY jevdesk ./jevdesk
RUN pip install --no-cache-dir . && useradd --create-home desk && mkdir -p /data && chown desk /data
USER desk
VOLUME ["/data"]
ENTRYPOINT ["jevdesk"]
CMD ["run"]
