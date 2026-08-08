#!/usr/bin/env bash
# Aesora AI Agent installer
#
# Pilihan penggunaan:
#   curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/aesora/main/install.sh | bash
#   git clone https://github.com/Mftrferdinand/aesora.git && cd aesora && bash install.sh
#
# Environment opsional:
#   AESORA_PYTHON=python3   # executable Python yang dipakai installer
set -euo pipefail

REPO_URL="https://github.com/Mftrferdinand/aesora.git"
BRANCH="main"
PYTHON_BIN="${AESORA_PYTHON:-python3}"
INSTALL_DIR="${AESORA_INSTALL_DIR:-$HOME/.local/share/aesora-source}"
TMP_DIR=""

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

need_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[x] Perintah wajib tidak ditemukan: $1" >&2
    exit 1
  }
}

need_command "$PYTHON_BIN"

print_banner() {
  local subtitle="SELF-HOSTED AI AGENT FRAMEWORK  ·  BY MFTRFERDINAND"
  local art
  art='   _   ___ ___  ___  ___    _       _   ___ ___ _  _ _____
  /_\ | __/ __|/ _ \| _ \  /_\ ___ /_\ / __| __| \| |_   _|
 / _ \| _|\__ \ (_) |   / / _ \___/ _ \ (_ | _|| .` | | |
/_/ \_\___|___/\___/|_|_\/_/ \_\ /_/ \_\___|___|_|\_| |_|'
  if [ -t 1 ] && [ "${NO_COLOR+x}" != x ] && [ "${TERM:-}" != "dumb" ]; then
    printf '\n\033[38;5;51m%s\033[0m\n\033[38;5;75m  %s\033[0m\n\n' "$art" "$subtitle"
  else
    printf '\n%s\n  %s\n\n' "$art" "$subtitle"
  fi
}

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "[x] Aesora butuh Python 3.10 atau lebih baru." >&2
  exit 1
fi

print_banner
echo "==> Installer"
echo "    Python : $($PYTHON_BIN --version)"

# Jika installer dieksekusi dari repo checkout, gunakan source saat ini.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/aesora" ]; then
  SOURCE_DIR="$SCRIPT_DIR"
  echo "    Source : local checkout ($SOURCE_DIR)"
else
  need_command git
  TMP_DIR="$(mktemp -d)"
  SOURCE_DIR="$TMP_DIR/aesora"
  echo "==> Mengunduh source Aesora…"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$SOURCE_DIR"
fi

echo "==> Install/update package…"
"$PYTHON_BIN" -m pip install --user --upgrade "$SOURCE_DIR"

# Termux sering tidak memasukkan ~/.local/bin ke PATH. Jika PREFIX/bin bisa
# ditulis, pasang wrapper kecil agar command `aesora` tersedia langsung.
if [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ] && [ -w "$PREFIX/bin" ]; then
  cat > "$PREFIX/bin/aesora" <<EOF
#!/usr/bin/env sh
exec "$PYTHON_BIN" -m aesora.cli "\$@"
EOF
  chmod +x "$PREFIX/bin/aesora"
  echo "    Command: $PREFIX/bin/aesora"
else
  USER_BIN="$HOME/.local/bin"
  case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *)
      echo ""
      echo "[!] Tambahkan ini ke ~/.bashrc atau restart shell agar command aesora terbaca:"
      echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
      ;;
  esac
fi

echo "==> Inisialisasi data Aesora (~/.aesora)…"
"$PYTHON_BIN" - <<'PY'
from aesora import skills
count = skills.seed_skills()
print(f"    OK · {count} skill baru ditambahkan")
PY

echo ""
echo "✓ Aesora terpasang."
echo ""
echo "Mulai setup:"
echo "  aesora setup"
echo ""
echo "Lalu cek:"
echo "  aesora doctor"
echo "  aesora gateway list"
echo ""
echo "Dokumentasi: https://github.com/Mftrferdinand/aesora"
