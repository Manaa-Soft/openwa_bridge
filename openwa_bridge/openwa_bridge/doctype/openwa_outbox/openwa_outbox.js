// Copyright (c) 2026, Manaa Soft and contributors
// For license information, please see license.txt

frappe.ui.form.on("OpenWA Outbox", {
	refresh(frm) {
		if (frm.doc.status === "Failed" && frappe.user_roles.includes("System Manager")) {
			frm.add_custom_button(__("Retry Now"), () => {
				frappe.call({
					method: "openwa_bridge.tasks.process_outbox_entry",
					args: { outbox_name: frm.doc.name },
					callback() {
						frappe.msgprint(__("Retry queued"));
						frm.reload_doc();
					},
				});
			}, __("Actions"));
		}

		if (frm.doc.status === "Pending" && frappe.user_roles.includes("System Manager")) {
			frm.add_custom_button(__("Cancel"), () => {
				frappe.confirm(
					__("Cancel this queued message?"),
					() => {
						frm.set_value("status", "Cancelled");
						frm.save();
					},
				);
			}, __("Actions"));
		}
	},
});
