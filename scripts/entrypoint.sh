#!/bin/sh
set -eu
umask 077
mkdir -p /data
if [ "$(id -u)" = 0 ]; then
  chown -R portal:portal /data
  exec gosu portal "$@"
fi
exec "$@"
