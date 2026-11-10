#!/usr/bin/env python3
"""Talk to the Arduino Uno from Node 1 over USB serial.

Interactive mode: type commands (see arduino/booth_hw/booth_hw.ino), e.g.
    TEST          run the self-test of all parts
    COUNT:3       countdown step
    SHUTTER       shutter sound + flash
Lines from the Arduino (BTN:START, ACK:..., READY) are printed as they arrive.

Demo mode plays one full booth sequence:
    python node1_main/serial_test.py --demo

Port is auto-detected (/dev/ttyACM* for original Uno, /dev/ttyUSB* for CH340 clones).
"""

import argparse
import glob
import sys
import threading
import time

import serial  # pyserial

BAUD = 115200

DEMO_SEQUENCE = [
    ("STATE:SLEEP", 2.0),
    ("STATE:IDLE", 2.0),
    ("COUNT:3", 1.0),
    ("COUNT:2", 1.0),
    ("GAZE:WARN", 2.0),
    ("GAZE:OK", 0.5),
    ("COUNT:1", 1.0),
    ("SHUTTER", 1.5),
    ("PROCESSING", 2.0),
    ("DONE", 3.0),
    ("ERROR:demo error", 2.0),
    ("STATE:IDLE", 0.5),
]


def find_port() -> str:
    ports = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        sys.exit("Arduino not found: no /dev/ttyACM* or /dev/ttyUSB*. Check the USB cable.")
    return ports[0]


def reader(ser: serial.Serial, stop: threading.Event, interactive: bool) -> None:
    while not stop.is_set():
        try:
            line = ser.readline().decode("utf-8", errors="replace").strip()
        except serial.SerialException as exc:
            print(f"\n[serial error] {exc}")
            stop.set()
            return
        if line and interactive:
            print(f"\n<< {line}\n>> ", end="", flush=True)
        elif line:
            print(f"<< {line}", flush=True)


def send(ser: serial.Serial, command: str) -> None:
    ser.write((command + "\n").encode("utf-8"))
    ser.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Arduino serial test for Node 1")
    parser.add_argument("--port", help="serial port, default: auto-detect")
    parser.add_argument("--demo", action="store_true", help="play a full booth sequence")
    args = parser.parse_args()

    port = args.port or find_port()
    ser = serial.Serial(port, BAUD, timeout=0.2)
    print(f"Opened {port} @ {BAUD}. Waiting for the Arduino to reboot...")
    time.sleep(2.0)  # opening the port resets the Uno

    stop = threading.Event()
    threading.Thread(target=reader, args=(ser, stop, not args.demo), daemon=True).start()

    try:
        if args.demo:
            for command, pause in DEMO_SEQUENCE:
                print(f">> {command}")
                send(ser, command)
                time.sleep(pause)
            print("Demo finished.")
        else:
            print("Type a command and press Enter (Ctrl+C to quit).")
            while not stop.is_set():
                command = input(">> ").strip()
                if command:
                    send(ser, command)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        ser.close()


if __name__ == "__main__":
    main()
