#!/bin/sh
# Sign-up is closed, so create one throwaway user+org per e2e script through the bootstrap CLI.
# Usage: E2E_TAG=<tag> E2E_PASSWORD=<pw> scripts/e2e-users.sh   (password goes via stdin, never argv)
set -e
for n in smoke docbuilder import invite fidelity basedoc editor round7 tables round9 round12 round13 round14 round15 locks lockstimeout focus prefs round19; do
  nl='\n'; [ "$n" = round15 ] && nl='\r\n'  # round15: a Windows-style CRLF from the pipe must not become part of the password
  printf "%s$nl" "$E2E_PASSWORD" | docker compose exec -T api python -m app.manage create-user \
    --email "e2e-$E2E_TAG-$n@example.com" --name "E2E User" --org "E2E $E2E_TAG-$n" --password-stdin
done
