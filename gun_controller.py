from __future__ import annotations

import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, Optional

import serial
import serial.tools.list_ports


def clamp(value: float, lo: float, hi: float) -> float:
	return max(lo, min(hi, value))


@dataclass
class GunConfig:
	baudrate: int = 9600
	serial_port: Optional[str] = None
	serial_timeout: float = 1.0

	pan_min: int = 0
	pan_max: int = 180
	tilt_min: int = 70
	tilt_max: int = 110
	pan_center: int = 90
	tilt_center: int = 90

	trigger_safe: int = 45
	trigger_fire: int = 135

	smoothing: float = 0.25
	sender_hz: float = 50.0


class GunController:
	"""Thread-safe pan/tilt/trigger controller with optional preview mode."""

	def __init__(self, config: Optional[GunConfig] = None):
		self.config = config or GunConfig()
		self._arduino: Optional[serial.Serial] = None
		self._running = False
		self._thread: Optional[threading.Thread] = None
		self._lock = threading.Lock()

		self.current_pan = float(self.config.pan_center)
		self.current_tilt = float(self.config.tilt_center)
		self.target_pan = float(self.config.pan_center)
		self.target_tilt = float(self.config.tilt_center)
		self.last_error: Optional[str] = None
		self.preview_mode = True

	@staticmethod
	def available_ports() -> list[str]:
		return [p.device for p in serial.tools.list_ports.comports()]

	@staticmethod
	def _is_likely_arduino_port(port: str) -> bool:
		patterns = (
			"ttyUSB",
			"ttyACM",
			"usbmodem",
			"usbserial",
			"wchusbserial",
			"cu.usb",
			"COM",
		)
		return any(token in port for token in patterns)

	def _resolve_serial_port(self) -> Optional[str]:
		if self.config.serial_port:
			return self.config.serial_port

		preferred = [
			"/dev/ttyUSB0",
			"/dev/ttyUSB1",
			"/dev/ttyACM0",
			"/dev/ttyACM1",
		]
		available = [p for p in self.available_ports() if self._is_likely_arduino_port(p)]

		for candidate in preferred:
			if candidate in available:
				return candidate

		return available[0] if available else None

	def connect(self) -> bool:
		port = self._resolve_serial_port()
		if not port:
			self.preview_mode = True
			self.last_error = "No serial device found. Running in preview mode."
			return False

		try:
			self._arduino = serial.Serial(
				port=port,
				baudrate=self.config.baudrate,
				timeout=self.config.serial_timeout,
			)
			time.sleep(2)
			self.preview_mode = False
			self.last_error = None
			self.config.serial_port = port
			return True
		except serial.SerialException as exc:
			self.preview_mode = True
			self.last_error = f"Failed to connect to {port}: {exc}"
			return False

	def start(self):
		if self._running:
			return

		if self._arduino is None:
			self.connect()

		self._running = True
		self._thread = threading.Thread(target=self._sender_loop, daemon=True)
		self._thread.start()

	def stop(self):
		self._running = False
		if self._thread and self._thread.is_alive():
			self._thread.join(timeout=1.0)

		with self._lock:
			self._safe_center_write()

		if self._arduino and self._arduino.is_open:
			try:
				self._arduino.close()
			except serial.SerialException:
				pass

	def _sender_loop(self):
		delay = 1.0 / max(self.config.sender_hz, 1.0)
		while self._running:
			with self._lock:
				self.current_pan += (self.target_pan - self.current_pan) * self.config.smoothing
				self.current_tilt += (self.target_tilt - self.current_tilt) * self.config.smoothing

				pan_i = int(clamp(self.current_pan, self.config.pan_min, self.config.pan_max))
				tilt_i = int(clamp(self.current_tilt, self.config.tilt_min, self.config.tilt_max))
				self._write(pan_i, tilt_i, self.config.trigger_safe)

			time.sleep(delay)

	def _write(self, pan: int, tilt: int, trigger: int):
		if self.preview_mode or self._arduino is None or not self._arduino.is_open:
			return

		payload = f"{pan},{tilt},{trigger}\n"
		try:
			self._arduino.write(payload.encode())
			self.last_error = None
		except serial.SerialException as exc:
			self.last_error = f"Serial write failed: {exc}"

	def _safe_center_write(self):
		pan_i = int(clamp(self.config.pan_center, self.config.pan_min, self.config.pan_max))
		tilt_i = int(clamp(self.config.tilt_center, self.config.tilt_min, self.config.tilt_max))
		self._write(pan_i, tilt_i, self.config.trigger_safe)

	def set_target_angles(self, pan: float, tilt: float):
		with self._lock:
			self.target_pan = clamp(pan, self.config.pan_min, self.config.pan_max)
			self.target_tilt = clamp(tilt, self.config.tilt_min, self.config.tilt_max)

	def set_from_joystick(self, x_norm: float, y_norm: float):
		"""
		Convert normalized joystick axis to pan/tilt targets.
		x_norm and y_norm are expected in range [-1.0, 1.0].
		"""
		x = clamp(x_norm, -1.0, 1.0)
		y = clamp(y_norm, -1.0, 1.0)

		pan_span = (self.config.pan_max - self.config.pan_min) / 2.0
		tilt_span = (self.config.tilt_max - self.config.tilt_min) / 2.0

		pan_target = self.config.pan_center + x * pan_span
		# Screen/joystick convention: upward drag means smaller y and higher tilt.
		tilt_target = self.config.tilt_center - y * tilt_span
		self.set_target_angles(pan_target, tilt_target)

	def center(self):
		self.set_target_angles(self.config.pan_center, self.config.tilt_center)

	def fire(self):
		with self._lock:
			pan_i = int(clamp(self.current_pan, self.config.pan_min, self.config.pan_max))
			tilt_i = int(clamp(self.current_tilt, self.config.tilt_min, self.config.tilt_max))
			self._write(pan_i, tilt_i, self.config.trigger_fire)
			time.sleep(0.2)
			self._write(pan_i, tilt_i, self.config.trigger_safe)

	def self_test(self) -> Dict[str, object]:
		result: Dict[str, object] = {
			"serial_connected": not self.preview_mode,
			"serial_port": self.config.serial_port,
			"last_error_before": self.last_error,
			"movement_cycle": "pending",
			"ok": False,
		}

		if self.preview_mode:
			self.center()
			result["movement_cycle"] = "skipped_preview_mode"
			result["ok"] = True
			return result

		# Run a tiny movement cycle and return to center.
		self.center()
		time.sleep(0.25)
		self.set_target_angles(self.config.pan_center + 8, self.config.tilt_center - 4)
		time.sleep(0.25)
		self.set_target_angles(self.config.pan_center - 8, self.config.tilt_center + 4)
		time.sleep(0.25)
		self.center()
		time.sleep(0.25)

		result["last_error_after"] = self.last_error
		result["movement_cycle"] = "completed"
		result["ok"] = self.last_error is None
		return result

	def state(self) -> Dict[str, object]:
		with self._lock:
			return {
				"connected": not self.preview_mode,
				"preview_mode": self.preview_mode,
				"serial_port": self.config.serial_port,
				"current_pan": round(self.current_pan, 2),
				"current_tilt": round(self.current_tilt, 2),
				"target_pan": round(self.target_pan, 2),
				"target_tilt": round(self.target_tilt, 2),
				"last_error": self.last_error,
				"limits": {
					"pan_min": self.config.pan_min,
					"pan_max": self.config.pan_max,
					"tilt_min": self.config.tilt_min,
					"tilt_max": self.config.tilt_max,
				},
				"config": asdict(self.config),
			}

