"""Guard kurucusu — sadece standart kütüphane (küçük exe).

GitHub release'ten GuardSecurity.exe indirir, SHA256 doğrular,
yüz modellerini (YuNet + SFace) progress bar ile çeker,
%LOCALAPPDATA%\\GuardSecurity altına koyar ve masaüstü kısayolu oluşturur.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "YakupAkn/guard"
EXE = "GuardSecurity.exe"
HEDEF = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "GuardSecurity"
# Ana uygulama modelleri %APPDATA%\GuardSecurity\modeller altinda arar
APPDATA_GUARD = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "GuardSecurity"
MODELLER_DIR = APPDATA_GUARD / "modeller"
UA = {"User-Agent": "guard-kurulum/1.0"}

# OpenCV Zoo / Hugging Face ayna
MODELLER = (
    {
        "ad": "face_detection_yunet_2023mar.onnx",
        "url": "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "sha256": None,
        "mb": 0.23,
    },
    {
        "ad": "face_recognition_sface_2021dec.onnx",
        "url": "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "mb": 38.7,
    },
)


def _ac(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60)


def sha256_dosya(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        while parca := f.read(1 << 20):
            h.update(parca)
    return h.hexdigest()


def indir(url: str, yol: Path, etiket: str = "") -> None:
    """Parça parça indirir, satırda yüzde gösterir."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(yol.suffix + ".part")
    with _ac(url) as r, open(gecici, "wb") as f:
        toplam = int(r.headers.get("Content-Length") or 0)
        alinan = 0
        while True:
            parca = r.read(1 << 16)
            if not parca:
                break
            f.write(parca)
            alinan += len(parca)
            if toplam:
                yuzde = alinan * 100 // toplam
                mb = alinan / (1024 * 1024)
                sys.stdout.write(f"\r  {etiket or yol.name}: %{yuzde:3d}  ({mb:.1f} MB)   ")
            else:
                sys.stdout.write(f"\r  {etiket or yol.name}: {alinan // 1024} KB   ")
            sys.stdout.flush()
    print()
    os.replace(gecici, yol)


def kisayol_olustur(ad: str, hedef: Path, calisma: Path | None = None) -> Path | None:
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                "[Environment]::GetFolderPath('Desktop')",
            ],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        desk = Path(r.stdout.decode("utf-8", "replace").strip() or str(Path.home() / "Desktop"))
    except Exception:
        desk = Path.home() / "Desktop"

    lnk = desk / f"{ad}.lnk"
    env = dict(os.environ)
    env.update(
        {
            "K_LNK": str(lnk),
            "K_HEDEF": str(hedef),
            "K_ARG": "",
            "K_CALISMA": str(calisma or hedef.parent),
            "K_IKON": f"{hedef},0",
            "K_ACIKLAMA": ad,
        }
    )
    betik = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK);"
        "$s.TargetPath=$env:K_HEDEF;$s.Arguments=$env:K_ARG;"
        "$s.WorkingDirectory=$env:K_CALISMA;$s.IconLocation=$env:K_IKON;"
        "$s.Description=$env:K_ACIKLAMA;$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", betik],
            env=env,
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return lnk if r.returncode == 0 and lnk.exists() else None
    except Exception:
        return None


def modelleri_indir() -> None:
    MODELLER_DIR.mkdir(parents=True, exist_ok=True)
    for m in MODELLER:
        hedef = MODELLER_DIR / m["ad"]
        if hedef.exists() and hedef.stat().st_size > 10_000:
            print(f"  {m['ad']}: zaten var, atlandi.")
            continue
        print(f"  model indiriliyor (~{m['mb']} MB)...")
        indir(m["url"], hedef, m["ad"])
        if m.get("sha256"):
            gercek = sha256_dosya(hedef)
            if gercek != m["sha256"]:
                hedef.unlink(missing_ok=True)
                raise RuntimeError(
                    f"SHA256 uyusmadi: {m['ad']}\n  beklenen {m['sha256']}\n  gelen   {gercek}"
                )


def exe_indir() -> Path:
    try:
        with _ac(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
            surum = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Surum bilgisi alinamadi (HTTP {e.code}). Repo public mi, release var mi?"
        ) from e

    etiket = surum["tag_name"]
    dosyalar = {a["name"]: a["browser_download_url"] for a in surum.get("assets", [])}
    if EXE not in dosyalar:
        raise RuntimeError(f"Release icinde {EXE} yok. Assets: {list(dosyalar)}")

    HEDEF.mkdir(parents=True, exist_ok=True)
    exe = HEDEF / EXE
    surum_dosyasi = HEDEF / "surum.txt"
    guncel = (
        exe.exists()
        and surum_dosyasi.exists()
        and surum_dosyasi.read_text().strip() == etiket
    )

    if guncel:
        print(f"Guard zaten guncel ({etiket}).")
        return exe

    print(f"Guard {etiket} indiriliyor...")
    indir(dosyalar[EXE], exe, EXE)

    if EXE + ".sha256" in dosyalar:
        beklenen = _ac(dosyalar[EXE + ".sha256"]).read().decode().split()[0].lower()
        gercek = sha256_dosya(exe)
        if gercek != beklenen:
            exe.unlink(missing_ok=True)
            raise RuntimeError("GuardSecurity.exe SHA256 uyusmadi, kurulum iptal.")

    surum_dosyasi.write_text(etiket)
    return exe


def kur() -> int:
    print("Guard kurulumu")
    print(f"Hedef: {HEDEF}\n")

    try:
        exe = exe_indir()
        print("\nYuz modelleri:")
        modelleri_indir()
    except Exception as e:
        print(f"\nHata: {e}")
        return 1

    lnk = kisayol_olustur("Guard Security", exe, HEDEF)
    print(f"\nKisayol: {lnk}" if lnk else "\nKisayol olusturulamadi.")

    cevap = input("Guard simdi baslatilsin mi? [E/h]: ").strip().lower()
    if cevap != "h":
        subprocess.Popen([str(exe)], cwd=str(HEDEF), creationflags=0x00000008)
    return 0


if __name__ == "__main__":
    kod = 1
    try:
        kod = kur()
    except KeyboardInterrupt:
        print("\nIptal.")
    except Exception as e:
        print(f"Hata: {e}")
    input("Kapatmak icin Enter...")
    sys.exit(kod)
