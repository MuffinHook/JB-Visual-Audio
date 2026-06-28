"""Directional overlay renderer.

Listens for detection state on a local UDP socket and draws a frameless,
click-through, always-on-top overlay. Enemies are shown as coloured arcs on a
guide ring whose angle is relative to the local player's heading, with brief
pop-up arcs flagging newly appeared enemies.

Windows only (relies on ``pywin32`` for click-through). Run with
``python overlay.py``.
"""

import json
import logging
import math
import socket
import threading
import time
from collections import namedtuple

from PyQt5 import QtCore, QtGui, QtWidgets
import win32con
import win32gui

import config

log = logging.getLogger("overlay")

# --- Guide ring / enemy indicators ------------------------------------------
ringRadius = 140
ringSpan = 18
ringAlpha = 64  # 25% opaque guide ring.

# Indicators fade and thin with distance, interpolated between these bounds.
minAlpha = 90
maxAlpha = 255
minPen = 4
maxPen = 10
maxDist = 120.0

# --- New-enemy pop-up arcs --------------------------------------------------
popupLife = 0.55
popupRadius = 155
popupSpan = 22
popupPen = 9

# How long stale detection state is honoured before the overlay blanks out.
staleAfter = 1.0
# Default player position, used until detection reports one.
home = {"x": 96, "y": 93}

teamColors = config.teamColors

# One enemy reduced to what we actually draw: a ring arc.
Arc = namedtuple("Arc", "angle alpha width")

# --- Shared state -----------------------------------------------------------
state = {
    "team": None,
    "heading": None,
    "player": dict(home),
    "enemies": [],
    "timestamp": 0.0,
}
state_lock = threading.Lock()

popups = []
popup_lock = threading.Lock()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def signed_diff(a, b):
    """Signed smallest difference between two angles (degrees)."""
    return (a - b + 180) % 360 - 180


def enemy_arc(ex, ey, px, py, heading):
    """Reduce an enemy position to a heading-relative ring arc."""
    dx = ex - px
    dy = ey - py

    dist = math.hypot(dx, dy)
    world = (math.degrees(math.atan2(dy, dx)) + 90) % 360
    angle = signed_diff(world, heading)

    t = 1.0 - clamp(dist / maxDist, 0.0, 1.0)
    alpha = int(minAlpha + (maxAlpha - minAlpha) * t)
    width = int(minPen + (maxPen - minPen) * t)

    return Arc(angle, alpha, width)


def enemy_team(team):
    """Map the local player's team to the team they see as enemies."""
    if team == "police":
        return "criminal"
    if team in ("criminal", "prisoner"):
        return "police"
    return "criminal"


def add_popup(angle, team):
    with popup_lock:
        popups.append({"angle": angle, "team": team, "born": time.time()})


def cleanup_popups():
    now = time.time()
    with popup_lock:
        popups[:] = [p for p in popups if now - p["born"] <= popupLife]


def udp_listener():
    """Receive detection packets, update shared state, and queue pop-ups."""
    global state

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.udpHost, config.udpPort))
    log.info("listening on %s:%s", config.udpHost, config.udpPort)

    last_seen = set()

    while True:
        try:
            data, _ = sock.recvfrom(65535)
            payload = json.loads(data.decode("utf-8"))

            log.debug(
                "received | team=%s heading=%s enemies=%d",
                payload.get("team"),
                payload.get("heading"),
                len(payload.get("enemies", [])),
            )

            heading = payload.get("heading")
            player = payload.get("player", {})
            enemies = payload.get("enemies", [])
            team = payload.get("team")

            px = player.get("x", home["x"])
            py = player.get("y", home["y"])

            seen = set()
            if heading is not None:
                foe = enemy_team(team)

                for e in enemies:
                    key = (e["x"], e["y"])
                    seen.add(key)

                    # Flag enemies that were not present in the previous packet.
                    if key not in last_seen:
                        arc = enemy_arc(e["x"], e["y"], px, py, heading)
                        add_popup(arc.angle, foe)

            last_seen = seen

            with state_lock:
                state = payload

        except Exception as exc:
            log.error("listener error: %r", exc)


class Overlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )

        screen = QtWidgets.QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self.cx = self.width() // 2
        self.cy = self.height() // 2

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        self.make_click_through()

    def make_click_through(self):
        """Apply layered + transparent window styles so clicks pass through."""
        hwnd = int(self.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style)

    def tick(self):
        cleanup_popups()
        self.update()

    def draw_arc(self, painter, radius, angle, span, color, width):
        """Draw a single arc on the ring, centred on ``angle``."""
        rect = QtCore.QRectF(
            self.cx - radius,
            self.cy - radius,
            radius * 2,
            radius * 2,
        )

        start = angle - span / 2 - 90

        pen = QtGui.QPen(color, width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int(-start * 16), int(-span * 16))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        with state_lock:
            snap = dict(state)

        heading = snap.get("heading")
        player = snap.get("player", {})
        enemies = snap.get("enemies", [])
        ts = snap.get("timestamp", 0.0)
        team = snap.get("team")

        if heading is None or time.time() - ts > staleAfter:
            painter.end()
            return

        # Guide ring.
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, ringAlpha), 2)
        painter.setPen(pen)
        painter.drawEllipse(QtCore.QPoint(self.cx, self.cy), ringRadius, ringRadius)

        px = player.get("x", home["x"])
        py = player.get("y", home["y"])
        rgb = teamColors.get(enemy_team(team), (255, 255, 255))

        # Persistent enemy indicators.
        for e in enemies:
            arc = enemy_arc(e["x"], e["y"], px, py, heading)
            color = QtGui.QColor(rgb[0], rgb[1], rgb[2], arc.alpha)
            self.draw_arc(painter, ringRadius, arc.angle, ringSpan, color, arc.width)

        # Transient pop-ups for newly spotted enemies.
        now = time.time()
        with popup_lock:
            active = list(popups)

        for p in active:
            t = 1.0 - clamp((now - p["born"]) / popupLife, 0.0, 1.0)

            c = teamColors.get(p["team"], (255, 255, 255))
            alpha = int(255 * t)
            width = max(3, int(popupPen * (0.6 + 0.4 * t)))

            color = QtGui.QColor(c[0], c[1], c[2], alpha)
            self.draw_arc(painter, popupRadius, p["angle"], popupSpan, color, width)

        painter.end()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    threading.Thread(target=udp_listener, daemon=True).start()

    app = QtWidgets.QApplication([])
    overlay = Overlay()
    overlay.show()
    app.exec_()


if __name__ == "__main__":
    main()
