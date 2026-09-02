#!/usr/bin/env bash
# Zeline cross-platform installer for Termux, Linux, macOS, and iOS/iSH.
#
# Download the versioned release asset + SHA256SUMS, verify it, then run:
#   bash install.sh
#
# Options:
#   --platform-info                  show detected platform and prerequisites only
#   --platform termux|linux|macos|ios-ish
#   --source PATH                    build and install an existing checkout
#   --install-root PATH              private Zeline runtime (default: ~/.local/share/zeline)
#   --bin-dir PATH                   command location (default: platform-specific)
#   --no-seed                        do not copy built-in skills during install
#
# Environment equivalents:
#   ZELINE_PYTHON, ZELINE_PLATFORM, ZELINE_INSTALL_ROOT, ZELINE_BIN_DIR
set -euo pipefail

VERSION="0.2.8"
REF="v0.2.8"
RELEASE_BASE="https://github.com/Mftrferdinand/Zeline/releases/download/${REF}"
WHEEL_NAME="zeline-${VERSION}-py3-none-any.whl"
PLATFORM="${ZELINE_PLATFORM:-}"
PLATFORM_INFO=0
SOURCE_OVERRIDE=""
INSTALL_ROOT="${ZELINE_INSTALL_ROOT:-$HOME/.local/share/zeline}"
BIN_DIR="${ZELINE_BIN_DIR:-}"
NO_SEED=0
PYTHON_BIN="${ZELINE_PYTHON:-}"
TMP_DIR=""
SOURCE_DIR=""
ARTIFACT=""

usage() {
  cat <<'EOF'
Zeline installer

Usage:
  bash install.sh [options]

Options:
  --platform-info                  Print platform support details and exit
  --platform PLATFORM             termux, linux, macos, or ios-ish
  --source PATH                   Build and install this local checkout
  --install-root PATH             Private runtime directory
  --bin-dir PATH                  Directory for the zeline command
  --no-seed                       Skip built-in skill initialization
  -h, --help                      Show this help
EOF
}

fail() { printf '[x] %s\n' "$*" >&2; exit 1; }
warn() { printf '[!] %s\n' "$*"; }
detail() { printf '    %s\n' "$*"; }
step() { printf '\n[ %s/4 ]  %s\n' "$1" "$2"; }

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT INT TERM

reject_line_breaks() {
  local label="$1" value="$2"
  case "$value" in
    *$'\n'*|*$'\r'*) fail "$label may not contain a newline or carriage return." ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --platform-info) PLATFORM_INFO=1; shift ;;
    --platform)
      [ "$#" -ge 2 ] || fail "--platform needs a value."
      PLATFORM="$2"; shift 2 ;;
    --source)
      [ "$#" -ge 2 ] || fail "--source needs a path."
      SOURCE_OVERRIDE="$2"; shift 2 ;;
    --install-root)
      [ "$#" -ge 2 ] || fail "--install-root needs a path."
      INSTALL_ROOT="$2"; shift 2 ;;
    --bin-dir)
      [ "$#" -ge 2 ] || fail "--bin-dir needs a path."
      BIN_DIR="$2"; shift 2 ;;
    --no-seed) NO_SEED=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1 (run with --help)." ;;
  esac
done

reject_line_breaks "--platform" "$PLATFORM"
reject_line_breaks "--source" "$SOURCE_OVERRIDE"
reject_line_breaks "--install-root" "$INSTALL_ROOT"
reject_line_breaks "--bin-dir" "$BIN_DIR"
reject_line_breaks "ZELINE_PYTHON" "$PYTHON_BIN"

