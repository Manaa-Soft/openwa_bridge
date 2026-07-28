frappe.ui.form.on("WhatsApp Catalog Product", {
    item_code: function (frm) {
        if (frm.doc.item_code) {
            frappe.call({
                method: "frappe.client.get",
                args: {
                    doctype: "Item",
                    name: frm.doc.item_code,
                },
                callback: function (r) {
                    if (r.message) {
                        let item = r.message;
                        if (!frm.doc.product_name)
                            frm.set_value("product_name", item.item_name);
                        if (!frm.doc.description)
                            frm.set_value("description", item.description);
                        if (!frm.doc.image)
                            frm.set_value("image", item.image);
                        if (!frm.doc.price)
                            frm.set_value("price", item.valuation_rate || 0);
                        if (!frm.doc.currency) {
                            frappe.db.get_single_value("Currency", "default_currency").then((c) => {
                                frm.set_value("currency", c || "USD");
                            });
                        }
                    }
                },
            });
        }
    },

    refresh: function (frm) {
        if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Sync to WhatsApp"), function () {
                frappe.call({
                    method: "openwa_bridge.catalog.sync_single_product",
                    args: { product_name: frm.doc.name },
                    callback: function (r) {
                        if (r.message && r.message.status === "ok") {
                            frappe.msgprint(__("Product synced to WhatsApp catalog."));
                            frm.reload_doc();
                        } else {
                            frappe.msgprint(__("Sync failed: ") + (r.message ? r.message.error : "Unknown"));
                        }
                    },
                });
            });

            frm.add_custom_button(__("Send to Chat"), function () {
                frappe.prompt(
                    [
                        {
                            fieldname: "chat_id",
                            label: __("Chat ID"),
                            fieldtype: "Data",
                            reqd: 1,
                            description: "e.g. 123456789@c.us or 987654321@g.us",
                        },
                    ],
                    function (values) {
                        frappe.call({
                            method: "openwa_bridge.catalog.send_single_product_to_chat",
                            args: {
                                product_name: frm.doc.name,
                                chat_id: values.chat_id,
                            },
                            callback: function (r) {
                                if (r.message && r.message.status === "ok") {
                                    frappe.msgprint(__("Product sent to chat."));
                                } else {
                                    frappe.msgprint(__("Send failed: ") + (r.message ? r.message.error : "Unknown"));
                                }
                            },
                        });
                    },
                    __("Send Product to Chat"),
                    __("Send")
                );
            });
        }
    },
});
