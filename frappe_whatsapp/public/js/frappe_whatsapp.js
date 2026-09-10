$(document).on('app_ready', function () {
	// waiting for page to load completely
	frappe.router.on("change", () => {
		var route = frappe.get_route();
		// all form's menu add the 'Send To Telegram' funcationality
		if (route && route[0] == "Form") {
			frappe.ui.form.on(route[1], {
				refresh: function (frm) {
					frm.page.add_menu_item(__("Send To Whatsapp"), function () {
						var user_name = frappe.user.name;
						var user_full_name = frappe.session.user_fullname;
						var reference_doctype = frm.doctype;
						var reference_name = frm.docname;
					var dialog = new frappe.ui.Dialog({
						'fields': [
							{ 'fieldname': 'ht', 'fieldtype': 'HTML' },
							{ 'label': 'Send as PDF', 'fieldname': 'send_pdf', 'fieldtype': 'Check',
								change() {
									var is_pdf = dialog.get_value('send_pdf');
									// Template only required for template sends
									var tpl = dialog.fields_dict.template;
									tpl.df.reqd = is_pdf ? 0 : 1;
									tpl.refresh();
									// Show/hide PDF-only fields
									dialog.fields_dict.print_format.toggle(is_pdf);
									dialog.fields_dict.pdf_filename.toggle(is_pdf);
								}},
							{ 'label': 'Select Template', 'fieldname': 'template', 'reqd': 1, 'fieldtype': 'Link', 'options': 'WhatsApp Templates' },
							{ 'label': 'Print Format', 'fieldname': 'print_format', 'fieldtype': 'Link', 'options': 'Print Format', 'default': 'Standard' },
							{ 'label': 'PDF Filename', 'fieldname': 'pdf_filename', 'fieldtype': 'Data' },
							{ 'label': 'Send to', 'fieldname': 'contact', 'reqd': 1, 'fieldtype': 'Link', 'options': 'Contact', change() {
			                let contact_name = dialog.get_value('contact');
			                if (contact_name) {
			                    frappe.call({
			                        method: 'frappe.client.get_value',
			                        args: {
			                            doctype: 'Contact',
			                            filters: { name: contact_name },
			                            fieldname: ['mobile_no']
			                        },
			                        callback: function (r) {
			                            if (r.message) {
			                                dialog.set_value('mobile_no', r.message.mobile_no);
			                            } else {
			                                dialog.set_value('mobile_no', '');
			                                frappe.msgprint('Mobile number not found for the selected contact.');
			                            }
			                        }
			                    });
			                } else {
			                    dialog.set_value('mobile_no', '');
			                }
			            }},
							{ 'label': 'Mobile no', 'fieldname': 'mobile_no', 'fieldtype': 'Data' },

						],
						'primary_action_label': 'Send',
						'title': 'Send a Telegram Message',
						primary_action: function () {
							var values = dialog.get_values();
							if (values) {
								var space = "\n" + "\n";
								// var the_message = "From : " + user_full_name + space + values.subject + space + values.message;

								if (!values.mobile_no) {
									frappe.msgprint(__("Please select a contact that has a mobile number."));
									return;
								}

								if (values.send_pdf) {
									// Send the current document as a PDF via OpenWA bridge
									frappe.call({
										method: "openwa_bridge.whatsapp_message.send_document_pdf",
										args: {
											to: values.mobile_no,
											reference_doctype: frm.doc.doctype,
											reference_name: frm.doc.name,
											print_format: values.print_format,
											filename: values.pdf_filename,
										},
										freeze: true,
										callback: (r) => {
											if (r.exc) {
												frappe.msgprint(__("Failed to send PDF. Check the Error Log."));
												return;
											}
											frappe.msgprint(__("Successfully Sent to: " + values.mobile_no));
											dialog.hide();
										}
									});
								} else {
									// send whatsapp msg (template)
									frappe.call({
										method: "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.send_template",
										args: {
											to: values.mobile_no,
											template: values.template,
											reference_doctype: frm.doc.doctype,
											reference_name: frm.doc.name
										},
										freeze: true,
										callback: (r) => {
											if (r.exc) {
												frappe.msgprint(__("Failed to send. Check the Error Log."));
												return;
											}
											frappe.msgprint(__("Successfully Sent to: " + values.mobile_no));
											dialog.hide();
										}
									});
								}

								// add comment
								var comment_message = values.send_pdf
									? 'To : ' + values.mobile_no + space + 'PDF:' + (values.pdf_filename || (frm.doc.name + '.pdf')) + ' | Print Format:' + values.print_format
									: 'To : ' + values.mobile_no + space + "Whatsapp Template:" + values.template;
								frappe.call({
									method: "frappe.desk.form.utils.add_comment",
									args: {
										reference_doctype: reference_doctype,
										reference_name: reference_name,
										content: comment_message,
										comment_by: frappe.session.user_fullname,
										comment_email: frappe.session.user
									},
								});
							}

						},
						no_submit_on_enter: true,
					});
					let template = dialog.fields_dict.template;
	                    if (template) {
	                        // Dynamically set the get_query function for the user field
	                        template.get_query = function() {
	                            return {
	                                filters: { "for_doctype": frm.doc.doctype },
	                                doctype: "WhatsApp Templates"
	                            };
	                        };
	                        // Refresh the field to apply the new query
	                        template.refresh();
	                    }
					// Hide PDF-only fields initially
					dialog.fields_dict.print_format.toggle(false);
					dialog.fields_dict.pdf_filename.toggle(false);
					dialog.show();
					});
				}
			});
		};
	})
});