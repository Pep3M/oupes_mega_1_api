"""Constants for the Oupes local backend-bridge integration."""

DOMAIN = "oupes_local"

CONF_BASE_URL = "base_url"
DEFAULT_BASE_URL = "http://localhost:8765"

# Mirror of the backend's ATTR_MAPPING; we read these from /status.
SENSORS: dict[str, dict] = {
    "battery_level":      {"name": "Battery Level",     "unit": "%",   "device_class": "battery",     "state_class": "measurement"},
    "total_output_power": {"name": "Total Output",      "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "ac_output_power":    {"name": "AC Output",         "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "dc12_output_power":  {"name": "DC 12V Output",     "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "pd_output_power":    {"name": "PD Output",         "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "usb_output_power":   {"name": "USB Output",        "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "total_input_power":  {"name": "Total Input",       "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "ac_input_power":     {"name": "AC Input",          "unit": "W",   "device_class": "power",       "state_class": "measurement"},
    "pv_input_power":     {"name": "PV Input",          "unit": "W",   "device_class": "power",       "state_class": "measurement"},
}

BINARY_SENSORS: dict[str, dict] = {
    "fast_charging": {"name": "Fast Charging", "device_class": "battery_charging"},
}
