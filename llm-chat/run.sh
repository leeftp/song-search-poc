#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "===== LLM Chat 起動スクリプト ====="

# .env ファイルがなければサンプルからコピー
if [ ! -f .env ]; then
  echo "[INFO] .env が見つかりません。.env.example からコピーします。"
  cp .env.example .env
  echo "[INFO] .env を編集してAPIキーを設定してください。"
fi

# 依存パッケージのインストール確認
if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "[INFO] 依存パッケージをインストールします..."
  pip3 install -r requirements.txt
fi

# certs/ に証明書があればHTTPSで起動（音声入力はセキュアコンテキストが必須）
SSL_ARGS=""
SCHEME="http"
if [ -f certs/cert.pem ] && [ -f certs/key.pem ]; then
  SSL_ARGS="--ssl-certfile certs/cert.pem --ssl-keyfile certs/key.pem"
  SCHEME="https"
fi

echo "[INFO] サーバーを起動します: ${SCHEME}://localhost:8000"
echo "[INFO] 終了するには Ctrl+C を押してください"
echo ""

python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload $SSL_ARGS
