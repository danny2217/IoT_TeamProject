#!/usr/bin/env python3
"""HC-SR04 proximity sensor -> WAKE_UP / LEAVE events over MQTT.

Runs on Node 3 (RPi 3B LCD). Can also be tested on the RPi 400 GPIO header.

Wiring (BCM numbering):
    VCC  -> 5V   (pin 2)
    GND  -> GND  (pin 6)
    TRIG -> GPIO23 (pin 16)
    ECHO -> 1 kOhm -> GPIO24 (pin 18), and GPIO24 -> 2 kOhm -> GND
            (voltage divider: the 5 V echo pulse becomes ~3.3 V)

Usage:
    python node3_sensor/proximity.py --test          # only print distances
    python node3_sensor/proximity.py                 # publish events to the broker
    python node3_sensor/proximity.py --host localhost
"""

import argparse
import os
import statistics
import sys
import time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

NODE = "node3"


class ProximityDetector:
    """Turns raw distance readings into WAKE_UP / LEAVE events.

    - WAKE_UP: median distance < wake_cm for `near_hits` readings in a row,
      and at least `cooldown_s` since the previous LEAVE (no instant re-wake
      when someone walks past the edge of the 1 m zone).
    - LEAVE: after a WAKE_UP, median distance > leave_cm for `leave_s` seconds.
    The gap between wake_cm and leave_cm (hysteresis) stops flickering at ~1 m.
    """

    def __init__(self, wake_cm=100.0, leave_cm=130.0, near_hits=3,
                 leave_s=5.0, cooldown_s=3.0, window=5):
        self.wake_cm = wake_cm
        self.leave_cm = leave_cm
        self.near_hits = near_hits
        self.leave_s = leave_s
        self.cooldown_s = cooldown_s
        self.samples = deque(maxlen=window)
        self.present = False
        self.near_count = 0
        self.far_since = None
        self.last_leave = float("-inf")

    def update(self, distance_cm: float, now: float):
        """Feed one reading. Returns ("WAKE_UP"|"LEAVE", median_cm) or None."""
        self.samples.append(distance_cm)
        median = statistics.median(self.samples)

        if not self.present:
            self.near_count = self.near_count + 1 if median < self.wake_cm else 0
            if self.near_count >= self.near_hits and now - self.last_leave >= self.cooldown_s:
                self.present = True
                self.near_count = 0
                self.far_since = None
                return "WAKE_UP", median
        else:
            if median > self.leave_cm:
                if self.far_since is None:
                    self.far_since = now
                elif now - self.far_since >= self.leave_s:
                    self.present = False
                    self.far_since = None
                    self.last_leave = now
                    return "LEAVE", median
            else:
                self.far_since = None
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="HC-SR04 proximity -> MQTT WAKE_UP")
    parser.add_argument("--test", action="store_true", help="print distances, no MQTT")
    parser.add_argument("--host", default=None, help="MQTT broker (default pb-node1.local)")
    parser.add_argument("--trigger", type=int, default=23, help="BCM pin for TRIG")
    parser.add_argument("--echo", type=int, default=24, help="BCM pin for ECHO")
    parser.add_argument("--wake-cm", type=float, default=100.0)
    parser.add_argument("--leave-cm", type=float, default=130.0)
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between readings")
    args = parser.parse_args()

    from gpiozero import DistanceSensor  # imported here so the detector can be unit-tested

    sensor = DistanceSensor(echo=args.echo, trigger=args.trigger,
                            max_distance=2.0, queue_len=3)
    detector = ProximityDetector(wake_cm=args.wake_cm, leave_cm=args.leave_cm)

    client = None
    if not args.test:
        from common.booth_mqtt import T, connect, make_payload
        client = connect(NODE, args.host)
        client.loop_start()

    print("Measuring... (Ctrl+C to stop)")
    try:
        while True:
            distance_cm = sensor.distance * 100.0  # gpiozero returns metres
            event = detector.update(distance_cm, time.monotonic())
            if args.test:
                state = "NEAR" if detector.present else "away"
                print(f"{distance_cm:6.1f} cm  [{state}]" + (f"  -> {event[0]}" if event else ""))
            elif event:
                name, median = event
                client.publish(T.PROXIMITY,
                               make_payload(event=name, distance_cm=round(median, 1)),
                               qos=1)
                print(f"{name} published ({median:.1f} cm)")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.close()
        if client is not None:
            from common.booth_mqtt import disconnect
            disconnect(client, NODE)  # needs the network loop still running
            client.loop_stop()


if __name__ == "__main__":
    main()
