# Tactical Remote Weapon Station (RWS) v1.0

A military-grade, 3D-printed remote weapon station designed for remote targeting and firing control. This is Version 1 - Target Acquisition System, capable of precise pan/tilt positioning and trigger actuation for mounted weapon systems.

## ⚠️ Important Notice
This project is designed for **military/defense applications** and should be used in accordance with all applicable laws and regulations. Version 1 focuses on targeting capabilities; Version 2 will integrate live-fire mechanisms.

## 🎯 System Capabilities (v1.0)
- **Remote Targeting:** Precision pan/tilt control (180° traverse, 40° elevation)
- **Trigger Actuation:** Right-click initiated firing sequence
- **Smooth Tracking:** Real-time interpolation for fluid target tracking
- **3D Printed Chassis:** Custom-designed in Blender for weapon mounting
- **Bounds Protection:** Software limits to prevent mechanical over-travel

## 🔧 Technical Specifications
| Component | Specification |
|-----------|--------------|
| Pan Range | 0° - 180° |
| Tilt Range | 70° - 110° (40° operational range) |
| Trigger Travel | 45° (safe) - 135° (fire) |
| Control System | Mouse-based targeting |
| Update Rate | ~50Hz |
| Smoothing | Configurable low-pass filter |

## 📦 Hardware Requirements
- Arduino (Uno/Nano)
- 3x High-torque servos (metal gear recommended)
- Custom 3D printed mount (STL files included)
- Power supply (5V/2A+ for servos)
- USB connection cable

## 🏗️ Mechanical Design
All components designed from scratch using Blender, with emphasis on:
- **Stability:** Rigid mounting platform for recoil management
- **Precision:** Zero-backlash servo mounting
- **Modularity:** Easy access for maintenance and upgrades
- **Future-ready:** Designed for v2.0 live-fire integration

## 🖥️ Software Architecture
```python
# Real-time targeting system
- Mouse position → Servo angles (smoothed)
- Right-click → Trigger sequence (45° → 135° → 45°)
- Threaded design for non-blocking operation
