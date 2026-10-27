"""Kamera acma ve kamera engeli (kapkaranlik goruntu) kontrolu."""
import cv2
import numpy as np


def kamera_ac(bildir=None):
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        for idx in (0, 1):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    if bildir:
                        bildir(backend, idx)
                    return cap
            cap.release()
    return None


def kamera_engeli_var_mi(frame) -> tuple[bool, float, float]:
    """(engel_var, ortalama_parlaklik, standart_sapma) dondurur."""
    if frame is None or frame.size == 0:
        return True, 0.0, 0.0
    gri = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    parlaklik = float(np.mean(gri))
    sapma = float(np.std(gri))
    engel = parlaklik < 12.0 or (parlaklik < 22.0 and sapma < 4.5)
    return engel, round(parlaklik, 2), round(sapma, 2)
