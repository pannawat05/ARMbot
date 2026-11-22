#!/usr/bin/env python3
"""Web dashboard backend for the arm: item list, pick queue, live camera, stop.

Serves the built Svelte dashboard (dashboard/dist) and a small JSON API. One worker
thread runs the queued picks one at a time with visual_pick.py's auto mode (pick, carry
to ArUco id 10, drop); after the queue empties the arm goes to the rest pose. The camera
is opened once and shared by the picks and the live stream.

Usage (run with the .venv-cpu interpreter, from python/):
  python armbot_server.py                 # dry run: real camera, no servo output
  python armbot_server.py --execute       # drive the real servos (asks first)
  python armbot_server.py --http          # plain HTTP; phones then cannot use mic or camera

Open https://<pi address>:8443 . Phones need HTTPS for the microphone and camera, so a
self-signed certificate is made on first start; accept the browser warning once.

API: GET /api/items, GET /api/status, POST /api/queue {"item_id"|"barcode"|"text", "count", "job_id"},
DELETE /api/queue/<id>, POST /api/stop, GET /stream.mjpg, GET /mqtt.json,
GET /api/inspections (results and Unknown review requests from the receiving dashboard),
GET /api/inspections/<key>.jpg, POST /api/reviews/<key> {"decision": "approved"|"rejected", "note"}.

MQTT (calibration/mqtt.json, skipped with --no-mqtt), with <p> the prefix ("armbot"):
  published, retained:  <p>/online "1"/"0", <p>/items, <p>/status (same JSON as the API)
  published:            <p>/event {"type": "queued"|"unknown", ...} after each queue command,
                        <p>/unknown {"message": "Unknown object detected", "box": ..., ...} and
                        <p>/unknown/image (JPEG with the box drawn) when a guess pick first
                        sees an object that has no class; the clean image and a YOLO-format
                        label are saved in captures/unknown/ (newest MAX_CAPTURES kept),
                        <p>/camera JPEG 320x240 every camera_every_s (default 1 s; 0 = off)
                        <p>/inspection/latest and <p>/inspection/review/latest (retained) the newest
                        inspection result and Unknown review request, picture included, for the
                        dashboards; <p>/inspection/unknown/response our review decision
                        {"type": "unknown_review_response", "request_id", "decision", ...}
  subscribed:           <p>/cmd/queue {"item_id"|"barcode"|"text", "source", "count", "job_id"},
                        <p>/cmd/cancel {"id"}, <p>/cmd/stop {},
                        <p>/inspection/result (QoS 1; mqtt.json "result_topic" or the environment
                        variable MQTT_RESULT_TOPIC to change it): the receiving dashboard's
                        inspection result (completed with its JPEG as base64, or not_found /
                        incomplete after a camera timeout, maybe without one), matched to a job
                        by the job_id that came with the queue command (or armbot_job_id);
                        <p>/inspection/unknown/request: an Unknown review request with the
                        receiving camera's first frame; <p>/cmd/review {"key", "decision"} from
                        our dashboard answers it. Pictures go to captures/inspection/ (newest
                        MAX_INSPECTIONS kept)

Safety: anyone who can reach the port can move the arm. Stop (button or POST /api/stop)
ends the current pick at the next camera look and sends the arm to the rest pose, the
same as Ctrl+C in visual_pick.py.
"""

import argparse
import base64
import binascii
import collections
import copy
import json
import mimetypes
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import visual_pick as vp  # noqa: E402
from pick_and_place import DryRunQuiet, open_camera  # noqa: E402
from servo_control_app import Pca9685  # noqa: E402

INVENTORY = ROOT / "calibration" / "inventory.json"
MARKER_MEMORY = ROOT / "calibration" / "marker_memory.json"  # base angle and reach of the last place
MQTT_CONFIG = ROOT / "calibration" / "mqtt.json"
DIST = ROOT / "dashboard" / "dist"
CAPTURES = ROOT / "captures" / "unknown"  # images and boxes of objects with no class
MAX_CAPTURES = 200  # oldest are deleted beyond this; the SD card is nearly full
INSPECTIONS = ROOT / "captures" / "inspection"  # pictures that came back with an inspection result
MAX_INSPECTIONS = 20  # results (and pictures) kept
MAX_RESULT_BYTES = 6_000_000  # a result message or its picture bigger than this is dropped
CERT_DIR = ROOT / "python" / ".armbot_certs"
STREAM_FPS = 8
MAX_PICKS_PER_JOB = 20  # a queue command asking for more is cut to this
ANNOTATED_FRESH_S = 1.0  # show the pick overlay while picks keep publishing it
CAMERA_RETRY_S = 5.0  # how often to try a camera that is missing or not sending frames
LOG_LINES = 200


