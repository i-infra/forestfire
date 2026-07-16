FROM litestream/litestream:latest AS litestream
FROM signal-cli-native:latest AS signal
RUN signal-cli --version | tee /tmp/signal-version

FROM python:3.13-slim AS libbuilder
WORKDIR /app
RUN python3.13 -m venv /app/venv
COPY pyproject.toml README.md /app/
COPY forest /app/forest
COPY mc_util /app/mc_util
RUN /app/venv/bin/pip install /app

FROM python:3.13-slim
WORKDIR /app
RUN mkdir -p /app/data
COPY --from=signal /usr/bin/signal-cli /app/signal-cli
COPY --from=signal /tmp/signal-version /app/
COPY --from=libbuilder /app/venv/lib/python3.13/site-packages /app/
COPY ./litestream_backup_accounts.yml /app/
COPY --from=litestream /usr/local/bin/litestream /app/litestreambin
COPY ./forest /app/forest
COPY ./mc_util /app/mc_util
COPY .git/COMMIT_EDITMSG CHANGELOG.md hellobot.py /app/
ENTRYPOINT ["python3.13", "/app/hellobot.py"]
