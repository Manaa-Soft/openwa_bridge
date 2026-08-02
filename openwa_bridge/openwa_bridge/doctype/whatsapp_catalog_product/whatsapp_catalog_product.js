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
                        if (!frm.doc.uom)
                            frm.set_value("uom", item.stock_uom || "");
                        if (!frm.doc.price_list) {
                            frappe.call({
                                method: "frappe.client.get_list",
                                args: {
                                    doctype: "Item Price",
                                    filters: {
                                        item_code: frm.doc.item_code,
                                        selling: 1,
                                    },
                                    fields: ["price_list"],
                                    limit: 1,
                                    order_by: "creation desc",
                                },
                                callback: function (r2) {
                                    if (r2.message && r2.message.length) {
                                        frm.set_value("price_list", r2.message[0].price_list);
                                    }
                                },
                            });
                        }
                    }
                },
            });
        }
    },

    refresh: function (frm) {
        if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
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
                            method: "openwa_bridge.catalog.send_product_to_chat",
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

            frm.add_custom_button(__("Send to Customer"), function () {
                frappe.prompt(
                    [
                        {
                            fieldname: "customer",
                            label: __("Customer"),
                            fieldtype: "Link",
                            options: "Customer",
                            reqd: 1,
                        },
                    ],
                    function (values) {
                        frappe.call({
                            method: "openwa_bridge.catalog.send_product_to_customer",
                            args: {
                                product_name: frm.doc.name,
                                customer: values.customer,
                            },
                            callback: function (r) {
                                if (r.message && r.message.status === "ok") {
                                    frappe.msgprint(__("Product sent to customer."));
                                } else {
                                    frappe.msgprint(__("Send failed: ") + (r.message ? r.message.error : "Unknown"));
                                }
                            },
                        });
                    },
                    __("Send Product to Customer"),
                    __("Send")
                );
            });
        }
    },
});
