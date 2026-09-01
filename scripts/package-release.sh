#!/usr/bin/env bash
set -euo pipefail
PROJECT_NAME="evoharness-alert"
mkdir -p target
tar --exclude='.git' --exclude='target' --exclude='data' -czf "target/${PROJECT_NAME}.tar.gz" .
echo "已打包 target/${PROJECT_NAME}.tar.gz"
