#!/usr/bin/env bash
set -euo pipefail
umask 077

request=/etc/x-ui/update-request
state=/var/lib/x-ui-zh-updates
[[ -f $request && ! -L $request ]] || { rm -f -- "$request"; exit 1; }
read -r run channel extra < <(timeout 2 head -c 32 -- "$request") || { rm -f -- "$request"; exit 1; }
[[ $run =~ ^[0-9]{16,20}$ && $channel =~ ^(stable|dev)$ && -z ${extra:-} ]] || { rm -f -- "$request"; exit 1; }

status=failed
report() {
    local result=$?
    trap - EXIT
    local temp
    temp=$(mktemp -- "$state/.result.XXXXXXXX")
    printf '{"state":"%s","runId":"%s","exitCode":%d,"finishedAt":%s}\n' "$status" "$run" "$result" "$(date +%s)" > "$temp"
    chgrp x-ui-zh "$temp"
    chmod 0640 "$temp"
    mv -fT -- "$temp" "$state/status.json"
    rm -f -- "$request"
    exit "$result"
}
trap report EXIT

args=(--update)
if [[ $channel == dev ]]; then args+=(--dev); fi
bash /usr/local/x-ui/install-online.sh "${args[@]}"
status=success
