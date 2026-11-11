#!/usr/bin/env bash
# Set up WSL-local mode: Python runs directly in WSL (no container).
#
#   bash scripts/setup_wsl.sh               # venv only; use Postgres from Docker (docker compose up -d db)
#   bash scripts/setup_wsl.sh --native-db   # also install Postgres inside WSL itself (port 5433)
#
# The virtual environment lives in ~/.venvs/fair-lending, on the Linux filesystem, because
# a venv under /mnt/c (the Windows drive) is very slow to import from.
set -euo pipefail

VENV="${VENV:-$HOME/.venvs/fair-lending}"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_DB=false
[[ "${1:-}" == "--native-db" ]] && NATIVE_DB=true

if ! python3 -c "import ensurepip" 2>/dev/null; then
    echo "Installing python3-venv (needs sudo) ..."
    sudo apt-get update
    sudo apt-get install -y python3-venv
fi

echo "Creating virtual environment in $VENV ..."
python3 -m venv --clear "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$PROJECT/requirements.txt"

# Settings applied every time the venv is activated. Replaced on each run of this script.
ACTIVATE="$VENV/bin/activate"
sed -i '/# fair-lending settings/,$d' "$ACTIVATE"
{
    echo "# fair-lending settings"
    # Edits on /mnt/c don't send file-change events into WSL, so Streamlit has to poll.
    echo "export STREAMLIT_SERVER_FILE_WATCHER_TYPE=poll"
} >> "$ACTIVATE"

if $NATIVE_DB; then
    echo "Installing PostgreSQL in WSL (needs sudo) ..."
    sudo apt-get update
    sudo apt-get install -y postgresql
    # Port 5433 so it never clashes with the Docker database on 5432.
    PG_CONF="$(ls /etc/postgresql/*/main/postgresql.conf | head -1)"
    sudo sed -i "s/^#\?port = .*/port = 5433/" "$PG_CONF"
    sudo systemctl enable --now postgresql
    sudo systemctl restart postgresql
    sudo -u postgres psql -p 5433 -tc "SELECT 1 FROM pg_roles WHERE rolname = 'hmda'" | grep -q 1 \
        || sudo -u postgres psql -p 5433 -c "CREATE ROLE hmda LOGIN PASSWORD 'hmda'"
    sudo -u postgres psql -p 5433 -tc "SELECT 1 FROM pg_database WHERE datname = 'hmda'" | grep -q 1 \
        || sudo -u postgres createdb -p 5433 -O hmda hmda
    echo "export DATABASE_URL=postgresql://hmda:hmda@localhost:5433/hmda" >> "$ACTIVATE"
    DB_NOTE="Postgres in WSL on localhost:5433 (empty: load the data with the commands in the README)."
else
    DB_NOTE="Postgres from Docker on localhost:5432. Start it with: docker compose up -d db"
fi

cat <<EOF

Done. Each time you open a WSL terminal:

    cd "$PROJECT"
    source "$ACTIVATE"
    streamlit run app.py

Database: $DB_NOTE
EOF
