#!/bin/bash
# 多模型对比工具 - 启动脚本

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  多模型对比工具 - 启动"
echo "=========================================="

if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo "[错误] 未找到 node，请先安装 Node.js 18+"
    exit 1
fi

echo ""
echo "检查后端依赖..."
cd backend
if [ ! -d "venv" ]; then
    echo "  创建虚拟环境..."
    python3 -m venv venv
fi
source venv/bin/activate
python3 -m pip install -r requirements.txt -q
echo "  后端依赖已就绪"

if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
    echo "  已加载 .env 配置"
else
    echo "  [提示] 未找到 .env 文件，请复制 .env.example 并填入 API Key"
fi

echo ""
echo "启动后端 (端口 6364)..."
python app.py &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"
cd ..

echo ""
echo "检查前端依赖..."
cd frontend
if [ ! -d "node_modules" ]; then
    echo "  安装前端依赖..."
    npm install
fi
echo "  前端依赖已就绪"

echo ""
echo "启动前端 (端口 6363)..."
npm run dev &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"
cd ..

echo ""
echo "=========================================="
echo "  全部启动完成"
echo "  前端: http://127.0.0.1:6363"
echo "  后端: http://127.0.0.1:6364"
echo "  API文档: http://127.0.0.1:6364/docs"
echo "  按 Ctrl+C 停止"
echo "=========================================="

wait