print_banner() {
  # Fixed-width identity: do not use ${#subtitle}. In a C/POSIX locale Bash may
  # count UTF-8 bytes (the bullet is 3 bytes), bending the frame on glibc.
  local top='╭───────────────────────────────────────╮'
  local title='│           Z  E  L  I  N  E            │'
  local mid='├───────────────────────────────────────┤'
  local subtitle="│   AGENTIC AI BY ZEROLINEAR • v${VERSION}   │"
  local bottom='╰───────────────────────────────────────╯'
  if [ -t 1 ] && [ "${NO_COLOR+x}" != x ] && [ "${TERM:-}" != "dumb" ]; then
    local frame='\033[38;5;25m' white='\033[97m\033[1m' blue='\033[38;5;39m' reset='\033[0m'
    printf "\n${frame}%s${reset}\n${frame}│${reset}${white}%s${reset}${frame}│${reset}\n${frame}%s${reset}\n${frame}│${reset}${blue}%s${reset}${frame}│${reset}\n${frame}%s${reset}\n" \
      "$top" "           Z  E  L  I  N  E            " "$mid" "   AGENTIC AI BY ZEROLINEAR • v${VERSION}   " "$bottom"
  else
    printf '\n%s\n%s\n%s\n%s\n%s\n' "$top" "$title" "$mid" "$subtitle" "$bottom"
  fi
}

detect_platform() {
  if [ -n "$PLATFORM" ]; then
    case "$PLATFORM" in
      termux|linux|macos|ios-ish) return ;;
      *) fail "Unsupported platform '$PLATFORM'. Use termux, linux, macos, or ios-ish." ;;
    esac
  fi

  if [ -n "${TERMUX_VERSION:-}" ] || printf '%s' "${PREFIX:-}" | grep -q 'com.termux'; then
    PLATFORM="termux"
    return
  fi
  if [ -n "${ISH_VERSION:-}" ] || [ -e /proc/ish ] || uname -a 2>/dev/null | grep -qi '\bish\b'; then
    PLATFORM="ios-ish"
    return
  fi
  case "$(uname -s 2>/dev/null || printf unknown)" in
    Darwin) PLATFORM="macos" ;;
    Linux) PLATFORM="linux" ;;
    *) fail "Unsupported OS. Zeline supports Termux, Linux, macOS, iOS/iSH, and Windows PowerShell." ;;
  esac
}

platform_label() {
  case "$PLATFORM" in
    termux) printf 'Termux / Android (termux)' ;;
    linux) printf 'Linux (linux)' ;;
    macos) printf 'macOS (macos)' ;;
    ios-ish) printf 'iOS / iPadOS through iSH (ios-ish)' ;;
  esac
}

platform_package_command() {
  case "$PLATFORM" in
    termux) printf 'pkg install python curl -y' ;;
    linux) printf 'Debian/Ubuntu: sudo apt install python3 python3-venv curl -y' ;;
    macos) printf 'xcode-select --install; brew install python' ;;
    ios-ish) printf 'apk add bash curl python3 py3-pip' ;;
  esac
}

platform_note() {
  case "$PLATFORM" in
    termux) printf 'Keep long-running gateways alive with termux-wake-lock.' ;;
    linux) printf 'No sudo is used by Zeline; only OS package installation may need it.' ;;
    macos) printf 'The private venv avoids Homebrew PEP 668 restrictions.' ;;
    ios-ish) printf 'Interactive CLI works in iSH; iOS may suspend background gateways when iSH leaves the foreground.' ;;
  esac
}