class LogTee:
    """Copies everything printed to the terminal into a buffer the dashboard can show."""

    def __init__(self, out):
        self.out = out
        self.lines = collections.deque(maxlen=LOG_LINES)
        self.partial = ""
        self.lock = threading.Lock()

    def write(self, text):
        self.out.write(text)
        with self.lock:
            self.partial += text
            *done, self.partial = self.partial.split("\n")
            self.lines.extend(line for line in done if line.strip())

    def flush(self):
        self.out.flush()

    def tail(self, n=40):
        with self.lock:
            return list(self.lines)[-n:]


class CameraHub:
    """One camera reader thread; picks get fresh frames, the stream gets the newest JPEG."""

    def __init__(self, camera_cfg, turn=0):
        self.camera_cfg = camera_cfg
        self.cam = None  # opened by the reader thread, so the dashboard starts without a camera
        self.turn = turn  # the same clockwise turn the picks apply (vp.image_turn)
        self.cond = threading.Condition()
        self.frame, self.seq = None, 0
        self.annotated, self.annotated_at = None, 0.0
        self.stop_event = threading.Event()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        failed = False
        while self.running:
            if self.cam is None:
                try:
                    self.cam = open_camera(self.camera_cfg)
                    print("Camera ready.")
                    failed = False
                except (SystemExit, Exception) as e:  # SerialCamera gives up with SystemExit
                    if not failed:
                        print(f"Camera not ready ({e}); retrying every {CAMERA_RETRY_S:.0f} s.")
                        failed = True
                    time.sleep(CAMERA_RETRY_S)
                continue
            try:
                frame = self.cam.read()
            except RuntimeError:
                continue
            except Exception as e:  # e.g. the USB cable moved and the camera reconnected
                print(f"Camera lost ({type(e).__name__}: {e}); reopening.")
                try:
                    self.cam.close()
                except Exception:
                    pass
                self.cam = None
                continue
            with self.cond:
                self.frame, self.seq = frame, self.seq + 1
                self.cond.notify_all()

    def read(self):
        """Wait for a frame newer than the call, so a pick never acts on a stale one."""
        with self.cond:
            wanted = self.seq + 1
            if not self.cond.wait_for(lambda: self.seq >= wanted, timeout=5):
                raise RuntimeError("no camera frame")
            return self.frame.copy()

    def publish(self, frame):
        with self.cond:
            self.annotated, self.annotated_at = frame, time.monotonic()

    def stop_requested(self):
        return self.stop_event.is_set()

    def jpeg(self, size=None, quality=75):
        import cv2

        with self.cond:
            fresh = self.annotated is not None and time.monotonic() - self.annotated_at < ANNOTATED_FRESH_S
            frame = self.annotated if fresh else self.frame
        if frame is None:
            return None
        if not fresh:
            frame = vp.turn_frame(frame, self.turn)
        if size:
            frame = cv2.resize(frame, size)
        return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()

    def close(self):
        """Detectors call this when a pick ends; the shared camera stays open."""

    def shutdown(self):
        self.running = False
        if self.cam is not None:
            self.cam.close()


class Inventory:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = json.loads(path.read_text(encoding="utf-8"))

    def items(self):
        with self.lock:
            return copy.deepcopy(self.data["items"])

    def find(self, item_id=None, barcode=None, text=None):
        with self.lock:
            for item in self.data["items"]:
                if item_id and item["id"] == item_id:
                    return copy.deepcopy(item)
                if barcode and item["barcode"] == barcode.strip():
                    return copy.deepcopy(item)
                if text and any(alias.lower() in text.lower() for alias in item["aliases"]):
                    return copy.deepcopy(item)
        return None

    def record_pick(self, item_id):
        with self.lock:
            for item in self.data["items"]:
                if item["id"] == item_id:
                    item["stock"] = max(0, item["stock"] - 1)
                    item["picks"] += 1
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)


RESULT_STATUSES = ("completed", "not_found", "incomplete")  # inspection_result statuses we accept
REVIEW_DECISIONS = ("approved", "rejected")


def _decode_jpeg(image, required):
    """(jpeg bytes or None, error or None) from a message's image object {"encoding": "base64", "data"}."""
    if image is None and not required:
        return None, None
    if not isinstance(image, dict) or image.get("encoding") != "base64" or not isinstance(image.get("data"), str):
        return None, "no base64 image"
    try:
        jpeg = base64.b64decode(image["data"], validate=True)
    except (binascii.Error, ValueError):
        return None, "image is not base64"
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_RESULT_BYTES:
        return None, "image is not a JPEG or is too big"
    return jpeg, None


