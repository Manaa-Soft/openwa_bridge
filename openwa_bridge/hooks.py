app_name = "openwa_bridge"
app_title = "OpenWA Bridge"
app_publisher = "Manaa Soft"
app_description = "Seamlessly integrates the OpenWA Gateway with the core frappe_whatsapp module for reliable WhatsApp routing."
app_email = "manaamnaa2018@gmail.com"
app_license = "gpl-3.0"
app_icon = "/assets/openwa_bridge/icons/openwa_bridge.svg"

# Send non-GET requests for this app's endpoints as native `application/json`
# bodies instead of form-encoded, per-key JSON-stringified values.
# use_json_request_body = True

# Apps
# ------------------

required_apps = ["frappe_whatsapp"]

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "OpenWA Bridge",
		"logo": "/assets/openwa_bridge/images/openwa_bridge.svg",
		"title": "OpenWA Bridge",
		"route": "/desk/whatsapp"
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/openwa_bridge/css/openwa_bridge.css"
app_include_js = "/assets/openwa_bridge/js/openwa_bridge.js"

# include js, css files in header of web template
# web_include_css = "/assets/openwa_bridge/css/openwa_bridge.css"
# web_include_js = "/assets/openwa_bridge/js/openwa_bridge.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "openwa_bridge/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"WhatsApp Account": "public/js/whatsapp_account.js"
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "openwa_bridge/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "openwa_bridge.utils.jinja_methods",
# 	"filters": "openwa_bridge.utils.jinja_filters"
# }

# Installation
# ------------

before_install = "openwa_bridge.install.before_install"
after_install = "openwa_bridge.install.after_install"

# Runs after every `bench migrate` — seeds/backfills Desktop Icon rows for the
# fork's Desktop Icons desk page (see install.after_migrate).
after_migrate = "openwa_bridge.install.after_migrate"

# Uninstallation
# ------------

before_uninstall = "openwa_bridge.uninstall.before_uninstall"
# after_uninstall = "openwa_bridge.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "openwa_bridge.utils.before_app_install"
# after_app_install = "openwa_bridge.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "openwa_bridge.utils.before_app_uninstall"
# after_app_uninstall = "openwa_bridge.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "openwa_bridge.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "openwa_bridge.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"WhatsApp Account": {
		"validate": "openwa_bridge.whatsapp_account.on_account_validate",
		"on_update": "openwa_bridge.whatsapp_account.on_account_update",
		"on_trash": "openwa_bridge.whatsapp_account.on_account_trash"
	}
}

# Scheduled Tasks
# ---------------
# Periodic session health check — restarts disconnected OpenWA sessions.

scheduler_events = {
	"hourly": [
		"openwa_bridge.tasks.hourly",
	],
	"daily": [
		"openwa_bridge.tasks.daily",
		"openwa_bridge.tasks.cleanup_old_outbox",
	],
	"all": [
		"openwa_bridge.tasks.process_pending_outbox",
		"openwa_bridge.tasks.reconcile_stale_outbox",
	],
}

# Testing
# -------

# before_tests = "openwa_bridge.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "openwa_bridge.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "openwa_bridge.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "openwa_bridge.task.get_dashboard_data"
# }

# Override DocType Classes
# -------------------------
# Routes outbound messages through OpenWA when the account is configured for it.

override_doctype_class = {
	"WhatsApp Message": "openwa_bridge.whatsapp_message.OverrideWhatsAppMessage",
	"WhatsApp Templates": "openwa_bridge.whatsapp_templates.OverrideWhatsAppTemplates",
	"WhatsApp Notification": "openwa_bridge.whatsapp_notification.OverrideWhatsAppNotification",
}

# Fixtures
# --------
# Track custom fields injected into upstream DocTypes so they survive migrations.

fixtures = [
	{
		"dt": "Custom Field",
		"filters": [["dt", "in", ["WhatsApp Account", "WhatsApp Templates", "WhatsApp Notification", "WhatsApp Message"]]],
	},
	{
		"dt": "Property Setter",
		"filters": [["doc_type", "=", "WhatsApp Account"]],
	}
]

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["openwa_bridge.utils.before_request"]
# after_request = ["openwa_bridge.utils.after_request"]

# Job Events
# ----------
# before_job = ["openwa_bridge.utils.before_job"]
# after_job = ["openwa_bridge.utils.after_job"]

# after_file_upload = ["openwa_bridge.utils.after_file_upload"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"openwa_bridge.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
