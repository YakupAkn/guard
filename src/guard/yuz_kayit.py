"""Yuz kaydi. Kullanim: python yuz_kayit.py [isim]  (isim yoksa pencere sorar)"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from guard.ayarlar import MODEL_ADLARI, veri_klasoru
from guard.kamera import kamera_ac, kamera_engeli_var_mi
from guard.yuz import modelleri_hazirla

HEDEF = 25          # alinacak ornek sayisi
MIN_ORNEK = 10      # bundan azsa kaydedilmez
ARALIK = 0.4        # ornekler arasi saniye
ESIK_SKOR = 0.9


def _isim_sor() -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog
        kok = tk.Tk()
        kok.withdraw()
        kok.attributes("-topmost", True)
        isim = simpledialog.askstring("Yüz kaydı", "Kaydedilecek kişinin adı:", parent=kok)
        kok.destroy()
        return isim
    except Exception:
        return input("Kaydedilecek kisinin adi: ")


def yuz_kaydet(isim: str | None = None, ana_klasor: Path | None = None) -> bool:
    klasor = Path(ana_klasor) if ana_klasor else veri_klasoru()
    model_dir = modelleri_hazirla(klasor)
    yuz_dir = klasor / "yuzler"
    yuz_dir.mkdir(parents=True, exist_ok=True)

    isim = (isim or _isim_sor() or "").strip().lower()
    if not isim:
        print("Isim girilmedi, iptal edildi.")
        return False

    yunet, sface = (model_dir / ad for ad in MODEL_ADLARI)
    if not yunet.exists() or not sface.exists():
        print(f"Model dosyalari bulunamadi: {model_dir}")
        return False

    det = cv2.FaceDetectorYN.create(str(yunet), "", (640, 480), ESIK_SKOR)
    rec = cv2.FaceRecognizerSF.create(str(sface), "")

    cam = kamera_ac()
    if cam is None:
        print("Kamera acilamadi.")
        return False

    vektorler = []
    son = 0.0
    okunamayan = 0
    while len(vektorler) < HEDEF:
        ok, kare = cam.read()
        if not ok or kare is None:
            okunamayan += 1
            if okunamayan > 100:
                print("Kameradan goruntu alinamiyor.")
                break
            time.sleep(0.05)
            continue
        okunamayan = 0

        if kamera_engeli_var_mi(kare)[0]:
            cv2.putText(kare, "KAMERA ENGELI: goruntu cok karanlik", (10, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        det.setInputSize((kare.shape[1], kare.shape[0]))
        _, yuzler = det.detect(kare)
        if yuzler is not None and len(yuzler) == 1 and time.time() - son >= ARALIK:
            vektorler.append(rec.feature(rec.alignCrop(kare, yuzler[0]))[0])
            son = time.time()
            x, y, w, h = yuzler[0][:4].astype(int)
            cv2.rectangle(kare, (x, y), (x + w, y + h), (0, 255, 0), 2)

        cv2.putText(kare, f"{isim}: {len(vektorler)}/{HEDEF}  (Cikis: Q)", (10, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 0), 2)
        cv2.imshow("Guard - yuz kaydi", kare)
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
            break

    cam.release()
    cv2.destroyAllWindows()

    if len(vektorler) < MIN_ORNEK:
        print(f"Yeterli ornek alinamadi (en az {MIN_ORNEK} gerekli), kaydedilmedi.")
        return False
    yol = yuz_dir / f"{isim}.npy"
    np.save(yol, np.array(vektorler, dtype=np.float32))
    print(f"Kaydedildi: {yol} ({len(vektorler)} ornek)")
    return True


if __name__ == "__main__":
    yuz_kaydet(sys.argv[1] if len(sys.argv) > 1 else None)
