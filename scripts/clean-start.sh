#!/bin/sh
# Clean-machine check in an isolated compose project (never touches the `docgen` project):
# fresh git clone -> .env from .env.example (other ports) -> up --build --wait -> hit the app as a seeded user ->
# back up the volumes -> `down -v` -> restore them -> up again WITHOUT seeding -> data still there -> tear down.
# Usage: scripts/clean-start.sh   (WEB_PORT / MAIL_UI_PORT override the defaults 8195 / 8126)
set -eu
P=docgen-clean; WEB=${WEB_PORT:-8195}; MAIL=${MAIL_UI_PORT:-8126}
SRC=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
T=$(mktemp -d "${TMPDIR:-/tmp}/docgen-clean.XXXXXX")
dc() { docker compose -p "$P" "$@"; }
cleanup() { (cd "$T/repo" 2>/dev/null && dc down -v --remove-orphans >/dev/null 2>&1) || true
            for v in pgdata repos files; do docker volume rm "${P}_$v" >/dev/null 2>&1 || true; done; rm -rf "$T"; }
trap cleanup EXIT
git clone -q "$SRC" "$T/repo" && cd "$T/repo"
cp .env.example .env
sed -i "s/^WEB_PORT=.*/WEB_PORT=$WEB/; s/^MAIL_UI_PORT=.*/MAIL_UI_PORT=$MAIL/; s#^PUBLIC_URL=.*#PUBLIC_URL=http://localhost:$WEB#" .env
grep -q '^SEED_DEMO=1' .env || { echo "FAIL: .env.example lacks SEED_DEMO=1"; exit 1; }
echo "== up --build (project $P, web :$WEB)"; dc up --build -d --wait

U=http://localhost:$WEB; J=$T/cookies
check() { # login as the seeded owner and look at the data
  rm -f "$J"; curl -fsS -c "$J" -b "$J" "$U/api/auth/csrf" >/dev/null
  C=$(awk '$6=="docgen_csrf"{print $7}' "$J")
  curl -fsS -c "$J" -b "$J" -H "X-CSRF-Token: $C" -H 'Content-Type: application/json' \
       -d '{"email":"owner@example.com","password":"demo-password-123"}' "$U/api/auth/login" | grep -q '"role":"owner"'
  curl -fsS -b "$J" "$U/api/folders" | grep -q Security
  curl -fsS -b "$J" "$U/api/documents" | grep -q 'Operations Manual'
  curl -fsS -b "$J" "$U/api/imports" | grep -q 'Imported sample'
}
curl -fsS "$U/api/health" | grep -q true && echo "ok health"
curl -fsS "$U/" | grep -q 'id="root"' && echo "ok frontend served"
curl -fsS "http://localhost:$MAIL/" >/dev/null && echo "ok mailpit"
check && echo "ok seeded data via login as owner@example.com"
dc logs api | grep -q 'seed: demo data created' && echo "ok seed ran on first start"

echo "== backup volumes, down -v, restore"
dc stop api db >/dev/null
for v in pgdata repos files; do
  docker run --rm -v "${P}_$v:/data:ro" -v "$T:/backup" alpine tar czf "/backup/$v.tgz" -C /data .
done
dc down -v >/dev/null
for v in pgdata repos files; do
  docker volume create "${P}_$v" >/dev/null
  docker run --rm -v "${P}_$v:/data" -v "$T:/backup:ro" alpine tar xzf "/backup/$v.tgz" -C /data
done
sed -i 's/^SEED_DEMO=.*/SEED_DEMO=0/' .env  # prove the data comes from the backup, not from the seed
dc up -d --wait
check && echo "ok data restored from volume backups (seed off)"
echo "CLEAN START + BACKUP/RESTORE: PASS"
