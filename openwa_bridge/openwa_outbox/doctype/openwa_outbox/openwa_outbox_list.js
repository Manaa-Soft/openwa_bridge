// Copyright (c) 2026, Manaa Soft and contributors
// For license information, please see license.txt

frappe.listview_settings["OpenWA Outbox"] = {
	get_indicator(doc) {
		const colors = {
			Pending: "orange",
			Sending: "blue",
			Sent: "green",
			Failed: "red",
			Cancelled: "grey",
		};
		return [__(doc.status), colors[doc.status] || "grey", `status,=,${doc.status}`];
	},
};
