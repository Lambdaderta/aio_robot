# Robot Agent Prototype — Leonardo build

Этот вариант проекта уже подрезан под Arduino Leonardo и 3 сервопривода:
- `base` -> D3, диапазон 0..180
- `gripper` -> D5, диапазон 0..90
- `shoulder` -> D13, диапазон 0..180

## Быстрый старт

```bash
~/.pyenv/versions/3.12.12/bin/python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..
python run.py
```

- frontend: `http://127.0.0.1:5173`
- backend: `http://127.0.0.1:8000`
- docs: `http://127.0.0.1:8000/docs`

Если виртуальное окружение уже создано:

```bash
source .venv/bin/activate
python run.py
```

Важно:
- backend нужно запускать на Python 3.10+
- на этой машине удобнее сразу использовать `~/.pyenv/versions/3.12.12/bin/python`
- `python run.py` теперь поднимает backend без `--reload`, чтобы избежать проблем со слежением за файлами

## Leonardo / Arduino sketch

Файл прошивки:
`arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino`

### Логика работы

Прошивка загружается **один раз**. После этого FastAPI держит serial-порт открытым и шлёт в Arduino команды:
- `PING`
- `STATUS`
- `SET base 120`
- `SET shoulder 70`
- `SET gripper 30`
- `PRESET HOME`
- `PRESET LIFT`
- `PRESET OPEN`
- `PRESET CLOSE`
- `PRESET LEFT/CENTER/RIGHT`
- `STOP`

Текущая версия прошивки работает в `safe_start` режиме:
- после включения сервы не attach'ятся автоматически
- первая команда движения attach'ит только нужную серву
- это убирает неожиданный рывок сразу после подачи питания

Новая прошивка нужна только если ты меняешь:
- пины
- диапазоны
- пресеты
- протокол

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

Если сервопривод крутится наоборот — просто поменяй местами `servoMin` и `servoMax`.

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
`GET /api/status`

### Подключиться к Arduino
`POST /api/hardware/connect`

```json
{
  "port": "/dev/cu.usbmodemXXXX",
  "baud_rate": 115200
}
```

### Двинуть сустав
`POST /api/manual/joint`

```json
{
  "joint_name": "base",
  "angle": 120
}
```

### Применить позу
`POST /api/manual/pose`

```json
{
  "joints": {
    "base": 90,
    "shoulder": 120,
    "gripper": 45
  }
}
```

## Безопасный первый запуск

1. Сначала залей прошивку.
2. Проверь, что Arduino отвечает на `PING` и `STATUS`.
3. Только потом подключай веб.
4. Переведи систему в `hardware` режим.
5. Сначала используй `HOME`.
6. Потом двигай по одному суставу.
7. Только потом используй `Apply full pose`.

## Питание серв

Не питай силовые сервы от Arduino 5V, если это не очень маленькая нагрузка. Лучше отдельное питание серв и общая земля с Arduino.
