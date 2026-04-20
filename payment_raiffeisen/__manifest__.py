{
    "name": "Payment Provider: Raiffeisen RaiAccept",
    "version": "19.0.1.5.0",
    "category": "Accounting/Payment Providers",
    "sequence": 370,
    "summary": "Accept online card payments via Raiffeisen Bank RaiAccept gateway (Serbia & SEE).",
    "description": " ",  # Non-empty to avoid loading README as description.
    "author": "Adria Mart",
    "website": "https://adriamart.rs",
    "license": "LGPL-3",
    "price": 0,
    "currency": "EUR",
    "images": ["static/description/banner.png"],
    "depends": [
        "payment",
    ],
    "data": [
        "views/redirect_form_templates.xml",
        "views/payment_provider_views.xml",
        "data/payment_provider_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "application": False,
    "installable": True,
    "auto_install": False,
}
