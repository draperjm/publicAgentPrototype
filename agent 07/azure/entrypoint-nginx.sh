#!/bin/sh
# Inject the container's actual cluster DNS resolver into the nginx config so
# variable-based proxy_pass can resolve ACA internal service hostnames at
# request time (not at startup, avoiding "host not found" crashes).
set -e
RESOLVER=$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)
sed -i "s/__RESOLVER__/${RESOLVER}/" /etc/nginx/conf.d/default.conf
exec nginx -g "daemon off;"
