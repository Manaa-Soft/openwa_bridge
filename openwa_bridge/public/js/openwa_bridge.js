// OpenWA Bridge – "Send To Whatsapp" dialog overlay (Send-as-PDF).
//
// Loaded via app_include_js on every desk page. The upstream frappe_whatsapp
// app injects a "Send To Whatsapp" menu item on every Form using its own
// (unedited) client script. This overlay removes that legacy item and replaces
// it with an enhanced dialog that can send the current document as a PDF via
// OpenWA's send-document API. All code lives in this app — nothing here
// modifies the upstream frappe_whatsapp app.

$(document).on("app_ready", () => {
    frappe.router.on("change", () => {
        const route = frappe.get_route();
        // register the replacement for every doctype form
        if (route && route[0] === "Form") {
            frappe.ui.form.on(route[1], {
                refresh(frm) {
                    _replace_send_menu_item(frm);
                },
            });
        }
    });
});

/**
 * Remove the legacy "Send To Whatsapp" menu item (added by the upstream
 * frappe_whatsapp client script) and add ours in its place.
 *
 * frm.page.add_menu_item() dedups by label (page.js:is_in_group_button_dropdown),
 * so a same-labelled item added second would silently keep the first one's
 * click handler. Removing the existing <li> first guarantees our dialog wins
 * regardless of script/event ordering.
 * @param {object} frm - The current form.
 */
function _replace_send_menu_item(frm) {
    const label = __("Send To Whatsapp");
    frm.page.menu.find("li").each(function () {
        const item_text = $(this).find(".menu-item-label").text().trim();
        if (item_text === label) {
            $(this).remove();
        }
    });
    frm.page.add_menu_item(label, () => _open_send_dialog(frm));
}

/**
 * Show the enhanced send dialog (template message or current doc as PDF).
 * @param {object} frm - The current form.
 */
function _open_send_dialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Send a Telegram Message"),
        no_submit_on_enter: true,
        fields: [
            { fieldname: "ht", fieldtype: "HTML" },
            {
                label: __("Send as PDF"),
                fieldname: "send_pdf",
                fieldtype: "Check",
                change() {
                    const is_pdf = dialog.get_value("send_pdf");
                    // Template is only required for template sends
                    const tpl = dialog.fields_dict.template;
                    tpl.df.reqd = is_pdf ? 0 : 1;
                    tpl.refresh();
                    // Show/hide PDF-only fields
                    dialog.fields_dict.print_format.toggle(is_pdf);
                    dialog.fields_dict.pdf_filename.toggle(is_pdf);
                },
            },
            {
                label: __("Select Template"),
                fieldname: "template",
                reqd: 1,
                fieldtype: "Link",
                options: "WhatsApp Templates",
            },
            {
                label: __("Print Format"),
                fieldname: "print_format",
                fieldtype: "Link",
                options: "Print Format",
                default: "Standard",
            },
            { label: __("PDF Filename"), fieldname: "pdf_filename", fieldtype: "Data" },
            {
                label: __("Send to"),
                fieldname: "contact",
                reqd: 1,
                fieldtype: "Link",
                options: "Contact",
                change() {
                    const contact_name = dialog.get_value("contact");
                    if (contact_name) {
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Contact",
                                filters: { name: contact_name },
                                fieldname: ["mobile_no"],
                            },
                            callback(r) {
                                if (r.message) {
                                    dialog.set_value("mobile_no", r.message.mobile_no);
                                } else {
                                    dialog.set_value("mobile_no", "");
                                    frappe.msgprint(__("Mobile number not found for the selected contact."));
                                }
                            },
                        });
                    } else {
                        dialog.set_value("mobile_no", "");
                    }
                },
            },
            { label: __("Mobile no"), fieldname: "mobile_no", fieldtype: "Data" },
        ],
        primary_action_label: __("Send"),
        primary_action: () => {
            _send_from_dialog(frm, dialog);
        },
    });

    // Only show templates configured for this doctype
    dialog.fields_dict.template.get_query = () => ({
        filters: { for_doctype: frm.doctype },
        doctype: "WhatsApp Templates",
    });
    dialog.fields_dict.template.refresh();

    // Hide PDF-only fields initially
    dialog.fields_dict.print_format.toggle(false);
    dialog.fields_dict.pdf_filename.toggle(false);
    dialog.show();
}

/**
 * Primary action for the send dialog.
 * @param {object} frm - The current form.
 * @param {object} dialog - The open dialog.
 */
function _send_from_dialog(frm, dialog) {
    const values = dialog.get_values();
    if (!values) return;

    if (!values.mobile_no) {
        frappe.msgprint(__("Please select a contact that has a mobile number."));
        return;
    }

    if (values.send_pdf) {
        // Send the current document as a PDF via the OpenWA bridge
        frappe.call({
            method: "openwa_bridge.whatsapp_message.send_document_pdf",
            args: {
                to: values.mobile_no,
                reference_doctype: frm.doctype,
                reference_name: frm.docname,
                print_format: values.print_format,
                filename: values.pdf_filename,
            },
            freeze: true,
            callback(r) {
                if (r.exc) {
                    frappe.msgprint(__("Failed to send PDF. Check the Error Log."));
                    return;
                }
                frappe.msgprint(__("Successfully Sent to: {0}", [values.mobile_no]));
                dialog.hide();
            },
        });
    } else {
        // Send a WhatsApp template message
        frappe.call({
            method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.send_template",
            args: {
                to: values.mobile_no,
                template: values.template,
                reference_doctype: frm.doctype,
                reference_name: frm.docname,
            },
            freeze: true,
            callback(r) {
                if (r.exc) {
                    frappe.msgprint(__("Failed to send. Check the Error Log."));
                    return;
                }
                frappe.msgprint(__("Successfully Sent to: {0}", [values.mobile_no]));
                dialog.hide();
            },
        });
    }

    // Add a timeline comment recording what was sent
    const space = "\n\n";
    const comment_message = values.send_pdf
        ? `To : ${values.mobile_no}${space}PDF:${values.pdf_filename || frm.docname + ".pdf"} | Print Format:${values.print_format}`
        : `To : ${values.mobile_no}${space}Whatsapp Template:${values.template}`;
    frappe.call({
        method: "frappe.desk.form.utils.add_comment",
        args: {
            reference_doctype: frm.doctype,
            reference_name: frm.docname,
            content: comment_message,
            comment_by: frappe.session.user_fullname,
            comment_email: frappe.session.user,
        },
    });
}
