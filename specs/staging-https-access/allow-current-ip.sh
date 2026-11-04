#!/usr/bin/env bash
# Run only via: ssh staging 'bash -s -- <observed-peer-ip> <dry-run|apply>' < this-file
set -euo pipefail
[[ $# == 2 ]] || exit 2
source_ip=$1
mode=$2
[[ $mode == dry-run || $mode == apply ]] || exit 2
python3 - "$source_ip" <<'PY'
import ipaddress, sys
value = sys.argv[1]
address = ipaddress.IPv4Address(value)
assert str(address) == value and address.is_global
PY
[[ ${SSH_CONNECTION%% *} == "$source_ip" ]] || exit 3
systemctl is-active --quiet search-tools-staging.service
systemctl is-active --quiet search-tools-import-worker.service
if systemctl is-active --quiet search-tools-pg.service; then exit 4; fi
[[ $(git -C /srv/search-tools rev-parse HEAD) == e33b0a22463366d5e19842307f31d80696bb1ecc ]] || exit 5
[[ -z $(git -C /srv/search-tools status --porcelain) ]] || exit 5
status=$(sudo -n env LC_ALL=C ufw status verbose)
[[ $status == *'Status: active'* && $status == *'Default: deny (incoming)'* ]] || exit 6
if [[ $mode == dry-run ]]; then
    sudo -n ufw --dry-run allow from "$source_ip" to any port 443 proto tcp comment 'Staging admin HTTPS current network'
else
    sudo -n ufw allow from "$source_ip" to any port 443 proto tcp comment 'Staging admin HTTPS current network'
    sudo -n ufw status verbose
fi
