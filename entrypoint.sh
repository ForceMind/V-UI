#!/bin/bash
set -e

# 确保数据目录存在
mkdir -p data

# 初始化数据库 (如果不存在)
if [ ! -f "data/v-ui.db" ]; then
    echo "Initializing database..."
    python bin/init_db.py
fi

# 启动应用
echo "Starting V-UI..."
exec python main.py
