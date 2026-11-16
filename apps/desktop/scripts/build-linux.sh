#!/usr/bin/env bash
# Build the Linux desktop packages: web UI -> backend sidecar -> Tauri (.deb, .rpm, AppImage).
#
#   scripts/build-linux.sh                    # everything
#   scripts/build-linux.sh --skip-sidecar     # reuse src-tauri/backend-dist from an earlier build
#   scripts/build-linux.sh --bundles deb      # a subset (comma-separated: deb,rpm,appimage)
#   scripts/build-linux.sh --python PATH      # passed to build-sidecar.sh (also --make-venv [DIR])
#
# Needs: Rust (rustup), Node 22 + npm, Python 3.12 build venv (see build-sidecar.sh), and the
# webkit2gtk-4.1 / gtk3 / libayatana-appindicator / librsvg development files. Nothing is signed;
# Linux packages do not self-update (no updater artefacts are produced).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$DESKTOP_ROOT/../.." && pwd)"
WEB_ROOT="$REPO_ROOT/apps/web"
BUNDLE_ROOT="$DESKTOP_ROOT/src-tauri/target/release/bundle"

die() { echo "build-linux: $*" >&2; exit 1; }
[[ "$(uname -s)" == "Linux" ]] || die "Linux only; use build-windows.ps1 on Windows"