default_bin_dir() {
  if [ "$PLATFORM" = "termux" ] && [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ] && [ -w "$PREFIX/bin" ]; then
    printf '%s/bin' "$PREFIX"
  elif [ "$PLATFORM" = "ios-ish" ] && [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    printf '/usr/local/bin'
  else
    printf '%s/.local/bin' "$HOME"
  fi
}

print_platform_info() {
  printf '\n[ PLATFORM ]\n'
  detail "Target       : $(platform_label)"
  detail "Version      : ${REF} (versioned release)"
  detail "Prerequisite : $(platform_package_command)"
  detail "Runtime      : private Python venv at $INSTALL_ROOT/venv"
  detail "Command      : ${BIN_DIR:-$(default_bin_dir)}/zeline"
  detail "Note         : $(platform_note)"
}

resolve_python() {
  if [ -n "$PYTHON_BIN" ]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "ZELINE_PYTHON not found: $PYTHON_BIN"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    fail "Python is missing. Run: $(platform_package_command)"
  fi
}

ensure_tmp_dir() {
  if [ -n "$TMP_DIR" ]; then
    return
  fi
  local temp_root="${TMPDIR:-$HOME}"
  reject_line_breaks "temporary directory" "$temp_root"
  mkdir -p "$temp_root"
  TMP_DIR="$(mktemp -d "$temp_root/zeline.XXXXXX")"
}

download_file() {
  local url="$1" destination="$2"
  case "$url" in https://*) ;; *) fail "Refusing non-HTTPS download: $url" ;; esac
  command -v curl >/dev/null 2>&1 || fail "curl is required for HTTPS-only downloads. Run: $(platform_package_command)"
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --output "$destination" "$url"
}

file_sha256() {
  local path="$1" line
  if command -v sha256sum >/dev/null 2>&1; then
    line="$(sha256sum "$path")"
  elif command -v shasum >/dev/null 2>&1; then
    line="$(shasum -a 256 "$path")"
  else
    fail "No SHA-256 tool found (need sha256sum or shasum)."
  fi
  printf '%s' "${line%% *}"
}

verify_release_artifact() {
  local artifact="$1" sums="$2" filename expected="" actual
  filename="$(basename "$artifact")"
  while read -r hash listed; do
    listed="${listed#\*}"
    if [ "$listed" = "$filename" ]; then
      expected="$hash"
      break
    fi
  done < "$sums"
  [ -n "$expected" ] || fail "SHA256SUMS has no entry for $filename."
  case "$expected" in
    *[!0-9a-fA-F]*) fail "SHA256SUMS expected digest is not 64 hexadecimal characters for $filename." ;;
  esac
  [ "${#expected}" -eq 64 ] || fail "SHA256SUMS expected digest is not 64 hexadecimal characters for $filename."
  actual="$(file_sha256 "$artifact")"
  [ "$actual" = "$expected" ] || fail "SHA-256 verification failed for $filename."
  detail "Verified : $filename (SHA-256)"
}

resolve_source_mode() {
  [ -n "$SOURCE_OVERRIDE" ] || return 0
  [ -f "$SOURCE_OVERRIDE/pyproject.toml" ] || fail "Not a Zeline checkout: $SOURCE_OVERRIDE"
  [ -d "$SOURCE_OVERRIDE/zeline" ] || fail "Not a Zeline checkout: $SOURCE_OVERRIDE"
  SOURCE_DIR="$(CDPATH= cd -- "$SOURCE_OVERRIDE" && pwd)"
}

build_local_wheel() {
  ensure_tmp_dir
  local build_venv="$TMP_DIR/build-venv" build_python artifacts staging
  artifacts="$TMP_DIR/artifacts"
  staging="$TMP_DIR/source"
  mkdir -p "$artifacts"
  # Never build in the checkout itself. setuptools writes build/ + egg-info;
  # two concurrent installer tests/users would race and corrupt each other.
  SOURCE_DIR="$SOURCE_DIR" STAGING="$staging" "$PYTHON_BIN" - <<'PY'
import os
import shutil
from pathlib import Path

source = Path(os.environ["SOURCE_DIR"])
target = Path(os.environ["STAGING"])
ignored = shutil.ignore_patterns(
    ".git", ".pytest_cache", ".venv", "venv", "build", "dist",
    "*.egg-info", "__pycache__", "*.pyc", "*.pyo",
)
shutil.copytree(source, target, symlinks=False, ignore=ignored)
PY
  "$PYTHON_BIN" -m venv "$build_venv" || fail "Could not create temporary build environment."
  build_python="$build_venv/bin/python"
  "$build_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$build_python" -m pip wheel --quiet --disable-pip-version-check --no-deps \
    --wheel-dir "$artifacts" "$staging"
  ARTIFACT="$artifacts/$WHEEL_NAME"
  [ -f "$ARTIFACT" ] || fail "Local build did not produce $WHEEL_NAME. Check version consistency."
  detail "Built    : $WHEEL_NAME from local checkout"
}

