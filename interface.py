from ultralytics import YOLO
import cv2
import os
import threading
from alert_service import send_emails
import webbrowser
import subprocess
import sys
import atexit
import signal
import time

from db import log_detection, init_db

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
app_path = os.path.join(SCRIPT_DIR, 'app.py')


def _start_flask() -> subprocess.Popen:
    kwargs = {
        "cwd": SCRIPT_DIR,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        pass

    return subprocess.Popen([sys.executable, app_path], **kwargs)


flask_process = _start_flask()


def _stop_flask_process() -> None:
    p = flask_process
    if not p or p.poll() is not None:
        return

    # Try graceful stop first.
    try:
        if os.name == "nt":
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            p.terminate()
    except Exception:
        pass

    # Wait a bit, then force-kill if needed.
    for _ in range(30):
        if p.poll() is not None:
            return
        time.sleep(0.1)

    try:
        p.terminate()
    except Exception:
        pass

    for _ in range(20):
        if p.poll() is not None:
            return
        time.sleep(0.1)

    try:
        p.kill()
    except Exception:
        pass


atexit.register(_stop_flask_process)


def _handle_exit_signal(signum, frame):
    _stop_flask_process()
    raise SystemExit(0)


for _sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
    if _sig is not None:
        try:
            signal.signal(_sig, _handle_exit_signal)
        except Exception:
            pass

webbrowser.open(f'http://localhost:5000')
print(f"--->>> Flask Server Running on http://localhost:5000")
model = YOLO('best.pt')

init_db()

batch = 0
img_cnt = 0
cooldown = 0
MAX_COOLDOWN = 3 
BATCH_SIZE = 5   

os.makedirs('batch', exist_ok=True)


results = model.predict(source='0', show=True, conf=0.50, stream=True)

for result in results:
    boxes = result.boxes
    

    if len(boxes) > 0:
        if cooldown >= MAX_COOLDOWN:
            cooldown = 0
            
            im_bgr = result.plot() 
            
            batch_dir = f'batch/{batch}'
            os.makedirs(batch_dir, exist_ok=True)
            
            image_rel_path = f'{batch}/{img_cnt}.jpg'
            cv2.imwrite(f'{batch_dir}/{img_cnt}.jpg', im_bgr)

            try:
                labels = []
                if boxes is not None and hasattr(boxes, "cls") and hasattr(boxes, "conf"):
                    cls_list = boxes.cls.tolist() if hasattr(boxes.cls, "tolist") else list(boxes.cls)
                    conf_list = boxes.conf.tolist() if hasattr(boxes.conf, "tolist") else list(boxes.conf)
                    names = getattr(result, "names", None) or {}
                    for cls_id, conf in zip(cls_list, conf_list):
                        name = names.get(int(cls_id), str(int(cls_id)))
                        labels.append({"class": name, "conf": float(conf)})

                max_conf = max((float(x.get("conf", 0.0)) for x in labels), default=None)
                log_detection(image_path=image_rel_path, batch=batch, labels=labels, max_conf=max_conf)
            except Exception:
                pass
            img_cnt += 1
            
            if img_cnt >= BATCH_SIZE:
                img_cnt = 0
                batch += 1

                threading.Thread(target=send_emails, args=(f'batch/{batch-1}',), daemon=True).start()
        else:
            cooldown += 1
    else:
        pass