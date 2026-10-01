FROM python:3.12-slim

ARG TARGETARCH=amd64

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    git \
    socat \
    tzdata \
    unzip \
    tar \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p bin && \
    case "${TARGETARCH}" in \
      amd64) XRAY_ASSET="Xray-linux-64.zip"; SB_ARCH="amd64" ;; \
      arm64) XRAY_ASSET="Xray-linux-arm64-v8a.zip"; SB_ARCH="arm64" ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    curl -fL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/latest/download/${XRAY_ASSET}" && \
    mkdir -p /tmp/xray && \
    unzip -oq /tmp/xray.zip -d /tmp/xray && \
    mv /tmp/xray/xray bin/xray && \
    chmod +x bin/xray && \
    rm -rf /tmp/xray /tmp/xray.zip && \
    SB_VERSION="$(curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest | grep '"tag_name"' | head -n1 | cut -d '"' -f4 | sed 's/^v//')" && \
    SB_DIR="sing-box-${SB_VERSION}-linux-${SB_ARCH}" && \
    curl -fL -o /tmp/sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${SB_VERSION}/${SB_DIR}.tar.gz" && \
    tar -xzf /tmp/sing-box.tar.gz -C /tmp && \
    mv "/tmp/${SB_DIR}/sing-box" bin/sing-box && \
    chmod +x bin/sing-box && \
    rm -rf "/tmp/${SB_DIR}" /tmp/sing-box.tar.gz

COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 2053

ENTRYPOINT ["./entrypoint.sh"]
