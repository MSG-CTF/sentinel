"""Export pinned local Git objects and create local-only service credentials."""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile

LOCAL = Path(__file__).resolve().parent
ROOT = LOCAL.parent.parent
SOURCES = {
    "scheduler": (ROOT / "instance-scheduler", "5c695142871a5a926b96d7befcaf0832c691933f"),
    "broker": (ROOT / "resource-broker", "99ff5b884e4bf2198dabca84a179c11f5a516785"),
    "frontend": (ROOT.parent / "front-team", "c0a85359be9878a212ab7b7ac955b898844430c5"),
    "runtime": (ROOT / "secure-provisioner", "19d2fdffef753bb9415861c4a6b382b6408455ea"),
}


def prepare():
    for name, (repository, revision) in SOURCES.items():
        destination = LOCAL / ".sources" / name
        marker = destination / ".sentinel-source"
        if marker.exists():
            if marker.read_text().strip() != revision:
                raise RuntimeError(f"Source revision mismatch: {name}")
            continue
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError(f"Refusing to overwrite an existing source directory: {name}")
        archive = subprocess.run(
            ["git", "-C", str(repository), "archive", "--format=tar", revision],
            check=True, capture_output=True, timeout=60,
        )
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
            source.extractall(destination, filter="data")
        marker.write_text(revision + "\n")
    secret_dir = LOCAL / ".assets"
    secret_dir.mkdir(exist_ok=True)
    secret_file = secret_dir / "services.env"
    if not secret_file.exists():
        keys = ["SCHEDULER_API_TOKEN", "SCHEDULER_BROKER_TOKEN", "SCHEDULER_RUNTIME_TOKEN",
                "INVENTORY_API_TOKEN", "ADMIN_TOKEN_SECRET", "ADMIN_PASSWORD", "RUNTIME_API_TOKEN"]
        contents = "".join(f"{key}={secrets.token_urlsafe(48)}\n" for key in keys)
        with secret_file.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(contents)
        os.chmod(secret_file, 0o600)
    values = dict(line.split("=", 1) for line in secret_file.read_text().splitlines() if "=" in line)
    bindings = {
        "scheduler.env": {key: key for key in ("SCHEDULER_API_TOKEN", "SCHEDULER_BROKER_TOKEN", "SCHEDULER_RUNTIME_TOKEN")},
        "broker.env": {"SCHEDULER_API_TOKEN": "SCHEDULER_BROKER_TOKEN", **{key: key for key in ("INVENTORY_API_TOKEN", "ADMIN_TOKEN_SECRET", "ADMIN_PASSWORD", "RUNTIME_API_TOKEN")}},
        "monitor.env": {key: key for key in ("SCHEDULER_API_TOKEN", "INVENTORY_API_TOKEN")},
        "runtime.env": {"PROVISIONER_SERVICE_TOKEN": "SCHEDULER_RUNTIME_TOKEN"},
    }
    for filename, mapping in bindings.items():
        path = secret_dir / filename
        path.write_text("".join(f"{name}={values[key]}\n" for name, key in mapping.items()), encoding="utf-8", newline="\n")
        os.chmod(path, 0o600)
    print(json.dumps({"prepared": list(SOURCES), "credentials": "local/.assets/services.env", "tokens_printed": False}))


if __name__ == "__main__":
    prepare()
