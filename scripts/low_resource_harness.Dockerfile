FROM alpine:3.22
ARG HARNESS_UID
ARG HARNESS_GID
RUN apk add --no-cache python3 python3-dev py3-pip cargo rust build-base libffi-dev openssl-dev ca-certificates
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN python3 -m venv /opt/harness \
    && /opt/harness/bin/pip install -r /tmp/requirements-runtime.txt \
    && /opt/harness/bin/pip check \
    && mkdir /work \
    && chown "${HARNESS_UID}:${HARNESS_GID}" /work
WORKDIR /src
