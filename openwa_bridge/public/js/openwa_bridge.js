// OpenWA Bridge – "Send To Whatsapp" dialog overlay (Send-as-PDF).
//
// Loaded via app_include_js on every desk page. The upstream frappe_whatsapp
// app injects a "Send To Whatsapp" menu item on every Form using its own
// (unedited) client script. This overlay removes that legacy item and replaces
// it with an enhanced dialog that can send the current document as a PDF via
// OpenWA's send-document API. All code lives in this app — nothing here
// modifies the upstream frappe_whatsapp app.
//
// In "Send as PDF" mode the dialog behaves like Frappe's own print page:
//   * Print Format, Language, Letter Head controls prefilled with the same
//     defaults the print page resolves (form layout format → Standard,
//     doc.language → print format default → system, doc.letter_head → default).
//   * Dynamic print settings (e.g. compact_item_print) from the whitelisted
//     `get_print_settings_to_show` endpoint.
//   * A live preview iframe rendered via `get_html_and_style`.
//   * Send is disabled until a preview renders successfully, so a document is
//     never sent before it has been confirmed renderable with these settings.

$(document).on("app_ready", () => {
    _ensure_pane_style();
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
 * Inject the stylesheet for the two-column PDF pane once per page load.
 */
function _ensure_pane_style() {
    if (document.getElementById("openwa-pdf-pane-style")) return;
    const style = document.createElement("style");
    style.id = "openwa-pdf-pane-style";
    style.textContent = `
        .openwa-pdf-pane { display: flex; gap: 16px; align-items: flex-start; }
        .openwa-pdf-controls { flex: 0 0 300px; min-width: 260px; }
        .openwa-pdf-controls .frappe-control { margin-bottom: 12px; }
        .openwa-pdf-controls .control-input { margin-top: 4px; }
        .openwa-pdf-settings-title {
            font-size: var(--text-sm); font-weight: 600; text-transform: uppercase;
            color: var(--text-muted); border-top: 1px solid var(--border-color);
            margin: 16px 0 4px; padding-top: 12px;
        }
        .openwa-pdf-preview { flex: 1 1 auto; min-width: 0; }
        .openwa-preview-iframe {
            width: 100%; height: 520px; border: 1px solid var(--border-color);
            border-radius: 6px; background: #fff;
        }
        .openwa-preview-status { margin-top: 8px; font-size: var(--text-sm); color: var(--text-muted); }
        .openwa-preview-status.error { color: var(--error-color); }
    `;
    document.head.appendChild(style);
}

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
        title: __("Send a WhatsApp Message"),
        size: "extra-large",
        no_submit_on_enter: true,
        fields: [
            {
                label: __("Send as PDF"),
                fieldname: "send_pdf",
                fieldtype: "Check",
                change() {
                    _toggle_pdf_mode(dialog, dialog.get_value("send_pdf"));
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
                fieldname: "ht_pdf",
                fieldtype: "HTML",
                options:
                    '<div class="openwa-pdf-pane">' +
                    '<div class="openwa-pdf-controls"></div>' +
                    '<div class="openwa-pdf-preview">' +
                    '<iframe class="openwa-preview-iframe" title="PDF Preview"></iframe>' +
                    '<div class="openwa-preview-status"></div>' +
                    "</div>" +
                    "</div>",
            },
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
    dialog.fields_dict.ht_pdf.toggle(false);
    dialog.show();

    // Build the PDF controls + preview pane once the dialog is rendered.
    dialog.openwa_pdf_state = _init_pdf_pane(dialog, frm);
    _set_send_enabled(dialog, true);
}

/**
 * Flip the dialog between template mode and "Send as PDF" mode.
 * @param {object} dialog - The open dialog.
 * @param {boolean} is_pdf - Whether PDF mode is active.
 */
function _toggle_pdf_mode(dialog, is_pdf) {
    const tpl = dialog.fields_dict.template;
    tpl.df.reqd = is_pdf ? 0 : 1;
    tpl.refresh();
    tpl.toggle(!is_pdf);
    dialog.fields_dict.ht_pdf.toggle(is_pdf);

    if (is_pdf) {
        const state = dialog.openwa_pdf_state;
        if (state) {
            _refresh_preview(dialog, state);
        } else {
            _set_send_enabled(dialog, false);
        }
    } else {
        _set_send_enabled(dialog, true);
    }
}

/**
 * Build the print-like controls and preview iframe for PDF mode.
 * @param {object} dialog - The open dialog.
 * @param {object} frm - The current form.
 * @returns {object} State object used by the preview/send flow.
 */
function _init_pdf_pane(dialog, frm) {
    const $wrapper = dialog.fields_dict.ht_pdf.$wrapper;
    const $controls = $wrapper.find(".openwa-pdf-controls");
    const $iframe = $wrapper.find(".openwa-preview-iframe");
    const $status = $wrapper.find(".openwa-preview-status");

    const state = {
        frm,
        $iframe,
        $status,
        settings: {},
        controls: [],
        ready: false,
        preview_ok: false,
        _req: null,
    };

    const make_control = (df, parent, change) =>
        frappe.ui.form.make_control({
            df: Object.assign({}, df, { change }),
            parent,
            render_input: 1,
        });

    // Print Format
    state.pf_ctl = make_control(
        {
            fieldtype: "Link",
            options: "Print Format",
            label: __("Print Format"),
            fieldname: "print_format",
            get_query: () => ({ filters: { doc_type: frm.doctype, print_format_for: "DocType" } }),
        },
        $controls,
        () => {
            _apply_print_format_defaults(state).then(() => _refresh_preview(dialog, state));
        }
    );

    // Language (mirrors the print page: Link to the Language doctype)
    state.lang_ctl = make_control(
        {
            fieldtype: "Link",
            options: "Language",
            label: __("Language"),
            fieldname: "language",
        },
        $controls,
        () => _refresh_preview(dialog, state)
    );

    // Letter Head
    state.lh_ctl = make_control(
        {
            fieldtype: "Link",
            options: "Letter Head",
            label: __("Letter Head"),
            fieldname: "letterhead",
            get_query: () => ({ filters: { letter_head_for: "DocType" } }),
        },
        $controls,
        () => _refresh_preview(dialog, state)
    );

    // PDF Filename + caption
    state.filename_ctl = make_control(
        {
            fieldtype: "Data",
            label: __("PDF Filename"),
            fieldname: "pdf_filename",
        },
        $controls,
        () => {}
    );
    state.caption_ctl = make_control(
        {
            fieldtype: "Data",
            label: __("Caption"),
            fieldname: "caption",
        },
        $controls,
        () => {}
    );

    // Dynamic print settings (e.g. compact_item_print) — mirrors the print page
    const $settings_title = $(
        '<div class="openwa-pdf-settings-title">' + __("Print Settings") + "</div>"
    ).appendTo($controls);
    $settings_title.hide();
    const $settings_parent = $("<div></div>").appendTo($controls);

    frappe
        .xcall("frappe.printing.page.print.print.get_print_settings_to_show", {
            doctype: frm.doctype,
            docname: frm.docname,
        })
        .then((settings) => {
            if (!settings || !settings.length) return;
            $settings_title.show();
            for (const df of settings) {
                const fieldname = df.fieldname;
                const ctl = make_control(df, $settings_parent, () => {
                    state.settings[fieldname] = ctl.get_value();
                    _refresh_preview(dialog, state);
                });
                state.controls.push(ctl);
                if (typeof df.default !== "undefined") {
                    ctl.set_input(df.default);
                } else if (df.fieldtype === "Check") {
                    ctl.set_input(0);
                }
            }
        });

    // Prefill defaults, then render the first preview once they're applied.
    _set_defaults(dialog, state).then(() => {
        state.ready = true;
        _refresh_preview(dialog, state);
    });

    return state;
}

/**
 * Prefill the PDF controls with the same defaults Frappe's print page uses.
 * Resolves print format → language → letterhead asynchronously. The format
 * default mirrors print.js set_default_print_format exactly:
 * `_layout_print_format || meta.default_print_format || ""` (falling back to
 * Standard for rendering).
 *
 * A default whose `doc_type` belongs to a different doctype (e.g. a Sales
 * Invoice format configured as the default for CRM Deal) can never render
 * that document, so it is replaced with Standard and a note is shown —
 * mirroring how Frappe degrades a missing/unusable format to Standard.
 * @param {object} dialog - The open dialog.
 * @param {object} state - PDF pane state.
 * @returns {Promise} Resolves when all defaults are applied.
 */
function _set_defaults(dialog, state) {
    const frm = state.frm;
    const tasks = [];

    tasks.push(state.filename_ctl.set_value(`${frm.docname}.pdf`));

    const pf = frm._layout_print_format || frm.meta.default_print_format || "Standard";
    const pf_task =
        pf === "Standard"
            ? Promise.resolve(pf)
            : frappe.db
                  .get_value("Print Format", pf, "doc_type")
                  .then(({ message }) => {
                      const pf_doctype = message?.doc_type || null;
                      if (pf_doctype && pf_doctype !== frm.doctype) {
                          state.$status
                              .removeClass("error")
                              .html(
                                  __(
                                      "Default print format '{0}' is for '{1}' and cannot be used for '{2}'; using Standard.",
                                      [pf, pf_doctype, frm.doctype]
                                  )
                              );
                          return "Standard";
                      }
                      return pf;
                  });

    tasks.push(
        pf_task.then((resolved_pf) =>
            state.pf_ctl
                .set_value(resolved_pf)
                .then(() => _apply_print_format_defaults(state))
        )
    );

    return Promise.all(tasks);
}

/**
 * Apply the language + letterhead defaults for the current print format.
 * Mirrors print.js refresh_print_format: set_default_print_language
 * (`doc.language || print_format.default_print_language || boot.lang`) and
 * update_letterhead_for_print_format (a named beta format's embedded
 * `format_data.letter_head`, else the document/default letterhead).
 * @param {object} state - PDF pane state.
 * @returns {Promise} Resolves once the language/letterhead controls are set.
 */
function _apply_print_format_defaults(state) {
    const frm = state.frm;
    const pf = state.pf_ctl.get_value() || "Standard";

    const format_req =
        pf === "Standard"
            ? Promise.resolve({})
            : frappe.db
                  .get_value(
                      "Print Format",
                      pf,
                      ["default_print_language", "format_data", "doc_type"]
                  )
                  .then(({ message }) => message || {});

    return format_req.then((fmt) => {
        const lang = frm.doc.language || fmt.default_print_language || frappe.boot.lang;
        return state.lang_ctl.set_value(lang).then(() => _resolve_letterhead(state, fmt));
    });
}

/**
 * Resolve the letterhead: a named format's embedded `format_data.letter_head`
 * wins (print.js update_letterhead_for_print_format), else the document's own
 * letter_head, else the site's default DocType letterhead.
 * @param {object} state - PDF pane state.
 * @param {object} fmt - Print Format doc fields ({format_data}).
 * @returns {Promise} Resolves once the letterhead control is set.
 */
function _resolve_letterhead(state, fmt) {
    if (fmt && fmt.format_data) {
        try {
            const data = JSON.parse(fmt.format_data);
            if (data && data.letter_head) {
                return state.lh_ctl.set_value(data.letter_head);
            }
        } catch (_) {
            // malformed format_data — fall through to document/default letterhead
        }
    }
    return _resolve_default_letterhead(state);
}

/**
 * Resolve the default letterhead: the document's own letter_head, else the
 * site's default DocType letterhead (matches the print page).
 * @param {object} state - PDF pane state.
 * @returns {Promise<string|null>}
 */
function _resolve_default_letterhead(state) {
    const frm = state.frm;
    if (frm.doc.letter_head) {
        return frappe.db
            .get_value("Letter Head", { name: frm.doc.letter_head, disabled: 0 }, "name")
            .then(({ message }) => {
                if (!message?.name) return _default_letterhead(state);
                return state.lh_ctl.set_value(message.name);
            });
    }
    return _default_letterhead(state);
}

/**
 * Fetch the site's default DocType letterhead and apply it to the control.
 * @param {object} state - PDF pane state.
 * @returns {Promise<string|null>}
 */
function _default_letterhead(state) {
    return frappe.db
        .get_value("Letter Head", { disabled: 0, is_default: 1, letter_head_for: "DocType" }, "name")
        .then(({ message }) => {
            if (message?.name) return state.lh_ctl.set_value(message.name);
            return frappe.db
                .get_value("Letter Head", { disabled: 0, is_default: 1 }, "name")
                .then((r) => {
                    if (r.message?.name) return state.lh_ctl.set_value(r.message.name);
                    return null;
                });
        });
}

/**
 * Render the live preview via get_html_and_style and gate the Send button on
 * a successful render. Uses the exact same args the print page passes.
 *
 * A named print format whose `doc_type` belongs to a different doctype can
 * never render this document (Frappe embeds Jinja errors in the output rather
 * than raising), so it is refused up-front with a clear message and Send stays
 * disabled. Standard and formats matching the current doctype render normally.
 * @param {object} dialog - The open dialog.
 * @param {object} state - PDF pane state.
 */
function _refresh_preview(dialog, state) {
    if (!state.ready) return;

    const frm = state.frm;
    const pf = state.pf_ctl.get_value() || "Standard";
    const lh = state.lh_ctl.get_value() || "";
    const lang = state.lang_ctl.get_value() || frappe.boot.lang;

    state.preview_ok = false;
    _set_send_enabled(dialog, false);
    state.$status.removeClass("error").html(__("Rendering preview…"));
    if (state._req) state._req.abort();

    if (pf !== "Standard") {
        frappe.db
            .get_value("Print Format", pf, "doc_type")
            .then(({ message }) => {
                const pf_doctype = message?.doc_type || null;
                if (pf_doctype && pf_doctype !== frm.doctype) {
                    state.$status
                        .addClass("error")
                        .html(
                            __(
                                "Print Format '{0}' is for '{1}' and cannot be used for '{2}'. Pick a format for '{2}' or Standard.",
                                [pf, pf_doctype, frm.doctype]
                            )
                        );
                    return;
                }
                _request_preview(dialog, state, pf, lh, lang);
            });
        return;
    }
    _request_preview(dialog, state, pf, lh, lang);
}

/**
 * Request the rendered HTML/style for the current settings.
 * @param {object} dialog - The open dialog.
 * @param {object} state - PDF pane state.
 * @param {string} pf - Print Format name.
 * @param {string} lh - Letter Head name ("" for none).
 * @param {string} lang - Language code.
 */
function _request_preview(dialog, state, pf, lh, lang) {
    state._req = frappe.call({
        method: "frappe.www.printview.get_html_and_style",
        args: {
            doc: state.frm.doc,
            print_format: pf,
            no_letterhead: lh ? 0 : 1,
            letterhead: lh,
            settings: state.settings,
            _lang: lang,
        },
        callback: (r) => {
            if (r.exc || !r.message || !r.message.html) {
                state.$status
                    .addClass("error")
                    .html(
                        _preview_error_text(r) ||
                            __("Preview could not be rendered. Send is disabled.")
                    );
                return;
            }
            const html =
                "<!DOCTYPE html><html><head><meta charset='utf-8'>" +
                `<style>${r.message.style || ""}</style></head><body>${r.message.html}</body></html>`;
            state.$iframe.attr("srcdoc", html);
            state.preview_ok = true;
            _set_send_enabled(dialog, true);
            state.$status.html(
                __("Preview ready — the PDF will be sent with these settings.")
            );
        },
    });
}

/**
 * Extract the server's actual error message from a frappe.call response, or
 * "" when none is available. Surfaces thrown ValidationErrors (e.g. a guard)
 * instead of a generic "could not render" message.
 * @param {object} r - frappe.call response.
 * @returns {string}
 */
function _preview_error_text(r) {
    const messages = r.message?._server_messages || r._server_messages;
    if (Array.isArray(messages) && messages.length) {
        for (const msg of messages) {
            try {
                const parsed = JSON.parse(msg);
                if (parsed && parsed.message) return parsed.message;
            } catch (_) {
                // not JSON — try the raw string
            }
        }
    }
    if (r.exc) {
        const lines = String(r.exc).trim().split("\n");
        for (let i = lines.length - 1; i >= 0; i--) {
            const line = lines[i].trim();
            if (line) return line;
        }
    }
    return "";
}

/**
 * Enable/disable the dialog's primary Send button.
 * @param {object} dialog - The open dialog.
 * @param {boolean} enabled - Whether Send should be clickable.
 */
function _set_send_enabled(dialog, enabled) {
    if (enabled) {
        dialog.enable_primary_action();
    } else {
        dialog.disable_primary_action();
    }
}

/**
 * Primary action for the send dialog.
 * @param {object} frm - The current form.
 * @param {object} dialog - The open dialog.
 */
function _send_from_dialog(frm, dialog) {
    const state = dialog.openwa_pdf_state || {};
    const values = dialog.get_values();
    if (!values) return;

    let pf = "Standard";
    let filename = "";
    let caption = "";
    let letterhead = "";
    let language = "";
    let settings_json = "{}";

    if (!values.mobile_no) {
        frappe.msgprint(__("Please select a contact that has a mobile number."));
        return;
    }

    if (values.send_pdf) {
        // Send the current document as a PDF via the OpenWA bridge
        pf = (state.pf_ctl && state.pf_ctl.get_value()) || "Standard";
        letterhead = (state.lh_ctl && state.lh_ctl.get_value()) || "";
        language = (state.lang_ctl && state.lang_ctl.get_value()) || "";
        filename = (state.filename_ctl && state.filename_ctl.get_value()) || "";
        caption = (state.caption_ctl && state.caption_ctl.get_value()) || "";
        settings_json = JSON.stringify(state.settings || {});

        frappe.call({
            method: "openwa_bridge.whatsapp_message.send_document_pdf",
            args: {
                to: values.mobile_no,
                reference_doctype: frm.doctype,
                reference_name: frm.docname,
                print_format: pf,
                filename: filename || undefined,
                caption: caption || undefined,
                letterhead: letterhead || undefined,
                language: language || undefined,
                settings: settings_json,
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
        ? `To : ${values.mobile_no}${space}PDF:${filename || frm.docname + ".pdf"} | Print Format:${pf}`
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
