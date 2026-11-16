# YOLO XYZ + DDQN + Raspberry Pi 5 + PCA9685 (C++/Python)

This ZIP combines the available project files in one repository. It includes a **dry-run integration scaffold**,
not a claim that autonomous physical pick-and-place is already calibrated.

## Hardware/software architecture
ESP32-CAM/OV3660 stream -> Raspberry Pi 5 Python YOLO XYZ -> legacy IK/DDQN dry-run -> (future) C++ PCA9685 daemon -> six servos.

The C++ controller uses Linux `i2c-dev`, not pigpio. The PCA9685 generates servo PWM in hardware; the Pi communicates over I2C.
The `servo_daemon` command protocol is `MOVE <six angles in degrees>`, `OFF`, and `QUIT`, but the physical bridge is intentionally
not connected by default.

## Install on Raspberry Pi OS
```bash
sudo apt update
sudo apt install -y build-essential cmake i2c-tools python3-venv
sudo raspi-config   # enable I2C
sudo reboot
```
Check the board address:
```bash
sudo i2cdetect -y 1
```
Build C++:
```bash
cd cpp
cmake -S . -B build
cmake --build build -j
```
Python:
```bash
cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install -r python/requirements.txt
```

## Required assets before camera dry-run
1. Copy YOLO weights to `models/best.pt`.
2. Create real `calibration/H_camera_to_robot.npy` from measured points at 640x480.
3. Edit `calibration/config.json`: actual stream URL, measured `table_z_mm`, class grasp offsets, tray XYZ.
4. Check model file and Python dependencies.

Run only dry-run:
```bash
python python/robot_pipeline.py --dry-run --once
```
To preview:
```bash
python python/robot_pipeline.py --dry-run --show
```

## Safety
- Physical servo motion is not enabled in the Python pipeline.
- Do not connect or run servo commands with uncalibrated limits.
- Use an external servo power supply sized for all six servos, common ground, and a safe mechanical test setup.
- The included DH/drawing estimates are provisional. The current original Kinematics is a 3-DOF positional approximation, not a validated six-axis model.
- The DDQN checkpoint was trained against the original state/dynamics assumptions. A changed robot geometry may require evaluation/retraining.
- A single homography only maps the table plane to XY; it does not measure object height/depth.

# Armbot MQTT Topics

ใช้ `<p>` แทน topic prefix ที่ตั้งใน `calibration/mqtt.json` (ค่าเริ่มต้น `armbot`) และต้องตั้ง prefix ฝั่งหน้าเว็บให้ตรงกัน Pi จะ publish/subscribe topics เหล่านี้เมื่อเปิด MQTT (ยกเว้นเมื่อใช้ `--no-mqtt`)

## Pi ส่งออก (publish)

| Topic | Payload | QoS | Retained | ส่งเมื่อไร / ผู้ใช้ |
|---|---|---|---|---|---|
| `<p>/online` | ข้อความ `"1"` หรือ `"0"` | 1 | ใช่ | `"1"` เมื่อเชื่อม broker; `"0"` เมื่อปิดโปรแกรมหรือ broker ตรวจพบว่าหลุด (Last Will) | สถานะออนไลน์ หน้าเว็บใช้แสดงสถานะการเชื่อมต่อ |
| `<p>/status` | JSON `{"mode","state","jobs":[...],"log":[...]}` | 0 | ใช่ | Pi ตรวจทุก 1 วินาทีและส่งเมื่อข้อมูลเปลี่ยน | สถานะแขน คิวงาน และ log; หน้าเว็บ subscribe topic นี้ |
| `<p>/items` | JSON `{"items":[...]}` | 1 | ใช่ | เมื่อเชื่อม broker และเมื่อรายการเปลี่ยน เช่น stock หรือจำนวนหยิบ | รายการจาก `calibration/inventory.json`; แต่ละ item มี `id`, `name`, `name_th`, `yolo_class`, `color_only`, `zone`, `stock`, `barcode`, `aliases`, `picks`; หน้าเว็บ subscribe topic นี้ |
| `<p>/event` | JSON `{"type":"queued","job":{...},"item":{...}}` หรือ `{"type":"unknown","query":{...}}` | 1 | ไม่ | ตอบกลับทุกคำสั่ง `<p>/cmd/queue` | ยืนยันว่าเพิ่มงานแล้ว (`queued`) หรือไม่พบ item (`unknown`); สำหรับ client ที่ต้องการผลตอบรับ หน้าเว็บปัจจุบันไม่ได้ subscribe topic นี้ |
| `<p>/unknown` | JSON มี `type`, `message`, `time`, `job_id`, `image_size`, `box`, `box_center_size`, `file` | 1 | ไม่ | เมื่อโหมดเดาวัตถุ (`yolo_class: "unknown"`) พบวัตถุที่ไม่มีคลาส | แจ้งกรอบวัตถุ; `box` เป็นพิกเซล ส่วน `box_center_size` เป็น center x/y และ width/height ที่หารด้วยขนาดภาพแล้ว ภาพต้นฉบับและ label เก็บใน `captures/unknown/` บน Pi; ใช้กับ client เฝ้าดู เช่น `python/watch_unknown.py` |
| `<p>/unknown/image` | JPEG binary ที่วาดกรอบสีแดง | 0 | ไม่ | ส่งทันทีหลัง `<p>/unknown` | ภาพวัตถุไม่รู้จัก; ใช้คู่กับ topic ด้านบน |
| `<p>/camera` | JPEG binary ขนาด 320×240 | 0 | ไม่ | ทุก `camera_every_s` วินาที (ค่าเริ่มต้น 1; ตั้ง 0 เพื่อปิด) เมื่อมีกล้องและเชื่อม broker | ภาพสดจากกล้อง; หน้าเว็บ cloud subscribe topic นี้ |

