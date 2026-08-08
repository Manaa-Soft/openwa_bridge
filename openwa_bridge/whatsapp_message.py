"""Outbound message router — overrides WhatsAppMessage.notify() to route via OpenWA."""
from __future__ import annotations

import frappe
import json
import re
import time
from datetime import datetime

from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
    WhatsAppMessage,
)
from frappe_whatsapp.utils import format_number
from frappe.exceptions import ValidationError
from openwa_bridge.utils import openwa_api, frappe_to_openwa_vars, get_api_key, _http_session, render_doc_as_pdf

_PLACEHOLDER_RE = re.compile(r"\{\{\s*\d+\s*\}\}")
_JINJA_MARKER_RE = re.compile(
    r"(\{%|\{\{\s*(frappe|jenv|doc|RLE|PDF|company)\b)"
)
# Bidirectional / zero-width control characters that leak into delivered text
# from template bodies (e.g. RLE/PDF placeholders used to force RTL) but should
# not reach the customer: LRM/RLE/LRE/LRO/RLI/LRI/FSI/PDI/MU and friends.
_BIDI_CONTROL_RE = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c\u206a-\u206f]")
_DELETION_CHARS_RE = re.compile("[\ufeff\u00ad]")


def _strip_bidi_controls(text: str) -> str:
    """Remove bidi/zero-width control characters from delivered text."""
    if not text:
        return text
    return _BIDI_CONTROL_RE.sub("", _DELETION_CHARS_RE.sub("", text))


def _is_jinja_template(body: str) -> bool:
    """True when the body uses Jinja (``{% ... %}`` or ``{{ identifier }}``)."""
    if _JINJA_MARKER_RE.search(body):
        return True
    # Non-numeric {{ ... }} expressions (e.g. {{ RLE }}, {{ company.company_name }})
    return bool(re.search(r"\{\{\s*[^\d}][^}]*\}\}", body))


def _is_placeholder_template(body: str) -> bool:
    """True when the body only uses ``{{1}}..{{n}}`` style placeholders."""
    stripped = body.strip()
    if not stripped:
        return False
    if _JINJA_MARKER_RE.search(stripped):
        return False
    return bool(_PLACEHOLDER_RE.search(stripped))


def _compose_template_text(template_doc, body: str) -> str:
    """Compose the delivered text from header + body + footer (all properties)."""
    parts = []
    header = (template_doc.get("header") or "").strip()
    footer = (template_doc.get("footer") or "").strip()
    if header:
        parts.append(header)
    if body:
        parts.append(body)
    if footer:
        parts.append(footer)
    return "\n\n".join(parts)


