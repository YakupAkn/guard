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


def modelleri_hazirla(ana_klasor: Path = KLASOR) -> Path:
    """Eksik ONNX modellerini bulunabildigi yerden AppData'ya kopyalar."""
    hedef = ana_klasor / "modeller"
    hedef.mkdir(parents=True, exist_ok=True)
    for ad in MODEL_ADLARI:
        dosya = hedef / ad
        if dosya.exists():
            continue
        for klasor in kaynak_adaylari("modeller"):
            if (klasor / ad).exists():
                try:
                    shutil.copy2(klasor / ad, dosya)
                    break
                except OSError:
                    pass
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
