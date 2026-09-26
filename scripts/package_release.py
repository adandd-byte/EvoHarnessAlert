"""Build a source delivery with an explicit file allowlist and SHA-256 manifest."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
NAME = "evoharness-alert"
DIRECTORIES = {
    "app": {".py", ".md", ".json", ".html", ".css", ".js", ".svg"},
    "skills": {".md", ".py", ".json", ".yaml", ".yml"},
    "scripts": {".py", ".sh"},
    "docs": {".md", ".json"},
    "examples": {".json", ".md"},
}
FILES = (
    "README.md", "DELIVERY.md", "requirements.txt", "Dockerfile",
    "docker-compose.yml", ".env.example", ".gitignore", ".dockerignore",
    ".github/workflows/test.yml", "data/alert-sample-dataset.json",
    "models/evoharness-alert-qwen2.5-7b/Modelfile",
)


def source_files() -> list[Path]:
    paths = {ROOT / name for name in FILES}
    for directory, extensions in DIRECTORIES.items():
        paths.update(
            path for path in (ROOT / directory).rglob("*")
            if path.suffix in extensions
            and not any(part.startswith(".") or part == "__pycache__"
                        for part in path.relative_to(ROOT).parts)
        )
    paths.update((ROOT / "tests").glob("test_*.py"))
    selected = []
    for path in sorted(paths):
        # Never follow links into local credentials, cloned repositories or runtime data.
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT):
            raise ValueError(f"Symlink is not distributable: {path.relative_to(ROOT)}")
        if not path.is_file():
            raise FileNotFoundError(path)
        selected.append(path)
    return selected


def main() -> None:
    payloads = {str(path.relative_to(ROOT)): path.read_bytes() for path in source_files()}
    manifest = {
        "name": NAME,
        "delivery_status": "development_snapshot_not_production_acceptance",
        "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(payloads.items())],
    }
    payloads["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    target = ROOT / "target"
    target.mkdir(exist_ok=True)
    zip_path = target / f"{NAME}.zip"
    tar_path = target / f"{NAME}.tar.gz"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()):
            archive.writestr(f"{NAME}/{name}", data)
    with tarfile.open(tar_path, "w:gz") as archive:
        for name, data in sorted(payloads.items()):
            info = tarfile.TarInfo(f"{NAME}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
    # Read back both deliverables and verify every file, not just archive creation.
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        for name, data in payloads.items():
            assert archive.read(f"{NAME}/{name}") == data
    with tarfile.open(tar_path) as archive:
        for name, data in payloads.items():
            assert archive.extractfile(f"{NAME}/{name}").read() == data
    print(f"已生成并校验 {len(manifest['files'])} 个文件：\n{zip_path}\n{tar_path}")


if __name__ == "__main__":
    main()
