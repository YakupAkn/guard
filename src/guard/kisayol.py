"""Masaustu kisayolu olusturur. Sadece standart kutuphane, PowerShell kullanir."""
import os
import subprocess
from pathlib import Path

_GIZLI = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_LNK_BETIGI = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:K_LNK);"
    "$s.TargetPath = $env:K_HEDEF; $s.Arguments = $env:K_ARG;"
    "$s.WorkingDirectory = $env:K_CALISMA; $s.IconLocation = $env:K_IKON;"
    "$s.Description = $env:K_ACIKLAMA; $s.Save()"
)


def masaustu_yolu() -> Path:
    """OneDrive ile tasinmis masaustunu da bulur."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, timeout=10, creationflags=_GIZLI,
        )
        yol = r.stdout.decode("utf-8", "replace").strip()
        if yol:
            return Path(yol)
    except Exception:
        pass
    return Path.home() / "Desktop"


def kisayol_olustur(ad: str, hedef, arguman: str = "", calisma=None) -> Path | None:
    """Masaustune ad.lnk koyar. Basarisizsa None dondurur."""
    hedef = Path(hedef)
    lnk = masaustu_yolu() / f"{ad}.lnk"
    env = dict(os.environ)
    env.update({
        "K_LNK": str(lnk),
        "K_HEDEF": str(hedef),
        "K_ARG": arguman,
        "K_CALISMA": str(calisma or hedef.parent),
        "K_IKON": f"{hedef},0",
        "K_ACIKLAMA": ad,
    })
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _LNK_BETIGI],
            env=env, capture_output=True, timeout=15, creationflags=_GIZLI,
        )
        return lnk if r.returncode == 0 and lnk.exists() else None
    except Exception:
        return None
