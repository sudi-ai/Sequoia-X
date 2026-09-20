#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/v8-radar
PACKAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$APP_DIR" "$APP_DIR/data_v8" "$APP_DIR/logs"
cp -a "$PACKAGE_DIR"/. "$APP_DIR"/

if command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip unzip gcc gcc-c++ python3-devel
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip unzip gcc gcc-c++ python3-devel
else
  apt-get update
  apt-get install -y python3 python3-venv python3-pip unzip build-essential
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/cloud_deploy/requirements_cloud.txt"

install -m 0644 "$APP_DIR/cloud_deploy/v8-radar.service" /etc/systemd/system/v8-radar.service
systemctl daemon-reload
systemctl enable v8-radar.service

echo "V8 code installed. Configure /etc/v8-radar.env before starting the service."

