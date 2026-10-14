#!/bin/sh
set -eu
case "${SSH_ORIGINAL_COMMAND:-}" in
  status|resources|start) exec /usr/bin/sudo -n /usr/local/libexec/sentinel-asset-command "$SSH_ORIGINAL_COMMAND" ;;
  *) printf '%s\n' 'Only status, resources and start are available'; exit 1 ;;
esac
