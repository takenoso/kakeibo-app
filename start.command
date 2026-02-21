#!/bin/bash
cd "$(dirname "$0")"
echo "家計簿アプリを起動します..."
echo "ブラウザで http://localhost:5000 を開いてください"
echo ""
open http://localhost:8080
python3 app.py
