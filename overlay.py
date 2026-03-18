import math
import json
import socket
import threading
import time
from PyQt5 import QtCore, QtGui, QtWidgets
import win32con
import win32gui

UDP_HOST = "127.0.0.1"
UDP_PORT = 50555

RING_RADIUS = 140
ARC_SPAN_DEG = 18

# 25% transparent ring
RING_ALPHA = 64

MIN_ALPHA = 90
MAX_ALPHA = 255
MIN_PEN = 4
MAX_PEN = 10
MAX_MAP_DIST = 120.0

POPUP_LIFETIME = 0.55
POPUP_RADIUS = 155
POPUP_SPAN_DEG = 22
POPUP_PEN_WIDTH = 9

TEAM_COLORS = {
    "police": (42, 204, 255),
    "criminal": (252, 40, 47),
    "prisoner": (253, 123, 49),
}

state = {
    "team": None,
    "heading": None,
    "player": {"x": 96, "y": 93},
    "enemies": [],
    "timestamp": 0.0,
}

state_lock = threading.Lock()
popups = []
popup_lock = threading.Lock()


def clamp(v, a, b):
    return max(a, min(b, v))


def angle_diff_signed(a, b):
    return (a - b + 180) % 360 - 180


def enemy_to_indicator(enemy_x, enemy_y, player_x, player_y, heading_deg):
    dx = enemy_x - player_x
    dy = enemy_y - player_y

    dist = math.hypot(dx, dy)
    world_angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
    rel_angle = angle_diff_signed(world_angle, heading_deg)

    t = 1.0 - clamp(dist / MAX_MAP_DIST, 0.0, 1.0)
    alpha = int(MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * t)
    pen_width = int(MIN_PEN + (MAX_PEN - MIN_PEN) * t)

    return {
        "relative_angle": rel_angle,
        "distance": dist,
        "alpha": alpha,
        "pen_width": pen_width,
    }


def guess_enemy_team(local_team):
    if local_team == "police":
        return "criminal"
    if local_team in ("criminal", "prisoner"):
        return "police"
    return "criminal"


def add_popup(relative_angle, team):
    with popup_lock:
        popups.append({
            "relative_angle": relative_angle,
            "team": team,
            "created": time.time(),
        })


def cleanup_popups():
    now = time.time()
    with popup_lock:
        popups[:] = [p for p in popups if now - p["created"] <= POPUP_LIFETIME]


def udp_listener():
    global state
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_HOST, UDP_PORT))

    last_enemy_signature = set()

    print(f"[Overlay] Listening on {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(65535)
            payload = json.loads(data.decode("utf-8"))

            print(
                f"[Overlay] Data received | "
                f"Team: {payload.get('team')} | "
                f"Heading: {payload.get('heading')} | "
                f"Enemies: {len(payload.get('enemies', []))}"
            )

            heading = payload.get("heading")
            player = payload.get("player", {})
            enemies = payload.get("enemies", [])
            local_team = payload.get("team")

            px = player.get("x", 96)
            py = player.get("y", 93)

            new_signature = set()
            if heading is not None:
                enemy_team = guess_enemy_team(local_team)

                for enemy in enemies:
                    ex = enemy["x"]
                    ey = enemy["y"]

                    sig = (ex, ey)
                    new_signature.add(sig)

                    if sig not in last_enemy_signature:
                        ind = enemy_to_indicator(ex, ey, px, py, heading)
                        add_popup(ind["relative_angle"], enemy_team)

            last_enemy_signature = new_signature

            with state_lock:
                state = payload

        except Exception as e:
            print("[Overlay ERROR]", e)


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

        self.center_x = self.width() // 2
        self.center_y = self.height() // 2

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        self.make_click_through()

    def make_click_through(self):
        hwnd = int(self.winId())
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex_style |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)

    def tick(self):
        cleanup_popups()
        self.update()

    def draw_arc(self, painter, radius, rel_angle, span_deg, color, pen_width):
        rect = QtCore.QRectF(
            self.center_x - radius,
            self.center_y - radius,
            radius * 2,
            radius * 2
        )

        start_deg = rel_angle - span_deg / 2 - 90

        pen = QtGui.QPen(color, pen_width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(
            rect,
            int(-start_deg * 16),
            int(-span_deg * 16)
        )

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        with state_lock:
            local_state = dict(state)

        heading = local_state.get("heading")
        player = local_state.get("player", {})
        enemies = local_state.get("enemies", [])
        ts = local_state.get("timestamp", 0.0)
        local_team = local_state.get("team")

        if heading is None or time.time() - ts > 1.0:
            painter.end()
            return

        # Guide ring at 25% transparency
        guide_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, RING_ALPHA), 2)
        painter.setPen(guide_pen)
        painter.drawEllipse(
            QtCore.QPoint(self.center_x, self.center_y),
            RING_RADIUS,
            RING_RADIUS
        )

        px = player.get("x", 96)
        py = player.get("y", 93)
        enemy_team = guess_enemy_team(local_team)
        enemy_rgb = TEAM_COLORS.get(enemy_team, (255, 255, 255))

        # Persistent enemy indicators
        for enemy in enemies:
            ind = enemy_to_indicator(enemy["x"], enemy["y"], px, py, heading)
            color = QtGui.QColor(enemy_rgb[0], enemy_rgb[1], enemy_rgb[2], ind["alpha"])
            self.draw_arc(
                painter,
                RING_RADIUS,
                ind["relative_angle"],
                ARC_SPAN_DEG,
                color,
                ind["pen_width"]
            )

        # Popups
        now = time.time()
        with popup_lock:
            active_popups = list(popups)

        for popup in active_popups:
            age = now - popup["created"]
            t = 1.0 - clamp(age / POPUP_LIFETIME, 0.0, 1.0)

            rgb = TEAM_COLORS.get(popup["team"], (255, 255, 255))
            alpha = int(255 * t)
            width = max(3, int(POPUP_PEN_WIDTH * (0.6 + 0.4 * t)))

            color = QtGui.QColor(rgb[0], rgb[1], rgb[2], alpha)
            self.draw_arc(
                painter,
                POPUP_RADIUS,
                popup["relative_angle"],
                POPUP_SPAN_DEG,
                color,
                width
            )

        painter.end()


if __name__ == "__main__":
    listener = threading.Thread(target=udp_listener, daemon=True)
    listener.start()

    app = QtWidgets.QApplication([])
    overlay = Overlay()
    overlay.show()
    app.exec_()