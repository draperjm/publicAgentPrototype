#!/bin/sh
# Only substitute our specific variables, leaving nginx variables ($uri, $http_host, etc.) intact
envsubst '${DECOMPOSER_HOST} ${ORCHESTRATOR_HOST}' \
  < /etc/nginx/templates/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
