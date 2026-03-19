from __future__ import annotations

import time
from datetime import datetime
from threading import RLock

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover
    serial = None
    list_ports = None

from ..models import HardwarePort


class SerialAdapterError(RuntimeError):
    pass


class SerialAdapter:
    def __init__(self) -> None:
        self._serial = None
        self._lock = RLock()
        self.last_response: str | None = None
        self.last_seen_at: datetime | None = None

    @property
    def is_available(self) -> bool:
        return serial is not None and list_ports is not None

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    @property
    def port(self) -> str | None:
        return self._serial.port if self._serial else None

    @property
    def baud_rate(self) -> int | None:
        return self._serial.baudrate if self._serial else None

    def list_ports(self) -> list[HardwarePort]:
        if list_ports is None:
            return []
        return [
            HardwarePort(device=item.device, description=item.description, hwid=item.hwid)
            for item in list_ports.comports()
        ]

    def connect(self, port: str, baud_rate: int = 115200, timeout: float = 1.0) -> dict:
        if serial is None:
            raise SerialAdapterError("pyserial is not installed. Run `python run.py --setup`.")

        with self._lock:
            self.disconnect()
            try:
                self._serial = serial.Serial(port=port, baudrate=baud_rate, timeout=timeout, write_timeout=timeout)
            except Exception as exc:
                self._serial = None
                raise SerialAdapterError(f"Failed to open serial port {port}: {exc}") from exc

            time.sleep(2.0)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()

            try:
                self.send_command("PING")
                status = self.send_command("STATUS")
                return status
            except Exception:
                self.disconnect()
                raise

    def disconnect(self) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

    def _read_line(self) -> str:
        if not self._serial or not self._serial.is_open:
            raise SerialAdapterError("Serial port is not connected")

        raw = self._serial.readline()
        if not raw:
            raise SerialAdapterError("No response from Arduino")
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            raise SerialAdapterError("Received empty response from Arduino")
        self.last_response = line
        self.last_seen_at = datetime.utcnow()
        return line

    @staticmethod
    def parse_line(line: str) -> dict:
        parts = line.split()
        payload: dict[str, str] = {"kind": parts[0] if parts else "UNKNOWN", "raw": line}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                payload[key.strip()] = value.strip()
            else:
                payload.setdefault("args", "")
                payload["args"] = (payload["args"] + " " + token).strip()
        return payload

    def send_command(self, command: str) -> dict:
        with self._lock:
            if not self._serial or not self._serial.is_open:
                raise SerialAdapterError("Serial port is not connected")
            try:
                self._serial.write((command.strip() + "\n").encode("utf-8"))
                self._serial.flush()
                line = self._read_line()
                return self.parse_line(line)
            except SerialAdapterError:
                raise
            except Exception as exc:
                raise SerialAdapterError(f"Serial command failed: {exc}") from exc

    def ping(self) -> dict:
        return self.send_command("PING")

    def get_status(self) -> dict:
        return self.send_command("STATUS")

    def set_joint(self, joint_name: str, angle: float) -> dict:
        return self.send_command(f"SET {joint_name} {round(float(angle), 2)}")

    def preset(self, name: str) -> dict:
        return self.send_command(f"PRESET {name.upper()}")

    def stop(self) -> dict:
        return self.send_command("STOP")


serial_adapter = SerialAdapter()
