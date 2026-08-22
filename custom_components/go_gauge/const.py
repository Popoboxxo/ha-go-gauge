"""Constants for Go Gauge HA."""
DOMAIN = "go_gauge"

MANUFACTURER = "Popoboxxo"
MODEL = "OpenCode Go Monitor"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_WARN_PERCENT = "warn_percent"

DEFAULT_HOST = "172.20.5.120"
DEFAULT_PORT = 9364
DEFAULT_WARN_PERCENT = 80
DEFAULT_SCAN_INTERVAL = 600  # seconds

WINDOW_LABELS = {"5h": "5h rolling", "week": "Weekly", "month": "Monthly"}
