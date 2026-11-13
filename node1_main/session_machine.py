"""Photo booth session state machine for Node 1 (pure logic, no MQTT, no serial).

States and their meaning are defined in docs/MQTT_PROTOCOL.md, section 3.3:

    SLEEP -> IDLE -> COUNTDOWN -> CAPTURE -> (next shot: COUNTDOWN -> CAPTURE)
          -> SELECT -> COMPOSE -> DONE -> IDLE        any failure -> ERROR -> IDLE

The machine is driven by two calls:
    machine.handle(event)   an input event (dict, see below)
    machine.tick()          call often (every ~50 ms); handles timers
and produces outputs that the caller drains with machine.drain():
    ("state",   {...})      publish to booth/session/state (retained)
    ("arduino", "COUNT:3")  send one line to the Arduino
    ("capture", {...})      publish to booth/cmd/capture
    ("compose", {...})      publish to booth/compose/request

Input events (dicts with a "type" key):
    {"type": "proximity", "event": "WAKE_UP" | "LEAVE"}
    {"type": "button"}                                     Arduino BTN:START
    {"type": "gesture", "gesture": "PALM" | "V_SIGN" | "FINGERS" | "THUMBS_UP", "value": 1..4}
    {"type": "gaze", "gaze_ok": bool}
    {"type": "faces", "face_count": int}
    {"type": "capture_done", "session_id": str, "shot": int, "node": str, "face_count": int?}
    {"type": "compose_result", "session_id": str, "status": "OK" | "ERROR", "url": str?, "error": str?}

Keeping this logic free of I/O lets it be tested with a fake clock
(tests/test_session_machine.py) and reused by the kiosk UI later.
"""

import time
from dataclasses import dataclass

SLEEP = "SLEEP"
IDLE = "IDLE"
COUNTDOWN = "COUNTDOWN"
CAPTURE = "CAPTURE"
SELECT = "SELECT"
COMPOSE = "COMPOSE"
DONE = "DONE"
ERROR = "ERROR"

# Number of raised fingers -> frame color (docs/MQTT_PROTOCOL.md 3.9)
FRAME_COLORS = {1: "white", 2: "pink", 3: "blue", 4: "black"}
DEFAULT_COLOR = "white"


@dataclass
class Config:
    shots: int = 2                       # capture rounds; each = front + side photo
    countdown_from: int = 3              # 3, 2, 1
    countdown_step_s: float = 1.0
    gaze_wait_s: float = 5.0             # max wait for eyes on the lens, then shoot anyway
    capture_delay_s: float = 0.5         # capture_at = now + this (both cameras fire together)
    capture_timeout_s: float = 10.0      # wait for capture/done after capture_at
    expected_cameras: tuple = ("node2", "node3")
    between_shots_s: float = 1.5
    select_timeout_s: float = 20.0       # no thumbs-up -> confirm current choice
    compose_timeout_s: float = 30.0
    done_hold_s: float = 20.0            # QR stays on screen
    error_hold_s: float = 5.0
    idle_timeout_s: float = 60.0         # nobody started a session -> back to SLEEP


def make_session_id(now: float) -> str:
    return "S" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


