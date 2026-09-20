#!/bin/sh
set -eu
cp /opt/astock-radar-v866/releases/design3_20260905_142250/rollback/previous_override.conf /etc/systemd/system/astock-v866-workbench.service.d/zz-fusion2.conf
systemctl daemon-reload
systemctl restart astock-v866-workbench.service
systemctl is-active astock-v866-workbench.service
