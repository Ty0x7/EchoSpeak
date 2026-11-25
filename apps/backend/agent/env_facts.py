"""Read-only facts about this computer's network and installed apps, for system_info (campaign round 3, 25 Nov 2026).

system_info reported only hardware, so questions like "am I on a VPN?" or "do I have GIMP installed?" had no
read-only answer outside a terminal (and Chat has none). The answers are in the kernel's tables and the desktop
entries, with nothing to run: which interfaces are up, physical or virtual, WireGuard or tun, and which one carries
the default route; and which applications the desktop lists.

Nothing here runs a command, makes a network request or reports an address. ``sys_root``/``proc_root`` exist for tests.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# Container and virtual-machine networks are virtual too, but they are not a VPN.
_LOCAL_VIRTUAL = re.compile(r"^(lo|docker\d*|br-.*|virbr\d*|veth.*|vnet\d*|podman\d*|cni\d*|flannel.*|lxcbr\d*|kube.*)$")
_VENDORS = (("proton", "Proton VPN"), ("pvpn", "Proton VPN"), ("mullvad", "Mullvad"), ("nordlynx", "NordVPN"),
            ("tailscale", "Tailscale"), ("zt", "ZeroTier"), ("wg", "WireGuard"), ("tun", "a tunnel (OpenVPN or similar)"),
            ("tap", "a tunnel (OpenVPN or similar)"), ("ppp", "a PPP/VPN link"), ("ipsec", "IPsec"))


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def interfaces(sys_root: str = "/sys") -> list[dict]:
    """Interfaces that are up: name, physical or virtual, and its kind (wireguard, tun, wlan, bridge, …)."""
    base = Path(sys_root) / "class" / "net"
    rows = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return rows
    for name in names:
        node = base / name
        try:
            flags = int(_read(node / "flags") or "0", 16)
        except ValueError:
            flags = 0
        if not flags & 0x1 or _read(node / "operstate") == "down":   # switched off, or no cable/carrier
            continue
        uevent = _read(node / "uevent")
        devtype = next((line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("DEVTYPE=")), "")
        kind = devtype or ("tun" if (node / "tun_flags").exists() else "")
        rows.append({"name": name, "physical": (node / "device").exists(), "kind": kind})
    return rows


def default_route(proc_root: str = "/proc") -> Optional[str]:
    """The interface the default route goes through (lowest metric), or None."""
    best = None
    for line in _read(Path(proc_root) / "net" / "route").splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 8 and cols[1] == "00000000" and cols[7] == "00000000":
            try:
                metric = int(cols[6])
            except ValueError:
                continue
            if best is None or metric < best[0]:
                best = (metric, cols[0])
    return best[1] if best else None


def _vendor(names: list[str]) -> str:
    for name in names:
        low = name.lower()
        for hint, label in _VENDORS:
            if low.startswith(hint) or hint in low and hint in {"proton", "pvpn", "mullvad", "nordlynx", "tailscale"}:
                return label
    return ""


def network_summary(sys_root: str = "/sys", proc_root: str = "/proc") -> str:
    up = interfaces(sys_root)
    route = default_route(proc_root)
    tunnels = [r for r in up if r["kind"] in {"wireguard", "tun"} or
               (not r["physical"] and not _LOCAL_VIRTUAL.match(r["name"]) and r["kind"] not in {"bridge"})]
    via = next((r for r in up if r["name"] == route), None)
    route_virtual = bool(via and not via["physical"] and not _LOCAL_VIRTUAL.match(via["name"]))
    names = [r["name"] for r in tunnels]
    vendor = _vendor(names)
    if route_virtual or (tunnels and any(r["kind"] in {"wireguard", "tun"} for r in tunnels)):
        verdict = ("Yes, traffic goes through a VPN" if route_virtual else "A VPN tunnel is up, but the default route "
                   "doesn't go through it") + (f" ({vendor})" if vendor else "")
    else:
        verdict = "No VPN: the default route goes through a physical connection" if route else "No default route"
    physical = [f"{r['name']}{' (Wi-Fi)' if r['kind'] == 'wlan' else ''}" for r in up if r["physical"]]
    detail = [f"default route via {route}" + (" (virtual)" if route_virtual else "")] if route else []
    if tunnels:
        detail.append("tunnels up: " + ", ".join(f"{r['name']}{' (' + r['kind'] + ')' if r['kind'] else ''}"
                                                   for r in tunnels))
    if physical:
        detail.append("physical: " + ", ".join(physical))
    if not detail:
        return verdict + "."
    text = "; ".join(detail)
    return f"{verdict}. {text[0].upper()}{text[1:]}."


def installed_apps(roots: Optional[list[str]] = None, limit: int = 120) -> list[str]:
    """Names of desktop applications (visible .desktop entries in the system and user application folders)."""
    roots = roots or ["/usr/share/applications", "/usr/local/share/applications",
                      str(Path.home() / ".local/share/applications"), "/var/lib/flatpak/exports/share/applications",
                      str(Path.home() / ".local/share/flatpak/exports/share/applications")]
    names: dict[str, str] = {}
    for root in roots:
        try:
            entries = sorted(Path(root).glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            text = _read(entry)
            section = text.split("[Desktop Entry]", 1)[-1].split("\n[", 1)[0]
            if re.search(r"(?m)^(NoDisplay|Hidden)\s*=\s*true", section):
                continue
            match = re.search(r"(?m)^Name\s*=\s*(.+)$", section)
            if match:
                names[entry.stem] = match.group(1).strip()      # a user entry overrides the system one
    return sorted(set(names.values()), key=str.lower)[:limit]
