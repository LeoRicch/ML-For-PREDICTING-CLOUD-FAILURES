"""Build an offline Windows ZIP from this checkout and its tested Python environment.

Run using the project's .venv Python after SETUP.bat. No absolute developer paths,
existing release ZIP or network download is needed. Runtime files stay out of Git.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import sysconfig
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "Sarah_Cloud_Lab_OFFLINE_Windows_x64.zip"


def copy_tree(source, destination, stdlib=False):
    source = Path(source)
    def ignore(directory, names):
        excluded = {"__pycache__", ".pytest_cache"}
        if stdlib and Path(directory) == source:
            excluded |= {"site-packages", "test", "idlelib", "tkinter", "turtledemo"}
        return [n for n in names if n in excluded or n.endswith(".pyc")]
    shutil.copytree(source, destination, ignore=ignore)


def application_files(root, version):
    root = Path(root)
    if Path(version).name != version or version in (".", ".."):
        raise ValueError("Invalid selected model version")
    files = []
    for name in ("cloudlab", "static", "templates", "tests", "scripts", "docs"):
        files.extend(p for p in (root/name).rglob("*") if p.is_file() and
                     "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.extend(p for p in root.iterdir() if p.is_file() and
                 p.suffix in (".py", ".bat", ".md", ".txt", ".ini"))
    files.append(root/"models/current.json")
    files.extend(p for p in (root/"models"/version).iterdir() if p.is_file() and
                 p.name != "selected_candidate.joblib")
    return sorted(files)


def write_manifest(stage):
    stage = Path(stage)
    manifest = {str(p.relative_to(stage)).replace("\\", "/"):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(stage.rglob("*")) if p.is_file() and p.name != "OFFLINE_MANIFEST.json"}
    (stage/"OFFLINE_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify_archive(path):
    prefix = "Sarah Cloud Lab Offline/"
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity verification failed")
        manifest = json.loads(archive.read(prefix+"OFFLINE_MANIFEST.json"))
        for name, expected in manifest.items():
            if hashlib.sha256(archive.read(prefix+name)).hexdigest() != expected:
                raise ValueError(f"Archive hash mismatch: {name}")
    return len(manifest)


def check_environment(root):
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise ValueError("Build this Windows package on 64-bit Windows.")
    if sys.version_info[:2] != (3, 14):
        raise ValueError("Use the tested Python 3.14 environment to bundle the saved model.")
    for line in (root/"requirements-lock.txt").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==", 1)
        if metadata.version(name) != version:
            raise ValueError(f"Install the locked dependency version: {name}=={version}")


def build(root=ROOT, output=None):
    root = Path(root).resolve()
    check_environment(root)
    python_home = Path(sys.base_prefix)
    site_packages = Path(sysconfig.get_path("platlib"))
    work = root/"work"
    work.mkdir(exist_ok=True)
    stage = work/("offline-build-"+uuid.uuid4().hex[:12])
    stage.mkdir(exist_ok=False)
    version = json.loads((root/"models/current.json").read_text(encoding="utf-8"))["version"]
    print("Copying application and selected model…", flush=True)
    for source in application_files(root, version):
        target = stage/source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    runtime = stage/"runtime"
    runtime.mkdir()
    print("Bundling Python and installed libraries…", flush=True)
    for name in ("python.exe", "pythonw.exe", "python3.dll", "python314.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        shutil.copy2(python_home/name, runtime/name)
    copy_tree(python_home/"DLLs", runtime/"DLLs")
    copy_tree(python_home/"Lib", runtime/"Lib", stdlib=True)
    copy_tree(site_packages, runtime/"Lib/site-packages")
    (runtime/"python314._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n..\nimport site\n", encoding="utf-8")
    (stage/"READ ME FIRST.txt").write_text(
        "SARAH CLOUD LAB - OFFLINE WINDOWS EDITION\n\n"
        "1. Copy this ZIP to the other 64-bit Windows computer.\n"
        "2. Right-click the ZIP and choose Extract All.\n"
        "3. Open the extracted Sarah Cloud Lab Offline folder.\n"
        "4. Double-click START_APP.bat. No setup or Internet is required.\n"
        "5. Keep the terminal window open. If the browser does not open, visit\n"
        "   http://127.0.0.1:5057 manually while the terminal is running.\n\n"
        "Python, dependencies, the trained model and retry recovery are included.\n"
        "If startup fails, retain the message and data/startup-error.log.\n"
        "Python's licence is runtime/LICENSE.txt; dependency notices remain in\n"
        "their dist-info directories under runtime/Lib/site-packages.\n",
        encoding="utf-8")
    readme = stage/"README.md"
    readme.write_text("# Offline Windows edition\n\nExtract the ENTIRE ZIP and double-click START_APP.bat. "
        "Python and all dependencies are included; no setup or Internet is required. "
        "The source-edition setup instructions below do not apply to this ZIP.\n\n"+
        readme.read_text(encoding="utf-8"), encoding="utf-8")
    (stage/"OFFLINE_BUILD.json").write_text(json.dumps(dict(
        created_utc=datetime.now(timezone.utc).isoformat(), python=sys.version.split()[0],
        platform="Windows x64", model_version=version), indent=2), encoding="utf-8")
    write_manifest(stage)
    output = Path(output).resolve() if output else root/"dist"/ARCHIVE_NAME
    if output.is_relative_to(stage):
        raise ValueError("The ZIP destination must be outside its staging directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    print("Compressing offline ZIP…", flush=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                archive.write(p, "Sarah Cloud Lab Offline/"+str(p.relative_to(stage)).replace("\\", "/"))
    verified = verify_archive(output)
    print(json.dumps(dict(archive=str(output),bytes=output.stat().st_size,
        files_verified=verified,stage=str(stage)), indent=2), flush=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Destination ZIP; defaults to dist/")
    args = parser.parse_args()
    build(output=args.output)
