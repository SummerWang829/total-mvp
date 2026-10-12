#!/usr/bin/env bash
# Downloads the LiveKit video server (one file) into bin/. Used for local development and tests.
set -e
cd "$(dirname "$0")/.."
VER=1.8.4
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)  P=linux_amd64 ;;
  Linux-aarch64) P=linux_arm64 ;;
  Darwin-arm64)  P=darwin_arm64 ;;
  Darwin-x86_64) P=darwin_amd64 ;;
  *) echo "这个系统暂不支持自动下载（Windows 请用 WSL，或到 https://github.com/livekit/livekit/releases 手动下载）"; exit 1 ;;
esac
mkdir -p bin
echo "下载 LiveKit $VER ($P) ..."
curl -fsSL "https://github.com/livekit/livekit/releases/download/v$VER/livekit_${VER}_${P}.tar.gz" | tar xz -C bin livekit-server
chmod +x bin/livekit-server
bin/livekit-server --version
