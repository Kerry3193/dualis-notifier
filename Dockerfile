FROM python:3.12-slim-bookworm
WORKDIR /notifier
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data

COPY requirements.txt /notifier/
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

RUN groupadd --gid 10001 notifier \
    && useradd --uid 10001 --gid notifier --no-create-home notifier \
    && mkdir /data && chown notifier:notifier /data

COPY *.py entrypoint.sh /notifier/
RUN chmod 0755 /notifier/entrypoint.sh
USER notifier
VOLUME ["/data"]
ENTRYPOINT ["/notifier/entrypoint.sh"]
CMD ["--watch"]
