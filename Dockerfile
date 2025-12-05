FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    git \
    socat \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 设置时区
ENV TZ=Asia/Shanghai

# 复制依赖并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 下载 Xray Core
RUN mkdir -p bin && \
    curl -L -o /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip && \
    unzip /tmp/xray.zip -d /tmp/xray && \
    mv /tmp/xray/xray bin/xray && \
    chmod +x bin/xray && \
    rm -rf /tmp/xray*

# 复制项目文件
COPY . .

# 赋予脚本执行权限
RUN chmod +x entrypoint.sh

# 暴露端口 (如果使用 host 模式，这个声明主要用于文档)
EXPOSE 2053

# 启动命令
ENTRYPOINT ["./entrypoint.sh"]
