

## Installation (Raspberry Pi OS)

### 1. System Dependencies & I2C Setup

```bash
sudo apt update
sudo apt install -y build-essential cmake i2c-tools python3-venv
sudo raspi-config  # Navigate to Interface Options -> I2C -> Enable
sudo reboot

```

After rebooting, verify that the PCA9685 board is detected on the I2C bus (typically at address `0x40`):

```bash
sudo i2cdetect -y 1

```

### 2. Build the C++ Servo Daemon

```bash
cd cpp
cmake -S . -B build
cmake --build build -j
cd ..

```

### 3. Set Up Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r python/requirements.txt

```

---

## Pre-Flight Checklist (Calibration & Assets)

Before running the vision pipeline, ensure the following assets and configurations are in place:

1. **Model Weights:** Place your trained YOLO weights at `models/best.pt`.
2. **Homography Matrix:** Generate a real `calibration/H_camera_to_robot.npy` using measured reference points at `640x480` resolution.
3. **System Config:** Update `calibration/config.json` with:
* Your actual ESP32-CAM stream URL
* Measured `table_z_mm`
* Class-specific grasp offsets
* Target tray `XYZ` coordinates


4. **Environment Check:** Verify that all Python packages in `requirements.txt` are installed in your active `.venv`.

---

## Usage

**Run a single dry-run inference pass (headless):**

```bash
python python/robot_pipeline.py --dry-run --once

```

**Run continuous dry-run with live visual preview:**

```bash
python python/robot_pipeline.py --dry-run --show

