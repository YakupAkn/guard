"""Guard kurucusu: son surumu GitHub release'ten indirir ve masaustune kisayol koyar.
Exe'si kucuk kalsin diye sadece standart kutuphane kullanir."""
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
BASLIKLAR = {"User-Agent": "guard-kurulum"}


def kisayol_olustur(ad, hedef, arguman="", calisma=None):
    """Bağımlılıksız masaüstü kısayolu (PowerShell)."""
    import os, subprocess
    from pathlib import Path
    hedef = Path(hedef)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8;[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        desk = Path(r.stdout.decode("utf-8", "replace").strip() or str(Path.home() / "Desktop"))
    except Exception:
        desk = Path.home() / "Desktop"
    lnk = desk / f"{ad}.lnk"
    env = dict(os.environ)
    env.update({
        "K_LNK": str(lnk), "K_HEDEF": str(hedef), "K_ARG": arguman,
        "K_CALISMA": str(calisma or hedef.parent), "K_IKON": f"{hedef},0", "K_ACIKLAMA": ad,
    })
    betik = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK);"
        "$s.TargetPath=$env:K_HEDEF;$s.Arguments=$env:K_ARG;"
        "$s.WorkingDirectory=$env:K_CALISMA;$s.IconLocation=$env:K_IKON;"
        "$s.Description=$env:K_ACIKLAMA;$s.Save()"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", betik],
                           env=env, capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return lnk if r.returncode == 0 and lnk.exists() else None
    except Exception:
        return None



def _ac(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=BASLIKLAR), timeout=30)


def indir(url: str, yol: Path) -> None:
    with _ac(url) as r, open(yol, "wb") as f:
        toplam = int(r.headers.get("Content-Length") or 0)
        alinan = 0
        while parca := r.read(1 << 16):
            f.write(parca)
            alinan += len(parca)
            if toplam:
                print(f"\r  indiriliyor %{alinan * 100 // toplam}", end="", flush=True)
    print()


def sha256(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        while parca := f.read(1 << 20):
            h.update(parca)
    return h.hexdigest()


def kur() -> int:
    try:
        with _ac(f"https://api.github.com/repos/{REPO}/releases/latest") as r:
            surum = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"Surum bilgisi alinamadi (HTTP {e.code}). Depo herkese acik mi, release var mi?")
        return 1

    etiket = surum["tag_name"]
    dosyalar = {a["name"]: a["browser_download_url"] for a in surum.get("assets", [])}
    if EXE not in dosyalar or EXE + ".sha256" not in dosyalar:
        print(f"Release icinde {EXE} ve {EXE}.sha256 olmali.")
        return 1

    HEDEF.mkdir(parents=True, exist_ok=True)
    exe = HEDEF / EXE
    surum_dosyasi = HEDEF / "surum.txt"
    guncel = exe.exists() and surum_dosyasi.exists() and surum_dosyasi.read_text().strip() == etiket

    if guncel:
        print(f"Guard zaten guncel ({etiket}).")
    else:
        print(f"Guard {etiket} indiriliyor...")
        beklenen = _ac(dosyalar[EXE + ".sha256"]).read().decode().split()[0].lower()
        gecici = exe.with_suffix(".part")
        indir(dosyalar[EXE], gecici)
        if sha256(gecici) != beklenen:
            gecici.unlink(missing_ok=True)
            print("Dosya dogrulanamadi (sha256 uyusmuyor). Kurulum iptal edildi.")
            return 1
        try:
            os.replace(gecici, exe)
        except PermissionError:
            print("Guard calisiyor. Tepsi ikonundan Cikis yapip tekrar dene.")
            return 1
        surum_dosyasi.write_text(etiket)

    lnk = kisayol_olustur("Guard Security", exe, calisma=HEDEF)
    print(f"Kisayol: {lnk}" if lnk else "Kisayol olusturulamadi.")

    if input("Guard simdi baslatilsin mi? [E/h]: ").strip().lower() != "h":
        subprocess.Popen([str(exe)], cwd=str(HEDEF), creationflags=0x00000008)  # DETACHED_PROCESS
    return 0


if __name__ == "__main__":
    kod = 1
    try:
        kod = kur()
    except Exception as e:
        print(f"Hata: {e}")
    input("Kapatmak icin Enter'a bas...")
    sys.exit(kod)
