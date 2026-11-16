# EchoSpeak on Linux

The desktop app (Tauri 2 shell + the shared web UI + a bundled Python backend) builds
and runs on Linux as `.deb`, `.rpm` and AppImage packages. It was built and verified on
Arch (Omarchy), Hyprland on Wayland, NVIDIA open driver 615.

Windows packaging is unchanged; everything Linux-specific lives in
`src-tauri/tauri.linux.conf.json` (merged over `tauri.conf.json` on Linux only),
`#[cfg(...)]`-gated Rust, and the two `*.sh` scripts.

## Install

| Package | For | Install | Remove |
|---|---|---|---|
| `EchoSpeak_<version>_amd64.deb` | Debian, Ubuntu and derivatives | `sudo apt install ./EchoSpeak_<version>_amd64.deb` | `sudo apt remove echo-speak` |
| `EchoSpeak-<version>-1.x86_64.rpm` | Fedora and derivatives | `sudo dnf install ./EchoSpeak-<version>-1.x86_64.rpm` | `sudo dnf remove EchoSpeak` |
| `EchoSpeak_<version>_amd64.AppImage` | Any x86_64 distribution | `chmod +x EchoSpeak_<version>_amd64.AppImage && ./EchoSpeak_<version>_amd64.AppImage` | delete the file |

