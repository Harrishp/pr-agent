#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=".env.gitea.local"
PORT_OVERRIDE=""
PYTHON_BIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --port)
      PORT_OVERRIDE="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--env-file .env.gitea.local] [--port 3000] [--python /path/to/python]" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [[ "${ENV_FILE}" != /* ]]; then
  ENV_FILE="${REPO_ROOT}/${ENV_FILE}"
fi

if [[ -f "${ENV_FILE}" ]]; then
  line_number=0
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line_number=$((line_number + 1))
    trimmed="${line#"${line%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"

    if [[ -z "${trimmed}" || "${trimmed}" == \#* ]]; then
      continue
    fi

    if [[ ! "${trimmed}" =~ ^[A-Za-z_][A-Za-z0-9_]*(__[A-Za-z0-9_]+)*= ]]; then
      echo "Invalid env entry at ${ENV_FILE}:${line_number}. Expected KEY=value with double-underscore config names." >&2
      exit 1
    fi

    name="${trimmed%%=*}"
    value="${trimmed#*=}"

    if [[ ${#value} -ge 2 ]]; then
      first_char="${value:0:1}"
      last_char="${value: -1}"
      if [[ ("${first_char}" == '"' && "${last_char}" == '"') || ("${first_char}" == "'" && "${last_char}" == "'") ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    export "${name}=${value}"
  done < "${ENV_FILE}"
fi

export CONFIG__GIT_PROVIDER="${CONFIG__GIT_PROVIDER:-gitea}"
if [[ "${CONFIG__GIT_PROVIDER}" != "gitea" ]]; then
  echo "CONFIG__GIT_PROVIDER must be 'gitea' for the Gitea webhook server." >&2
  exit 1
fi

missing=()
for name in GITEA__URL GITEA__PERSONAL_ACCESS_TOKEN GITEA__WEBHOOK_SECRET OPENAI__KEY; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("${name}")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing required environment variables: ${missing[*]}." >&2
  echo "Copy .env.gitea.local.example to .env.gitea.local and fill them in." >&2
  exit 1
fi

if [[ -n "${PORT_OVERRIDE}" ]]; then
  export PORT="${PORT_OVERRIDE}"
else
  export PORT="${PORT:-3000}"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Python was not found. Install Python 3.12+, then create .venv and install requirements." >&2
    exit 1
  fi
fi

PYTHON_VERSION="$("${PYTHON_BIN}" --version)"
"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ is required; found {sys.version.split()[0]}")
PY

cd "${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

echo "Starting PR-Agent Gitea webhook server on port ${PORT}."
echo "Using ${PYTHON_VERSION}."
echo "Local test URL: http://127.0.0.1:${PORT}/docs"
echo "Configure Gitea webhook URL as: http://<this-host>:${PORT}/api/v1/gitea_webhooks"

exec "${PYTHON_BIN}" pr_agent/servers/gitea_app.py
