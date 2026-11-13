#!/usr/bin/env python3
"""Собирает готовые .exe и .run с личными Python/Tk, .NET и Piper.

python3 tools/build-release.py --target linux-x64
python3 tools/build-release.py --target windows-x64   # нужен makensis
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tarfile
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def download(name, spec, cache):
    path = cache / name
    def valid():
        if not path.is_file():
            return False
        digest = hashlib.new(spec["algorithm"])
        with path.open("rb") as src:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == spec["hash"].lower()
    if not valid():
        print(f"Загрузка {name}…", flush=True)
        part = cache / (name+".part")
        try:
            request = urllib.request.Request(spec["url"], headers={"User-Agent": "LocalDub-build"})
            with urllib.request.urlopen(request, timeout=90) as src, part.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
        if not valid():
            path.unlink(missing_ok=True)
            raise RuntimeError(f"Контрольная сумма не совпала: {name}")
    return path


def unpack(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as src:
            for item in src.infolist():
                target = (dest / item.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError("Некорректный путь в архиве")
            src.extractall(dest)
    else:
        with tarfile.open(archive) as src:
            src.extractall(dest, filter="data")


def mszip_cab(blob):
    """Читает MSZIP CAB Microsoft, не запуская установщик редистрибутива."""
    if blob[:4] != b"MSCF":
        raise ValueError("Не CAB")
    files_offset = struct.unpack_from("<I", blob, 16)[0]
    folder_count, file_count, flags = struct.unpack_from("<HHH", blob, 26)
    if flags & 3:
        raise ValueError("Многотомный CAB не поддерживается")
    folder_reserve = data_reserve = 0
    pos = 36
    if flags & 4:
        header_reserve, folder_reserve, data_reserve = struct.unpack_from("<HBB", blob, pos)
        pos += 4+header_reserve
    folders = []
    for _ in range(folder_count):
        offset, count, compression = struct.unpack_from("<IHH", blob, pos)
        if compression != 1:
            raise ValueError("Ожидался MSZIP")
        pos += 8+folder_reserve
        data = bytearray()
        for _ in range(count):
            packed, unpacked = struct.unpack_from("<HH", blob, offset+4)
            offset += 8+data_reserve
            block = blob[offset:offset+packed]
            if block[:2] != b"CK":
                raise ValueError("Неверный блок MSZIP")
            decoder = zlib.decompressobj(-15, zdict=bytes(data[-32768:])) if data else zlib.decompressobj(-15)
            decoded = decoder.decompress(block[2:])+decoder.flush()
            if len(decoded) != unpacked:
                raise ValueError("Неверный размер блока MSZIP")
            data.extend(decoded)
            offset += packed
        folders.append(data)
    files = {}
    pos = files_offset
    for _ in range(file_count):
        size, offset, folder = struct.unpack_from("<IIH", blob, pos)
        pos += 16
        end = blob.index(b"\0", pos)
        name = blob[pos:end].decode("utf-8")
        pos = end+1
        files[name] = bytes(folders[folder][offset:offset+size])
    return files


def extract_vc_runtime(exe, destination):
    # У закреплённого VC_redist два CAB: интерфейс установки и payload с MSI/CAB.
    blob = exe.read_bytes()
    pos = 0
    required = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
    found = {}
    while (pos := blob.find(b"MSCF", pos)) != -1:
        size = struct.unpack_from("<I", blob, pos+8)[0]
        files = mszip_cab(blob[pos:pos+size])
        for data in files.values():
            if data[:4] != b"MSCF":
                continue
            for name, dll in mszip_cab(data).items():
                if name.endswith("_amd64") and name[:-6] in required:
                    found[name[:-6]] = dll
        pos += size
    if set(found) != required:
        raise RuntimeError("В официальном VC_redist не найдены все x64 DLL")
    for name, data in found.items():
        (destination / name).write_bytes(data)


def build(target, options, lock):
    stage = options.build / target
    if stage.exists():
        shutil.rmtree(stage)
    payload = stage / "payload"
    payload.mkdir(parents=True)
    for name in ("localdub.py",):
        shutil.copy2(ROOT / name, payload / name)
    shutil.copy2(ROOT / "packaging/boot.py", payload / "boot.py")
    for name in ("ld", "web"):
        shutil.copytree(ROOT / name, payload / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "wtd/prebuilt", payload / "wtd/prebuilt")
    runtime = payload / "runtime"
    python_archive = download(target+"-python", lock[target+"-python"], options.cache)
    unpack(python_archive, runtime)
    unpack(download(target+"-dotnet", lock[target+"-dotnet"], options.cache), runtime / "dotnet")
    unpack(download(target+"-piper", lock[target+"-piper"], options.cache), runtime)
    if target == "windows-x64":
        extract_vc_runtime(download("windows-x64-vcredist", lock["windows-x64-vcredist"], options.cache), runtime / "piper")
    voices = runtime / "voices"
    voices.mkdir(parents=True)
    for name in ("ru_RU-dmitri-medium.onnx", "ru_RU-dmitri-medium.onnx.json", "MODEL_CARD"):
        shutil.copy2(download("voice-"+name, lock["voice-"+name], options.cache), voices / name)
    licenses = payload / "LICENSES"
    licenses.mkdir()
    shutil.copytree(ROOT / "packaging/licenses", licenses, dirs_exist_ok=True)
    shutil.copy2(ROOT / "packaging/THIRD_PARTY.md", licenses / "README.md")
    shutil.copy2(ROOT / "packaging/runtime-lock.json", licenses / "runtime-lock.json")
    sources = licenses / "sources"
    sources.mkdir()
    shutil.copy2(download("espeak-source", lock["espeak-source"], options.cache), sources / "espeak-ng.zip")
    # Не везём pip, тесты Python и заголовки для сборки расширений.
    python = runtime / "python"
    for pattern in ("**/__pycache__", "**/site-packages/pip", "**/site-packages/pip-*.dist-info", "**/site-packages/setuptools", "**/site-packages/setuptools-*.dist-info"):
        for path in python.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
    for path in list(python.rglob("test")) + list(python.rglob("tests")):
        if path.is_dir():
            shutil.rmtree(path)
    # Отпечаток всех файлов определяет отдельную папку готовой версии.
    digest = hashlib.sha256()
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(payload).as_posix().encode())
            digest.update(path.read_bytes())
    build_id = digest.hexdigest()[:16]
    if target == "linux-x64":
        output = options.output / "LocalDub-linux-x64.run"
        script = (ROOT / "packaging/localdub.run.sh").read_text().replace("@BUILD_ID@", build_id)
        script = script.replace("@PAYLOAD_LINE@", str(script.count("\n")+1))
        with output.open("wb") as out:
            out.write(script.encode())
            with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as archive:
                for path in sorted(payload.iterdir()):
                    archive.add(path, arcname=path.name)
        output.chmod(0o755)
    else:
        output = options.output / "LocalDub-windows-x64.exe"
        compiler = options.makensis or shutil.which("makensis")
        if not compiler:
            raise RuntimeError("Для .exe нужен NSIS: установите makensis или передайте --makensis")
        command = [compiler, f"-DOUTPUT={output}", f"-DPAYLOAD={payload}", f"-DBUILD_ID={build_id}", str(ROOT / "packaging/localdub.nsi")]
        # Windows makensis принимает /D; в Linux — -D.
        if os.name == "nt":
            command = [compiler] + ["/"+arg[1:] if arg.startswith("-D") else arg for arg in command[1:]]
        subprocess.run(command, check=True)
    print(f"Готово: {output} ({output.stat().st_size//1048576} МиБ)", flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["linux-x64", "windows-x64", "all"], required=True)
    parser.add_argument("--cache", type=Path, default=Path.home()/".cache/localdub-build")
    parser.add_argument("--build", type=Path, default=ROOT/"build/release")
    parser.add_argument("--output", type=Path, default=ROOT/"dist")
    parser.add_argument("--makensis")
    options = parser.parse_args()
    for key in ("cache", "build", "output"):
        path = getattr(options, key).resolve()
        path.mkdir(parents=True, exist_ok=True)
        setattr(options, key, path)
    lock = json.loads((ROOT/"packaging/runtime-lock.json").read_text())
    targets = ["linux-x64", "windows-x64"] if options.target == "all" else [options.target]
    outputs = [build(target, options, lock) for target in targets]
    manifest = options.output/"SHA256SUMS.txt"
    manifest.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in sorted(options.output.iterdir()) if path.suffix in (".exe", ".run")), encoding="utf-8")


if __name__ == "__main__":
    main()
