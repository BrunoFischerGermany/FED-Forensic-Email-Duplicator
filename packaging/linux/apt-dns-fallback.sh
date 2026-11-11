#!/usr/bin/env bash
# Runner (Ubuntu): löst der Paketspiegel nicht auf, öffentliche DNS-Server als Ausweich eintragen.
# Auf GitHub-Runnern fiel die Namensauflösung zeitweise aus (z. B. azure.archive.ubuntu.com).
set -euo pipefail
host=${1:-azure.archive.ubuntu.com}
for _ in 1 2 3; do
  getent hosts "$host" >/dev/null && exit 0
  sleep 5
done
echo "::warning::DNS löst $host nicht auf – nutze 1.1.1.1 / 8.8.8.8"
sudo mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNS=1.1.1.1 8.8.8.8\nFallbackDNS=9.9.9.9\n' | sudo tee /etc/systemd/resolved.conf.d/fed-fallback.conf >/dev/null
sudo systemctl restart systemd-resolved
sleep 2
getent hosts "$host" >/dev/null || { echo "::error::DNS weiterhin gestört"; exit 1; }
