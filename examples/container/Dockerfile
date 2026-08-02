FROM python:3.13-slim AS runtime

ARG DEPFIX_VERSION=0.3.0
RUN python -m pip install --no-cache-dir "depfix==${DEPFIX_VERSION}"
WORKDIR /app
COPY .depfix/imports.lock /app/.depfix/imports.lock
RUN depfix install /app/.depfix/imports.lock --frozen
COPY application.py /app/application.py

ENV DEPFIX_FROZEN=1
CMD ["python", "/app/application.py"]
