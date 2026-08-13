#!/usr/bin/env bash
# Zeline agentic AI framework installer
#
# Pilihan penggunaan:
#   curl -fsSL https://raw.githubusercontent.com/Mftrferdinand/Zerolinear/main/install.sh | bash
#   git clone https://github.com/Mftrferdinand/Zerolinear.git && cd Zerolinear && bash install.sh
#
# Environment opsional:
#   ZELINE_PYTHON=python3   # executable Python yang dipakai installer
set -euo pipefail

REPO_URL="https://github.com/Mftrferdinand/Zerolinear.git"
BRANCH="main"
PYTHON_BIN="${ZELINE_PYTHON:-${ZELINE_PYTHON:-python3}}"
INSTALL_DIR="${ZELINE_INSTALL_DIR:-${ZELINE_INSTALL_DIR:-$HOME/.local/share/zeline-source}}"
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
  local title="Z  E  L  I  N  E"
  local subtitle="AGENTIC AI BY ZEROLINEAR • v0.1.0"
  # Inner width = widest line + 6 padding; build the frame to match.
  local inner=$(( ${#subtitle} + 6 ))
  local bar
  bar=$(printf '─%.0s' $(seq 1 "$inner"))
  # Center helper
  center() { local s="$1"; local pad=$(( (inner - ${#s}) / 2 )); printf '%*s%s%*s' "$pad" '' "$s" "$(( inner - ${#s} - pad ))" ''; }
  local t s
  t=$(center "$title"); s=$(center "$subtitle")
  if [ -t 1 ] && [ "${NO_COLOR+x}" != x ] && [ "${TERM:-}" != "dumb" ]; then
    local f='\033[38;5;25m' w='\033[97m\033[1m' b='\033[38;5;39m' r='\033[0m'
    printf "\n${f}╭%s╮${r}\n${f}│${r}${w}%s${r}${f}│${r}\n${f}├%s┤${r}\n${f}│${r}${b}%s${r}${f}│${r}\n${f}╰%s╯${r}\n\n" "$bar" "$t" "$bar" "$s" "$bar"
  else
    printf '\n╭%s╮\n│%s│\n├%s┤\n│%s│\n╰%s╯\n\n' "$bar" "$t" "$bar" "$s" "$bar"
  fi
}

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "[x] Zeline butuh Python 3.10 atau lebih baru." >&2
  exit 1
fi

print_banner
echo "==> Installer"
echo "    Python : $($PYTHON_BIN --version)"

# Jika installer dieksekusi dari repo checkout, gunakan source saat ini.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/pyproject.toml" ] && [ -d "$SCRIPT_DIR/zeline" ]; then
  SOURCE_DIR="$SCRIPT_DIR"
  echo "    Source : local checkout ($SOURCE_DIR)"
else
  need_command git
  TMP_DIR="$(mktemp -d)"
  SOURCE_DIR="$TMP_DIR/zeline"
  echo "==> Mengunduh source Zeline…"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$SOURCE_DIR"
fi

echo "==> Install/update package…"
# pip harus ada; sebagian sistem (mis. macOS baru, beberapa distro) perlu ensurepip.
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  echo "    pip belum ada, mencoba ensurepip…"
  "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  echo "[x] pip tidak tersedia untuk $PYTHON_BIN. Install pip dulu (mis. 'python3 -m ensurepip' atau paket pip OS-mu)." >&2
  exit 1
fi

# Instalasi lintas-OS yang tahan PEP 668 (externally-managed-environment):
# 1) coba `pip install --user` (Termux/Linux user-site normal),
# 2) kalau ditolak env yang dikelola OS (macOS Homebrew / Debian baru), ulangi
#    dengan `--break-system-packages` supaya tidak butuh sudo/venv manual.
pip_install() {
  "$PYTHON_BIN" -m pip install --user --upgrade "$SOURCE_DIR" "$@"
}
install_log="$(pip_install 2>&1)" && install_ok=1 || install_ok=0
if [ "$install_ok" != 1 ]; then
  if printf '%s' "$install_log" | grep -qiE "externally-managed-environment|externally managed"; then
    echo "    Environment dikelola OS (PEP 668) → mengulang dengan --break-system-packages…"
    install_log="$(pip_install --break-system-packages 2>&1)" && install_ok=1 || install_ok=0
  fi
fi
if [ "$install_ok" != 1 ]; then
  printf '%s\n' "$install_log" >&2
  echo "[x] Gagal memasang paket Zeline. Lihat pesan pip di atas." >&2
  exit 1
fi

# Termux sering tidak memasukkan ~/.local/bin ke PATH. Jika PREFIX/bin bisa
# ditulis, pasang wrapper kecil agar command `zeline` tersedia langsung.
if [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ] && [ -w "$PREFIX/bin" ]; then
  cat > "$PREFIX/bin/zeline" <<EOF
#!/usr/bin/env sh
exec "$PYTHON_BIN" -m zeline.cli "\$@"
EOF
  chmod +x "$PREFIX/bin/zeline"
  echo "    Command: $PREFIX/bin/zeline"
else
  USER_BIN="$HOME/.local/bin"
  case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *)
      echo ""
      echo "[!] Tambahkan ini ke ~/.bashrc atau restart shell agar command zeline terbaca:"
      echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
      ;;
  esac
fi

echo "==> Inisialisasi data Zeline (~/.zeline)…"
"$PYTHON_BIN" - <<'PY'
from zeline import skills
count = skills.seed_skills()
print(f"    OK · {count} skill baru ditambahkan")
PY

echo ""
echo "✓ Zeline terpasang."
echo ""
echo "Mulai Zeline:"
echo "  zeline"
echo ""
echo "Pilih satu gateway dengan tombol ↑/↓, lalu lanjutkan dengan:"
echo "  zeline model"
echo ""
echo "Setelah selesai, cek:"
echo "  zeline doctor"
echo ""
echo "Dokumentasi: https://github.com/Mftrferdinand/Zerolinear"