def _send_typing_indicator(base_url: str, session_id: str, api_key: str, chat_id: str, state: str = "typing") -> None:
    """Send a typing, recording, or paused indicator for a WhatsApp chat.
    
    Parameters:
        state (str): Indicator state to send. Defaults to ``"typing"``.
    """
    try:
        headers = {"Content-Type": "application/json", "X-API-Key": api_key}
        _http_session.post(
            f"{base_url}/api/sessions/{session_id}/chats/typing",
            json={"chatId": chat_id, "state": state},
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass  # Typing indicators are cosmetic — never block the send flow


class OverrideWhatsAppMessage(WhatsAppMessage):
    """Intercepts all outbound messages and routes OpenWA-enabled accounts through the gateway."""

    def before_validate(self) -> None:
        """Snapshot the caller-set reference and preserve the source doc for rendering.

        The CRM app registers a ``doc_events`` validate hook
        (``crm.api.whatsapp.validate``) that resolves the recipient's number to a
        Contact/Lead/Deal and overwrites ``reference_doctype``/``reference_name`` on
        every save — including outgoing messages whose reference was set explicitly
        (``send_document_pdf``, template sends, the WhatsApp dialog). We snapshot the
        reference here (``before_validate`` runs before ``validate``).

        For template/PDF sends the source document (e.g. a Sales Invoice) is also
        captured into ``openwa_render_doctype``/``openwa_render_name``. That lets the
        reference be relinked to the CRM record (so the message appears in the CRM
        thread) while the dynamic header image still renders the source document.
        """
        self._explicit_reference = (self.reference_doctype, self.reference_name)

        needs_source = bool(getattr(self, "template", None)) or bool(
            getattr(self, "openwa_send_pdf", None)
        )
        if (
            needs_source
            and self.reference_doctype
            and self.reference_name
            and self.reference_doctype not in ("CRM Deal", "CRM Lead", "Contact")
            and not getattr(self, "openwa_render_doctype", None)
        ):
            self.openwa_render_doctype = self.reference_doctype
            self.openwa_render_name = self.reference_name

    def before_save(self) -> None:
        """Restore the explicit reference that CRM's validate hook clobbered — unless
        the source doc was captured for rendering.

        Runs in the ``before_save`` phase, which is strictly after ``validate``.
        When ``openwa_render_doctype`` is set the source document is preserved for
        the dynamic header image, so the reference can stay relinked to the CRM
        Deal/Lead — that is what makes the message appear in the CRM thread. Messages
        with no reference (e.g. incoming webhook messages) are left alone so CRM's
        auto-linking from the sender's number still applies.

        Outgoing template messages are also given a fully-rendered body here (Jinja
        or placeholder-substituted) so the caption, Desk list and CRM thread all show
        the real text instead of an empty message.
        """
        explicit = getattr(self, "_explicit_reference", (None, None))
        if explicit != (None, None) and not getattr(self, "openwa_render_doctype", None):
            self.reference_doctype, self.reference_name = explicit

        if getattr(self, "template", None) and getattr(self, "type", None) == "Outgoing":
            self._prepare_template_message()

    # ------------------------------------------------------------------
    # Template body rendering (Jinja / placeholder / plain)
    # ------------------------------------------------------------------

    _TEMPLATE_MESSAGE_SENTINEL = "Template message"

    def _prepare_template_message(self) -> None:
        """Fill the message text from the linked WhatsApp Templates doc.

        Covers every creation path (Desk dialog, CRM, notifications, bulk):
        - Jinja bodies (``{% ... %}`` / ``{{ doc.field }}``) are rendered against the
          reference document with ``frappe`` in context and composed as
          header + body + footer, delivered as free text.
        - Placeholder bodies (``{{1}}..{{n}}``) get their variables extracted from the
          template's ``field_names`` against the reference doc and are delivered via
          the OpenWA ``send-template`` endpoint.
        - Plain bodies are used verbatim.

        Never raises: a rendering failure logs an error and keeps the raw body so the
        send still proceeds.
        """
        current = getattr(self, "message", None)
        if current and current != self._TEMPLATE_MESSAGE_SENTINEL:
            return

        try:
            template_doc = frappe.get_doc("WhatsApp Templates", self.template)
        except Exception as exc:
            frappe.log_error(
                title="OpenWA: failed to load template for message",
                message=f"Msg {self.name}, Template {self.template}: {exc}",
            )
            return

        body = (template_doc.get("template") or "").strip()
        if not body:
            return

        try:
            if _is_jinja_template(body):
                self._render_jinja_template(template_doc, body)
            elif _is_placeholder_template(body):
                self._prepare_placeholder_template(template_doc, body)
            else:
                self.message = _compose_template_text(template_doc, body)
                self.message_type = "Manual"
                self.use_template = 0
        except ValidationError:
            # A placeholder template with no variable source must abort the send
            # (never deliver literal {{1}} tokens). Propagate the ValidationError.
            raise
        except Exception:
            frappe.log_error(
                title="OpenWA: template body render failed",
                message=(
                    f"Msg {self.name}, Template {self.template}, "
                    f"Ref {self.reference_doctype} {self.reference_name}\n"
                    f"{frappe.get_traceback()}"
                ),
            )
            self.message = current or body
            self.message_type = "Manual"
            self.use_template = 0

    def _get_render_doc(self):
        """Load the document the template renders against (e.g. the invoice), best-effort."""
        doctype = getattr(self, "openwa_render_doctype", None) or self.reference_doctype
        name = getattr(self, "openwa_render_name", None) or self.reference_name
        if not doctype or not name:
            return None
        try:
            return frappe.get_doc(doctype, name)
        except Exception:
            return None

    def _render_jinja_template(self, template_doc, body: str) -> None:
        """Render a Jinja body against the reference doc and set the message text."""
        doc = self._get_render_doc()
        if doc is None:
            rendered = body
        else:
            rendered = frappe.render_template(body, {"doc": doc, "frappe": frappe})
        self.message = _compose_template_text(
            template_doc, _strip_bidi_controls((rendered or "")).strip()
        )
        self.message_type = "Manual"
        self.use_template = 0

    def _prepare_placeholder_template(self, template_doc, body: str) -> None:
        """Set variables and switch to the OpenWA send-template endpoint."""
        params = self._extract_template_params(template_doc)
        if params:
            self.template_parameters = json.dumps(params)
            self.message = body
            self.message_type = "Template"
            self.use_template = 1
            return

        # A placeholder template without derivable variables must NOT be sent as
        # free text — the customer would receive literal "{{1}}" tokens. Refuse
        # with a clear message instead of silently delivering a broken message.
        var_count = len(_PLACEHOLDER_RE.findall(body))
        frappe.throw(
            frappe._(
                "Template '{0}' requires {1} variable(s) but none could be "
                "resolved. Map them under 'Fields' on a WhatsApp Notification "
                "for '{2}' that uses this template, or set 'Field Names' on the "
                "WhatsApp Templates doc, then try again."
            ).format(self.template, var_count, self._source_doctype()),
            title=frappe._("OpenWA: Template variables missing"),
        )

    def _source_doctype(self) -> str:
        """Return the doctype the template renders against (source, not CRM relink)."""
        return getattr(self, "openwa_render_doctype", None) or self.reference_doctype or ""

    def _extract_template_params(self, template_doc) -> list:
        """Explicit variables win; otherwise map fields onto the reference doc.

        Field names come from (in order): the template's ``field_names``, or the
        ``Fields`` child table of an enabled WhatsApp Notification that uses this
        template for the source doctype (the user maintains the variable mapping
        there).
        """
        if self.template_parameters:
            try:
                parsed = json.loads(self.template_parameters)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        if self.body_param:
            try:
                bp = json.loads(self.body_param)
                return list(bp.values()) if isinstance(bp, dict) else (bp if isinstance(bp, list) else [])
            except (json.JSONDecodeError, TypeError):
                return []

        field_names = []
        if template_doc.get("field_names"):
            field_names = [f.strip() for f in str(template_doc.field_names).split(",") if f.strip()]
        if not field_names:
            field_names = self._field_names_from_notification()
        if not field_names:
            return []

        doc = self._get_render_doc()
        if doc is None:
            return []
        params = []
        for field_name in field_names:
            try:
                value = doc.get_formatted(field_name)
            except Exception:
                value = doc.get(field_name) if hasattr(doc, "get") else None
            params.append(value if value is not None else "")

        # If none of the mapped fields yielded a real value, treat as no params
        if not any(str(p).strip() for p in params):
            return []
        return params

    def _field_names_from_notification(self) -> list:
        """Borrow the ordered variable mapping from a WhatsApp Notification.

        The user maintains template variables in the notification's ``Fields``
        child table (``WhatsApp Notification``), so when the template itself has
        no ``field_names`` we reuse the first enabled notification that uses this
        template for the source doctype.
        """
        doctype = self._source_doctype()
        if not doctype or not self.template:
            return []
        try:
            names = frappe.get_all(
                "WhatsApp Notification",
                filters={
                    "reference_doctype": doctype,
                    "template": self.template,
                    "disabled": 0,
                },
                pluck="name",
                limit_page_length=10,
            )
        except Exception:
            return []
        for notif_name in names:
            try:
                notif = frappe.get_doc("WhatsApp Notification", notif_name)
            except Exception:
                continue
            fields = notif.get("fields") or []
            field_names = [f.field_name for f in fields if getattr(f, "field_name", None)]
            if field_names:
                return field_names
        return []

    def notify(self, data: dict) -> None:
        """Intercept before Meta API call. ``data`` is the fully-built Meta payload."""
        account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)

        if account and account.get("openwa_enabled"):
            # Interactive / flow have no OpenWA equivalent — fall back to Meta
            if self.content_type in ("interactive", "flow"):
                frappe.log_error(
                    title="OpenWA Bridge: Fallback to Meta",
                    message=(
                        f"Msg {self.name}: '{self.content_type}' unsupported by OpenWA. "
                        "Routing via Meta API."
                    ),
                )
                return super().notify(data)

            # Do NOT create the outbox entry here — we are inside before_insert()
            # where self.name is still None (db_insert hasn't run yet).  Instead,
            # set a flag so after_insert() can create the entry with the real name.
            self._openwa_outbox_needed = True
            return

        return super().notify(data)

    def after_insert(self):
        """Create the outbox entry now that self.name is assigned.

        ``notify()`` sets ``_openwa_outbox_needed`` during ``before_insert()``
        when ``self.name`` is still ``None``.  We create the outbox entry here
        so the background worker can find the linked WhatsApp Message.
        """
        if not getattr(self, "_openwa_outbox_needed", False):
            return

        outbox = frappe.get_doc({
            "doctype": "OpenWA Outbox",
            "whatsapp_message": self.name,
            "whatsapp_account": self.whatsapp_account,
            "content_type": self.content_type,
            "status": "Pending",
            "max_attempts": 5,
            "scheduled_at": getattr(self, "openwa_scheduled_at", None) or None,
        })
        outbox.insert(ignore_permissions=True)

        # Scheduled messages are held in Pending until scheduled_at — do not
        # enqueue them now; the scheduler safety-net picks them up when due.
        if outbox.scheduled_at and outbox.scheduled_at > datetime.now():
            return

        try:
            frappe.enqueue(
                "openwa_bridge.tasks.process_outbox_entry",
                queue="long",
                timeout=300,
                job_id=f"openwa_outbox::{outbox.name}",
                deduplicate=True,
                outbox_name=outbox.name,
            )
        except Exception:
            frappe.log_error(
                title="OpenWA: Failed to enqueue outbox entry",
                message=f"Outbox {outbox.name} will be picked up by scheduler safety-net.",
            )

    # ------------------------------------------------------------------
    # OpenWA dispatchers
    # ------------------------------------------------------------------

    def _ensure_session_ready(self, account: "WhatsAppAccount") -> None:  # noqa: F821
        """Verify the OpenWA session is truly connected to WhatsApp.

        A session can be ``ready`` (engine alive) without being connected
        to WhatsApp (no phone number).  This method verifies the phone
        field is set — only then is the session actually usable.

        Raises ``frappe.ValidationError`` if the session cannot be recovered.
        """
        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = get_api_key(account)

        headers = {"Content-Type": "application/json", "X-API-Key": api_key}

        # Check current status
        session_data = None
        try:
            resp = _http_session.get(
                f"{base_url}/api/sessions/{session_id}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                session_data = resp.json()
                status = session_data.get("status", "unknown")
                phone = session_data.get("phone")
                if status == "ready" and phone:
                    return  # truly connected — engine alive AND WhatsApp linked
                # Engine alive but not connected to WhatsApp — need restart
            elif resp.status_code == 404:
                frappe.throw(
                    f"OpenWA session '{session_id}' no longer exists on the server. "
                    "Please re-setup the WhatsApp Account from the form.",
                    title="Session Deleted",
                )
            elif resp.status_code == 429:
                # Rate-limited — assume session is fine, don't restart.
                frappe.logger().info(
                    f"OpenWA pre-send: session '{session_id}' rate-limited "
                    f"— assuming connected, proceeding with send"
                )
                return
            else:
                status = "disconnected"
        except Exception:
            status = "disconnected"

        # Not ready — attempt restart
        frappe.logger().info(
            f"OpenWA pre-send: session '{session_id}' status is "
            f"'{getattr(session_data, 'get', lambda k, d=None: d)('status', 'unknown')}' "
            f"(phone={getattr(session_data, 'get', lambda k, d=None: d)('phone', None)}) "
            f"— attempting restart before sending message {self.name}"
        )
        try:
            start_resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/start",
                headers=headers,
                timeout=60,
            )
            if start_resp.status_code not in (200, 201, 400):
                frappe.throw(
                    f"OpenWA session restart failed: HTTP {start_resp.status_code}"
                )
        except Exception as exc:
            frappe.throw(
                f"OpenWA session is {status} and restart failed: {exc}"
            )

        # Poll for readiness (up to 15s, 1s intervals) — wait longer
        # because the engine needs time to reconnect to WhatsApp after
        # restart.  We require BOTH status=ready AND phone set.
        for i in range(15):
            time.sleep(1)
            try:
                verify = _http_session.get(
                    f"{base_url}/api/sessions/{session_id}",
                    headers=headers,
                    timeout=5,
                )
                if verify.status_code == 200:
                    vdata = verify.json()
                    new_status = vdata.get("status", "unknown")
                    phone = vdata.get("phone")
                    if new_status == "ready" and phone:
                        frappe.logger().info(
                            f"OpenWA pre-send: session '{session_id}' restarted "
                            f"— connected as {phone}"
                        )
                        return
                    if new_status == "qr_ready":
                        frappe.throw(
                            "OpenWA session restarted but requires QR re-scan. "
                            "Please open the WhatsApp Account form and scan the QR code."
                        )
            except Exception:
                pass

        # Not ready after wait — let outbox retry handle it
        frappe.throw(
            f"OpenWA session is {status} and could not be recovered automatically. "
            "The message will be retried. You can also click 'Reconnect' on the "
            "WhatsApp Account form."
        )

    def _get_pdf_settings(self) -> dict:
        """Return the stored dynamic print settings (e.g. ``compact_item_print``) as a dict."""
        raw = getattr(self, "openwa_print_settings", None)
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _send_via_openwa(self, account: "WhatsAppAccount", meta_payload: dict) -> None:  # noqa: F821
        """
        Translate and send the message through the OpenWA Gateway.
        
        Parameters:
        	account (WhatsAppAccount): WhatsApp account containing the OpenWA session configuration.
        	meta_payload (dict): Message payload data used to construct the OpenWA request.
        
        Raises:
        	Exception: If the session is unavailable, the message content is invalid or unsupported, or OpenWA rejects the request.
        """
        # Idempotency guard: if the message already has a message_id, it was
        # already sent (possibly by a previous attempt or webhook reconciliation).
        # Do NOT send again — just return.
        if self.message_id:
            frappe.logger().info(
                f"OpenWA: skipping send for {self.name} — "
                f"message_id '{self.message_id}' already set"
            )
            return

        # Ensure session is ready before attempting to send
        self._ensure_session_ready(account)

        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = get_api_key(account)

        raw_number = format_number(self.to)
        chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

        # Send typing indicator before non-bulk, non-template, non-reaction sends
        if self.content_type in ("text", "image", "video", "audio", "document", "location", "contact", "sticker"):
            _send_typing_indicator(base_url, session_id, api_key, chat_id, "typing")

        # --- template via OpenWA send-template endpoint ---
        if self.use_template and self.template:
            openwa_tid = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
            if openwa_tid:
                params: dict[str, str] = {}
                if self.body_param:
                    try:
                        bp = json.loads(self.body_param)
                        if isinstance(bp, dict):
                            params = {f"param{k}": v for k, v in bp.items()}
                        elif isinstance(bp, list):
                            params = {f"param{i + 1}": v for i, v in enumerate(bp)}
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif self.template_parameters:
                    try:
                        raw = json.loads(self.template_parameters)
                        params = {f"param{i + 1}": v for i, v in enumerate(raw)}
                    except (json.JSONDecodeError, TypeError):
                        pass

                if not params:
                    # Check if the template body actually has placeholders
                    tmpl_body = frappe.db.get_value("WhatsApp Templates", self.template, "template") or ""
                    has_placeholders = bool(re.search(r"\{\{.*?\}\}", tmpl_body))
                    if has_placeholders:
                        frappe.throw(
                            f"Template '{self.template}' requires variables but none were provided. "
                            "Fill in 'Template Variables' on the Bulk WhatsApp Message, "
                            "or configure the notification's 'Fields' child table."
                        )

                send_payload = {"chatId": chat_id, "templateId": openwa_tid, "vars": params}
                frappe.logger().info(f"OpenWA send-template payload: {json.dumps(send_payload, default=str)}")
                resp = _http_session.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-template",
                    json=send_payload,
                    headers=headers,
                    timeout=30,
                )
                # Stale template ID recovery: if 404, look up by name and retry
                if resp.status_code == 404:
                    new_tid = self._recover_template_id(account, base_url, session_id, api_key, headers)
                    if new_tid and new_tid != openwa_tid:
                        send_payload["templateId"] = new_tid
                        resp = _http_session.post(
                            f"{base_url}/api/sessions/{session_id}/messages/send-template",
                            json=send_payload,
                            headers=headers,
                            timeout=30,
                        )
            else:
                message_body = self._translate_template_payload()
                resp = _http_session.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-text",
                    json={"chatId": chat_id, "text": message_body},
                    headers=headers,
                    timeout=30,
                )

        elif self.is_reply and self.reply_to_message_id and self.content_type == "text":
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/reply",
                json={
                    "chatId": chat_id,
                    "quotedMessageId": self.reply_to_message_id,
                    "text": self.message,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "text":
            text_payload: dict = {"chatId": chat_id, "text": self.message}
            # Support @mentions — extract JIDs from message body (e.g. @12345@c.us)
            import re as _re
            mention_matches = _re.findall(r"(\d{5,15})@c\.us", self.message)
            if mention_matches:
                text_payload["mentions"] = [f"{m}@c.us" for m in mention_matches]
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-text",
                json=text_payload,
                headers=headers,
                timeout=30,
            )

        elif self.content_type in ("image", "video", "audio", "document"):
            media = meta_payload.get(self.content_type, {}) or {}
            link = media.get("link", "")
            b64 = media.get("base64", "")
            mimetype = media.get("mimetype", "")
            filename = media.get("filename", "")

            # Send-as-PDF: render the linked reference document at send time.
            # No base64 is stored on the doc — regenerated on every attempt so
            # retries/backoff stay safe and the PDF is always current.
            # The PDF is rendered from the render fields so the reference can be a
            # CRM record (Deal/Lead/Contact) while the PDF is another doc (e.g. Sales
            # Invoice) — see send_document_pdf.
            render_doctype = getattr(self, "openwa_render_doctype", None) or self.reference_doctype
            render_name = getattr(self, "openwa_render_name", None) or self.reference_name
            if (
                self.content_type == "document"
                and not link
                and not b64
                and getattr(self, "openwa_send_pdf", 0)
                and render_doctype
                and render_name
            ):
                pdf_bytes = render_doc_as_pdf(
                    render_doctype,
                    render_name,
                    self.openwa_print_format or "Standard",
                    letterhead=getattr(self, "openwa_letterhead", None) or None,
                    language=getattr(self, "openwa_language", None) or None,
                    settings=self._get_pdf_settings(),
                )
                if not pdf_bytes:
                    frappe.throw(
                        f"Failed to render {render_doctype} "
                        f"{render_name} as PDF. Check the Print Format and "
                        "that 'Allow Print for Draft' is enabled for draft documents."
                    )
                import base64 as _b64
                b64 = _b64.b64encode(pdf_bytes).decode()
                mimetype = "application/pdf"
                filename = self.openwa_pdf_filename or f"{render_name}.pdf"

            if not link and not b64:
                frappe.throw(
                    "OpenWA media messages require a URL in the message field: "
                    f'{{"{self.content_type}": {{"link": "https://..."}}}} '
                    'or a base64 payload: '
                    f'{{"{self.content_type}": {{"base64": "...", "mimetype": "image/jpeg"}}}}'
                )
            payload: dict = {"chatId": chat_id}
            if link:
                payload["url"] = link
            if b64:
                payload["base64"] = b64
            if mimetype:
                payload["mimetype"] = mimetype
            if filename:
                payload["filename"] = filename
            if self.content_type != "audio":
                payload["caption"] = self.message
            endpoint = f"send-{self.content_type}"
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/{endpoint}",
                json=payload,
                headers=headers,
                timeout=60,
            )

            # OpenWA limitation: POST /messages/reply is text-only. Media replies
            # are sent unquoted; when a reply is requested, follow up with a *text*
            # reply that quotes the just-sent media messageId (option B workaround).
            if self.is_reply and self.reply_to_message_id and resp.status_code < 400:
                try:
                    media_id = resp.json().get("messageId")
                except Exception:
                    media_id = None
                if media_id:
                    reply_resp = _http_session.post(
                        f"{base_url}/api/sessions/{session_id}/messages/reply",
                        json={
                            "chatId": chat_id,
                            "quotedMessageId": media_id,
                            "text": self.message,
                        },
                        headers=headers,
                        timeout=30,
                    )
                    if reply_resp.status_code >= 400:
                        frappe.log_error(
                            title="OpenWA Media-Reply Workaround Failed",
                            message=(
                                f"Media sent (id {media_id}) but the quoted text "
                                f"reply failed: {reply_resp.status_code} {reply_resp.text}"
                            ),
                        )

        elif self.content_type == "reaction":
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/react",
                json={
                    "chatId": chat_id,
                    "messageId": meta_payload.get("reaction", {}).get("message_id"),
                    "emoji": self.message,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "location":
            location_data = json.loads(self.message) if self.message else {}
            lat = location_data.get("latitude")
            lng = location_data.get("longitude")
            if lat is None or lng is None:
                frappe.throw(
                    "Location messages require JSON in the message field: "
                    '{"latitude": -6.2088, "longitude": 106.8456, "description": "...", "address": "..."}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-location",
                json={
                    "chatId": chat_id,
                    "latitude": float(lat),
                    "longitude": float(lng),
                    "description": location_data.get("description", ""),
                    "address": location_data.get("address", ""),
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "contact":
            contact_data = json.loads(self.message) if self.message else {}
            contact_name = contact_data.get("contact_name", "")
            contact_number = contact_data.get("contact_number", "")
            if not contact_name or not contact_number:
                frappe.throw(
                    "Contact messages require JSON in the message field: "
                    '{"contact_name": "John Doe", "contact_number": "+1234567890"}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-contact",
                json={
                    "chatId": chat_id,
                    "contactName": contact_name,
                    "contactNumber": contact_number,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "sticker":
            sticker_data = json.loads(self.message) if self.message else {}
            sticker_url = sticker_data.get("url", "")
            sticker_b64 = sticker_data.get("base64", "")
            if not sticker_url and not sticker_b64:
                frappe.throw(
                    "Sticker messages require JSON in the message field: "
                    '{"url": "https://example.com/sticker.webp"} or '
                    '{"base64": "<base64-encoded data>"}'
                )
            sticker_payload: dict = {"chatId": chat_id}
            if sticker_url:
                sticker_payload["url"] = sticker_url
            if sticker_b64:
                sticker_payload["base64"] = sticker_b64
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-sticker",
                json=sticker_payload,
                headers=headers,
                timeout=60,
            )

        elif self.content_type == "order":
            poll_data = json.loads(self.message) if self.message else {}
            poll_name = poll_data.get("name", "")
            poll_options = poll_data.get("options", [])
            if not poll_name or len(poll_options) < 2:
                frappe.throw(
                    "Poll messages require JSON in the message field: "
                    '{"name": "Question?", "options": ["Option 1", "Option 2"], "allowMultipleAnswers": false}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-poll",
                json={
                    "chatId": chat_id,
                    "name": poll_name,
                    "options": poll_options,
                    "allowMultipleAnswers": poll_data.get("allowMultipleAnswers", False),
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "edit":
            edit_data = json.loads(self.message) if self.message else {}
            chat_id_to_edit = edit_data.get("chat_id", chat_id)
            message_id_to_edit = edit_data.get("message_id", "")
            new_body = edit_data.get("body", self.message)
            if not message_id_to_edit:
                frappe.throw(
                    "Edit messages require JSON in the message field: "
                    '{"chat_id": "12345@c.us", "message_id": "ABC123", "body": "new text"}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/edit",
                json={
                    "chatId": chat_id_to_edit,
                    "messageId": message_id_to_edit,
                    "body": new_body,
                },
                headers=headers,
                timeout=30,
            )

        else:
            frappe.throw(
                f"Content type '{self.content_type}' cannot be routed via OpenWA."
            )

        if resp.status_code >= 400:
            try:
                err_body = resp.text
            except Exception:
                err_body = "(no body)"

            # OpenWA guarantee: persistSentState always returns 201+messageId
            # on success (swallows DB errors). failSend throws → 500 with NO
            # messageId. So ANY 4xx/5xx means the message was NOT delivered.
            # Log for diagnostics and let the caller (_fail_outbox via outbox
            # processor) handle retry with backoff.
            frappe.log_error(
                title=f"OpenWA API Error ({resp.status_code})",
                message=(
                    f"POST {resp.url} returned {resp.status_code}\n"
                    f"Request body: {json.dumps(resp.request.body.decode() if resp.request.body else '', default=str)}\n"
                    f"Response: {err_body}"
                ),
            )
            resp.raise_for_status()

        res_data = resp.json()

        # Extract message ID — support both top-level messageId and nested key.id
        msg_id = res_data.get("messageId") or (
            res_data.get("key", {}).get("id") if isinstance(res_data.get("key"), dict) else None
        )

        if msg_id:
            frappe.db.set_value(
                "WhatsApp Message",
                self.name,
                {"message_id": msg_id, "status": "Sent"},
            )
        else:
            # Message was accepted by OpenWA (HTTP 200+) but no ID returned.
            # Mark as Sent anyway to prevent infinite retries — the ack
            # webhook will set the real message_id when it arrives.
            frappe.db.set_value(
                "WhatsApp Message",
                self.name,
                {"status": "Sent"},
            )
            frappe.log_error(
                title="OpenWA: No messageId in response",
                message=(
                    f"Msg {self.name}: POST succeeded (HTTP {resp.status_code}) "
                    f"but response had no messageId.\n"
                    f"Response: {json.dumps(res_data, default=str)[:2000]}"
                ),
            )

    # ------------------------------------------------------------------
    # Template ID recovery (stale ID after session recreate)
    # ------------------------------------------------------------------

    def _recover_template_id(self, account, base_url, session_id, api_key, headers):
        """If template is missing on OpenWA, create or re-sync it, then return the ID."""
        try:
            frappe_name = frappe.db.get_value("WhatsApp Templates", self.template, "actual_name")
            if not frappe_name:
                frappe_name = self.template.lower().replace(" ", "_")

            # Try to find by name on OpenWA
            try:
                frappe_body = frappe.db.get_value("WhatsApp Templates", self.template, "template") or ""
                converted_body = frappe_to_openwa_vars(frappe_body)

                templates = openwa_api(account, "GET", "/templates")
                for t in templates:
                    if t.get("name") == frappe_name:
                        # Found by name — check if content matches
                        if t.get("body") == converted_body:
                            new_id = t.get("id")
                            frappe.db.set_value(
                                "WhatsApp Templates", self.template,
                                "openwa_template_id", new_id, update_modified=False,
                            )
                            frappe.db.commit()
                            frappe.log_error(
                                title="OpenWA: Template ID recovered",
                                message=f"Template '{self.template}' found on OpenWA → ID {new_id}",
                            )
                            return new_id
                        else:
                            # Content differs — update the OpenWA template
                            openwa_api(account, "PUT", f"/templates/{t['id']}", json_data={
                                "name": frappe_name,
                                "body": converted_body,
                                "header": frappe_to_openwa_vars(
                                    frappe.db.get_value("WhatsApp Templates", self.template, "header") or ""
                                ) or None,
                                "footer": frappe.db.get_value("WhatsApp Templates", self.template, "footer") or None,
                            })
                            frappe.db.set_value(
                                "WhatsApp Templates", self.template,
                                "openwa_template_id", t["id"], update_modified=False,
                            )
                            frappe.db.commit()
                            frappe.log_error(
                                title="OpenWA: Template content updated",
                                message=f"Template '{self.template}' content was stale on OpenWA, updated.",
                            )
                            return t["id"]
            except Exception:
                pass

            # Not found on OpenWA — create it there from Frappe doc
            frappe.log_error(
                title="OpenWA: Template missing, re-syncing",
                message=f"Template '{self.template}' not found on OpenWA. Creating it now.",
            )
            tmpl_doc = frappe.get_doc("WhatsApp Templates", self.template)
            old_id = tmpl_doc.openwa_template_id
            tmpl_doc._sync_to_openwa()
            frappe.db.commit()
            # Re-read from DB — _sync_to_openwa uses db.set_value which
            # doesn't update the in-memory doc attribute
            new_id = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
            if new_id and new_id != old_id:
                frappe.log_error(
                    title="OpenWA: Template re-synced",
                    message=f"Template '{self.template}' created on OpenWA → ID {new_id}",
                )
                return new_id
            else:
                frappe.log_error(
                    title="OpenWA: Template re-sync failed",
                    message=(
                        f"Template '{self.template}' — _sync_to_openwa did not produce a new ID. "
                        f"Old ID was {old_id}. The session may be dead."
                    ),
                )
                return None
        except Exception as e:
            frappe.log_error(
                title="OpenWA: Template ID recovery failed",
                message=str(e),
            )
        return None

    # ------------------------------------------------------------------
    # Template translation
    # ------------------------------------------------------------------

    def _translate_template_payload(self) -> str:
        """Convert structured Meta templates into substituted OpenWA strings."""
        try:
            template_doc = frappe.get_doc("WhatsApp Templates", self.template)
            body_text = template_doc.template
        except Exception:
            body_text = frappe.db.get_value(
                "WhatsApp Templates", self.template, "template"
            ) or ""

        params: list[str] = []
        if self.body_param:
            try:
                bp = json.loads(self.body_param)
                if isinstance(bp, dict):
                    params = list(bp.values())
                elif isinstance(bp, list):
                    params = bp
            except (json.JSONDecodeError, TypeError):
                pass
        elif self.template_parameters:
            params = json.loads(self.template_parameters)

        for i, val in enumerate(params, 1):
            body_text = body_text.replace(f"{{{{{i}}}}}", str(val))

        return body_text


@frappe.whitelist()
def send_document_pdf(
    to: str,
    reference_doctype: str,
    reference_name: str,
    print_format: str | None = None,
    filename: str | None = None,
    caption: str | None = None,
    letterhead: str | None = None,
    language: str | None = None,
    settings: str | None = None,
) -> str:
    """Create a WhatsApp Message that sends the reference document as a PDF.

    The PDF is rendered at send time (in the outbox worker) from
    ``openwa_render_doctype`` / ``openwa_render_name`` (which default to the
    passed reference) using ``openwa_print_format``, so nothing large is
    stored on the WhatsApp Message doc and retries regenerate a current copy.

    When the CRM app is installed, the reference is linked to the CRM record
    that matches the recipient number (Contact, or its CRM Lead / CRM Deal), so
    the message appears in the CRM thread — while the PDF still renders the
    passed document. If the number matches no CRM record (or sending already
    from a CRM doctype), the reference stays the passed document.

    Args:
        to: Recipient phone number (any format — normalized to ``<num>@c.us``).
        reference_doctype: DocType of the document to render (e.g. "Sales Invoice").
        reference_name: Name of the document to render.
        print_format: Print Format name (default "Standard").
        filename: Delivered filename (default ``<reference_name>.pdf``).
        caption: Optional caption text (default "").
        letterhead: Letter Head name (default: resolved at render time).
        language: Print language code (default: resolved at render time).
        settings: JSON string of dynamic print settings (e.g. ``{"compact_item_print": 1}``).

    Returns:
        str: The created WhatsApp Message name.

    Raises:
        frappe.ValidationError: If the document cannot be created.
    """
    doc = frappe.get_doc({
        "doctype": "WhatsApp Message",
        "to": to,
        "type": "Outgoing",
        "message_type": "Manual",
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "content_type": "document",
        "message": caption or "",
        "openwa_send_pdf": 1,
        "openwa_print_format": print_format or "Standard",
        "openwa_pdf_filename": filename or f"{reference_name}.pdf",
        "openwa_letterhead": letterhead or None,
        "openwa_language": language or None,
        "openwa_print_settings": settings or None,
        "openwa_render_doctype": reference_doctype,
        "openwa_render_name": reference_name,
    })
    crm_reference = _resolve_crm_reference(to, reference_doctype)
    if crm_reference:
        doc.reference_doctype, doc.reference_name = crm_reference
    doc.save(ignore_permissions=True)
    return doc.name


def _resolve_crm_reference(to: str, reference_doctype: str) -> tuple[str, str] | None:
    """Return the CRM doc (Contact, CRM Lead or CRM Deal) matching ``to``, if any.

    Links an outgoing PDF message to the CRM thread that already exists for the
    recipient's number. The PDF is rendered from the render fields, so the
    reference can safely point at the CRM record without losing the source
    document. Best-effort: any failure leaves the reference untouched.
    """
    if reference_doctype in ("CRM Deal", "CRM Lead", "Contact"):
        return None
    if "crm" not in frappe.get_installed_apps():
        return None
    try:
        from crm.integrations.api import get_contact_lead_or_deal_from_number

        docname, doctype = get_contact_lead_or_deal_from_number(to)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "OpenWA: failed to resolve CRM reference for send_document_pdf",
        )
        return None
    if docname and doctype:
        return (doctype, docname)
    return None