```

*(Append `--no-mqtt` to either command if you want to run locally without connecting to an MQTT broker.)*

---

## Safety & Hardware Limitations

> [!WARNING]
> **Do not connect or run servo commands with uncalibrated joint limits.** Physical servo actuation is disabled in the Python pipeline by default to prevent mechanical damage.

* **Power Supply:** Always use a dedicated external power supply rated for the stall current of all six servos, sharing a common ground (`GND`) with the Raspberry Pi.
* **Kinematics Approximation:** The included DH parameters and drawings are provisional. The current kinematics solver is a **3-DOF positional approximation**, not a validated 6-axis model.
* **RL Policy Assumptions:** The included DDQN checkpoint was trained against original state/dynamics assumptions. Any changes to robot link geometry require re-evaluation or retraining.
* **Planar Mapping:** A single 2D homography matrix only maps the table plane to `(X, Y)` coordinates; it cannot measure object height or depth (`Z`).

---

## Armbot MQTT Topics

ใช้ `<p>` แทน topic prefix ที่ตั้งใน `calibration/mqtt.json` (ค่าเริ่มต้นคือ `armbot`) โดยต้องตั้งค่า prefix ฝั่งหน้าเว็บให้ตรงกัน Raspberry Pi จะ publish และ subscribe topics เหล่านี้เมื่อเปิดใช้งาน MQTT (ยกเว้นเมื่อรันพร้อม flag `--no-mqtt`)

### 1. Pi ส่งออก (Publish)

| Topic | Payload | QoS | Retained | เงื่อนไขการส่ง | คำอธิบายและผู้ใช้งาน |
| --- | --- | --- | --- | --- | --- |
| `<p>/online` | `"1"` หรือ `"0"` | 1 | ใช่ | `"1"` เมื่อเชื่อม broker; `"0"` เมื่อปิดโปรแกรมหรือหลุดการเชื่อมต่อ (Last Will) | สถานะออนไลน์ของ Pi (หน้าเว็บใช้แสดงสถานะการเชื่อมต่อ) |
| `<p>/status` | JSON `{"mode","state","jobs":[...],"log":[...]}` | 0 | ใช่ | ตรวจสอบทุก 1 วินาที และส่งเมื่อข้อมูลมีการเปลี่ยนแปลง | สถานะแขนกล คิวงาน และ log ล่าสุด (หน้าเว็บ subscribe) |
| `<p>/items` | JSON `{"items":[...]}` | 1 | ใช่ | เมื่อเชื่อม broker และเมื่อข้อมูลสินค้าเปลี่ยน (เช่น stock หรือจำนวนหยิบ) | รายการสินค้าจาก `calibration/inventory.json` ประกอบด้วย `id`, `name`, `name_th`, `yolo_class`, `color_only`, `zone`, `stock`, `barcode`, `aliases`, `picks` (หน้าเว็บ subscribe) |
| `<p>/event` | JSON `{"type":"queued","job":{...},"item":{...}}` หรือ `{"type":"unknown","query":{...}}` | 1 | ไม่ | ตอบกลับทันทีทุกครั้งที่ได้รับคำสั่ง `<p>/cmd/queue` | ยืนยันว่าเพิ่มงานสำเร็จ (`queued`) หรือไม่พบสินค้า (`unknown`) สำหรับ client ภายนอก (หน้าเว็บปัจจุบันไม่ได้ใช้) |
| `<p>/unknown` | JSON (`type`, `message`, `time`, `job_id`, `image_size`, `box`, `box_center_size`, `file`) | 1 | ไม่ | เมื่อโหมดเดาวัตถุ (`yolo_class: "unknown"`) พบวัตถุที่ไม่มีคลาส | แจ้งพิกัดกรอบวัตถุ (`box` = พิกเซล, `box_center_size` = ค่า normalized 0–1) ภาพต้นฉบับและ label บันทึกไว้ที่ `captures/unknown/` บน Pi ใช้ร่วมกับ `python/watch_unknown.py` |
| `<p>/unknown/image` | JPEG binary (วาดกรอบสีแดง) | 0 | ไม่ | ส่งทันทีหลังจาก `<p>/unknown` | ภาพถ่ายวัตถุที่ไม่รู้จัก ใช้แสดงผลคู่กับ topic ด้านบน |
| `<p>/inspection/latest` | JSON ผลตรวจล่าสุด (`key`, `status` = `completed`/`not_found`/`incomplete`, `job_id`, `requested`, `detected`, `image` เป็น base64 หรือ `null`) | 1 | ใช่ | เมื่อ Pi รับ `<p>/inspection/result` ใหม่ที่ผ่านการตรวจแล้ว | ให้หน้า Dashboard แสดงผลตรวจและรูปทันที แม้เปิดหน้าทีหลังหรือใช้ผ่านคลาวด์ |
| `<p>/inspection/review/latest` | JSON คำขอตรวจ Unknown ล่าสุด (`key`, `request_id`, `job_id`, `requested`, `decision`, `image`) | 1 | ใช่ | เมื่อรับคำขอใหม่ หรือเมื่อตอบคำขอแล้ว | ให้หน้า Dashboard แสดงคำขอที่รอตรวจพร้อมปุ่มตอบ |
| `<p>/inspection/unknown/response` | JSON `{"schema_version":1,"type":"unknown_review_response","request_id","job_id","armbot_job_id","decision":"approved"\|"rejected","note","reviewed_at"}` | 1 | ไม่ | เมื่อกดปุ่ม "ถูกต้อง"/"ไม่ถูกต้อง" บน Dashboard | ผลตรวจ Unknown ส่งกลับไปที่ Dashboard ฝั่งรับของ โดยใช้ `request_id` เดิม (ฝั่งรับของต้องเพิ่มตัวรับ topic นี้) |
| `<p>/camera` | JPEG binary (`320×240`) | 0 | ไม่ | ทุก `camera_every_s` วินาที (ค่าเริ่มต้น `1`, ตั้ง `0` เพื่อปิด) เมื่อกล้องพร้อมใช้งาน | ภาพสดจากกล้องสำหรับหน้าเว็บ Cloud |

---

### 2. Pi รับคำสั่ง (Subscribe `<p>/cmd/#`)

หน้าเว็บหรือ client อื่นสามารถส่งคำสั่งควบคุมมายัง topic ต่อไปนี้:

