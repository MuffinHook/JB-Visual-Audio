import math
import time
import json
import socket
import cv2
import numpy as np
import pyautogui

MARKER_TEMPLATE_PATH = "Images/playerMarker.png"

P1 = (12, 925)
P2 = (204, 1111)

LEFT = min(P1[0], P2[0])
TOP = min(P1[1], P2[1])
WIDTH = abs(P2[0] - P1[0])
HEIGHT = abs(P2[1] - P1[1])

REGION_CENTER_X = WIDTH // 2
REGION_CENTER_Y = HEIGHT // 2

MARKER_SCAN_CONFIDENCE = 0.2
TEAM_REFRESH_INTERVAL = 2.5
TEAM_CENTER_TOLERANCE = 0.10
MARKER_COLOR_TOLERANCE = 0.05

HEADING_BLOCK_MIN = 110
HEADING_BLOCK_MAX = 130
MIN_ARROW_BLOB_AREA = 20

MARKER_POLICE = (42, 204, 255)
MARKER_CRIM_PRISONER = (252, 40, 47)

ARROW_TEAM_COLORS = {
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

UDP_HOST = "127.0.0.1"
UDP_PORT = 50555
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_overlay_state(team, heading, enemy_boxes):
    enemies = []
    for x, y, w, h, _ in enemy_boxes:
        enemies.append({
            "x": int(x + w // 2),
            "y": int(y + h // 2),
        })

    payload = {
        "team": str(team) if team is not None else None,
        "heading": float(heading) if heading is not None else None,
        "player": {
            "x": int(REGION_CENTER_X),
            "y": int(REGION_CENTER_Y),
        },
        "enemies": enemies,
        "timestamp": float(time.time()),
    }

    try:
        sock.sendto(json.dumps(payload).encode("utf-8"), (UDP_HOST, UDP_PORT))
        print(f"[Detection] Sent packet | enemies={len(enemies)}")
    except Exception as e:
        print("[Detection SEND ERROR]", repr(e))

def channel_tol(v, tol):
    return max(2, int(round(v * tol)))

def color_match(pixel_rgb, target_rgb, tol):
    return (
        abs(int(pixel_rgb[0]) - target_rgb[0]) <= channel_tol(target_rgb[0], tol) and
        abs(int(pixel_rgb[1]) - target_rgb[1]) <= channel_tol(target_rgb[1], tol) and
        abs(int(pixel_rgb[2]) - target_rgb[2]) <= channel_tol(target_rgb[2], tol)
    )

def classify_marker_team(pixel_rgb):
    if color_match(pixel_rgb, MARKER_POLICE, MARKER_COLOR_TOLERANCE):
        return "police"
    if color_match(pixel_rgb, MARKER_CRIM_PRISONER, MARKER_COLOR_TOLERANCE):
        return "criminal_or_prisoner"
    return None

def classify_arrow_team(pixel_rgb):
    for team, colors in ARROW_TEAM_COLORS.items():
        for c in colors:
            if color_match(pixel_rgb, c, TEAM_CENTER_TOLERANCE):
                return team
    return None

def get_team_from_center_pixel(region_rgb):
    pixel = tuple(int(v) for v in region_rgb[REGION_CENTER_Y, REGION_CENTER_X])
    return classify_arrow_team(pixel)

def angle_diff(a, b):
    d = (a - b + 180) % 360 - 180
    return abs(d)

def is_bad_rotation_jump(new_angle, cached_angle, tolerance=0.10):
    if cached_angle is None:
        return False
    diff = angle_diff(new_angle, cached_angle)
    for base in (90, 180, 270):
        percent_error = abs(diff - base) / base
        if percent_error <= tolerance:
            return True
    return False

def make_color_mask_rgb(region_rgb, colors, tol=0.05):
    mask = np.zeros((region_rgb.shape[0], region_rgb.shape[1]), dtype=np.uint8)
    for color in colors:
        lower = np.array([
            max(0, color[0] - channel_tol(color[0], tol)),
            max(0, color[1] - channel_tol(color[1], tol)),
            max(0, color[2] - channel_tol(color[2], tol)),
        ], dtype=np.uint8)
        upper = np.array([
            min(255, color[0] + channel_tol(color[0], tol)),
            min(255, color[1] + channel_tol(color[1], tol)),
            min(255, color[2] + channel_tol(color[2], tol)),
        ], dtype=np.uint8)

        this_mask = cv2.inRange(region_rgb, lower, upper)
        mask = cv2.bitwise_or(mask, this_mask)
    return mask

def point_angle(prev_pt, pt, next_pt):
    ax, ay = prev_pt[0] - pt[0], prev_pt[1] - pt[1]
    bx, by = next_pt[0] - pt[0], next_pt[1] - pt[1]

    mag_a = math.hypot(ax, ay)
    mag_b = math.hypot(bx, by)
    if mag_a == 0 or mag_b == 0:
        return 180.0

    dot = ax * bx + ay * by
    value = max(-1.0, min(1.0, dot / (mag_a * mag_b)))
    return math.degrees(math.acos(value))

def get_arrow_tip_from_contour(cnt):
    hull = cv2.convexHull(cnt)
    pts = hull.reshape(-1, 2)
    if len(pts) < 3:
        return None, None

    peri = cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, 0.04 * peri, True).reshape(-1, 2)
    if len(approx) < 3:
        approx = pts

    M = cv2.moments(cnt)
    if M["m00"] == 0:
        return None, None

    cx = float(M["m10"] / M["m00"])
    cy = float(M["m01"] / M["m00"])

    best_pt = None
    best_index = -1
    best_score = float("inf")

    n = len(approx)
    for i in range(n):
        prev_pt = approx[(i - 1) % n]
        pt = approx[i]
        next_pt = approx[(i + 1) % n]

        ang = point_angle(prev_pt, pt, next_pt)
        dist = math.hypot(pt[0] - cx, pt[1] - cy)
        score = ang - (dist * 0.35)

        if score < best_score:
            best_score = score
            best_pt = pt
            best_index = i

    if best_pt is None:
        return None, None

    return tuple(best_pt), (approx, best_index)

def get_arrow_angle_from_contour(cnt):
    M = cv2.moments(cnt)
    if M["m00"] == 0:
        return None, None, None

    cx = float(M["m10"] / M["m00"])
    cy = float(M["m01"] / M["m00"])

    tip, extra = get_arrow_tip_from_contour(cnt)
    if tip is None:
        return None, None, None

    approx, tip_index = extra
    other_pts = [tuple(p) for i, p in enumerate(approx) if i != tip_index]

    if len(other_pts) >= 2:
        bx = sum(p[0] for p in other_pts) / len(other_pts)
        by = sum(p[1] for p in other_pts) / len(other_pts)
    else:
        bx, by = cx, cy

    dx = tip[0] - bx
    dy = tip[1] - by

    angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
    return angle, (int(round(cx)), int(round(cy))), (int(tip[0]), int(tip[1]))

def get_arrow_blob(region_rgb):
    all_arrow_colors = []
    for vals in ARROW_TEAM_COLORS.values():
        all_arrow_colors.extend(vals)

    mask = make_color_mask_rgb(region_rgb, all_arrow_colors, TEAM_CENTER_TOLERANCE)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_score = None

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_ARROW_BLOB_AREA:
            continue

        M = cv2.moments(cnt)
        if M["m00"] == 0:
            continue

        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        dist = math.hypot(cx - REGION_CENTER_X, cy - REGION_CENTER_Y)

        score = dist - area * 0.2
        if best is None or score < best_score:
            best = cnt
            best_score = score

    return best

def get_arrow_heading(region_rgb):
    best = get_arrow_blob(region_rgb)
    if best is None:
        return None, None

    scale = 4
    upscaled_mask = np.zeros((HEIGHT * scale, WIDTH * scale), dtype=np.uint8)

    contour_scaled = (best.reshape(-1, 2) * scale).astype(np.int32).reshape(-1, 1, 2)
    cv2.drawContours(upscaled_mask, [contour_scaled], -1, 255, thickness=cv2.FILLED)

    contours, _ = cv2.findContours(upscaled_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, None

    cnt = max(contours, key=cv2.contourArea)

    angle, centroid_pt, tip_pt = get_arrow_angle_from_contour(cnt)
    if angle is None:
        return None, None

    centroid_pt = (centroid_pt[0] // scale, centroid_pt[1] // scale)
    tip_pt = (tip_pt[0] // scale, tip_pt[1] // scale)

    return angle, (centroid_pt, tip_pt)

def dedupe_boxes(boxes, dist_thresh=6):
    out = []
    for box in boxes:
        x, y, w, h, team = box
        x, y, w, h = int(x), int(y), int(w), int(h)

        keep = True
        for ox, oy, ow, oh, oteam in out:
            if team == oteam and abs(x - ox) <= dist_thresh and abs(y - oy) <= dist_thresh:
                keep = False
                break
        if keep:
            out.append((x, y, w, h, team))
    return out

marker_template = cv2.imread(MARKER_TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)
if marker_template is None:
    raise FileNotFoundError(f"Could not load template: {MARKER_TEMPLATE_PATH}")

cv2.namedWindow("Detection", cv2.WINDOW_NORMAL)
cv2.moveWindow("Detection", 1920, 0)

cached_team = None
cached_heading = None
last_team_check = 0.0

while True:
    screenshot = pyautogui.screenshot(region=(LEFT, TOP, WIDTH, HEIGHT))
    region_rgb = np.array(screenshot)
    region_bgr = cv2.cvtColor(region_rgb, cv2.COLOR_RGB2BGR)
    region_gray = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)

    cv2.rectangle(region_bgr, (0, 0), (WIDTH - 1, HEIGHT - 1), (255, 0, 0), 2)

    now = time.time()

    if now - last_team_check >= TEAM_REFRESH_INTERVAL or cached_team is None:
        new_team = get_team_from_center_pixel(region_rgb)
        if new_team is not None:
            cached_team = new_team
        last_team_check = now

    new_heading, arrow_points = get_arrow_heading(region_rgb)

    if new_heading is not None:
        if cached_heading is None:
            cached_heading = new_heading
        else:
            diff = angle_diff(new_heading, cached_heading)
            if not (HEADING_BLOCK_MIN <= diff <= HEADING_BLOCK_MAX) and not is_bad_rotation_jump(new_heading, cached_heading, tolerance=0.10):
                cached_heading = new_heading

    if cached_team:
        cv2.putText(region_bgr, f"Team: {cached_team}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

    if cached_heading is not None:
        cv2.putText(region_bgr, f"Heading: {cached_heading:.1f}", (6, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        rad = math.radians(cached_heading - 90)
        tip_len = 26
        acx, acy = REGION_CENTER_X, REGION_CENTER_Y
        atx = int(acx + math.cos(rad) * tip_len)
        aty = int(acy + math.sin(rad) * tip_len)

        cv2.circle(region_bgr, (acx, acy), 3, (0, 255, 255), -1)
        cv2.circle(region_bgr, (atx, aty), 4, (255, 255, 0), -1)
        cv2.line(region_bgr, (acx, acy), (atx, aty), (255, 255, 0), 2)

    enemy_boxes = []

    try:
        matches = list(pyautogui.locateAll(
            marker_template,
            region_gray,
            confidence=MARKER_SCAN_CONFIDENCE,
            grayscale=True
        ))
    except pyautogui.ImageNotFoundException:
        matches = []
    except Exception:
        matches = []

    for m in matches:
        cx = m.left + m.width // 2
        cy = m.top + m.height // 2

        if not (0 <= cx < WIDTH and 0 <= cy < HEIGHT):
            continue

        center_pixel = tuple(int(v) for v in region_rgb[cy, cx])
        marker_team = classify_marker_team(center_pixel)
        if marker_team is None:
            continue

        if cached_team == "police":
            if marker_team != "criminal_or_prisoner":
                continue
            display_team = "enemy"
        elif cached_team in ("criminal", "prisoner"):
            if marker_team != "police":
                continue
            display_team = "enemy"
        else:
            continue

        enemy_boxes.append((int(m.left), int(m.top), int(m.width), int(m.height), display_team))

    enemy_boxes = dedupe_boxes(enemy_boxes)

    send_overlay_state(cached_team, cached_heading, enemy_boxes)

    for x, y, w, h, team in enemy_boxes:
        cv2.rectangle(region_bgr, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(region_bgr, team, (x, max(10, y - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

    cv2.imshow("Detection", region_bgr)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

    time.sleep(0.02)

cv2.destroyAllWindows()
sock.close()