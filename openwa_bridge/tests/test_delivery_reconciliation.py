"""Regression coverage for recipient matching and unresolved outbound sends."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import requests


class TestRecipientReconciliation(TestCase):
	def setUp(self):
		self.frappe = self.enterContext(patch("openwa_bridge.inbound.frappe"))
		self.messages = []
		self.frappe.db.get_value.side_effect = self._get_value
		self.frappe.get_all.side_effect = lambda doctype, filters, **kwargs: (
			[SimpleNamespace(name="OUTBOX-1", status="Pending")]
			if filters["whatsapp_message"] == "MATCH"
			else []
		)

	def _get_value(self, doctype, filters, field=None, **kwargs):
		# Model both the old suffix lookup and exact comparisons over fixtures.
		if isinstance(filters, str):
			return next(row.get(field) for row in self.messages if row["name"] == filters)
		rows = self.messages
		if "message_id" in filters and not isinstance(filters["message_id"], tuple):
			rows = [row for row in rows if row["message_id"] == filters["message_id"]]
		else:
			rows = [row for row in rows if not row["message_id"]]
		if "to" in filters:
			operator, value = filters["to"]
			rows = [
				row
				for row in rows
				if (row["to"] in value if operator == "in" else row["to"].endswith(value.lstrip("%")))
			]
		return rows[0]["name"] if rows else None

	def _message(self, name, recipient):
		return {"name": name, "to": recipient, "message_id": "", "status": "Pending"}

	def test_sent_event_rejects_partial_recipient(self):
		from openwa_bridge.inbound import _handle_message_sent

		self.messages = [self._message("UNRELATED", "9912345")]
		_handle_message_sent({"id": "sent-id", "to": "12345@c.us"})
		self.frappe.db.set_value.assert_not_called()

	def test_sent_event_selects_complete_recipient_even_when_older(self):
		from openwa_bridge.inbound import _handle_message_sent

		for recipient in ("12345", "12345@c.us", "12345@s.whatsapp.net", "12345@lid"):
			with self.subTest(recipient=recipient):
				self.frappe.db.set_value.reset_mock()
				self.messages = [
					self._message("UNRELATED", "9912345"),
					self._message("MATCH", recipient),
				]
				_handle_message_sent({"id": "sent-id", "to": "12345@c.us"})
				self.frappe.db.set_value.assert_called_once_with(
					"WhatsApp Message", "MATCH", "message_id", "sent-id"
				)

	def test_ack_rejects_partial_recipient_before_outbox_reconciliation(self):
		from openwa_bridge.inbound import _handle_status_update

		self.messages = [self._message("UNRELATED", "9912345")]
		_handle_status_update({"messageId": "ack-id", "status": "delivered", "chatId": "12345@c.us"})
		self.frappe.db.set_value.assert_not_called()
		self.frappe.get_all.assert_not_called()

	def test_phone_ack_assigns_id_and_reconciles_outbox(self):
		from openwa_bridge.inbound import _handle_status_update

		for recipient in ("12345", "12345@c.us", "12345@s.whatsapp.net"):
			with self.subTest(recipient=recipient):
				self.frappe.db.set_value.reset_mock()
				self.messages = [self._message("MATCH", recipient)]
				_handle_status_update({"messageId": "ack-id", "status": "read", "chatId": "12345@c.us"})
				self.frappe.db.set_value.assert_any_call("WhatsApp Message", "MATCH", "message_id", "ack-id")
				self.frappe.db.set_value.assert_any_call("OpenWA Outbox", "OUTBOX-1", {"status": "Sent"})

	def test_group_ack_assigns_id_and_reconciles_outbox(self):
		from openwa_bridge.inbound import _handle_status_update

		for group in ("12345-67890", "120363123456789"):
			for stored in (group, f"{group}@g.us"):
				for message_id in ("ack-id", "opaque_ack_id", f"true_{group}@g.us_ABC"):
					with self.subTest(group=group, stored=stored, message_id=message_id):
						self.frappe.db.set_value.reset_mock()
						self.messages = [self._message("MATCH", stored)]
						_handle_status_update(
							{
								"messageId": message_id,
								"status": "delivered",
								"chatId": f"{group}@g.us",
							}
						)
						self.frappe.db.set_value.assert_any_call(
							"WhatsApp Message", "MATCH", "message_id", message_id
						)
						self.frappe.db.set_value.assert_any_call(
							"OpenWA Outbox", "OUTBOX-1", {"status": "Sent"}
						)

	def test_group_ack_uses_legacy_encoded_jid_when_chat_is_absent(self):
		from openwa_bridge.inbound import _find_whatsapp_message

		self.messages = [self._message("MATCH", "12345-67890@g.us")]
		self.assertEqual(_find_whatsapp_message("true_12345-67890@g.us_ABC"), "MATCH")

	def test_normalized_group_ack_matches_full_jid(self):
		from openwa_bridge.inbound import _find_whatsapp_message

		self.messages = [self._message("MATCH", "12345-67890@g.us")]
		self.assertEqual(_find_whatsapp_message("ack-id", chat_id="12345-67890"), "MATCH")

	def test_non_numeric_individual_recipient_is_rejected(self):
		from openwa_bridge.inbound import _find_whatsapp_message

		self.assertIsNone(_find_whatsapp_message("ack-id", chat_id="not-a-phone@c.us"))
		self.frappe.db.set_value.assert_not_called()


class TestDynamicHeaderDelivery(TestCase):
	def setUp(self):
		self.frappe = self.enterContext(patch("openwa_bridge.tasks.frappe"))
		self.session = self.enterContext(patch("openwa_bridge.utils._http_session"))
		self.enterContext(patch("openwa_bridge.utils.render_doc_as_image", return_value=b"\xff\xd8\xffimage"))
		self.enterContext(patch("openwa_bridge.utils.get_api_key", return_value="local-test-key"))
		self.enterContext(patch("openwa_bridge.tasks.get_api_key", return_value="local-test-key"))
		self.enterContext(
			patch("openwa_bridge.tasks.get_account_setting", side_effect=lambda a, f, d=None: d)
		)
		breaker = self.enterContext(patch("openwa_bridge.utils.OpenWACircuitBreaker"))
		breaker.return_value.is_open.return_value = False
		self.frappe.cache.return_value.get_value.return_value = None
		self.enterContext(patch("frappe_whatsapp.utils.format_number", side_effect=lambda number: number))
		self.fail = self.enterContext(patch("openwa_bridge.tasks._fail_outbox"))
		self.msg = MagicMock(
			template="TMPL-1",
			to="12345",
			use_template=True,
			template_parameters=None,
			reference_doctype="Sales Invoice",
			reference_name="INV-1",
			openwa_render_doctype=None,
			openwa_render_name=None,
			message_id="",
			content_type="text",
			attach=None,
		)
		self.msg.name = "MSG-1"
		self.template = SimpleNamespace(
			name="TMPL-1",
			openwa_dynamic_header=True,
			openwa_print_format="Standard",
			openwa_include_letterhead=False,
			header=None,
			template="Invoice attached",
			footer=None,
		)
		self.account = MagicMock()
		self.account.name = "ACCOUNT-1"
		self.account.get.side_effect = lambda key, default=None: {
			"openwa_base_url": "http://openwa.test",
			"openwa_session_id": "session-1",
			"openwa_enabled": True,
		}.get(key, default)
		self.enterContext(patch("openwa_bridge.tasks.get_cached_account", return_value=self.account))
		self.outbox = SimpleNamespace(
			name="OUTBOX-1",
			status="Pending",
			attempts=0,
			next_retry_at=None,
			scheduled_at=None,
			whatsapp_message="MSG-1",
			whatsapp_account="ACCOUNT-1",
		)
		self.frappe.get_doc.side_effect = lambda doctype, *args, **kwargs: {
			"WhatsApp Templates": self.template,
			"WhatsApp Message": self.msg,
			"OpenWA Outbox": self.outbox,
			"Sales Invoice": SimpleNamespace(),
		}[doctype]
		self.session.get.return_value = self._response(200, {"status": "ready", "phone": "12345"})

	def _response(self, status, body):
		response = requests.Response()
		response.status_code = status
		import json

		response._content = json.dumps(body).encode()
		response.headers["Content-Type"] = "application/json"
		return response

	def test_conflicts_retry_same_key_before_completing_outbox(self):
		from openwa_bridge.tasks import process_outbox_entry

		for code in ("IDEMPOTENCY_KEY_IN_PROGRESS", "IDEMPOTENCY_OUTCOME_UNKNOWN"):
			with self.subTest(code=code):
				self.frappe.db.set_value.reset_mock()
				self.session.post.reset_mock()
				self.fail.reset_mock()
				self.session.post.side_effect = [
					self._response(409, {"code": code, "message": "Unresolved send"}),
					self._response(201, {"messageId": "image-id"}),
				]
				process_outbox_entry("OUTBOX-1")
				self.fail.assert_called_once()
				self.assertFalse(
					any(
						call.args[:3] == ("OpenWA Outbox", "OUTBOX-1", {"status": "Sent"})
						for call in self.frappe.db.set_value.call_args_list
					)
				)
				self.msg._send_via_openwa.assert_not_called()

				process_outbox_entry("OUTBOX-1")
				keys = [
					call.kwargs["headers"]["Idempotency-Key"] for call in self.session.post.call_args_list
				]
				self.assertEqual(keys, ["frappe-MSG-1-hdr", "frappe-MSG-1-hdr"])
				self.frappe.db.set_value.assert_any_call("OpenWA Outbox", "OUTBOX-1", {"status": "Sent"})
				self.msg._send_via_openwa.assert_not_called()

	def test_success_without_message_id_stays_unresolved(self):
		from openwa_bridge.tasks import process_outbox_entry

		self.session.post.return_value = self._response(200, {})
		process_outbox_entry("OUTBOX-1")
		self.fail.assert_called_once()
		self.assertIn("no messageId", self.fail.call_args.args[1])
		self.msg._send_via_openwa.assert_not_called()
		self.assertFalse(
			any(
				call.args[:3] == ("OpenWA Outbox", "OUTBOX-1", {"status": "Sent"})
				for call in self.frappe.db.set_value.call_args_list
			)
		)

	def test_definite_image_failure_uses_text_fallback(self):
		from openwa_bridge.tasks import _send_outbox_message

		for status in (400, 500):
			with self.subTest(status=status):
				self.msg._send_via_openwa.reset_mock()
				self.session.post.return_value = self._response(status, {"message": "Send failed"})
				_send_outbox_message(self.msg, self.account, self.outbox)
				self.msg._send_via_openwa.assert_called_once_with(self.account, {})

	def test_confirmed_image_skips_text_fallback(self):
		from openwa_bridge.tasks import _send_outbox_message

		self.session.post.return_value = self._response(201, {"key": {"id": "image-id"}})
		_send_outbox_message(self.msg, self.account, self.outbox)
		self.frappe.db.set_value.assert_any_call("WhatsApp Message", "MSG-1", "message_id", "image-id")
		self.msg._send_via_openwa.assert_not_called()


class TestMediaReplyIdempotency(TestCase):
	@patch("openwa_bridge.whatsapp_message.frappe")
	@patch("openwa_bridge.whatsapp_message._send_typing_indicator")
	@patch("openwa_bridge.whatsapp_message._http_session")
	@patch("openwa_bridge.whatsapp_message.get_api_key", return_value="local-test-key")
	@patch("openwa_bridge.whatsapp_message.format_number", return_value="12345")
	def test_follow_up_key_is_distinct_and_stable(
		self, mock_number, mock_key, session, mock_typing, mock_frappe
	):
		from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage

		for name in ("MSG-1", "X" * 255):
			with self.subTest(name=name):
				session.post.reset_mock()
				session.post.side_effect = [
					MagicMock(status_code=201, json=lambda: {"messageId": "image-id"}),
					MagicMock(status_code=201),
				] * 2
				msg = SimpleNamespace(
					name=name,
					to="12345",
					message_id="",
					content_type="image",
					message="Caption",
					use_template=False,
					is_reply=True,
					reply_to_message_id="original-id",
					reference_doctype=None,
					reference_name=None,
					_ensure_session_ready=MagicMock(),
				)
				account = {"openwa_base_url": "http://openwa.test", "openwa_session_id": "session-1"}
				for _ in range(2):
					OverrideWhatsAppMessage._send_via_openwa(
						msg, account, {"image": {"link": "https://example.test/image"}}
					)
				calls = session.post.call_args_list
				keys = [call.kwargs["headers"]["Idempotency-Key"] for call in calls]
				self.assertNotEqual(keys[0], keys[1])
				self.assertEqual(keys[:2], keys[2:])
				self.assertTrue(calls[0].args[0].endswith("/messages/send-image"))
				self.assertTrue(calls[1].args[0].endswith("/messages/reply"))
				self.assertEqual(calls[1].kwargs["json"]["quotedMessageId"], "image-id")
				self.assertTrue(all(len(key) <= 255 for key in keys))


class TestStructuredValidationErrors(TestCase):
	def test_non_string_errors_preserve_http_error_details_and_response(self):
		from openwa_bridge.utils import openwa_api

		bodies = (
			{"message": ["chatId is required", "text must be a string"]},
			{"error": {"message": ["chatId is required", {"text": "invalid"}]}},
			{"message": {"field": "chatId"}},
			{"error": {"message": 42}},
			{"error": {"message": True}},
		)
		with patch("openwa_bridge.utils.get_api_key", return_value="local-test-key"):
			with patch("openwa_bridge.utils._http_session.request") as request:
				for body in bodies:
					with self.subTest(body=body):
						response = MagicMock(status_code=400, json=lambda: body, text="")
						request.return_value = response
						with self.assertRaises(requests.HTTPError) as caught:
							openwa_api(
								{"openwa_base_url": "http://openwa.test", "openwa_session_id": "s1"},
								"POST",
								"/messages/send-text",
								{},
							)
						self.assertIs(caught.exception.response, response)
						self.assertIn("OpenWA HTTP 400", str(caught.exception))
						self.assertTrue(
							any(detail in str(caught.exception) for detail in ("chatId", "42", "true"))
						)

	def test_empty_body_fallback_and_truncation(self):
		from openwa_bridge.utils import openwa_error_message

		response = MagicMock(status_code=400, json=lambda: {}, text="  ")
		self.assertTrue(openwa_error_message(response).endswith("(no body)"))
		response.json = MagicMock(return_value={"message": ["x" * 1100]})
		body = openwa_error_message(response).split(" — ", 1)[1]
		self.assertEqual(len(body), 1000)
