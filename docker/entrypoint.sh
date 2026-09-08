#!/bin/sh
set -eu
if [ "$(id -u)" = "0" ]; then
  mkdir -p /var/lib/midas/raw /var/lib/midas/lake
  chown 65532:65532 /var/lib/midas/raw /var/lib/midas/lake
  exec setpriv --reuid=65532 --regid=65532 --clear-groups "$@"
fi
exec "$@"