def _counts(value):
    return {str(k): n for k, n in value.items() if type(n) is int} if isinstance(value, dict) else {}


class InspectionStore:
    """Messages from the receiving dashboard kept with their pictures: inspection results
    (completed, not_found, incomplete) or Unknown review requests. Each has a "key" made from
    the sender's dedupe id (event_id, result_id or request_id), safe as a file name."""

    def __init__(self, folder=INSPECTIONS, keep=MAX_INSPECTIONS):
        self.folder, self.keep = folder, keep
        self.lock = threading.Lock()
        self.items = []  # newest last; each is the message without its picture, plus "image_file"
        self.folder.mkdir(parents=True, exist_ok=True)
        for old in sorted(self.folder.glob("*.json"), key=lambda f: f.stat().st_mtime)[-keep:]:
            try:
                item = json.loads(old.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            item.setdefault("key", old.stem)  # saved before keys existed: named by event_id
            self.items.append(item)

    @staticmethod
    def _key(value):
        return "".join(c if c.isalnum() or c in "_-" else "-" for c in str(value))[:100]

    @staticmethod
    def _load(raw, kind):
        if len(raw) > MAX_RESULT_BYTES:
            return None, "message too big"
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            return None, "not JSON"
        if not isinstance(body, dict) or body.get("type") != kind:
            return None, f"not an {kind}"
        job_id = body.get("job_id")
        if not isinstance(job_id, str) or not job_id or len(job_id) > 100:
            return None, "job_id missing"
        return body, None

    @classmethod
    def parse_result(cls, raw):
        """(result, jpeg or None) from an inspection_result message, or (None, reason). A completed
        result carries its picture; not_found and incomplete (camera timeout) may have none."""
        body, err = cls._load(raw, "inspection_result")
        if body is None:
            return None, err
        status = body.get("status")
        if status not in RESULT_STATUSES:
            return None, f"unknown status {status!r}"
        if type(body.get("event_id")) is int:
            dedupe = f"event-{body['event_id']}"
        elif isinstance(body.get("result_id"), str) and body["result_id"]:
            dedupe = body["result_id"]
        else:
            return None, "neither event_id nor result_id"
        jpeg, err = _decode_jpeg(body.get("image"), required=status == "completed")
        if err:
            return None, err
        key = cls._key(dedupe)
        result = {"key": key, "kind": "result", "status": status, "reason": body.get("reason"),
                  "event_id": body.get("event_id"), "result_id": body.get("result_id"),
                  "job_id": body["job_id"], "armbot_job_id": body.get("armbot_job_id"),
                  "completed_at": str(body.get("completed_at") or ""),
                  "requested": _counts(body.get("requested")), "detected": _counts(body.get("detected")),
                  "received_at": time.time(), "image_file": f"{key}.jpg" if jpeg else None}
        return result, jpeg

    @classmethod
    def parse_review(cls, raw):
        """(request, jpeg) from an unknown_review_request message, or (None, reason)."""
        body, err = cls._load(raw, "unknown_review_request")
        if body is None:
            return None, err
        if not isinstance(body.get("request_id"), str) or not body["request_id"]:
            return None, "request_id missing"
        jpeg, err = _decode_jpeg(body.get("image"), required=True)
        if err:
            return None, err
        key = cls._key(body["request_id"])
        request = {"key": key, "kind": "review", "status": str(body.get("status") or "awaiting_review"),
                   "request_id": body["request_id"], "event_id": body.get("event_id"),
                   "job_id": body["job_id"], "armbot_job_id": body.get("armbot_job_id"),
                   "captured_at": str(body.get("captured_at") or ""), "requested": _counts(body.get("requested")),
                   "decision": None, "note": "", "received_at": time.time(), "image_file": f"{key}.jpg"}
        return request, jpeg

    def add(self, item, jpeg):
        """Save a parsed message; False when its key is already stored (the sender retries)."""
        with self.lock:
            if any(i["key"] == item["key"] for i in self.items):
                return False
            if jpeg:
                (self.folder / item["image_file"]).write_bytes(jpeg)
            self._save(item)
            self.items.append(item)
            for old in self.items[:-self.keep]:
                (self.folder / f"{old['key']}.json").unlink(missing_ok=True)
                if old.get("image_file"):
                    (self.folder / old["image_file"]).unlink(missing_ok=True)
            self.items = self.items[-self.keep:]
        return True

    def _save(self, item):
        (self.folder / f"{item['key']}.json").write_text(json.dumps(item, ensure_ascii=False), "utf-8")

    def update(self, key, **fields):
        """Change a stored item (a review decision); the updated copy, or None if unknown."""
        with self.lock:
            for item in self.items:
                if item["key"] == key:
                    item.update(fields)
                    self._save(item)
                    return dict(item)
        return None

    def list(self):
        with self.lock:
            return [dict(i) for i in reversed(self.items)]

    def is_review(self, key):
        with self.lock:
            return any(i["key"] == key and i.get("kind") == "review" for i in self.items)

    def image(self, key):
        with self.lock:
            name = next((i.get("image_file") for i in self.items if i["key"] == key), None)
        path = self.folder / name if name else None
        return path.read_bytes() if path and path.is_file() else None

    def with_image(self, item):
        """The item as JSON for MQTT, its picture inline as base64 (or null)."""
        jpeg = self.image(item["key"])
        return json.dumps({**item, "image": {"content_type": "image/jpeg", "encoding": "base64",
                                             "data": base64.b64encode(jpeg).decode()} if jpeg else None},
                          ensure_ascii=False)


class Robot:
    """Queue plus the worker thread that runs one pick at a time."""

    def __init__(self, execute, inventory, hub):
        self.execute = execute
        self.inventory = inventory
        self.hub = hub
        self.main_cfg = vp.load_config(vp.DEFAULT_CONFIG)
        self.vcfg = vp.load_config(vp.VISUAL_CONFIG)
        self.lock = threading.Lock()
        self.jobs = []
        self.next_id = 1
        self.state = "idle"
        self.model = None
        hw = self.main_cfg["hardware"]
        self.pca = Pca9685(hw["i2c_bus"], int(hw["pca9685_address"], 0), keep_outputs=True) if execute else DryRunQuiet()
        self.on_unknown = None  # set by MqttBridge: callback(info, annotated_jpeg)
        # Where the ArUco marker was last aimed at; kept between picks and across restarts.
        try:
            self.marker_memory = json.loads(MARKER_MEMORY.read_text("utf-8"))
        except (OSError, ValueError):
            self.marker_memory = {}
        self.wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def add(self, item, source, count=1, ref=None):
        """Queue count picks of this item as one job; never more than the stock (at least 1).
        ref is the sender's own job id (a string); inspection results come back under it."""
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, item["stock"] if item["stock"] > 0 else 1, MAX_PICKS_PER_JOB))
        with self.lock:
            job = {"id": self.next_id, "item_id": item["id"], "name": item["name"], "name_th": item["name_th"],
                   "source": source, "count": count, "done": 0, "state": "queued", "message": "",
                   "created": time.time()}
            if ref:
                job["ref"] = ref
            self.next_id += 1
            self.jobs.append(job)
        self.wake.set()
        return job

    def inspected(self, item):
        """Note an inspection result or Unknown review on the job it belongs to: the same ref
        (our sender's job_id) or our own job number as armbot_job_id. Its id, or None."""
        with self.lock:
            for job in self.jobs:
                if job.get("ref") == item["job_id"] or str(job["id"]) == str(item.get("armbot_job_id")):
                    if item["kind"] == "review":
                        job["review"] = {"key": item["key"], "status": item["status"], "decision": item["decision"]}
                    else:
                        job["inspection"] = {"key": item["key"], "status": item["status"],
                                             "requested": item["requested"], "detected": item["detected"]}
                    return job["id"]
        return None

    def cancel(self, job_id):
        with self.lock:
            for job in self.jobs:
                if job["id"] == job_id and job["state"] == "queued":
                    job["state"] = "cancelled"
                    return True
        return False

    def stop(self):
        with self.lock:
            for job in self.jobs:
                if job["state"] == "queued":
                    job["state"] = "cancelled"
            busy = self.state == "picking"
        if busy:
            self.hub.stop_event.set()
        return busy

    def status(self):
        with self.lock:
            recent = [j for j in self.jobs if j["state"] in ("queued", "running")] + \
                     [j for j in self.jobs if j["state"] not in ("queued", "running")][-15:]
            return {"mode": "execute" if self.execute else "dry-run", "state": self.state,
                    "jobs": copy.deepcopy(recent)}

    def _next(self):
        with self.lock:
            for job in self.jobs:
                if job["state"] == "queued":
                    job["state"] = "running"
                    return job
        return None

    def _worker(self):
        while True:
            job = self._next()
            if job is None:
                if self.state == "picking":
                    self._rest()
                self.wake.wait(timeout=1)
                self.wake.clear()
                continue
            self.state = "picking"
            self.hub.stop_event.clear()
            ok, message = self._run_job(job)
            with self.lock:
                job["state"], job["message"] = ("done" if ok else "failed"), message

    def _run_job(self, job):
        """Pick the job's count, one full cycle each, stopping at the first failure or when the stock runs out."""
        count = job.get("count", 1)
        for n in range(count):
            item = self.inventory.find(item_id=job["item_id"])
            if n and item["stock"] <= 0:
                return False, f"{job['done']}/{count}: out of stock"
            if count > 1:
                print(f"-- {job['name']}: pick {n + 1} of {count}")
            ok, message = self._pick(job)
            if not ok:
                return False, message if count == 1 else f"{job['done']}/{count}: {message}"
            self.inventory.record_pick(job["item_id"])
            with self.lock:
                job["done"] += 1
        return True, "picked and placed" if count == 1 else f"picked and placed {count}/{count}"

    def _pick(self, job):
        item = self.inventory.find(item_id=job["item_id"])
        print(f"== Job {job['id']}: {item['name']}")
        # Read the settings again for every pick, so tuning the config needs no restart
        # (the camera settings are the exception: they apply at start).
        self.main_cfg = vp.load_config(vp.DEFAULT_CONFIG)
        self.vcfg = vp.load_config(vp.VISUAL_CONFIG)
        vcfg = copy.deepcopy(self.vcfg)
        vp.apply_object_settings(vcfg, item["yolo_class"])
        try:
            if not item["color_only"] and self.model is None:
                import torch
                from ultralytics import YOLO

                torch.set_num_threads(3)
                self.model = YOLO(str(vp.rel(self.main_cfg["yolo"]["weights"])))
            detector = vp.Detector(self.main_cfg, vcfg, item["yolo_class"], False, item["color_only"],
                                   self.execute, cam=self.hub, model=self.model)
            detector.on_unknown = lambda frame, best, box: self._unknown_seen(job, frame, best, box)
            arm = vp.Arm(self.pca, self.main_cfg, vcfg, dry_run=not self.execute)
            picks = vp.auto_run(arm, detector, vcfg, max_picks=1, wait_for_hand=False, memory=self.marker_memory)
            if self.marker_memory:
                MARKER_MEMORY.write_text(json.dumps(self.marker_memory), "utf-8")
        except vp.StopRequested:
            print("Stopped from the dashboard. Going to the rest pose.")
            self._rest()
            return False, "stopped"
        except SystemExit as e:
            print(f"Pick refused: {e}")
            return False, str(e)
        except Exception as e:  # keep the server alive; the message shows on the dashboard
            print(f"Pick failed: {type(e).__name__}: {e}")
            return False, f"{type(e).__name__}: {e}"
        if picks == 1:
            warning = getattr(arm, "warning", None)  # e.g. the object was beyond the arm's reach
            return True, "picked and placed" + (f" (warning: {warning})" if warning else "")
        return False, getattr(detector, "stop_reason", None) or "not found"

    def _unknown_seen(self, job, frame, best, box):
        """An object with no class was seen: save the clean image with a YOLO-format label
        (class 0, centre x y, width height, as fractions) for later training, and announce it."""
        try:
            import cv2

            CAPTURES.mkdir(parents=True, exist_ok=True)
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = (int(v) for v in box)
            name = f"unknown-{time.strftime('%Y%m%d-%H%M%S')}-job{job['id']}"
            cv2.imwrite(str(CAPTURES / f"{name}.jpg"), frame)
            norm = [round(v, 4) for v in ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h)]
            (CAPTURES / f"{name}.txt").write_text("0 " + " ".join(f"{v:.4f}" for v in norm) + "\n")
            for old in sorted(CAPTURES.glob("unknown-*.jpg"))[:-MAX_CAPTURES]:
                old.unlink(missing_ok=True)
                old.with_suffix(".txt").unlink(missing_ok=True)
            shown = frame.copy()
            cv2.rectangle(shown, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.putText(shown, "Unknown object", (x1, max(18, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            info = {"type": "unknown_object", "message": "Unknown object detected", "time": time.time(),
                    "job_id": job["id"], "image_size": [w, h],
                    "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    "box_center_size": dict(zip(("cx", "cy", "w", "h"), norm)),
                    "file": f"captures/unknown/{name}.jpg"}
            print(f"Unknown object detected: box ({x1},{y1})-({x2},{y2}), saved {info['file']}.")
            if self.on_unknown:
                self.on_unknown(info, cv2.imencode(".jpg", shown, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes())
        except Exception as e:  # a failed capture must never stop the pick
            print(f"Unknown-object capture failed: {type(e).__name__}: {e}")

    def _rest(self):
        self.state = "resting"
        try:
            vp.Arm(self.pca, self.main_cfg, copy.deepcopy(self.vcfg), dry_run=not self.execute).go_rest()
            print("At rest.")
        except Exception as e:
            print(f"Rest failed: {type(e).__name__}: {e}")
        self.state = "idle"


class MqttBridge:
    """Mirrors the queue and status on an MQTT broker and takes commands from it."""

    def __init__(self, cfg, robot, inventory, log, hub=None, inspections=None):
        import paho.mqtt.client as mqtt

        self.robot, self.inventory, self.log, self.hub = robot, inventory, log, hub
        self.inspections = inspections
        self.camera_every_s = cfg.get("camera_every_s", 1.0)  # 0 = do not send snapshots
        self.p = cfg["prefix"]
        self.result_topic = os.environ.get("MQTT_RESULT_TOPIC", "").strip() or cfg.get("result_topic") \
            or f"{self.p}/inspection/result"
        if self.result_topic.startswith(f"{self.p}/cmd/") or any(c in self.result_topic for c in "#+\0") \
                or self.result_topic == f"{self.p}/inspection/latest":
            print(f"MQTT result topic {self.result_topic!r} is not allowed; using the default.")
            self.result_topic = f"{self.p}/inspection/result"
        self.review_topic = f"{self.p}/inspection/unknown/request"  # Unknown review requests to us
        self.response_topic = f"{self.p}/inspection/unknown/response"  # our decisions, by request_id
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"armbot-{os.uname().nodename}")
        if cfg.get("username"):  # public brokers such as broker.hivemq.com take no login
            self.client.username_pw_set(cfg["username"], cfg.get("password"))
        self.client.will_set(f"{self.p}/online", "0", qos=1, retain=True)
        self.client.on_connect = self._connected
        self.closing = False
        self.client.on_disconnect = lambda *a: self.closing or print("MQTT disconnected; retrying.")
        self.client.on_message = self._message
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(cfg["host"], cfg["port"], keepalive=30)
        self.client.loop_start()
        robot.on_unknown = self.unknown
        threading.Thread(target=self._publisher, daemon=True).start()

    def unknown(self, info, jpeg):
        """Announce an object with no class: the details as JSON, then the image with its box."""
        self._send("unknown", info)
        self.client.publish(f"{self.p}/unknown/image", jpeg, qos=0)

    def _send(self, topic, body, retain=False):
        self.client.publish(f"{self.p}/{topic}", json.dumps(body, ensure_ascii=False), qos=1, retain=retain)

    def _connected(self, client, _userdata, _flags, reason, _props):
        if reason.is_failure:
            print(f"MQTT connection refused: {reason}")
            return
        print(f"MQTT connected; topics {self.p}/#")
        client.subscribe([(f"{self.p}/cmd/#", 1), (self.result_topic, 1), (self.review_topic, 1)])
        client.publish(f"{self.p}/online", "1", qos=1, retain=True)
        self._send("items", {"items": self.inventory.items()}, retain=True)

    def _received(self, payload, kind):
        """An inspection result or Unknown review request from the receiving dashboard: keep it,
        note it on its job, and pass it on to our dashboards (retained, picture included)."""
        if self.inspections is None:
            return
        parse = self.inspections.parse_review if kind == "review" else self.inspections.parse_result
        item, jpeg = parse(payload)
        if item is None:
            print(f"Inspection {kind} ignored: {jpeg}.")
            return
        if not self.inspections.add(item, jpeg):
            return  # the sender retries until the broker confirms; the first copy is enough
        job = self.robot.inspected(item)
        what = (f"Unknown review request {item['request_id']}: asked {item['requested']}" if kind == "review" else
                f"Inspection {item['status']} {item['key']}: asked {item['requested']}, found {item['detected']}")
        print(f"{what} for {item['job_id']}" + (f"; job {job}" if job else "; no queued job with that id") + ".")
        self.publish_item(item)

    def publish_item(self, item):
        topic = "inspection/review/latest" if item["kind"] == "review" else "inspection/latest"
        self.client.publish(f"{self.p}/{topic}", self.inspections.with_image(item), qos=1, retain=True)

    def decide(self, key, decision, note=""):
        """Our answer to an Unknown review request: saved, noted on the job, sent back on
        <p>/inspection/unknown/response with the same request_id. The updated request, or None."""
        if decision not in REVIEW_DECISIONS or not self.inspections.is_review(key):
            return None
        item = self.inspections.update(key, decision=decision, note=str(note)[:500],
                                       status=decision, reviewed_at=time.time())
        self.robot.inspected(item)
        self._send_to(self.response_topic, {
            "schema_version": 1, "type": "unknown_review_response", "request_id": item["request_id"],
            "job_id": item["job_id"], "armbot_job_id": item["armbot_job_id"], "decision": decision,
            "note": item["note"], "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        print(f"Unknown review {item['request_id']}: {decision}" + (f" ({item['note']})" if item["note"] else "") + ".")
        self.publish_item(item)
        return item

    def _send_to(self, topic, body):
        self.client.publish(topic, json.dumps(body, ensure_ascii=False), qos=1, retain=False)

    def _message(self, _client, _userdata, msg):
        if msg.topic == self.result_topic:
            return self._received(msg.payload, "result")
        if msg.topic == self.review_topic:
            return self._received(msg.payload, "review")
        try:
            body = json.loads(msg.payload or b"{}")
        except json.JSONDecodeError:
            body = {}
        cmd = msg.topic.removeprefix(f"{self.p}/cmd/")
        if cmd == "queue":
            item = self.inventory.find(item_id=body.get("item_id"), barcode=body.get("barcode"), text=body.get("text"))
            ref = body.get("job_id") if isinstance(body.get("job_id"), str) and len(body["job_id"]) <= 100 else None
            if item is None:
                self._send("event", {"type": "unknown", "query": body, **({"job_id": ref} if ref else {})})
            else:
                job = self.robot.add(item, body.get("source", "mqtt"), body.get("count", body.get("quantity", 1)), ref)
                self._send("event", {"type": "queued", "job": job, "item": item, **({"job_id": ref} if ref else {})})
        elif cmd == "cancel":
            self.robot.cancel(int(body.get("id", 0)))
        elif cmd == "stop":
            self.robot.stop()
        elif cmd == "review" and self.inspections is not None:  # from our dashboard in MQTT mode
            self.decide(InspectionStore._key(body.get("key", "")), body.get("decision"), body.get("note", ""))

    def _publisher(self):
        last_status = last_items = None
        last_camera = 0.0
        while True:
            if self.hub and self.camera_every_s and self.client.is_connected() \
                    and time.monotonic() - last_camera >= self.camera_every_s:
                jpg = self.hub.jpeg(size=(320, 240), quality=60)  # about 10 KB, for the cloud dashboard
                if jpg:
                    self.client.publish(f"{self.p}/camera", jpg, qos=0)
                last_camera = time.monotonic()
            status = json.dumps({**self.robot.status(), "log": self.log.tail()}, ensure_ascii=False)
            items = json.dumps({"items": self.inventory.items()}, ensure_ascii=False)
            if self.client.is_connected():
                if status != last_status:
                    self.client.publish(f"{self.p}/status", status, qos=0, retain=True)
                    last_status = status
                if items != last_items:
                    self.client.publish(f"{self.p}/items", items, qos=1, retain=True)
                    last_items = items
            time.sleep(1)

    def close(self):
        self.closing = True
        self.client.publish(f"{self.p}/online", "0", qos=1, retain=True).wait_for_publish(2)
        self.client.loop_stop()
        self.client.disconnect()


class QuietServer(ThreadingHTTPServer):
    """Browsers drop TLS connections mid-way (certificate prompts, closed tabs); that is not an error."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_handler(robot, inventory, hub, log, mqtt_cfg=None, inspections=None, decide=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # keep the terminal for robot messages

        def _json(self, body, code=200):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/items":
                return self._json({"items": inventory.items()})
            if path == "/api/status":
                return self._json({**robot.status(), "log": log.tail()})
            if path == "/stream.mjpg":
                return self._stream()
            if path == "/api/inspections":
                return self._json({"results": inspections.list() if inspections else []})
            if path.startswith("/api/inspections/") and path.endswith(".jpg"):
                jpeg = inspections.image(InspectionStore._key(path[17:-4])) if inspections else None
                if jpeg is None:
                    return self._json({"error": "not found"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                return self.wfile.write(jpeg)
            if path == "/mqtt.json":
                if not mqtt_cfg:
                    return self._json({"error": "MQTT off"}, 404)
                return self._json({k: mqtt_cfg[k] for k in ("ws_url", "username", "password", "prefix")})
            self._static(path)

        def do_POST(self):
            path = self.path.split("?")[0]
            if path == "/api/queue":
                body = self._body()
                item = inventory.find(item_id=body.get("item_id"), barcode=body.get("barcode"),
                                      text=body.get("text"))
                if item is None:
                    return self._json({"error": "unknown item", "query": body}, 404)
                ref = body.get("job_id") if isinstance(body.get("job_id"), str) and len(body["job_id"]) <= 100 else None
                return self._json({"job": robot.add(item, body.get("source", "click"),
                                                    body.get("count", body.get("quantity", 1)), ref), "item": item})
            if path == "/api/stop":
                return self._json({"stopping": robot.stop()})
            if path.startswith("/api/reviews/"):  # {"decision": "approved"|"rejected", "note"}
                body = self._body()
                key = InspectionStore._key(path[len("/api/reviews/"):])
                if decide:
                    item = decide(key, body.get("decision"), body.get("note", ""))
                elif inspections and body.get("decision") in REVIEW_DECISIONS and inspections.is_review(key):
                    # no MQTT: the decision stays here
                    item = inspections.update(key, decision=body["decision"], status=body["decision"],
                                              note=str(body.get("note", ""))[:500], reviewed_at=time.time())
                else:
                    item = None
                return self._json({"review": item} if item else {"error": "unknown request or decision"},
                                  200 if item else 400)
            self._json({"error": "not found"}, 404)

        def do_DELETE(self):
            parts = self.path.split("?")[0].strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "queue"] and parts[2].isdigit():
                return self._json({"cancelled": robot.cancel(int(parts[2]))})
            self._json({"error": "not found"}, 404)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    jpg = hub.jpeg()
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    time.sleep(1 / STREAM_FPS)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

        def _static(self, path):
            target = (DIST / path.lstrip("/")).resolve()
            if path == "/" or not target.is_file() or DIST.resolve() not in target.parents:
                target = DIST / "index.html"  # single-page app
            if not target.is_file():
                return self._json({"error": "dashboard not built; run npm run build in dashboard/"}, 503)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def ensure_certificate():
    """Self-signed certificate for this Pi's name and addresses, made once."""
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    CERT_DIR.mkdir(exist_ok=True)
    host = os.uname().nodename
    ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    san = ",".join([f"DNS:{host}", f"DNS:{host}.local", "DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in ips])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                    "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={host}",
                    "-addext", f"subjectAltName={san}"], check=True, capture_output=True)
    return cert, key


def main():
    ap = argparse.ArgumentParser(description="Armbot web dashboard server")
    ap.add_argument("--execute", action="store_true", help="drive the real servos (default: dry run)")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--http", action="store_true", help="plain HTTP instead of HTTPS")
    ap.add_argument("--camera", choices=("serial", "usb"), help="override camera.source in the config")
    ap.add_argument("--no-mqtt", action="store_true", help="do not connect to the MQTT broker")
    ap.add_argument("--yes", action="store_true", help="skip the --execute safety question (systemd service)")
    args = ap.parse_args()

    if args.execute and not args.yes and not vp.ask("Servo power on, area clear, hand on the arm? The dashboard will move it"):
        print("Cancelled.")
        return
    log = LogTee(sys.stdout)
    sys.stdout = log
    inventory = Inventory(INVENTORY)
    camera_cfg = vp.load_config(vp.DEFAULT_CONFIG)["camera"]
    camera_cfg["source"] = args.camera or camera_cfg["source"]
    hub = CameraHub(camera_cfg, turn=vp.image_turn(vp.load_config(vp.VISUAL_CONFIG)["directions"]))
    robot = Robot(args.execute, inventory, hub)
    mqtt_cfg = None if args.no_mqtt or not MQTT_CONFIG.exists() else json.loads(MQTT_CONFIG.read_text("utf-8"))
    inspections = InspectionStore()
    bridge = MqttBridge(mqtt_cfg, robot, inventory, log, hub, inspections) if mqtt_cfg else None
    server = QuietServer(("0.0.0.0", args.port), make_handler(robot, inventory, hub, log, mqtt_cfg, inspections,
                                                              bridge.decide if bridge else None))
    scheme = "http"
    if not args.http:
        cert, key = ensure_certificate()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    ips = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
    print(f"Dashboard ({'EXECUTE' if args.execute else 'dry run'}): "
          + ", ".join(f"{scheme}://{ip}:{args.port}" for ip in ips + ["localhost"]))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down: stopping the queue and going to the rest pose.")
        robot.stop()
        deadline = time.monotonic() + 30
        while robot.state != "idle" and time.monotonic() < deadline:
            time.sleep(0.2)
        robot._rest()
    finally:
        server.server_close()
        hub.shutdown()
        if bridge:
            bridge.close()


if __name__ == "__main__":
    main()
