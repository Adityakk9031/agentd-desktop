"""Build the bundled agentd runtime with PyInstaller into desktop/runtime/.

    python desktop/build_runtime.py

The desktop installer (npm run dist:win) then ships it, so users need no Python.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "runtime"
WORK = HERE / "build" / "pyinstaller"

# Imported by name at run time (model providers, server, tools), so PyInstaller must be told
COLLECT = [
    "agentd", "uvicorn", "fastapi", "langchain.chat_models", "langchain.embeddings", "langchain.agents",
    "langchain_core", "langgraph", "langchain_mcp_adapters", "mcp.client", "mcp.server", "mcp.shared",
    "langchain_openai", "langchain_xai", "langchain_google_genai", "langchain_anthropic", "keyring",
]  # not all of "mcp" or "langchain": their optional command-line parts need packages we do not ship
OPTIONAL = ["uiautomation", "comtypes", "playwright", "discord", "slack_sdk"]


VENV = HERE / "build" / "venv"
EXTRAS = "models,browser,desktop,channels"


def ensure_clean_env() -> None:
    """Build inside a fresh environment holding only agentd's own dependencies, so nothing else gets bundled."""
    python = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if Path(sys.executable).resolve() == python.resolve():
        return
    if not python.exists():
        print("Creating a clean build environment...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-q", f"{ROOT}[{EXTRAS}]", "pyinstaller>=6.10"], check=True)
    sys.exit(subprocess.run([str(python), str(Path(__file__).resolve())]).returncode)


def main() -> int:
    import importlib.util

    ensure_clean_env()

    args = [sys.executable, "-m", "PyInstaller", str(HERE / "runtime_entry.py"), "--name", "agentd", "--noconfirm",
            "--console", "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--collect-data", "agentd", "--hidden-import", "agentd.server.app", "--hidden-import", "agentd.tools.mcp_server"]
    for package in COLLECT + [p for p in OPTIONAL if importlib.util.find_spec(p)]:
        args += ["--collect-submodules", package]
    for package in ("playwright", "uiautomation", "certifi", "tiktoken"):
        if importlib.util.find_spec(package):
            args += ["--collect-data", package]
    print("Building the runtime (this takes a few minutes)...", flush=True)
    done = subprocess.run(args, cwd=ROOT)
    if done.returncode != 0:
        return done.returncode
    built = WORK / "dist" / "agentd"
    for item in OUT.iterdir() if OUT.exists() else []:
        if item.name != "README.txt":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    OUT.mkdir(exist_ok=True)
    for item in built.iterdir():
        target = OUT / item.name
        shutil.copytree(item, target) if item.is_dir() else shutil.copy2(item, target)
    print(f"Runtime ready in {OUT} ({sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file()) // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
