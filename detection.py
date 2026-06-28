import json
import logging
import math
import socket
import time

import cv2
import numpy as np
import pyautogui

import config

log = logging.getLogger("detection")

markerImg = "Images/playerMarker.png"

p1 = (12, 925)
p2 = (204, 1111)

left = min(p1[0], p2[0])
top = min(p1[1], p2[1])
width = abs(p2[0] - p1[0])
height = abs(p2[1] - p1[1])

centerX = width // 2
centerY = height // 2

markerConf = 0.2
teamRefresh = 2.5
arrowTol = 0.10
markerTol = 0.05

# ignore heading jumps near 90/180/270, they're usually arrow flips
flipMin = 110
flipMax = 130
minBlobArea = 20

policeMarker = config.teamColors["police"]
crimMarker = config.teamColors["criminal"]

arrowColors = {
    "police": [
        (42, 204, 255),
        (32, 156, 197),
    ],
    "prisoner": [
        (253, 123, 49),
        (192, 93, 37),
    ],
    "criminal": [
        (252, 40, 47),
        (194, 31, 36),
    ],
}


def send_state(sock, team, heading, boxes):
    enemies = [
        {"x": int(x + w // 2), "y": int(y + h // 2)}
        for x, y, w, h, _ in boxes
    ]

    payload = {
        "team": str(team) if team is not None else None,
        "heading": float(heading) if heading is not None else None,
        "player": {"x": int(centerX), "y": int(centerY)},
        "enemies": enemies,
        "timestamp": float(time.time()),
    }

    try:
        sock.sendto(
            json.dumps(payload).encode("utf-8"),
            (config.udpHost, config.udpPort),
        )
        log.debug("sent packet | enemies=%d", len(enemies))
    except OSError as exc:
        log.error("send failed: %r", exc)


def channel_tol(value, tol):
    return max(2, int(round(value * tol)))


def color_match(pix, target, tol):
    return (
        abs(int(pix[0]) - target[0]) <= channel_tol(target[0], tol)
        and abs(int(pix[1]) - target[1]) <= channel_tol(target[1], tol)
        and abs(int(pix[2]) - target[2]) <= channel_tol(target[2], tol)
    )


def classify_marker(pix):
    if color_match(pix, policeMarker, markerTol):
        return "police"
    if color_match(pix, crimMarker, markerTol):
        return "criminal_or_prisoner"
    return None


def classify_arrow(pix):
    for team, colors in arrowColors.items():
        for c in colors:
            if color_match(pix, c, arrowTol):
                return team
    return None


def center_team(rgb):
    pix = tuple(int(v) for v in rgb[centerY, centerX])
    return classify_arrow(pix)


def color_mask(rgb, colors, tol=0.05):
    mask = np.zeros((rgb.shape[0], rgb.shape[1]), dtype=np.uint8)
    for c in colors:
        lo = np.array(
            [
                max(0, c[0] - channel_tol(c[0], tol)),
                max(0, c[1] - channel_tol(c[1], tol)),
                max(0, c[2] - channel_tol(c[2], tol)),
            ],
            dtype=np.uint8,
        )
        hi = np.array(
            [
                min(255, c[0] + channel_tol(c[0], tol)),
                min(255, c[1] + channel_tol(c[1], tol)),
                min(255, c[2] + channel_tol(c[2], tol)),
            ],
            dtype=np.uint8,
        )
        mask = cv2.bitwise_or(mask, cv2.inRange(rgb, lo, hi))
    return mask


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def is_flip(new, prev, tol=0.10):
    if prev is None:
        return False
    diff = angle_diff(new, prev)
    for base in (90, 180, 270):
        if abs(diff - base) / base <= tol:
            return True
    return False


def corner_angle(a, p, b):
    ax, ay = a[0] - p[0], a[1] - p[1]
    bx, by = b[0] - p[0], b[1] - p[1]

    ma = math.hypot(ax, ay)
    mb = math.hypot(bx, by)
    if ma == 0 or mb == 0:
        return 180.0

    dot = ax * bx + ay * by
    cos = max(-1.0, min(1.0, dot / (ma * mb)))
    return math.degrees(math.acos(cos))


def find_tip(cnt):
    hull = cv2.convexHull(cnt)
    pts = hull.reshape(-1, 2)
    if len(pts) < 3:
        return None, None

    peri = cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, 0.04 * peri, True).reshape(-1, 2)
    if len(approx) < 3:
        approx = pts

    m = cv2.moments(cnt)
    if m["m00"] == 0:
        return None, None

    cx = float(m["m10"] / m["m00"])
    cy = float(m["m01"] / m["m00"])

    tip = None
    tip_i = -1
    best = float("inf")

    n = len(approx)
    for i in range(n):
        p = approx[i]
        ang = corner_angle(approx[(i - 1) % n], p, approx[(i + 1) % n])
        dist = math.hypot(p[0] - cx, p[1] - cy)
        score = ang - dist * 0.35

        if score < best:
            best = score
            tip = p
            tip_i = i

    if tip is None:
        return None, None

    return tuple(tip), (approx, tip_i)


def arrow_angle(cnt):
    m = cv2.moments(cnt)
    if m["m00"] == 0:
        return None, None, None

    cx = float(m["m10"] / m["m00"])
    cy = float(m["m01"] / m["m00"])

    tip, extra = find_tip(cnt)
    if tip is None:
        return None, None, None

    approx, tip_i = extra
    tail = [tuple(p) for i, p in enumerate(approx) if i != tip_i]

    if len(tail) >= 2:
        tx = sum(p[0] for p in tail) / len(tail)
        ty = sum(p[1] for p in tail) / len(tail)
    else:
        tx, ty = cx, cy

    angle = (math.degrees(math.atan2(tip[1] - ty, tip[0] - tx)) + 90) % 360
    return angle, (int(round(cx)), int(round(cy))), (int(tip[0]), int(tip[1]))


def pick_arrow(rgb):
    colors = []
    for vals in arrowColors.values():
        colors.extend(vals)

    mask = color_mask(rgb, colors, arrowTol)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_score = None

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < minBlobArea:
            continue

        m = cv2.moments(cnt)
        if m["m00"] == 0:
            continue

        cx = int(m["m10"] / m["m00"])
        cy = int(m["m01"] / m["m00"])
        dist = math.hypot(cx - centerX, cy - centerY)

        score = dist - area * 0.2
        if best is None or score < best_score:
            best = cnt
            best_score = score

    return best


def arrow_heading(rgb):
    arrow = pick_arrow(rgb)
    if arrow is None:
        return None, None

    # upscale so the tip resolves cleanly
    scale = 4
    big = np.zeros((height * scale, width * scale), dtype=np.uint8)

    scaled = (arrow.reshape(-1, 2) * scale).astype(np.int32).reshape(-1, 1, 2)
    cv2.drawContours(big, [scaled], -1, 255, thickness=cv2.FILLED)

    contours, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None

    cnt = max(contours, key=cv2.contourArea)

    angle, centroid, tip = arrow_angle(cnt)
    if angle is None:
        return None, None

    centroid = (centroid[0] // scale, centroid[1] // scale)
    tip = (tip[0] // scale, tip[1] // scale)

    return angle, (centroid, tip)


def dedupe(boxes, thresh=6):
    out = []
    for x, y, w, h, team in boxes:
        x, y, w, h = int(x), int(y), int(w), int(h)

        if any(
            team == ot and abs(x - ox) <= thresh and abs(y - oy) <= thresh
            for ox, oy, ow, oh, ot in out
        ):
            continue
        out.append((x, y, w, h, team))
    return out


def find_enemies(rgb, gray, template, team):
    try:
        matches = list(
            pyautogui.locateAll(template, gray, confidence=markerConf, grayscale=True)
        )
    except pyautogui.ImageNotFoundException:
        matches = []
    except Exception:
        matches = []

    boxes = []
    for m in matches:
        cx = m.left + m.width // 2
        cy = m.top + m.height // 2

        if not (0 <= cx < width and 0 <= cy < height):
            continue

        pix = tuple(int(v) for v in rgb[cy, cx])
        kind = classify_marker(pix)
        if kind is None:
            continue

        if team == "police":
            if kind != "criminal_or_prisoner":
                continue
        elif team in ("criminal", "prisoner"):
            if kind != "police":
                continue
        else:
            continue

        boxes.append((int(m.left), int(m.top), int(m.width), int(m.height), "enemy"))

    return dedupe(boxes)


def draw_debug(bgr, team, heading, boxes):
    cv2.rectangle(bgr, (0, 0), (width - 1, height - 1), (255, 0, 0), 2)

    if team:
        cv2.putText(
            bgr, f"Team: {team}", (6, 18),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2,
        )

    if heading is not None:
        cv2.putText(
            bgr, f"Heading: {heading:.1f}", (6, 38),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2,
        )

        rad = math.radians(heading - 90)
        tip = 26
        tx = int(centerX + math.cos(rad) * tip)
        ty = int(centerY + math.sin(rad) * tip)

        cv2.circle(bgr, (centerX, centerY), 3, (0, 255, 255), -1)
        cv2.circle(bgr, (tx, ty), 4, (255, 255, 0), -1)
        cv2.line(bgr, (centerX, centerY), (tx, ty), (255, 255, 0), 2)

    for x, y, w, h, team in boxes:
        cv2.rectangle(bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            bgr, team, (x, max(10, y - 3)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1,
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    template = cv2.imread(markerImg, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"Could not load template: {markerImg}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    cv2.namedWindow("Detection", cv2.WINDOW_NORMAL)
    cv2.moveWindow("Detection", 1920, 0)

    team = None
    heading = None
    last_team = 0.0

    log.info("Detection started; press 'q' in the window to quit.")

    try:
        while True:
            shot = pyautogui.screenshot(region=(left, top, width, height))
            rgb = np.array(shot)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

            now = time.time()
            if now - last_team >= teamRefresh or team is None:
                found = center_team(rgb)
                if found is not None:
                    team = found
                last_team = now

            new_heading, _ = arrow_heading(rgb)
            if new_heading is not None:
                if heading is None:
                    heading = new_heading
                else:
                    diff = angle_diff(new_heading, heading)
                    if not (flipMin <= diff <= flipMax) and not is_flip(
                        new_heading, heading
                    ):
                        heading = new_heading

            enemies = find_enemies(rgb, gray, template, team)

            send_state(sock, team, heading, enemies)

            draw_debug(bgr, team, heading, enemies)
            cv2.imshow("Detection", bgr)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

            time.sleep(0.02)
    finally:
        cv2.destroyAllWindows()
        sock.close()
        log.info("Detection stopped.")


if __name__ == "__main__":
    main()