download_release_wheel() {
  ensure_tmp_dir
  local sums="$TMP_DIR/SHA256SUMS"
  ARTIFACT="$TMP_DIR/$WHEEL_NAME"
  download_file "$RELEASE_BASE/SHA256SUMS" "$sums"
  download_file "$RELEASE_BASE/$WHEEL_NAME" "$ARTIFACT"
  verify_release_artifact "$ARTIFACT" "$sums"
}

venv_remediation() {
  case "$PLATFORM" in
    linux) detail "Install venv support: sudo apt install python3-venv" ;;
    ios-ish) detail "Install Python tooling: apk add python3 py3-pip" ;;
    termux) detail "Refresh Python: pkg install python -y" ;;
    macos) detail "Install a complete Python: brew install python" ;;
  esac
}

shell_quote() {
  # POSIX single-quote escaping. Newlines were rejected during argument parsing.
  local value="$1"
  value=${value//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

install_wrapper() {
  mkdir -p "$BIN_DIR"
  local launcher="$BIN_DIR/zeline" temporary="$BIN_DIR/.zeline.tmp" quoted_python
  quoted_python="$(shell_quote "$VENV_PYTHON")"
  {
    printf '%s\n' '#!/usr/bin/env sh'
    printf 'exec %s -m zeline.cli "$@"\n' "$quoted_python"
  } > "$temporary"
  chmod 755 "$temporary"
  mv -f "$temporary" "$launcher"
}

detect_platform
if [ -z "$BIN_DIR" ]; then
  BIN_DIR="$(default_bin_dir)"
fi
reject_line_breaks "resolved bin directory" "$BIN_DIR"

print_banner
print_platform_info
if [ "$PLATFORM_INFO" -eq 1 ]; then
  exit 0
fi

resolve_python
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  fail "Zeline needs Python 3.10 or newer (found: $($PYTHON_BIN --version 2>&1))."
fi
resolve_source_mode

step 1 "ENVIRONMENT"
detail "Platform : $(platform_label)"
detail "Python   : $($PYTHON_BIN --version 2>&1)"
detail "Runtime  : $INSTALL_ROOT"
if [ -n "$SOURCE_DIR" ]; then
  detail "Source   : local checkout ($SOURCE_DIR)"
else
  detail "Source   : verified release $REF"
fi

step 2 "VERIFIED PACKAGE"
if [ -n "$SOURCE_DIR" ]; then
  build_local_wheel
else
  download_release_wheel
fi

step 3 "PRIVATE RUNTIME"
VENV_DIR="$INSTALL_ROOT/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
mkdir -p "$INSTALL_ROOT"
if [ ! -x "$VENV_PYTHON" ]; then
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    warn "Python could not create a virtual environment."
    venv_remediation
    exit 1
  fi
fi
"$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check --upgrade "$ARTIFACT"
install_wrapper
detail "Runtime : $VENV_DIR"
detail "Command : $BIN_DIR/zeline"
if [ "$NO_SEED" -eq 0 ]; then
  ZELINE_HOME="${ZELINE_HOME:-$HOME/.zeline}" "$VENV_PYTHON" - <<'PY'
from zeline import skills
print(f"    Skills  : {skills.seed_skills()} new built-in skills")
PY
else
  detail "Skills  : skipped (--no-seed)"
fi

step 4 "VERIFY"
"$BIN_DIR/zeline" --version
if ! printf ':%s:' "$PATH" | grep -Fq ":$BIN_DIR:"; then
  warn "$BIN_DIR is not on PATH in this shell."
  detail "Run now : $BIN_DIR/zeline"
  detail "Persist : export PATH=\"$BIN_DIR:\$PATH\""
fi

printf '\nZeline is ready on %s.\n' "$(platform_label)"
printf '  Setup        : zeline setup\n'
printf '  Tools        : zeline tools list\n'
printf '  Integrations : zeline mcp list\n'
printf '  Health       : zeline doctor\n'
printf '  Release      : %s\n' "$REF"
printf '  Docs         : https://github.com/Mftrferdinand/Zeline\n'