| Topic | ตัวอย่าง Payload | การทำงาน |
| --- | --- | --- |
| `<p>/cmd/queue` | `{"item_id":"relay","source":"voice","count":2}` | เพิ่มงานหยิบเข้าคิวด้วย `item_id` |
|  | `{"barcode":"ARM-0002","source":"barcode"}` | ค้นหาและเพิ่มงานจากรหัส `barcode` |
|  | `{"text":"เอารีเลย์","source":"mqtt","quantity":1}` | ค้นหาสินค้าจากคำใน `aliases` |
| `<p>/cmd/cancel` | `{"id":3}` | ยกเลิกงานหมายเลข `3` (หากกำลังหยิบอยู่จะหยุดและกลับท่าพักทันที) |
| `<p>/cmd/stop` | `{}` | **หยุดฉุกเฉิน:** หยุดงานปัจจุบัน ยกเลิกงานทั้งหมดในคิว และกลับสู่ท่าพัก (`rest pose`) |
| `<p>/inspection/result` | `{"type":"inspection_result","status":"completed"\|"not_found"\|"incomplete","job_id","event_id" หรือ "result_id","requested","detected","image"}` (QoS 1) | รับผลตรวจจาก Dashboard ฝั่งรับของ ตัดข้อความซ้ำด้วย `event_id` (หรือ `result_id` เมื่อกล้องหมดเวลา ซึ่งอาจไม่มีรูป) เก็บที่ `captures/inspection/` (ล่าสุด 20) และผูกกับคิวที่ส่ง `job_id` เดียวกัน (หรือ `armbot_job_id` = เลขงานของเรา) เปลี่ยน topic ได้ด้วย `result_topic` ใน `calibration/mqtt.json` หรือ `MQTT_RESULT_TOPIC` |
| `<p>/inspection/unknown/request` | `{"type":"unknown_review_request","request_id","job_id","armbot_job_id","requested","image"}` (QoS 1) | คำขอให้ทีมตรวจ Unknown (เรา) ดูภาพเฟรมแรกจากกล้องฝั่งรับของ ตัดซ้ำด้วย `request_id` แสดงบน Dashboard ในส่วน "รอทีมตรวจ Unknown" |
| `<p>/cmd/review` | `{"key":"queue-43","decision":"approved","note":""}` | ตอบคำขอตรวจ Unknown จาก Dashboard ของเรา (โหมด HTTP ใช้ `POST /api/reviews/<key>`) แล้ว Pi ส่งผลไปที่ `<p>/inspection/unknown/response` |

> **หมายเหตุสำหรับ `<p>/cmd/queue`:**
> * เลือกระบุตัวระบุสินค้าเพียง **อย่างใดอย่างหนึ่ง** จาก `item_id`, `barcode` หรือ `text`
> * `count` หรือ `quantity` ใช้ระบุจำนวนชิ้น (ค่าเริ่มต้นคือ `1` และจะถูกจำกัดไม่ให้เกิน `stock` คงเหลือหรือลิมิตต่อรอบงาน)
> * `job_id` (สตริง ไม่เกิน 100 ตัวอักษร) เป็นทางเลือก: Pi จะส่งกลับใน `<p>/event` และใช้ผูกกับผลตรวจใน `<p>/inspection/result`
> * `source` เป็นทางเลือก (หากไม่ระบุ ค่าเริ่มต้นคือ `"mqtt"`)
> 
> 

---

### 3. โครงสร้างข้อมูลใน `<p>/status`

| ฟิลด์ | ค่าที่เป็นไปได้ | ความหมาย |
| --- | --- | --- |
| `mode` | `execute` / `dry-run` | `execute` = สั่งขยับแขนกลจริง, `dry-run` = จำลองการทำงานโดยไม่ส่งสัญญาณไปยัง servo |
| `state` | `idle` / `picking` / `resting` | สถานะปัจจุบัน: ว่าง / กำลังหยิบวัตถุ / กำลังกลับท่าพัก |
| `jobs[].id` | ตัวเลข (Integer) | หมายเลขงาน (ใช้อ้างอิงสำหรับคำสั่ง `cmd/cancel`) |
| `jobs[].item_id`, `name`, `name_th` | เช่น `relay`, `รีเลย์โมดูล` | รหัสและชื่อของสินค้าที่ถูกสั่งหยิบ |
| `jobs[].source` | `voice` / `barcode` / `click` / `mqtt` | ช่องทางที่มาของคำสั่ง |
| `jobs[].count`, `jobs[].done` | ตัวเลข (Integer) | จำนวนที่สั่งทั้งหมด (`count`) และจำนวนที่หยิบสำเร็จแล้ว (`done`) |
| `jobs[].state` | `queued` → `running` → `done` / `failed` / `cancelled` | ลำดับสถานะของแต่ละงานในคิว |
| `jobs[].message` | ข้อความ (String) | รายละเอียดผลลัพธ์ หรือสาเหตุเมื่องานไม่สำเร็จ (`failed`) |
| `jobs[].created` | Unix timestamp | เวลาที่สร้างคำสั่งงาน |
| `log` | Array of Strings | รายการข้อความ log ล่าสุดของโปรแกรมควบคุมการหยิบ |

```

Would you like me to translate the entire document into one language (all English or all Thai), or add a directory tree section showing how the `cpp/`, `python/`, `models/`, and `calibration/` folders are organized?

```
