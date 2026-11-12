"""Optional Linux uinput key output and duplicate gesture suppression."""
import time

DEFAULT = {
    "dial_up": ["KEY_VOLUMEUP"],
    "dial_down": ["KEY_VOLUMEDOWN"],
    "swipe_left": ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_LEFT"],
    "swipe_right": ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_RIGHT"],
    "swipe_up": ["KEY_LEFTMETA"],
    "swipe_down": ["KEY_ESC"],
    "index_tap": ["KEY_PLAYPAUSE"],
    "index_doubletap": ["KEY_MUTE"],
    "middle_tap": ["KEY_NEXTSONG"],
    "middle_doubletap": ["KEY_PREVIOUSSONG"],
}


class Router:
    def __init__(self, mapping=None, clock=time.monotonic):
        self.mapping, self.clock = DEFAULT if mapping is None else mapping, clock
        self.seen, self.last = [], {}

    def gesture(self, data):
        if data.get("synthetic"):
            return None
        action, derived, finger = data["action"], data["derived_action"], data["finger"]
        directions = {"buttonLeft": "left", "buttonRight": "right", "buttonUp": "up", "buttonDown": "down"}
        taps = {"singleTap": "tap", "doubleTap": "doubletap", "buttonHold": "hold"}
        source = "raw"
        if finger == "thumb" and (derived in directions or action in directions.values()):
            source = "derived" if derived in directions else "raw"
            name = "swipe_" + directions.get(derived, action)
        else:
            source = "derived" if derived in taps else "raw"
            name = finger + "_" + taps.get(derived, action)
        identity = tuple(data.get(k) for k in ("sequence", "timestamp_us", "finger", "action", "derived_action"))
        if identity in self.seen:
            return None
        self.seen = (self.seen + [identity])[-128:]
        now = self.clock()
        last = self.last.get(name)
        if last and last[0] != source and now - last[1] < .18:
            return None
        self.last[name] = source, now
        return name if name in self.mapping else None


class Keyboard:
    def __init__(self, mapping):
        try:
            from evdev import UInput, ecodes
        except ImportError:
            raise ValueError("desktop controls need: pip install evdev") from None
        self.ecodes = ecodes
        self.mapping = {name: [ecodes.ecodes[key] for key in keys] for name, keys in mapping.items()}
        allowed = sorted({code for codes in self.mapping.values() for code in codes})
        self.device = UInput({ecodes.EV_KEY: allowed}, name="Kinesis Neural Band")

    def press(self, name):
        codes = self.mapping[name]
        try:
            for code in codes:
                self.device.write(self.ecodes.EV_KEY, code, 1)
            self.device.syn()
        finally:
            for code in reversed(codes):
                self.device.write(self.ecodes.EV_KEY, code, 0)
            self.device.syn()

    def close(self):
        self.device.close()


class Dial:
    """Relative pinch/gyro dial, translated from PinchDial and DialRouter."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hand = None
        self.last_gyro = self.last_stamp = self.pressed = None
        self.can_arm = self.engaged = False
        self.bias, self.integral = [0.] * 3, [0.] * 3
        self.axis = None
        self.remainder = 0.
        self.last_step = float("-inf")

    def reset(self):
        self.engaged = False
        self.axis = None
        self.integral = [0.] * 3
        self.remainder = 0.

    def tick(self):
        now = self.clock()
        if ((self.last_gyro is not None and now - self.last_gyro >= .35)
                or (self.pressed is not None and now - self.pressed >= 10)):
            self.pressed = None
            self.can_arm = False
            self.reset()
        if not self.engaged and self.can_arm and self.pressed is not None and now - self.pressed >= .18 and self.last_gyro is not None and now - self.last_gyro < .35:
            self.reset()
            self.engaged = True

    def feed(self, event, data):
        import math
        self.tick()
        now = self.clock()
        if event == "handedness":
            self.hand = data["hand"]
            self.pressed = None
            self.can_arm = False
            self.reset()
        elif event == "gesture" and not data.get("synthetic") and data["finger"] == "index":
            if data["action"] == "press" or data["derived_action"] == "buttonPress":
                if self.pressed is None:
                    self.pressed = now
                    self.can_arm = self.last_gyro is not None and now - self.last_gyro < .35
            if data["action"] == "release" or data["derived_action"] in ("buttonRelease", "buttonHoldRelease"):
                self.pressed = None
                self.can_arm = False
                self.reset()
        elif event == "gyro":
            previous = self.last_stamp
            self.last_stamp, self.last_gyro = data["timestamp_us"], now
            dt = (self.last_stamp - previous) / 1e6 if previous is not None else 0
            if not 0 < dt <= .05:
                if self.engaged:
                    self.pressed = None
                    self.can_arm = False
                    self.reset()
                return None
            values = data["values"]
            if self.pressed is None and all(abs(v-b) < 45 for v,b in zip(values, self.bias)):
                alpha = 1 - math.exp(-dt/2)
                self.bias = [b + (v-b)*alpha for v,b in zip(values,self.bias)]
            if not self.engaged or self.hand is None:
                return None
            delta = [(v-b)*.07*dt for v,b in zip(values,self.bias)]
            self.integral = [a+b for a,b in zip(self.integral,delta)]
            if self.axis is None:
                candidate = max(range(3), key=lambda i: abs(self.integral[i]))
                if abs(self.integral[candidate]) < .25:
                    return None
                self.axis = candidate
                movement = self.integral[candidate]
            else:
                movement = delta[self.axis]
            movement *= -1 if self.hand == "left" else 1
            if self.remainder * movement < 0:
                self.remainder = 0.
            self.remainder = max(-2., min(2., self.remainder + movement*.5))
            if now - self.last_step >= .08 and abs(self.remainder) >= 1:
                direction = 1 if self.remainder > 0 else -1
                self.remainder = math.fmod(self.remainder, 1)
                self.last_step = now
                return "dial_up" if direction > 0 else "dial_down"
        return None
