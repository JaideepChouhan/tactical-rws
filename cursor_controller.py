import pyautogui
import serial
import time
import threading
from pynput import mouse  # Better for click detection

# --- Configuration ---
SERIAL_PORT = '/dev/ttyUSB0'
BAUDRATE = 9600
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
TRIGGER_HOME = 45
TRIGGER_ACTIVE = 135

# Disable pyautogui failsafe
pyautogui.FAILSAFE = False

# Smoothing factor (0-1, lower = smoother but more delay)
SMOOTHING = 0.3
# ---

# Global variables for smoothing
current_pan = 90
current_tilt = 90
target_pan = 90
target_tilt = 90
arduino = None

def map_value(x, in_min, in_max, out_min, out_max):
    """Maps a value from one range to another."""
    if in_max == in_min:
        return out_min
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

def bound_mouse():
    """Keep mouse within screen boundaries"""
    x, y = pyautogui.position()
    x = max(0, min(x, SCREEN_WIDTH - 1))
    y = max(0, min(y, SCREEN_HEIGHT - 1))
    if (x, y) != pyautogui.position():
        pyautogui.moveTo(x, y)
    return x, y

def trigger_sequence():
    """Move trigger from 45° → 135° → 45°"""
    global arduino, current_pan, current_tilt
    
    # Send active position
    data = f"{int(current_pan)},{int(current_tilt)},{TRIGGER_ACTIVE}\n"
    arduino.write(data.encode())
    print(f"🔥 Trigger: {TRIGGER_ACTIVE}°")
    time.sleep(0.2)
    
    # Return to home
    data = f"{int(current_pan)},{int(current_tilt)},{TRIGGER_HOME}\n"
    arduino.write(data.encode())
    print(f"⬅️ Trigger: {TRIGGER_HOME}°")

def on_click(x, y, button, pressed):
    """Mouse click handler"""
    if button == mouse.Button.right and pressed:
        print("\n🔥 RIGHT CLICK DETECTED!")
        threading.Thread(target=trigger_sequence, daemon=True).start()
    return True

def smooth_move():
    """Smoothly move servos toward target"""
    global current_pan, current_tilt, target_pan, target_tilt, arduino
    
    while True:
        # Apply low-pass filter for smooth movement
        current_pan = current_pan * (1 - SMOOTHING) + target_pan * SMOOTHING
        current_tilt = current_tilt * (1 - SMOOTHING) + target_tilt * SMOOTHING
        
        # Send to Arduino
        data = f"{int(current_pan)},{int(current_tilt)},{TRIGGER_HOME}\n"
        arduino.write(data.encode())
        
        # Small delay for smoothness
        time.sleep(0.02)

def main():
    global arduino, target_pan, target_tilt
    
    print(f"Screen: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    print("Connecting to Arduino...")
    
    try:
        # Connect to Arduino
        arduino = serial.Serial(port=SERIAL_PORT, baudrate=BAUDRATE, timeout=1)
        time.sleep(2)
        print("✅ Connected!")
        print("• Move mouse to control pan/tilt (smoothed)")
        print("• Right-click to fire trigger")
        print("• Mouse automatically bounded to screen")
        print("Press Ctrl+C to exit\n")
        
        # Start mouse listener in background
        listener = mouse.Listener(on_click=on_click)
        listener.start()
        
        # Start smooth movement thread
        smooth_thread = threading.Thread(target=smooth_move, daemon=True)
        smooth_thread.start()
        
        # Main loop - just update targets
        while True:
            # Get and bound mouse position
            x, y = bound_mouse()
            
            # Update target positions
            target_pan = map_value(x, 0, SCREEN_WIDTH, 0, 180)
            target_tilt = map_value(y, 0, SCREEN_HEIGHT, 110, 70)
            
            # Display status occasionally
            print(f"Target: Pan={int(target_pan):3d}° Tilt={int(target_tilt):3d}° | Current: {int(current_pan):3d}° {int(current_tilt):3d}°", end='\r')
            
            time.sleep(0.05)
            
    except KeyboardInterrupt:
        print("\n\n👋 Exiting...")
    finally:
        if arduino and arduino.is_open:
            # Return to center on exit
            arduino.write("90,90,45\n".encode())
            time.sleep(0.3)
            arduino.close()
            print("Serial connection closed.")

if __name__ == "__main__":
    main()