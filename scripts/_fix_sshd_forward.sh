#!/bin/bash
set -e
CFG=/etc/ssh/sshd_config
cp -a "$CFG" /etc/ssh/sshd_config.bak.seeed-arm || true
if grep -qE '^\s*AllowTcpForwarding' "$CFG"; then
  sed -i 's/^\s*AllowTcpForwarding.*/AllowTcpForwarding yes/' "$CFG"
else
  echo 'AllowTcpForwarding yes' >> "$CFG"
fi
if grep -qE '^\s*GatewayPorts' "$CFG"; then
  sed -i 's/^\s*GatewayPorts.*/GatewayPorts clientspecified/' "$CFG"
else
  echo 'GatewayPorts clientspecified' >> "$CFG"
fi
# Also disable if Match blocks deny
systemctl reload ssh 2>/dev/null || service ssh reload 2>/dev/null || true
echo '--- sshd forward settings ---'
grep -nE 'AllowTcpForwarding|GatewayPorts|AllowStreamLocalForwarding' "$CFG" || true
