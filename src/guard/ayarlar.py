"""Yollar ve ortak sabitler."""
import os
import sys
from pathlib import Path

UYGULAMA = "GuardSecurity"
MODEL_ADLARI = (
    "face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx",
)
# Gorev Zamanlayici betikleri failed.flag / unlock.flag dosyalarini burada olusturuyor
ESKI_KLASOR = Path(r"C:\Projeler\guard")


def veri_klasoru() -> Path:
    appdata = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    hedef = Path(appdata) / UYGULAMA
    hedef.mkdir(parents=True, exist_ok=True)
    return hedef


def paketlenmis() -> bool:
    return getattr(sys, "frozen", False)


def kaynak_adaylari(alt: str = "") -> list[Path]:
    """Dashboard ve modellerin aranacagi klasorler, oncelik sirasiyla."""
    kokler = []
    if paketlenmis() and hasattr(sys, "_MEIPASS"):
        kokler.append(Path(sys._MEIPASS))
    kokler += [Path(__file__).resolve().parent, Path.cwd(), ESKI_KLASOR]
    return [k / alt if alt else k for k in kokler]
