from __future__ import annotations

import argparse
import queue
import threading
import time
from typing import Optional

from gun_controller import GunConfig, GunController, clamp


def map_value(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    if in_max == in_min:
        return out_min
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def _import_mouse_dependencies():
    try:
        import pyautogui
        from pynput import mouse
        return pyautogui, mouse
    except Exception as exc:
        raise RuntimeError(
            "Mouse mode requires pyautogui and pynput with desktop access. "
            "Install dependencies and run in a graphical session."
        ) from exc


def run_mouse_mode(controller: GunController):
    pyautogui, mouse = _import_mouse_dependencies()

    pyautogui.FAILSAFE = False

    width, height = pyautogui.size()
    if width <= 1 or height <= 1:
        width, height = 1920, 1080

    print(f"Mouse mode active. Screen: {width}x{height}")
    print("Move mouse to aim. Right-click to fire. Ctrl+C to exit.")

    def on_click(_x, _y, button, pressed):
        if button == mouse.Button.right and pressed:
            threading.Thread(target=controller.fire, daemon=True).start()

    listener = mouse.Listener(on_click=on_click)
    listener.start()

    try:
        while True:
            x, y = pyautogui.position()
            x = clamp(x, 0, width - 1)
            y = clamp(y, 0, height - 1)

            target_pan = map_value(x, 0, width, controller.config.pan_min, controller.config.pan_max)
            target_tilt = map_value(y, 0, height, controller.config.tilt_max, controller.config.tilt_min)
            controller.set_target_angles(target_pan, target_tilt)

            state = controller.state()
            print(
                f"Target: {state['target_pan']:.1f}/{state['target_tilt']:.1f} | "
                f"Current: {state['current_pan']:.1f}/{state['current_tilt']:.1f}",
                end="\r",
                flush=True,
            )
            time.sleep(0.04)
    finally:
        listener.stop()


def run_terminal_mode(controller: GunController):
    input_queue: queue.Queue[str] = queue.Queue()

    def reader():
        while True:
            try:
                line = input()
            except EOFError:
                input_queue.put("quit")
                return
            input_queue.put(line.strip())

    threading.Thread(target=reader, daemon=True).start()

    print("Terminal mode active.")
    print("Commands: joy <x> <y> | set <pan> <tilt> | center | fire | status | help | quit")

    while True:
        try:
            cmd = input_queue.get(timeout=0.15)
        except queue.Empty:
            continue

        if not cmd:
            continue

        parts = cmd.split()
        op = parts[0].lower()

        if op in {"quit", "exit", "q"}:
            print("Exiting terminal mode.")
            return

        if op == "help":
            print("joy <x> <y>: normalized joystick values in range -1..1")
            print("set <pan> <tilt>: direct target angles")
            print("center: move to center")
            print("fire: execute trigger sequence")
            print("status: show state")
            print("quit: exit")
            continue

        if op == "center":
            controller.center()
            print("Centered.")
            continue

        if op == "fire":
            threading.Thread(target=controller.fire, daemon=True).start()
            print("Fire command issued.")
            continue

        if op == "status":
            print(controller.state())
            continue

        if op == "joy":
            if len(parts) != 3:
                print("Usage: joy <x> <y>")
                continue
            try:
                x = float(parts[1])
                y = float(parts[2])
                controller.set_from_joystick(x, y)
                print(f"Joystick set: x={x:.2f} y={y:.2f}")
            except ValueError:
                print("Invalid values. Use numeric input.")
            continue

        if op == "set":
            if len(parts) != 3:
                print("Usage: set <pan> <tilt>")
                continue
            try:
                pan = float(parts[1])
                tilt = float(parts[2])
                controller.set_target_angles(pan, tilt)
                print(f"Target set: pan={pan:.1f} tilt={tilt:.1f}")
            except ValueError:
                print("Invalid values. Use numeric input.")
            continue

        print("Unknown command. Type help.")


def parse_args():
    parser = argparse.ArgumentParser(description="Gun controller with mouse and terminal modes")
    parser.add_argument("--mode", choices=["mouse", "terminal"], default="terminal")
    parser.add_argument("--port", default=None, help="Serial port, example: /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    return parser.parse_args()


def main():
    args = parse_args()
    config = GunConfig(serial_port=args.port, baudrate=args.baud)
    controller = GunController(config)

    controller.connect()
    controller.start()

    state = controller.state()
    if state["connected"]:
        print(f"Connected on {state['serial_port']}")
    else:
        print("Preview mode started (no serial connection).")
        if state["last_error"]:
            print(state["last_error"])

    try:
        if args.mode == "mouse":
            run_mouse_mode(controller)
        else:
            run_terminal_mode(controller)
    except KeyboardInterrupt:
        print("Interrupted by user.")
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
