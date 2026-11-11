#!/usr/bin/env bash
# FED – Linux-Build im Container (GPL-3.0-only)
#
# Läuft in einem älteren Ubuntu-Container (siehe build.yml, docker run --network host). Die glibc des
# Containers bestimmt, auf welchen Distributionen das gebaute FED läuft – unabhängig davon, welche
# Ubuntu-Versionen GitHub als Runner anbietet.
#
# Erwartet: Arbeitsordner = Repository, FED_VERSION gesetzt, optional GITHUB_OUTPUT.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "::group::Systempakete"
apt-get update
apt-get install -y --no-install-recommends ca-certificates git curl xz-utils build-essential \
  patchelf ccache libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libfreetype6 \
  libdbus-1-3 libglib2.0-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxrender1 \
  libxi6 libsm6 libice6 fonts-dejavu-core software-properties-common gpg-agent
echo "::endgroup::"

echo "::group::Python 3.14 (deadsnakes)"
add-apt-repository -y ppa:deadsnakes/ppa
apt-get install -y --no-install-recommends python3.14 python3.14-dev python3.14-venv
python3.14 -m venv /opt/fed-python
export PATH=/opt/fed-python/bin:$PATH
ldd --version | head -1
python --version
echo "::endgroup::"

# Repository gehört dem Runner-Benutzer, nicht root
git config --global --add safe.directory "$PWD"

python packaging/set_version.py "$FED_VERSION"
python -m pip install --upgrade pip
python -m pip install ".[build]"
python packaging/build.py

archive=$(ls dist/FED-*-linux-*.tar.gz | head -1)
python packaging/selftest.py "$archive"
# Dateien wieder dem Runner-Benutzer geben (Cache, Upload, Aufräumen)
if [ -n "${HOST_UID:-}" ]; then
  chown -R "$HOST_UID:$HOST_GID" dist build "$HOME/.cache/Nuitka" 2>/dev/null || true
fi
