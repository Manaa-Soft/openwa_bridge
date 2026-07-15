// WhatsApp Account – OpenWA QR Code and Session Management
// Loaded via doctype_js hook for the WhatsApp Account doctype.

frappe.ui.form.on("WhatsApp Account", {
    refresh(frm) {
        // Clear any lingering QR refresh timer when the form reloads.
        _clear_qr_timer(frm);

        if (frm.is_new() || !frm.doc.openwa_enabled) return;

        if (!frm.doc.openwa_session_id) {
            // No session yet — show the one-click setup button.
            frm.page.set_indicator(__("Not Setup"), "red");
            _add_setup_button(frm);
            return;
        }

        // Session exists — check its live status and show appropriate UI.
        frappe.call({
            method: "openwa_bridge.whatsapp_account.get_openwa_session_status",
            args: { account_name: frm.doc.name },
            callback(r) {
                if (!r || !r.message) return;
                const s = r.message.status;

                if (s === "ready") {
                    frm.page.set_indicator(__("Connected"), "green");
                    _add_disconnect_button(frm);
                } else if (s === "qr_ready" || s === "initializing") {
                    frm.page.set_indicator(__("Scan QR Code"), "orange");
                    _show_qr_code(frm);
                } else if (s === "not_found") {
                    // Session was deleted from OpenWA — stale ID in Frappe
                    frm.page.set_indicator(__("Session Deleted — Re-setup Required"), "red");
                    _add_reset_and_setup_button(frm);
                } else if (s === "error") {
                    // OpenWA server unreachable
                    frm.page.set_indicator(__("OpenWA Unreachable"), "red");
                    _add_reconnect_button(frm);
                } else if (s === "failed") {
                    // Session is corrupted/failed — reconnect will delete + recreate
                    frm.page.set_indicator(__("Session Failed — Reconnect to Fix"), "red");
                    _add_reconnect_button(frm);
                } else {
                    // disconnected / created / unknown
                    frm.page.set_indicator(__("Disconnected"), "red");
                    _add_reconnect_button(frm);
                }
            },
        });
    },

    openwa_enabled(frm) {
        frm.reload_doc();
    },
});

// ---------------------------------------------------------------------------
// Button helpers
// ---------------------------------------------------------------------------

function _add_setup_button(frm) {
    frm.add_custom_button(
        __("Setup OpenWA"),
        () => {
            _run_setup(frm);
        },
        __("OpenWA")
    );
}

function _add_disconnect_button(frm) {
    frm.add_custom_button(
        __("Disconnect"),
        () => {
            frappe.confirm(
                __("Disconnect the WhatsApp session?"),
                () => {
                    frappe.call({
                        method: "openwa_bridge.whatsapp_account.stop_openwa_session",
                        args: { account_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Disconnecting..."),
                        callback() {
                            frappe.show_alert({
                                message: __("Session disconnected"),
                                indicator: "green",
                            });
                            frm.reload_doc();
                        },
                    });
                }
            );
        },
        __("OpenWA")
    );
}

function _add_reconnect_button(frm) {
    frm.add_custom_button(
        __("Reconnect"),
        () => {
            frappe.confirm(
                __("Reconnect the WhatsApp session? If the session is stuck, the app will force-kill the crashed process and restart it (no QR re-scan needed). If that fails, the session will be recreated."),
                () => { _show_qr_code(frm); }
            );
        },
        __("OpenWA")
    );
}

function _add_reset_and_setup_button(frm) {
    frm.add_custom_button(
        __("Setup OpenWA (New Session)"),
        () => {
            frappe.confirm(
                __("The old session was deleted from OpenWA. Clear the stale session ID and create a new one?"),
                () => {
                    frappe.call({
                        method: "openwa_bridge.whatsapp_account.reset_openwa_session",
                        args: { account_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Resetting session..."),
                        callback() {
                            frappe.show_alert({
                                message: __("Session ID cleared. Starting new setup..."),
                                indicator: "green",
                            });
                            // Reload to clear stale ID, then run setup
                            frm.reload_doc();
                        },
                    });
                }
            );
        },
        __("OpenWA")
    );
}

// ---------------------------------------------------------------------------
// One-click setup
// ---------------------------------------------------------------------------

function _run_setup(frm) {
    frappe.call({
        method: "openwa_bridge.whatsapp_account.setup_openwa_session",
        args: { account_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Creating session and fetching QR code..."),
        callback(r) {
            if (!r || !r.message) return;
            const msg = r.message;

            if (msg.status === "error") {
                frappe.msgprint({
                    title: __("Setup Failed"),
                    indicator: "red",
                    message: msg.error || __("Unknown error"),
                });
                return;
            }

            if (msg.session_id) {
                frappe.show_alert({
                    message: __("Session created: {0}", [msg.session_id.substring(0, 8) + "..."]),
                    indicator: "green",
                });
            }

            if (msg.qr_code) {
                _render_qr(frm, msg.qr_code);
            } else if (msg.status === "ready") {
                frm.reload_doc();
            }
        },
    });
}

// ---------------------------------------------------------------------------
// QR code display
// ---------------------------------------------------------------------------

function _show_qr_code(frm) {
    frappe.call({
        method: "openwa_bridge.whatsapp_account.get_openwa_qr",
        args: { account_name: frm.doc.name },
        freeze: true,
        freeze_message: __("Fetching QR code..."),
        callback(r) {
            if (!r || !r.message) return;
            const msg = r.message;

            if (msg.qr_code) {
                _render_qr(frm, msg.qr_code);
            } else if (msg.status === "ready") {
                _clear_qr_timer(frm);
                frm.reload_doc();
            } else {
                frm.fields_dict.openwa_qr_html.$wrapper.html(
                    '<p style="color:#ef4444; padding:10px;">' +
                        (msg.error || __("Failed to get QR code")) +
                    "</p>"
                );
                _clear_qr_timer(frm);
            }
        },
    });
}

function _render_qr(frm, qr_data_url) {
    frm.fields_dict.openwa_qr_html.$wrapper.html(
        '<div style="text-align:center; padding:20px;">' +
            '<img src="' +
            qr_data_url +
            '" ' +
            'style="max-width:300px; border:2px solid #d1d5db; border-radius:8px;" />' +
            '<p style="margin-top:12px; color:#6b7280; font-size:13px;">' +
            __("Scan with WhatsApp to connect") +
            "</p>" +
            '<p style="color:#9ca3af; font-size:11px;">' +
            __("QR expires in 60 seconds — auto-refreshes every 55 s") +
            "</p>" +
        "</div>"
    );

    _clear_qr_timer(frm);
    frm._qr_refresh_timer = setInterval(() => {
        _show_qr_code(frm);
    }, 55000);
}

function _clear_qr_timer(frm) {
    if (frm._qr_refresh_timer) {
        clearInterval(frm._qr_refresh_timer);
        frm._qr_refresh_timer = null;
    }
}
