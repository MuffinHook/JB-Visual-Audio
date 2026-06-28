"""Shared configuration for the JB Visual Audio overlay system.

The detection process (``detection.py``) and the rendering process
(``overlay.py``) run independently and communicate over a local UDP socket.
Values that both processes must agree on live here so the two cannot drift
out of sync.
"""

# --- Networking -------------------------------------------------------------
# Local UDP channel used to stream detection state to the overlay.
udpHost = "127.0.0.1"
udpPort = 1780

# --- Team colours (RGB) -----------------------------------------------------
# Canonical minimap colour for each team.
teamColors = {
    "police": (42, 204, 255),
    "criminal": (252, 40, 47),
    "prisoner": (253, 123, 49),
}
