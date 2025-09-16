#!/bin/bash
export NODE_OPTIONS="--max-old-space-size=4096"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

cd "${PROJECT_DIR}"

echo ">>> 清理旧的构建结果..."
rm -rf packages/agentsociety/agentsociety/_dist \
       packages/agentsociety-community/agentsociety_community/_dist \
       frontend/dist || true

echo ">>> 安装前端依赖 (npm ci)..."
cd frontend
npm ci

echo ">>> 开始前端构建 (npm run build)..."
npm run build

echo ">>> 拷贝构建结果到 _dist 目录..."
cd ..
mkdir -p packages/agentsociety/agentsociety/_dist \
         packages/agentsociety-community/agentsociety_community/_dist

cp -r frontend/dist/* packages/agentsociety/agentsociety/_dist/
cp -r frontend/dist/* packages/agentsociety-community/agentsociety_community/_dist/

echo ">>> ✅ 前端重新打包完成，记得刷新浏览器查看效果。"
