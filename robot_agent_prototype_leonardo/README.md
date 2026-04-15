# Robot Arm Hardware Console — Leonardo build

Этот проект теперь намеренно урезан до честного hardware-only ядра. В нем больше нет `demo`/`sim` режимов, псевдо-чата, intent parser и "агента", который только имитирует работу.

Что реально работает:

- FastAPI backend открывает serial-порт к Arduino Leonardo.
- UI подключает порт, показывает статус и логи.
- Ручные команды отправляют реальные `SET joint angle` в Arduino.
- Presets отправляют реальные `PRESET NAME` в Arduino.
- `STOP` отправляет реальный `STOP`.
- Если Arduino не подключена, движение блокируется, а не симулируется.
- В браузере есть камера-визор на `MediaPipe Pose`, который переводит движение руки в `base` и `shoulder`.

План настоящей агентной системы лежит здесь:

- [`docs/AGENT_SYSTEM_PLAN.md`](docs/AGENT_SYSTEM_PLAN.md)

## Текущая механика

Arduino Leonardo sketch рассчитан на 3 сервопривода:

- `base` -> D3, диапазон 0..180
- `gripper` -> D5, диапазон 0..90
- `shoulder` -> D13, диапазон 0..180

Если сейчас физически работают только 2 оси, оставь `gripper` неподключенным или не отправляй на него команды до калибровки.

## Vision MVP

Сейчас vision-часть сделана как браузерный прототип:

- камера берется через `getUserMedia()` в frontend
- поза вычисляется через `@mediapipe/tasks-vision`
- для старта используется официальный `pose_landmarker_lite.task`
- в робот отправляются только `base` и `shoulder`
- `gripper` пока остается вне vision-пути

Как это использовать:

1. Подними приложение.
2. Открой камеру в новом блоке vision.
3. Поставь руку в нейтральную позу, когда она смотрит вверх.
4. Нажми `Capture calibration` в этой позе.
5. Включи `Send to robot`.
6. Двигай рукой влево/вправо и вверх/вниз, чтобы менять `base` и `shoulder`.

Маппинг сейчас намеренно простой:

- калибровка задает точку `90/90`
- смещение руки по горизонтали двигает `base`
- смещение руки по вертикали двигает `shoulder`
- для стабильности есть сглаживание и deadband

Если хочешь расширять дальше, следующий логичный шаг после этого MVP - вынести обработку в отдельный worker и потом уже добавлять логику для gripper.

## Быстрый старт

```bash
~/.pyenv/versions/3.12.12/bin/python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..
python run.py
```

Если виртуальное окружение уже создано:

```bash
source .venv/bin/activate
python run.py
```

Адреса:

- frontend: `http://127.0.0.1:5173`
- backend: `http://127.0.0.1:8000`
- docs: `http://127.0.0.1:8000/docs`

Важно:

- backend нужно запускать на Python 3.10+
- `python run.py` поднимает backend без `--reload`, чтобы избежать проблем со слежением за файлами
- frontend работает через Vite proxy и обращается к backend по `/api/*`

## Arduino sketch

Файл прошивки:

```text
arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino
```

Прошивка загружается один раз. После этого FastAPI держит serial-порт открытым и отправляет команды:

```text
PING
STATUS
SET base 120
SET shoulder 70
SET gripper 30
PRESET HOME
PRESET LIFT
PRESET OPEN
PRESET CLOSE
PRESET LEFT
PRESET CENTER
PRESET RIGHT
STOP
```

Sketch также содержит `CYCLE`, `WAVE`, `DEMO` и `PARK`. Они реальны, потому что выполняются на Arduino, но для первых тестов безопаснее начинать с `HOME`, `SET` по одному joint и `STOP`.

Текущая версия прошивки работает в `safe_start` режиме:

- после включения сервы не attach'ятся автоматически
- первая команда движения attach'ит только нужную серву
- это уменьшает риск неожиданного рывка сразу после подачи питания

Новая прошивка нужна только если меняются:

- пины
- диапазоны
- пресеты
- serial-протокол

## Где менять конфиг под свою механику

### 1. Пины и диапазоны на Arduino

Открой `arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino` и найди блок:

```cpp
JointConfig JOINTS[] = {
  {"base", 3, 0, 180, 0, 180, 90},
  {"gripper", 5, 0, 90, 0, 90, 45},
  {"shoulder", 13, 0, 180, 0, 180, 90},
};
```

Формат строки:

```cpp
{"name", pin, logicalMin, logicalMax, servoMin, servoMax, defaultAngle}
```

Если сервопривод крутится наоборот, поменяй местами `servoMin` и `servoMax`.

Пример инверсии:

```cpp
{"base", 3, 0, 180, 180, 0, 90}
```

### 2. Лимиты на backend

Открой `backend/control/service.py` и синхронно поменяй `JOINT_LIMITS`.

### 3. Начальные значения UI

Открой `backend/models.py` и синхронно поменяй `RobotState.joints`.

## Ручная проверка API

### Получить статус

```text
GET /api/status
```

### Посмотреть serial-порты

```text
GET /api/hardware/ports
```

### Подключиться к Arduino

```text
POST /api/hardware/connect
```

```json
{
  "port": "/dev/cu.usbmodemXXXX",
  "baud_rate": 115200
}
```

### Двинуть сустав

```text
POST /api/manual/joint
```

```json
{
  "joint_name": "base",
  "angle": 120
}
```

### Применить позу

```text
POST /api/manual/pose
```

```json
{
  "joints": {
    "base": 90,
    "shoulder": 120,
    "gripper": 45
  }
}
```

### Запустить Arduino preset

```text
POST /api/manual/preset/HOME
```

### Остановить движение

```text
POST /api/manual/stop
```

## Безопасный первый запуск

1. Залей прошивку.
2. Подключи питание серв правильно: не питай силовые сервы от Arduino 5V.
3. Сделай общую землю между отдельным питанием серв и Arduino.
4. Проверь, что Arduino отвечает на `PING` и `STATUS`.
5. Подключи веб-интерфейс.
6. Сначала используй `HOME`.
7. Потом двигай по одному суставу маленькими шагами.
8. Только после этого используй `Apply full pose`.
9. Держи `STOP` под рукой.

## Что удалено

- `/api/chat`
- `/api/mode`
- `demo`/`sim` execution modes
- `backend/agent.py`
- `backend/router.py`
- `backend/skills/robot_arm.py`
- frontend pseudo-command panel

Следующий шаг — не возвращать фейковый чат, а добавить отдельный agent service с tool allowlist, camera/CV tools, safety-gate и видимым trace каждого решения.
