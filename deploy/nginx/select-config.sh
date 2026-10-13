#!/bin/sh
# Serve HTTPS once a Let's Encrypt certificate exists; until then serve plain
# HTTP so certbot can complete its webroot challenge.
set -e
if [ -f /etc/letsencrypt/live/locatediscount.ng/fullchain.pem ]; then
  cp /etc/nginx/templates-ld/https.conf /etc/nginx/conf.d/default.conf
  echo "select-config: HTTPS"
else
  cp /etc/nginx/templates-ld/http.conf /etc/nginx/conf.d/default.conf
  echo "select-config: HTTP only (no certificate yet)"
fi

# Reload periodically in the background so renewed certificates are picked up.
(while :; do sleep 6h; nginx -s reload; done) &
