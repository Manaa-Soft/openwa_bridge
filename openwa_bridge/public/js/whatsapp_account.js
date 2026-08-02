// WhatsApp Account – OpenWA QR Code and Session Management
// Loaded via doctype_js hook for the WhatsApp Account doctype.

frappe.ui.form.on("WhatsApp Account", {
    refresh(frm) {
        _clear_qr_timer(frm);

        if (frm.is_new() || !frm.doc.openwa_enabled) return;

        if (!frm.doc.openwa_session_id) {
            frm.page.set_indicator(__("Not Setup"), "red");
            _add_setup_button(frm);
            return;
        }

        // Session exists — check its live status from OpenWA.
        frappe.call({
            method: "openwa_bridge.whatsapp_account.get_openwa_session_status",
            args: { account_name: frm.doc.name },
            callback(r) {
                if (!r || !r.message) return;
                const s = r.message.status;

                if (s === "ready") {
                    const phone = r.message.phone || "";
                    const pushName = r.message.push_name || "";
                    const label = [pushName, phone].filter(Boolean).join(" · ") || __("Connected");
                    frm.page.set_indicator(label, "green");
                    _add_disconnect_button(frm);
                    _add_manage_events_button(frm);
                    _add_unlink_button(frm);
                    _add_delete_session_button(frm);
                } else if (s === "qr_ready" || s === "initializing") {
                    frm.page.set_indicator(__("Scan QR Code"), "orange");
                    _show_qr_code(frm);
                    _add_delete_session_button(frm);
                } else if (s === "not_found") {
                    frm.page.set_indicator(__("Session Deleted"), "red");
                    _add_reconnect_button(frm);
                } else if (s === "auth_error") {
                    frm.page.set_indicator(__("Auth Error — Check API Key"), "red");
                    _add_reconnect_button(frm);
                    _add_delete_session_button(frm);
                } else if (s === "error") {
                    const errMsg = r.message.error || __("OpenWA Error");
                    frm.page.set_indicator(errMsg, "red");
                    _add_reconnect_button(frm);
                } else if (s === "action_required") {
                    // OpenWA 0.12.0+: the "What's new" onboarding modal needs a
                    // human. Recover re-drives the engine (no QR rescan).
                    const lastError = r.message.last_error || __("WhatsApp onboarding modal needs acknowledgment");
                    frm.page.set_indicator(__("Action Required"), "orange");
                    frappe.show_alert({
                        message: __("Action Required: {0}", [lastError]),
                        indicator: "orange",
                        duration: 8,
                    });
                    _add_recover_button(frm);
                    _add_manage_events_button(frm);
                    _add_delete_session_button(frm);
                } else if (s === "failed") {
                    frm.page.set_indicator(__("Session Failed — Reconnect to Fix"), "red");
                    _add_reconnect_button(frm);
                    _add_delete_session_button(frm);
                } else if (s === "disconnected" && r.message.engine_loaded) {
                    // Live engine still registered — automatic reconnect backoff
                    // is in progress. Start would answer 400, so leave it alone.
                    frm.page.set_indicator(__("Reconnecting..."), "orange");
                    _add_manage_events_button(frm);
                    _add_delete_session_button(frm);
                } else {
                    // disconnected (no engine) / created / unknown
                    frm.page.set_indicator(__("Disconnected"), "red");
                    _add_reconnect_button(frm);
                    _add_manage_events_button(frm);
                    _add_delete_session_button(frm);
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
        () => { _run_setup(frm); },
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

/**
 * Add a button that confirms and starts WhatsApp session reconnection.
 * @param {object} frm - The WhatsApp account form.
 */
function _add_reconnect_button(frm) {
    frm.add_custom_button(
        __("Reconnect"),
        () => {
            frappe.confirm(
                __("Reconnect the WhatsApp session? If the session is stuck, the app will force-kill and restart. If the session was deleted, a new one will be created."),
                () => { _show_qr_code(frm); }
            );
        },
        __("OpenWA")
    );
}

/**
 * Add a button that restarts the WhatsApp session using its stored login.
 * @param {Object} frm - The WhatsApp account form.
 */
function _add_recover_button(frm) {
    frm.add_custom_button(
        __("Recover"),
        () => {
            frappe.confirm(
                __("Restart the WhatsApp session to clear the onboarding modal? The session will stop and start again using the stored login — no QR scan is needed."),
                () => {
                    frappe.call({
                        method: "openwa_bridge.whatsapp_account.recover_openwa_session",
                        args: { account_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Recovering session..."),
                        callback(r) {
                            const msg = r.message || {};
                            if (msg.status === "error") {
                                frappe.msgprint({
                                    title: __("Recovery Failed"),
                                    indicator: "red",
                                    message: msg.error,
                                });
                                return;
                            }
                            frappe.show_alert({
                                message: __("Session restarted"),
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

/**
 * Adds a button that unlinks the connected device while preserving the OpenWA session.
 */
function _add_unlink_button(frm) {
    frm.add_custom_button(
        __("Unlink"),
        () => {
            frappe.confirm(
                __("Unlink this device from the WhatsApp account? The device is removed from the account's Linked Devices and a fresh QR scan (or pairing code) is required to reconnect. The session is not deleted from OpenWA — delete the session separately to remove local data too."),
                () => {
                    frappe.call({
                        method: "openwa_bridge.whatsapp_account.logout_openwa_session",
                        args: { account_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Unlinking device..."),
                        callback(r) {
                            const msg = r.message || {};
                            if (msg.status === "error") {
                                frappe.msgprint({
                                    title: __("Unlink Failed"),
                                    indicator: "red",
                                    message: msg.error,
                                });
                                return;
                            }
                            if (msg.status === "incomplete") {
                                frappe.msgprint({
                                    title: __("Unlink Incomplete"),
                                    indicator: "orange",
                                    message: msg.error,
                                });
                                return;
                            }
                            frappe.show_alert({
                                message: __("Device unlinked — scan the QR code to reconnect"),
                                indicator: "green",
                            });
                            frm.reload_doc();
                        },
                    });
                }
            );
        },
        __("OpenWA"),
        true  // right-aligned
    );
}

/**
 * Add a button for permanently deleting the OpenWA session.
 * @param {Object} frm - The WhatsApp Account form.
 */
function _add_delete_session_button(frm) {
    frm.add_custom_button(
        __("Delete Session"),
        () => {
            frappe.confirm(
                __("Permanently delete this session from OpenWA? This removes the engine, all stored messages, and webhooks. You will need to set up a new session."),
                () => {
                    frappe.call({
                        method: "openwa_bridge.whatsapp_account.delete_openwa_session",
                        args: { account_name: frm.doc.name },
                        freeze: true,
                        freeze_message: __("Deleting session..."),
                        callback(r) {
                            if (r.message && r.message.status === "error") {
                                frappe.msgprint({
                                    title: __("Delete Failed"),
                                    indicator: "red",
                                    message: r.message.error,
                                });
                                return;
                            }
                            frappe.show_alert({
                                message: __("Session deleted from OpenWA"),
                                indicator: "green",
                            });
                            frm.reload_doc();
                        },
                    });
                }
            );
        },
        __("OpenWA"),
        true  // right-aligned
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
                _clear_qr_timer(frm);
                frappe.msgprint({
                    title: __("QR Code Error"),
                    indicator: "red",
                    message: msg.error || __("Failed to get QR code"),
                });
            }
        },
    });
}

function _render_qr(frm, qr_data_url) {
    _clear_qr_timer(frm);

    const d = new frappe.ui.Dialog({
        title: __("Scan QR Code with WhatsApp"),
        size: "small",
        onhide() { _clear_qr_timer(frm); },
    });

    d.$body.html(
        '<div style="text-align:center; padding:20px;">' +
            '<img src="' + qr_data_url + '" ' +
            'style="width:280px; height:280px; border:3px solid #d1d5db; border-radius:12px; image-rendering: pixelated;" />' +
            '<p style="margin-top:16px; color:#374151; font-size:14px; font-weight:500;">' +
                __("Scan with WhatsApp to connect") +
            "</p>" +
            '<p style="color:#6b7280; font-size:12px;">' +
                __("QR expires in 60 seconds — auto-refreshes every 55 s") +
            "</p>" +
        "</div>"
    );
    d.show();

    frm._qr_dialog = d;
    frm._qr_refresh_timer = setInterval(() => {
        _show_qr_code(frm);
    }, 55000);
}

function _clear_qr_timer(frm) {
    if (frm._qr_refresh_timer) {
        clearInterval(frm._qr_refresh_timer);
        frm._qr_refresh_timer = null;
    }
    if (frm._qr_dialog) {
        frm._qr_dialog.hide();
        frm._qr_dialog = null;
    }
}

// ---------------------------------------------------------------------------
// Webhook Events Management
// ---------------------------------------------------------------------------

const _ALL_WEBHOOK_EVENTS = [
    { event: "message.received", label: __("Message Received"), desc: __("When a message is received") },
    { event: "message.sent", label: __("Message Sent"), desc: __("When a message is sent") },
    { event: "message.ack", label: __("Message ACK"), desc: __("Message acknowledgment status") },
    { event: "message.failed", label: __("Message Failed"), desc: __("When a message fails to send") },
    { event: "message.revoked", label: __("Message Revoked"), desc: __("When a message is deleted/revoked") },
    { event: "message.reaction", label: __("Message Reaction"), desc: __("When a reaction is received") },
    { event: "session.status", label: __("Session Status"), desc: __("Session status changes") },
    { event: "session.qr", label: __("Session QR"), desc: __("When QR code is generated") },
    { event: "session.authenticated", label: __("Session Authenticated"), desc: __("When session is authenticated") },
    { event: "session.disconnected", label: __("Session Disconnected"), desc: __("When a session disconnects") },
    { event: "group.join", label: __("Group Join"), desc: __("When someone joins a group") },
    { event: "group.leave", label: __("Group Leave"), desc: __("When someone leaves a group") },
    { event: "group.update", label: __("Group Update"), desc: __("When group info is updated") },
    { event: "*", label: __("All Events"), desc: __("Subscribe to all events (wildcard)") },
];

function _add_manage_events_button(frm) {
    frm.add_custom_button(__("Manage Webhook Events"), () => {
        _show_events_dialog(frm);
    }, __("OpenWA"));
}

function _show_events_dialog(frm) {
    let current_events = [];
    try {
        current_events = JSON.parse(frm.doc.openwa_webhook_events || "[]");
    } catch (e) {
        current_events = [];
    }

    const is_wildcard = current_events.includes("*");

    let rows = _ALL_WEBHOOK_EVENTS.map((item) => {
        const checked = is_wildcard || current_events.includes(item.event);
        return `
            <div style="display:flex; align-items:center; padding:8px 12px; border-bottom:1px solid #e5e7eb;">
                <input type="checkbox" class="openwa-event-cb" value="${item.event}"
                    ${checked ? "checked" : ""}
                    style="margin-right:12px; width:18px; height:18px; cursor:pointer;" />
                <div>
                    <div style="font-weight:500; font-size:13px;">${item.label}</div>
                    <div style="color:#6b7280; font-size:11px;">${item.desc} <code style="font-size:10px;">${item.event}</code></div>
                </div>
            </div>
        `;
    }).join("");

    const d = new frappe.ui.Dialog({
        title: __("Manage Webhook Events"),
        size: "large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "events_html",
                options: `
                    <div style="margin-bottom:12px; padding:10px; background:#f3f4f6; border-radius:6px; font-size:12px; color:#374151;">
                        ${__("Select which events OpenWA should send to this webhook URL. Changes take effect when you save the account.")}
                    </div>
                    <div style="border:1px solid #d1d5db; border-radius:6px; overflow:hidden;">
                        ${rows}
                    </div>
                `,
            },
        ],
        primary_action_label: __("Apply"),
        primary_action: () => {
            const selected = [];
            d.$wrapper.find(".openwa-event-cb:checked").each(function () {
                selected.push($(this).val());
            });

            if (selected.length === 0) {
                frappe.msgprint(__("Please select at least one event."));
                return;
            }

            frm.set_value("openwa_webhook_events", JSON.stringify(selected));
            d.hide();
            frappe.show_alert({
                message: __("Selected {0} event(s). Save the account to apply.", [selected.length]),
                indicator: "blue",
            });
        },
    });

    d.show();
}
