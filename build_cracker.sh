#!/bin/bash
# Build the native channel cracker (optional; the client falls back to Python).
# Needs OpenSSL. macOS: brew install openssl@3
set -e
cd "$(dirname "$0")"
if command -v brew >/dev/null && brew --prefix openssl@3 >/dev/null 2>&1; then
  SSL=$(brew --prefix openssl@3)
  cc -O3 -o channel_crack channel_crack.c -I"$SSL/include" -L"$SSL/lib" -lcrypto -lpthread
else
  cc -O3 -o channel_crack channel_crack.c -lcrypto -lpthread   # Linux / system OpenSSL
fi
echo "built ./channel_crack"
