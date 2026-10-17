"""Build the pinned Runtime export without installing tools system-wide."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

from prepare_assets import LOCAL, SOURCES, prepare


def build():
    prepare()
    tool_dir = LOCAL / ".tooling" / "go-1.26"
    executable = tool_dir / "go/bin/go.exe"
    receipt = tool_dir / "download.json"
    if not executable.exists():
        with urllib.request.urlopen("https://go.dev/dl/?mode=json&include=all", timeout=30) as response:
            releases = json.load(response)
        release = next(item for item in releases if item["version"].startswith("go1.26.") and item["stable"])
        archive = next(item for item in release["files"] if item["os"] == "windows"
                       and item["arch"] == "amd64" and item["kind"] == "archive")
        tool_dir.mkdir(parents=True, exist_ok=True)
        download = tool_dir / archive["filename"]
        with urllib.request.urlopen("https://go.dev/dl/" + archive["filename"], timeout=120) as response, download.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        with download.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != archive["sha256"] or download.stat().st_size != archive["size"]:
            raise RuntimeError("Go archive checksum mismatch")
        with zipfile.ZipFile(download) as package:
            for member in package.namelist():
                if not (tool_dir / member).resolve().is_relative_to(tool_dir.resolve()):
                    raise RuntimeError("Unsafe Go archive member")
            package.extractall(tool_dir)
        receipt.write_text(json.dumps({"version": release["version"], "sha256": actual}), encoding="utf-8")
    if not receipt.exists():
        raise RuntimeError("Go download verification receipt missing")
    destination = LOCAL / ".execution/build"
    destination.mkdir(parents=True, exist_ok=True)
    build_env = dict(os.environ, GOOS="linux", GOARCH="amd64", CGO_ENABLED="0",
                     GOTOOLCHAIN="local", GOMODCACHE=str(tool_dir / "modules"),
                     GOCACHE=str(tool_dir / "build-cache"))
    subprocess.run([str(executable), "build", "-trimpath", "-ldflags=-s -w", "-o",
                    str(destination / "provisioner"), "./cmd/provisioner"],
                   cwd=LOCAL / ".sources/runtime", env=build_env, timeout=900, check=True)
    with (destination / "provisioner").open("rb") as stream:
        binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    (destination / "build.json").write_text(json.dumps({
        "source_revision": SOURCES["runtime"][1], "binary_sha256": binary_hash,
        "toolchain": json.loads(receipt.read_text()),
    }, indent=2), encoding="utf-8")
    print(json.dumps({"built": "msg-sla-runtime:19d2fdff", "binary_sha256": binary_hash}))


if __name__ == "__main__":
    build()
