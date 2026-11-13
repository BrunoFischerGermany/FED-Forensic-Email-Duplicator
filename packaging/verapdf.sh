#!/usr/bin/env bash
# Prüft alle PDFs eines Ordners mit veraPDF auf PDF/A-3b (offizielles Docker-Image, nur Linux-Runner).
# Aufruf: bash packaging/verapdf.sh <ordner>
set -euo pipefail
dir="$(cd "${1:?Ordner fehlt}" && pwd)"
image="verapdf/cli:v1.28.2"
mapfile -t files < <(cd "$dir" && find . -maxdepth 1 -name '*.pdf' -printf '/data/%P\n' | sort)
if [ "${#files[@]}" -eq 0 ]; then
  echo "::error::keine PDFs in $dir"
  exit 1
fi
docker pull -q "$image" >/dev/null
out="$(docker run --rm -v "$dir:/data:ro" "$image" --flavour 3b --format text "${files[@]}" || true)"
echo "$out"
pass="$(grep -c '^PASS ' <<<"$out" || true)"
if [ "$pass" -ne "${#files[@]}" ]; then
  # Details der Regelverstöße für die fehlgeschlagenen Dateien
  docker run --rm -v "$dir:/data:ro" "$image" --flavour 3b --format text --verbose "${files[@]}" | grep -v '^PASS ' || true
  echo "::error::PDF/A-3b: nur $pass von ${#files[@]} PDFs gültig"
  exit 1
fi
echo "PDF/A-3b: alle ${#files[@]} PDFs gültig"