class SessionMachine:
    def __init__(self, config: Config = None, clock=time.time):
        self.cfg = config or Config()
        self.clock = clock
        self._out = []
        self.state = None
        self.last_screen_cmd = None      # re-sent to the Arduino after it reboots
        self.face_count = None
        self._reset_session()
        self._go_sleep()

    # ------------------------------------------------------------------ outputs
    def drain(self):
        out, self._out = self._out, []
        return out

    def _arduino(self, cmd: str, screen: bool = True) -> None:
        self._out.append(("arduino", cmd))
        if screen:
            self.last_screen_cmd = cmd

    def _emit_state(self) -> None:
        payload = {"session_id": self.session_id, "state": self.state, "shot": self.shot}
        if self.layout:
            payload["layout"] = self.layout
        if self.frame_color:
            payload["frame_color"] = self.frame_color
        if self.state == DONE and self.url:
            payload["url"] = self.url
        if self.state == ERROR and self.error:
            payload["error"] = self.error
        self._out.append(("state", payload))

    # ------------------------------------------------------------- transitions
    def _reset_session(self) -> None:
        self.session_id = None
        self.shot = 0
        self.layout = None
        self.frame_color = None
        self.url = None
        self.error = None
        self.deadline = None
        self.step = 0
        self.gaze_ok = None
        self.gaze_warned = False
        self.capture_at = None
        self.shutter_sent = False
        self.received = set()
        self.next_shot_at = None

    def _set(self, state: str) -> None:
        self.state = state
        self._emit_state()

    def _go_sleep(self) -> None:
        self._reset_session()
        self._set(SLEEP)
        self._arduino("STATE:SLEEP")

    def _go_idle(self) -> None:
        self._reset_session()
        self.last_activity = self.clock()
        self._set(IDLE)
        self._arduino("STATE:IDLE")

    def _start_session(self) -> None:
        now = self.clock()
        self._reset_session()
        self.session_id = make_session_id(now)
        self.last_activity = now
        self._update_layout()
        self._set(IDLE)
        self._arduino("LCD:Get ready!|Show a V sign")

    def _start_countdown(self) -> None:
        now = self.clock()
        self.shot += 1
        self.step = self.cfg.countdown_from
        self.deadline = now + self.cfg.countdown_step_s
        self.gaze_ok = None              # no data yet = Node 2 not running = treat as OK
        self.gaze_warned = False
        self.next_shot_at = None
        self._set(COUNTDOWN)
        self._arduino(f"COUNT:{self.step}")

    def _start_capture(self) -> None:
        now = self.clock()
        self.capture_at = now + self.cfg.capture_delay_s
        self.deadline = self.capture_at + self.cfg.capture_timeout_s
        self.shutter_sent = False
        self.received = set()
        self._set(CAPTURE)
        self._out.append(("capture", {"session_id": self.session_id, "shot": self.shot,
                                      "capture_at": int(self.capture_at * 1000)}))

    def _start_select(self) -> None:
        self.deadline = self.clock() + self.cfg.select_timeout_s
        self.frame_color = None
        self._set(SELECT)
        self._arduino("LCD:Pick a frame|Show 1-4 fingers")

    def _confirm_frame(self) -> None:
        self.frame_color = self.frame_color or DEFAULT_COLOR
        self.deadline = self.clock() + self.cfg.compose_timeout_s
        self._set(COMPOSE)
        self._out.append(("compose", {"session_id": self.session_id,
                                      "frame_color": self.frame_color}))
        self._arduino("PROCESSING")

    def _done(self, url: str) -> None:
        self.url = url
        self.deadline = self.clock() + self.cfg.done_hold_s
        self._set(DONE)
        self._arduino("DONE")

    def _fail(self, reason: str) -> None:
        self.error = reason
        self.deadline = self.clock() + self.cfg.error_hold_s
        self._set(ERROR)
        self._arduino("ERROR:" + reason[:16])

    def _update_layout(self) -> None:
        if self.face_count:              # 0 or None: keep the previous layout
            self.layout = "wide" if self.face_count >= 3 else "vertical"

    # ------------------------------------------------------------------ inputs
    def handle(self, event: dict) -> None:
        kind = event.get("type")
        now = self.clock()
        if self.state == IDLE:
            self.last_activity = now

        if kind == "proximity":
            if event.get("event") == "WAKE_UP" and self.state == SLEEP:
                self._go_idle()
            elif (event.get("event") == "LEAVE" and self.state == IDLE
                  and self.session_id is None):
                self._go_sleep()      # people step back during a session: ignore

        elif kind == "button":
            self._start_or_advance(confirm=True)

        elif kind == "gesture":
            gesture = event.get("gesture")
            if gesture == "PALM" and self.session_id is None and self.state in (SLEEP, IDLE):
                self._start_or_advance(confirm=False)
            elif gesture == "V_SIGN" and self.state == IDLE and self.session_id:
                self._start_countdown()
            elif gesture == "FINGERS" and self.state == SELECT:
                color = FRAME_COLORS.get(event.get("value"))
                if color and color != self.frame_color:
                    self.frame_color = color
                    self._emit_state()
                    self._arduino(f"LCD:Frame: {color}|Thumbs up = OK")
            elif gesture == "THUMBS_UP" and self.state == SELECT:
                self._confirm_frame()

        elif kind == "gaze" and self.state == COUNTDOWN:
            self.gaze_ok = bool(event.get("gaze_ok"))

        elif kind == "faces":
            count = event.get("face_count")
            if isinstance(count, int) and count != self.face_count:
                self.face_count = count
                old = self.layout
                self._update_layout()
                if self.layout != old and self.session_id:
                    self._emit_state()

        elif kind == "capture_done":
            if (self.state == CAPTURE and event.get("session_id") == self.session_id
                    and event.get("shot") == self.shot):
                self.received.add(event.get("node"))
                if event.get("node") == "node2" and isinstance(event.get("face_count"), int):
                    self.handle({"type": "faces", "face_count": event["face_count"]})

        elif kind == "compose_result":
            if self.state == COMPOSE and event.get("session_id") == self.session_id:
                if event.get("status") == "OK":
                    self._done(event.get("url"))
                else:
                    self._fail(event.get("error") or "compose failed")

    def _start_or_advance(self, confirm: bool) -> None:
        """Start button / palm: wake up, start a session or move to the next step."""
        if self.state == SLEEP:
            self._go_idle()
            self._start_session()
        elif self.state == IDLE and self.session_id is None:
            self._start_session()
        elif confirm and self.state == IDLE:
            self._start_countdown()          # button replaces the V sign
        elif confirm and self.state == SELECT:
            self._confirm_frame()            # button replaces the thumbs-up

    # ------------------------------------------------------------------ timers
    def tick(self) -> None:
        now = self.clock()
        if self.state == IDLE:
            if now - self.last_activity >= self.cfg.idle_timeout_s:
                self._go_sleep()

        elif self.state == COUNTDOWN:
            if self.step > 0:
                if now >= self.deadline:
                    self.step -= 1
                    if self.step > 0:
                        self._arduino(f"COUNT:{self.step}")
                        self.deadline += self.cfg.countdown_step_s
                    elif self.gaze_ok is False:
                        self.gaze_warned = True
                        self.deadline = now + self.cfg.gaze_wait_s
                        self._arduino("GAZE:WARN")
                    else:
                        self._start_capture()
            else:                                   # waiting for eyes on the lens
                if self.gaze_ok is not False:
                    self._arduino("GAZE:OK")
                    self._start_capture()
                elif now >= self.deadline:
                    self._start_capture()           # shoot anyway, never hang

        elif self.state == CAPTURE:
            if not self.shutter_sent and now >= self.capture_at:
                self.shutter_sent = True
                self._arduino("SHUTTER", screen=False)
            if self.next_shot_at is not None:
                if now >= self.next_shot_at:
                    self._start_countdown()
            elif set(self.cfg.expected_cameras) <= self.received:
                if self.shot < self.cfg.shots:
                    self.next_shot_at = now + self.cfg.between_shots_s
                    self._arduino(f"LCD:Nice!|Next shot {self.shot + 1}/{self.cfg.shots}")
                else:
                    self._start_select()
            elif now >= self.deadline:
                missing = ",".join(sorted(set(self.cfg.expected_cameras) - self.received))
                self._fail(f"no photo {missing}")

        elif self.state == SELECT:
            if now >= self.deadline:
                self._confirm_frame()

        elif self.state == COMPOSE:
            if now >= self.deadline:
                self._fail("compose timeout")

        elif self.state in (DONE, ERROR):
            if now >= self.deadline:
                self._go_idle()
