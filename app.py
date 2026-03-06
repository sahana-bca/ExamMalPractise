from flask import Flask, render_template, send_from_directory, request
import os

from db import (
    get_latest_images,
    get_receiver_email,
    get_stats as get_db_stats,
    init_db,
    run_readonly_query,
    set_receiver_email,
)

app = Flask(__name__)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECTION_FOLDER=SCRIPT_DIR+"/batch/"

init_db()


def _is_local_request() -> bool:
    # Basic protection: only allow from localhost.
    return request.remote_addr in {"127.0.0.1", "::1"}

@app.route('/')
def index():
    images = get_latest_images(limit=500)
    return render_template('index.html', images=images)

@app.route('/detections/<path:filename>')
def serve_image(filename):
    return send_from_directory(DETECTION_FOLDER, filename)

@app.route('/api/stats')
def get_stats():
    stats = get_db_stats()
    stats["folder"] = DETECTION_FOLDER
    return stats


@app.route('/api/config/receiver', methods=['GET', 'POST'])
def receiver_config():
    if not _is_local_request():
        return {"ok": False, "error": "Local requests only."}, 403

    if request.method == 'GET':
        return {"ok": True, "receiver": get_receiver_email(default="")}

    data = request.get_json(silent=True) or {}
    receiver = str(data.get("receiver", "")).strip()
    if not receiver or "@" not in receiver:
        return {"ok": False, "error": "Invalid email."}, 400
    set_receiver_email(receiver_email=receiver)
    return {"ok": True, "receiver": receiver}


@app.route('/api/sql', methods=['POST'])
def sql_console():
    if not _is_local_request():
        return {"ok": False, "error": "Local requests only."}, 403

    data = request.get_json(silent=True) or {}
    sql = data.get("sql", "")
    limit = data.get("limit", 200)
    try:
        limit_int = int(limit)
    except Exception:
        limit_int = 200

    result = run_readonly_query(sql=str(sql), limit=max(1, min(limit_int, 1000)))
    status = 200 if result.get("ok") else 400
    return result, status
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)