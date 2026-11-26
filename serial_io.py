"""Serial port detection and the background reader thread."""

import threading

import serial
import serial.tools.list_ports

MAX_SERIAL_LINE_BYTES = 4096


def is_stlink(port) -> bool:
    """True if a pyserial port looks like an ST-Link Virtual COM Port.

    Matches the description text (Windows uses several wordings) and falls
    back to the STMicroelectronics USB vendor ID 0x0483.
    """
    desc = (port.description or "").lower()
    if "stlink" in desc or "st-link" in desc or "st link" in desc:
        return True
    if getattr(port, "vid", None) == 0x0483:
        return True
    return False


class SerialReader(threading.Thread):
    """Read full lines off the serial port and push them to a queue.

    Runs in a background thread so the GUI never blocks. Widget updates
    happen on the main thread via the queue, because tkinter isn't
    thread-safe.
    """

    def __init__(self, ser, out_queue, stop_event):
        super().__init__(daemon=True)
        self.ser = ser
        self.out_queue = out_queue
        self.stop_event = stop_event

    def run(self):
        pending = bytearray()
        discarding = False
        while not self.stop_event.is_set():
            try:
                raw = self.ser.readline(MAX_SERIAL_LINE_BYTES)
            except (serial.SerialException, OSError) as e:
                self.out_queue.put(("error", (self.stop_event, f"Serial error: {e}")))
                return
            if not raw:
                continue

            # readline can return a partial line after a serial timeout.
            if not discarding:
                if len(pending) + len(raw) <= MAX_SERIAL_LINE_BYTES:
                    pending.extend(raw)
                else:
                    pending.clear()
                    discarding = True
            if not raw.endswith(b"\n"):
                continue

            if not discarding:
                line = pending.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    self.out_queue.put(("line", (self.stop_event, line)))
            pending.clear()
            discarding = False
