# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

spec_dir = Path(SPECPATH).resolve()
desktop_dir = spec_dir.parent
repo_root = desktop_dir.parents[1]
backend_root = repo_root / "apps" / "backend"


def data_tree(source: Path, destination: str):
    if not source.exists():
        return []
    rows = []
    for path in source.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative_parent = path.relative_to(source).parent
        rows.append((str(path), str(Path(destination) / relative_parent)))
    return rows


datas = []
for filename in ("SOUL.md",):
    path = backend_root / filename
    if path.exists():
        datas.append((str(path), "."))
datas += data_tree(backend_root / "skills", "skills")
datas += data_tree(backend_root / "workspaces", "workspaces")

# Local speech (faster-whisper): its VAD model files and CTranslate2's native DLLs.
datas += collect_data_files("faster_whisper")
for package in ("trafilatura", "justext", "ddgs", "primp"):
    datas += collect_data_files(package)
binaries_extra = collect_dynamic_libs("ctranslate2")
# Local embeddings (agent/embeddings.py) and the voice VAD run on onnxruntime.
binaries_extra += collect_dynamic_libs("onnxruntime")

for distribution in (
    "langchain",
    "langchain-core",
    "langchain-community",
    "langchain-openai",
    "mcp",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

hiddenimports = (
    collect_submodules("agent")
    + collect_submodules("api")
    # MCP's optional CLI exits during import without its CLI extras installed.
    # EchoSpeak uses the SDK, so avoid scanning or bundling the CLI package.
    + collect_submodules("mcp", filter=lambda name: name != "mcp.cli" and not name.startswith("mcp.cli."))
    # LangChain uses lazy __getattr__ exports; Analysis cannot discover Runnable.
    + collect_submodules("langchain_core")
    + collect_submodules("langchain_openai")
    + collect_submodules("langchain_google_genai")
    + collect_submodules("langchain_ollama")
    + collect_submodules("ddgs.engines")
    + collect_submodules("py7zr")
    + ["onnxruntime", "tokenizers", "trafilatura", "lxml.html.clean", "primp", "websockets.sync.client"]
)

a = Analysis(
    [str(desktop_dir / "backend" / "echospeak_backend.py")],
    pathex=[str(backend_root)],
    binaries=binaries_extra,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PyTorch is not used: embeddings run on onnxruntime. Excluding it keeps a dev
    # venv that happens to have torch installed from adding ~1 GB to the bundle.
    excludes=["pytest", "mcp.cli", "torch", "torchvision", "torchaudio", "transformers", "sentence_transformers"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

# One-folder build: Python starts straight from the installed files. The old
# one-file build unpacked ~2 GB to %TEMP% on every launch (15-20 s) and ran a
# second worker process that Windows gave its own console window.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="echospeak-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console subsystem keeps stdout/stderr pipes to the desktop host; the
    # host spawns it with CREATE_NO_WINDOW, so no window is shown.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="echospeak-backend",
)