## Pi รับเข้า (subscribe `<p>/cmd/#`): หน้าเว็บหรือ client อื่นส่งมาสั่งงาน

| Topic | Payload ตัวอย่าง | ผลที่เกิดขึ้น |
|---|---|---|
| `<p>/cmd/queue` | `{"item_id":"relay","source":"voice","count":2}` | เพิ่มงานหยิบเข้าคิวด้วย item id; เลือกใช้เพียงหนึ่งใน `item_id`, `barcode` หรือ `text` |
| | `{"barcode":"ARM-0002","source":"barcode"}` | หาของจากบาร์โค้ด |
| | `{"text":"เอารีเลย์","source":"mqtt","quantity":1}` | หาของจากคำใน `aliases`; `count` หรือ `quantity` ระบุจำนวน (ค่าเริ่มต้น 1; ถูกจำกัดตาม stock และขีดจำกัดต่อ job) |
| `<p>/cmd/cancel` | `{"id":3}` | ยกเลิกงานหมายเลข 3; ถ้ากำลังหยิบจะหยุดและกลับท่าพัก |
| `<p>/cmd/stop` | `{}` | หยุดงานที่กำลังทำและยกเลิกทั้งคิว จากนั้นกลับท่าพัก (rest pose) |

> ใน `cmd/queue` ใส่เพียงหนึ่งอย่างจาก `item_id`, `barcode` หรือ `text`; `source` ไม่ใส่ได้ (ค่าเริ่มต้น `"mqtt"`)

## ฟิลด์ใน `<p>/status`

| ฟิลด์ | ค่าที่เป็นไปได้ | ความหมาย |
|---|---|---|
| `mode` | `execute` / `dry-run` | `execute` ขยับแขนจริง, `dry-run` ไม่ส่งสัญญาณไป servo |
| `state` | `idle` / `picking` / `resting` | ว่างอยู่ / กำลังหยิบ / กำลังกลับท่าพัก |
| `jobs[].id` | ตัวเลข | หมายเลขงาน ใช้กับ `cmd/cancel` |
| `jobs[].item_id`, `name`, `name_th` | เช่น `relay`, `รีเลย์โมดูล` | ของที่สั่งหยิบ |
| `jobs[].source` | `voice` / `barcode` / `click` / `mqtt` | ช่องทางที่สั่งเข้ามา |
| `jobs[].count`, `jobs[].done` | จำนวนที่สั่ง / จำนวนที่หยิบสำเร็จ | ความคืบหน้าของงานหยิบหลายชิ้น |
| `jobs[].state` | `queued` → `running` → `done` / `failed` / `cancelled` | สถานะของงานแต่ละงาน |
| `jobs[].message` | ข้อความ | เหตุผลเมื่องานไม่สำเร็จ หรือรายละเอียดผลงาน |
| `jobs[].created` | Unix time | เวลาที่สั่งงาน |
| `log` | รายการข้อความ | log ล่าสุดของโปรแกรมหยิบ |
