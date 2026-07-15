# Copyright (c) 2025, Manaa Soft and contributors
# License: See LICENSE file

import frappe
from frappe.model.document import Document


DEFAULTS = {
    "openwa_rate_limit": 60,
    "openwa_api_timeout": 30,
    "openwa_session_start_timeout": 60,
    "openwa_cb_threshold": 5,
    "openwa_cb_cooldown": 300,
    "openwa_max_outbox_attempts": 5,
}


class OpenWABridgeSettings(Document):
    def validate(self):
        for field, default in DEFAULTS.items():
            if not getattr(self, field, None):
                setattr(self, field, default)
