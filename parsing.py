"""Parse serial lines from the controller and format telemetry values.

The COM port carries controller status, boat telemetry, and radio frames:

1. Controller status:
       sail=-19  rudder=0  | dropped=0  overruns=0
       x_raw=1800  offset=-248  angle=-5

2. Telemetry echoed back from the boat over XBee:
       XBee RX: {"tb":0,"tlat":0,...,"wa":0"sa":347,}

   The telemetry payload is almost-JSON but malformed (missing/extra commas),
   so we parse it with a tolerant regex instead of json.loads().

3. XBee API frames forwarded as hex, including delimiter and checksum:
       XBee API: 7E00028A0075
"""

import re

# sail=-19  rudder=0  | dropped=0  overruns=0
SAIL_RUDDER_RE = re.compile(
    r"sail\s*=\s*(-?\d+)\s+rudder\s*=\s*(-?\d+)\s*\|\s*"
    r"dropped\s*=\s*(\d+)\s+overruns\s*=\s*(\d+)"
)
JOYSTICK_RE = re.compile(r"x_raw=\d+\s+offset=-?\d+\s+angle=(-?\d+)")

# Tolerant "key":value matcher.  Handles ints, floats and negatives.
# It ignores commas entirely, so the malformed JSON in the screenshot
# (missing comma / trailing comma) parses cleanly.
KV_RE = re.compile(r'"([A-Za-z_]\w*)"\s*:\s*(-?\d+(?:\.\d+)?)')

XBEE_API_PREFIX = "XBee API:"
AT_VALUE_BYTES = {"DH": 4, "DL": 4, "DB": 1, "EA": 2, "ER": 2, "TR": 2, "VR": 4}


def parse_sail_rudder(line: str):
    """Return dict for a controller status line, or None."""
    m = SAIL_RUDDER_RE.search(line)
    if not m:
        joystick = JOYSTICK_RE.fullmatch(line)
        if joystick:
            # The API-mode controller sends sa=0 and its measured rudder command.
            return {"sail": 0, "rudder": int(joystick.group(1))}
        return None
    return {
        "sail": int(m.group(1)),
        "rudder": int(m.group(2)),
        "dropped": int(m.group(3)),
        "overruns": int(m.group(4)),
    }


def parse_xbee(line: str):
    """Return dict of telemetry fields for an 'XBee RX:' line, or None."""
    _, prefix, payload = line.partition("XBee RX:")
    if not prefix:
        return None
    pairs = KV_RE.findall(payload)
    if not pairs:
        return None
    out = {}
    for key, raw in pairs:
        out[key] = float(raw) if "." in raw else int(raw)
    return out


def parse_xbee_api(line: str):
    """Validate the AP=1 hex frame and extract link diagnostics for the GUI."""
    if not line.startswith(XBEE_API_PREFIX):
        return None
    try:
        frame = bytes.fromhex(line[len(XBEE_API_PREFIX):])
    except ValueError:
        return None

    if len(frame) < 5 or frame[0] != 0x7E:
        return None
    if int.from_bytes(frame[1:3], "big") != len(frame) - 4:
        return None
    if (sum(frame[3:]) & 0xFF) != 0xFF:
        return None

    data = frame[3:-1]
    frame_type = data[0]
    if frame_type == 0x88 and len(data) >= 5:  # Local AT response
        command = data[2:4].decode("ascii", errors="replace")
        max_bytes = AT_VALUE_BYTES.get(command)
        if max_bytes is None:
            return None
        value = None
        if data[4] == 0:
            if not 1 <= len(data) - 5 <= max_bytes:
                return None
            value = int.from_bytes(data[5:], "big")
        return {"kind": "at", "command": command, "value": value}

    if frame_type == 0x8B and len(data) == 7:  # Extended transmit status
        return {"kind": "tx", "retries": data[4], "status": data[5],
                "discovery": data[6]}

    if frame_type == 0x90 and len(data) >= 12:  # Receive packet
        return {"kind": "rx", "source": data[1:9].hex().upper(),
                "options": data[11]}

    if frame_type == 0x8A and len(data) == 2:  # Modem status
        return {"kind": "modem", "status": data[1]}

    return None


# Telemetry field metadata.  The human-readable labels are my best guess
# at your protocol (t* = target/setpoint, c* = current boat state); they
# are easy to rename here without touching anything else.
TELEMETRY_FIELDS = [
    # key,    label,                 group
    ("tb",   "Target Bearing",       "Target / Setpoints"),
    ("tlat", "Target Latitude",      "Target / Setpoints"),
    ("tlon", "Target Longitude",     "Target / Setpoints"),
    ("tsa",  "Target Sail Angle",    "Target / Setpoints"),
    ("tfa",  "Target Flap Angle",    "Target / Setpoints"),
    ("tra",  "Target Rudder Angle",  "Target / Setpoints"),
    ("clat", "Current Latitude",     "Boat State"),
    ("clon", "Current Longitude",    "Boat State"),
    ("cb",   "Current Bearing",      "Boat State"),
    ("wa",   "Wind Angle",           "Boat State"),
    ("sa",   "Sail Angle",           "Boat State"),
]

DEGREE_FIELDS = {"tb", "tsa", "tfa", "tra", "cb", "wa", "sa"}
LATLON_FIELDS = {"tlat", "tlon", "clat", "clon"}


def fmt_value(key: str, v) -> str:
    if key in LATLON_FIELDS:
        return f"{float(v):.6f}"
    if isinstance(v, float):
        s = f"{v:.2f}"
    else:
        s = str(v)
    if key in DEGREE_FIELDS:
        s += "\u00b0"
    return s