SKIP_SIDECAR=0
BUNDLES="deb,rpm,appimage"
SIDECAR_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sidecar) SKIP_SIDECAR=1; shift ;;
    --bundles) BUNDLES="${2:?--bundles needs a list}"; shift 2 ;;
    --python) SIDECAR_ARGS+=("--python" "${2:?--python needs a path}"); shift 2 ;;
    --make-venv)
      if [[ $# -gt 1 && "${2:0:1}" != "-" ]]; then SIDECAR_ARGS+=("--make-venv" "$2"); shift 2
      else SIDECAR_ARGS+=("--make-venv"); shift; fi ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if ! command -v cargo >/dev/null 2>&1 && [[ -x "$HOME/.cargo/bin/cargo" ]]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi
command -v cargo >/dev/null 2>&1 || die "Rust is not installed (https://rustup.rs)"
command -v npm >/dev/null 2>&1 || die "npm is not installed"
for module in webkit2gtk-4.1 gtk+-3.0 librsvg-2.0; do
  pkg-config --exists "$module" || die "missing development files for $module (see apps/desktop/LINUX.md)"
done

echo "==> Web UI"
[[ -d "$WEB_ROOT/node_modules" ]] || npm --prefix "$WEB_ROOT" ci
npm --prefix "$WEB_ROOT" run build

echo "==> Backend sidecar"
if [[ $SKIP_SIDECAR -eq 1 ]]; then
  [[ -x "$DESKTOP_ROOT/src-tauri/backend-dist/echospeak-backend" ]] \
    || die "--skip-sidecar given but src-tauri/backend-dist/echospeak-backend is missing"
  echo "Reusing $DESKTOP_ROOT/src-tauri/backend-dist"
else
  "$SCRIPT_DIR/build-sidecar.sh" "${SIDECAR_ARGS[@]}"
fi

[[ -d "$DESKTOP_ROOT/node_modules" ]] || npm --prefix "$DESKTOP_ROOT" ci
PRODUCT="$(node -p 'require(process.argv[1]).productName' "$DESKTOP_ROOT/src-tauri/tauri.conf.json")"
# The bundled linuxdeploy ships an older strip that cannot read binaries from current
# toolchains (.relr.dyn); the release profile already strips the app binary.
export NO_STRIP="${NO_STRIP:-true}"
# The web UI was built above, so skip tauri.conf.json's beforeBuildCommand.
NO_WEB_BUILD='"build":{"beforeBuildCommand":""}'

# `|| true`: with --bundles appimage alone grep selects nothing and exits 1, which pipefail turned into a
# silent exit of the whole script right after the backend build.
PACKAGES="$(tr ',' '\n' <<<"$BUNDLES" | { grep -Ev '^appimage$' || true; } | paste -sd, -)"
if [[ -n "$PACKAGES" ]]; then
  echo "==> Tauri ($PACKAGES)"
  (cd "$DESKTOP_ROOT" && npx --no-install tauri build --bundles "$PACKAGES" --config "{$NO_WEB_BUILD}")
fi

if grep -qx appimage <<<"$(tr ',' '\n' <<<"$BUNDLES")"; then
  # linuxdeploy walks every ELF file in the AppDir. Given the PyInstaller backend it
  # fails (its libraries resolve only through the bootloader's LD_LIBRARY_PATH), or,
  # with that path supplied, copies ~700 MB of duplicates and rewrites the bundle's
  # rpaths. So Tauri builds the AppImage around a placeholder backend, then the real,
  # untouched backend goes into the AppDir and the AppImage is packed again.
  echo "==> Tauri (appimage, shell only)"
  PLACEHOLDER="$DESKTOP_ROOT/.build/appimage-placeholder-backend"
  mkdir -p "$PLACEHOLDER"
  echo "Replaced by the PyInstaller backend in build-linux.sh" > "$PLACEHOLDER/PLACEHOLDER"
  (cd "$DESKTOP_ROOT" && npx --no-install tauri build --bundles appimage \
    --config "{$NO_WEB_BUILD,\"bundle\":{\"resources\":{\"backend-dist/\":null,\"../.build/appimage-placeholder-backend/\":\"backend/\"}}}")
  APPDIR="$BUNDLE_ROOT/appimage/$PRODUCT.AppDir"
  APPIMAGE="$(find "$BUNDLE_ROOT/appimage" -maxdepth 1 -name '*.AppImage' -newer "$PLACEHOLDER/PLACEHOLDER" | head -n1)"
  [[ -d "$APPDIR/usr/lib/$PRODUCT/backend" && -n "$APPIMAGE" ]] || die "Tauri did not leave $APPDIR and an AppImage"
  echo "==> AppImage: adding the backend and packing again"
  rm -rf -- "$APPDIR/usr/lib/$PRODUCT/backend"
  cp -a -- "$DESKTOP_ROOT/src-tauri/backend-dist" "$APPDIR/usr/lib/$PRODUCT/backend"
  PLUGIN="${XDG_CACHE_HOME:-$HOME/.cache}/tauri/linuxdeploy-plugin-appimage.AppImage"
  [[ -x "$PLUGIN" ]] || die "linuxdeploy-plugin-appimage not found at $PLUGIN (Tauri downloads it on the first AppImage build)"
  rm -f -- "$APPIMAGE"
  ARCH=x86_64 LDAI_OUTPUT="$APPIMAGE" APPIMAGE_EXTRACT_AND_RUN=1 "$PLUGIN" --appdir "$APPDIR"
  [[ -f "$APPIMAGE" ]] || die "repacking the AppImage failed"
  chmod +x "$APPIMAGE"
fi

echo "==> Artefacts"
mapfile -t ARTEFACTS < <(find "$BUNDLE_ROOT/deb" "$BUNDLE_ROOT/rpm" "$BUNDLE_ROOT/appimage" \
  -maxdepth 1 -type f \( -name '*.deb' -o -name '*.rpm' -o -name '*.AppImage' \) 2>/dev/null | sort)
[[ ${#ARTEFACTS[@]} -gt 0 ]] || die "no packages were produced under $BUNDLE_ROOT"
SUMS="$BUNDLE_ROOT/SHA256SUMS"
: > "$SUMS"
for file in "${ARTEFACTS[@]}"; do
  bytes=$(stat -c %s "$file")
  sum=$(sha256sum "$file" | cut -d' ' -f1)
  printf '%s  %s\n' "$sum" "$(basename "$file")" >> "$SUMS"
  printf '%s\n  size   %s (%s bytes)\n  sha256 %s\n' "$file" "$(numfmt --to=iec --suffix=B "$bytes")" "$bytes" "$sum"
done
echo "Checksums: $SUMS"