**Which distribution versions a build runs on depends on where it was built** (see
[glibc](#glibc-and-where-to-build) below). Packages built on current Arch need glibc
2.44 and only run on equally current rolling distributions.

- The packages install `/usr/bin/echospeak-desktop`, the backend under
  `/usr/lib/EchoSpeak/backend/`, a menu entry (`EchoSpeak.desktop`, category Office)
  and hicolor icons (32–1024 px).
- The `.deb` depends on `libwebkit2gtk-4.1-0`, `libgtk-3-0` and
  `libayatana-appindicator3-1` (added by Tauri) and recommends `xdg-utils` and
  `xdg-desktop-portal`. The `.rpm` requires `webkit2gtk4.1`, `gtk3` and
  `libayatana-appindicator-gtk3` (Fedora names; on openSUSE use the AppImage).
- The AppImage needs FUSE 2 to mount itself. Without it, run
  `./EchoSpeak_<version>_amd64.AppImage --appimage-extract-and-run`
  (or set `APPIMAGE_EXTRACT_AND_RUN=1`).
- On Arch/Omarchy, use the AppImage, or extract the `.deb`
  (`bsdtar -xf ... data.tar.gz`) into a PKGBUILD. A native PKGBUILD is a recommended
  follow-up.

### glibc and where to build

Both halves are tied to the build machine's glibc:

- the Rust shell links against it (built on Arch, Nov 2026: `GLIBC_2.39`);
- PyInstaller copies host libraries the Python packages load into the backend
  (`libglib-2.0`, `libstdc++`, `libX11`, and glibc's own `libmvec`). Built on Arch,
  these need `GLIBC_2.44`.

So release packages must be built on the oldest distribution they should support,
for example in a `debian:13` or `ubuntu:24.04` container (Python 3.12 from the
distribution or a standalone build, Rust via rustup, the `-dev` packages listed under
Build). Check a build with `objdump -T <file> | grep -o 'GLIBC_2\.[0-9]*' | sort -uV | tail -1`
over `usr/bin/echospeak-desktop` and the backend's `.so` files.

## Where things are

| What | Location |
|---|---|
| User data (single-writer, owned by one backend) | `~/.local/share/ai.echospeak.desktop/runtime/` |
| Desktop and backend logs | `~/.local/share/ai.echospeak.desktop/logs/` (`EchoSpeak.log`, `backend.log`) |
| Endpoint file (address + per-launch key of the running backend) | `$XDG_RUNTIME_DIR/echospeak/endpoint.json` (0600 file, 0700 folder) |
| Webview storage (localStorage, caches) | `~/.local/share/ai.echospeak.desktop/` (beside `runtime/`) |
| Window size/position | `~/.config/ai.echospeak.desktop/.window-state.json` |

`ECHOSPEAK_DESKTOP_DATA_DIR`, `ECHOSPEAK_DESKTOP_LOG_DIR` and `ECHOSPEAK_RUNTIME_DIR`
override the first three (used for disposable test runs); webview storage and window
state always use the paths above.

## One backend, shared with the terminal client

The backend holds an exclusive lock on its data folder, so the app and the terminal
client (`apps/terminal/echospeak`) must share one backend, not start one each.

When the app starts on Linux it first reads the endpoint file
(`$ECHOSPEAK_RUNTIME_DIR`, else `$XDG_RUNTIME_DIR/echospeak/endpoint.json`, else
`<data dir>/runtime/endpoint.json`, the same rules as `agent/endpoint_file.py`). It
attaches to that backend instead of starting its own only when all of these hold:

- the file is a regular file (not a symlink) owned by you with mode 0600, schema 1;
- its pid is alive;
- it listens on `127.0.0.1` and requires an API key;
- its `data_dir` is the app's data folder;
- `/health` answers and `/startup/readiness` accepts the key and speaks protocol 1.

Otherwise the app starts its own backend as before, and that backend publishes the
endpoint file itself, so `echospeak` in a terminal finds it.

- An attached backend is never stopped by the app. Quitting the app leaves it running.
  If it goes away while the app is open, the app starts its own.
- A backend the app started stops when the app quits (SIGTERM, then SIGKILL after 5 s),
  and exits by itself within about a second if the app is killed (it watches the app's pid).
- The terminal's `echospeak serve` defaults to the same folder
  (`${XDG_DATA_HOME:-~/.local/share}/ai.echospeak.desktop/runtime`), so whichever starts
  first owns the backend and the other attaches. Older terminal builds defaulted to
  `~/.local/share/echospeak`; with those, pass
  `ECHOSPEAK_DATA_DIR=~/.local/share/ai.echospeak.desktop/runtime`.
- If another backend holds the data folder but cannot be attached (for example it runs
  without an API key), the app's own backend fails on the lock and the app says so
  instead of retrying.

## Build

Requirements: Rust (rustup, stable), Node 22 + npm, Python 3.12, and the development
files for WebKitGTK 4.1, GTK 3, libayatana-appindicator and librsvg (Arch:
`webkit2gtk-4.1 gtk3 libayatana-appindicator librsvg`; Debian/Ubuntu:
`libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev build-essential`;
Fedora: `webkit2gtk4.1-devel gtk3-devel libayatana-appindicator-gtk3-devel librsvg2-devel`).

```bash
cd apps/desktop
scripts/build-sidecar.sh --make-venv   # once: hash-checked Python 3.12 build venv in .build/venv-linux
scripts/build-linux.sh                 # web UI -> backend -> deb, rpm, AppImage
scripts/build-linux.sh --skip-sidecar  # reuse src-tauri/backend-dist from the last backend build
scripts/build-linux.sh --bundles deb   # a subset
```

Artefacts land in `src-tauri/target/release/bundle/{deb,rpm,appimage}/`; the script
prints each path, size and SHA-256, and writes `SHA256SUMS` next to them.

- **Backend.** `build-sidecar.sh` mirrors `build-sidecar.ps1`: PyInstaller 6.21.0
  one-folder build of `backend/echospeak_backend.spec`, the bundle's `--self-check`, then
  staging at `src-tauri/backend-dist/`. The build Python must match
  `apps/backend/requirements.lock.txt`. That lock is resolved on Windows, so
  `--make-venv` installs it with `--require-hashes` minus the Windows-only pins
  (pywin32, pywinauto, pywinpty), plus `python3-xlib` (hash-pinned) that pyautogui needs
  on Linux. `ECHOSPEAK_ALLOW_UNLOCKED_BUILD=1` downgrades a lock mismatch to a warning.
- **AppImage.** linuxdeploy inspects every ELF file in the AppDir. On the PyInstaller
  folder it fails, or, given the folder's library path, copies about 700 MB of
  duplicates and rewrites the bundle's rpaths. So `build-linux.sh` lets Tauri build the
  AppImage around a placeholder backend, then copies the real backend into the AppDir
  unchanged and repacks it with `linuxdeploy-plugin-appimage`.
- Nothing is signed. No updater artefacts are produced on Linux.

## Known limitations

- **Updates.** Linux builds do not update themselves. Settings > About says updates
  come from the package manager or a newer AppImage. The Windows updater, its signing
  and `latest.json` are untouched.
- **NVIDIA on Wayland.** WebKitGTK's DMA-BUF renderer makes the compositor drop the app
  ("Error 71 (Protocol error) dispatching to Wayland display"). When an NVIDIA driver is
  loaded and `WEBKIT_DISABLE_DMABUF_RENDERER` is unset, the app sets it to `1`. Set it to
  `0` yourself to opt out.
- **Custom title bar on tiling compositors.** The windows are undecorated (Wayland
  `app_id`/class `echospeak-desktop`). On Hyprland they open tiled and take the tile's
  size, ignoring the 960 x 640 minimum, so a narrow tile clips the layout, including the
  title bar's buttons. Hyprland has no minimized state, and Omarchy suppresses
  maximize requests from all apps by default. Floating the windows gives them their
  intended size. Omarchy / Hyprland 0.56+ (Lua config, e.g. `~/.config/hypr/hyprland.lua`):

  ```lua
  o.window({ class = "^(echospeak-desktop)$" }, { float = true, center = true })
  o.window({ class = "^(echospeak-desktop)$", title = "^(EchoSpeak)$" }, { size = { 1440, 900 } })
  ```

  On older, hyprlang-configured Hyprland, write the same rule in the syntax your version's
  wiki page (Window Rules) shows.

  Closing the main window with the compositor (Super+W) quits the app, like the title
  bar's close button.
- **Tray.** "Keep running in the tray" needs a StatusNotifierItem host (Waybar's tray,
  KDE, GNOME with the AppIndicator extension). Without one the icon is invisible.
- **Windows-only features.** Desktop control (window listing, input), the app launcher
  (Start menu / Store apps) and Windows SAPI voices are not available. Desktop control
  on Wayland would need `grim`/`hyprctl` for reading and `ydotool`/uinput for input,
  which is a privileged design decision.
- **Package name.** Tauri derives the Debian package name from the product name:
  `echo-speak`.
