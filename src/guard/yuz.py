"""Yuz tanima: YuNet ile tespit, SFace ile eslestirme."""
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from guard.ayarlar import MODEL_ADLARI, kaynak_adaylari, veri_klasoru

KLASOR = veri_klasoru()

HZ = 6              # saniyede tanima sayisi
ESIK = 0.363        # SFace cosine esigi
TESPIT_SKOR = 0.9
TANIDIK_SURE = 3.0  # tanidik yuz kaybolsa da bu kadar sn tanidik sayilir


# Hugging Face aynaları (OpenCV Zoo LFS sorunlarını aşar)
_MODEL_URL = {
    "face_detection_yunet_2023mar.onnx":
        "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
}


def _indir_model(url: str, yol: Path) -> None:
    """Basit progress ile model indirir (urllib, ek paket yok)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "GuardSecurity"})
    with urllib.request.urlopen(req, timeout=120) as r, open(yol, "wb") as f:
        toplam = int(r.headers.get("Content-Length") or 0)
        alinan = 0
        while True:
            parca = r.read(1 << 16)
            if not parca:
                break
            f.write(parca)
            alinan += len(parca)
            if toplam:
                print(f"\r  {yol.name}: %{alinan * 100 // toplam}", end="", flush=True)
    print()


def modelleri_hazirla(ana_klasor: Path = KLASOR) -> Path:
    """Eksik ONNX modellerini kopyalar; yoksa internetten indirir."""
    hedef = ana_klasor / "modeller"
    hedef.mkdir(parents=True, exist_ok=True)
    for ad in MODEL_ADLARI:
        dosya = hedef / ad
        if dosya.exists() and dosya.stat().st_size > 10_000:
            continue
        for klasor in kaynak_adaylari("modeller"):
            kaynak = klasor / ad
            if kaynak.exists():
                try:
                    shutil.copy2(kaynak, dosya)
                    break
                except OSError:
                    pass
        if dosya.exists() and dosya.stat().st_size > 10_000:
            continue
        url = _MODEL_URL.get(ad)
        if not url:
            continue
        try:
            print(f"Model indiriliyor: {ad}")
            gecici = dosya.with_suffix(".part")
            _indir_model(url, gecici)
            gecici.replace(dosya)
        except Exception as e:
            print(f"Model indirilemedi ({ad}): {e}")
    return hedef


def _normalle(m: np.ndarray) -> np.ndarray:
    m = np.atleast_2d(m).astype(np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


class Taniyici:
    def __init__(self, ana_klasor: Path | None = None):
        self.klasor = Path(ana_klasor) if ana_klasor else KLASOR
        self.model_dir = modelleri_hazirla(self.klasor)
        self.yuz_dir = self.klasor / "yuzler"
        self.yuz_dir.mkdir(parents=True, exist_ok=True)

        yunet = self.model_dir / MODEL_ADLARI[0]
        sface = self.model_dir / MODEL_ADLARI[1]
        if not yunet.exists() or not sface.exists():
            raise FileNotFoundError(f"Yuz tanima modelleri bulunamadi: {self.model_dir}")

        self.det = cv2.FaceDetectorYN.create(str(yunet), "", (640, 480), TESPIT_SKOR)
        self.rec = cv2.FaceRecognizerSF.create(str(sface), "")
        self.kisiler: dict[str, np.ndarray] = {}
        self.kisileri_yukle()
        self._son_calisma = 0.0
        self._son_tanidik = 0.0
        self.son_isim = None
        self.son_skor = 0.0

    def kisileri_yukle(self) -> None:
        self.kisiler.clear()
        for f in self.yuz_dir.glob("*.npy"):
            try:
                self.kisiler[f.stem] = _normalle(np.load(f))
            except Exception:
                pass

    def _eslesen_isim(self, vektor):
        # SFace cosine skoru, normalize vektorlerin nokta carpimidir
        v = _normalle(vektor)[0]
        en_iyi, en_iyi_skor = None, -1.0
        for isim, ornekler in self.kisiler.items():
            skor = float((ornekler @ v).max())
            if skor > en_iyi_skor:
                en_iyi, en_iyi_skor = isim, skor
        self.son_skor = en_iyi_skor if en_iyi_skor > 0 else 0.0
        return en_iyi if en_iyi_skor >= ESIK else None

    def bak(self, kare):
        simdi = time.time()
        if simdi - self._son_calisma < 1.0 / HZ:
            return None
        self._son_calisma = simdi

        if not self.kisiler:
            return "YUZ_YOK"

        self.det.setInputSize((kare.shape[1], kare.shape[0]))
        _, yuzler = self.det.detect(kare)
        if yuzler is None or len(yuzler) == 0:
            return "TANIDIK" if simdi - self._son_tanidik < TANIDIK_SURE else "YUZ_YOK"

        yabanci_var = False
        for y in yuzler:
            vektor = self.rec.feature(self.rec.alignCrop(kare, y))[0]
            isim = self._eslesen_isim(vektor)
            if isim:
                self._son_tanidik = simdi
                self.son_isim = isim
            else:
                yabanci_var = True
        return "YABANCI" if yabanci_var else "TANIDIK"
