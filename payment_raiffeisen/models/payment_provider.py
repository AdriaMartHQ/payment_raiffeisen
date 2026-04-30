import logging
import re
import unicodedata
from urllib.parse import urljoin

import requests

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# ── RaiAccept API Constants ──────────────────────────────────────────
RAIACCEPT_AUTH_URL = "https://authenticate.raiaccept.com"
RAIACCEPT_AUTH_CLIENT_ID = "kr2gs4117arvbnaperqff5dml"
RAIACCEPT_API_BASE = "https://trapi.raiaccept.com"

# ISO 3166-1 alpha-2 → alpha-3 (comprehensive)
_COUNTRY_ISO3 = {
    # SEE / CEE (primary market)
    "AL": "ALB", "AT": "AUT", "BA": "BIH", "BG": "BGR", "CH": "CHE",
    "CZ": "CZE", "DE": "DEU", "GR": "GRC", "HR": "HRV", "HU": "HUN",
    "ME": "MNE", "MK": "MKD", "PL": "POL", "RO": "ROU", "RS": "SRB",
    "SI": "SVN", "SK": "SVK", "XK": "XKX",
    # Western Europe
    "BE": "BEL", "DK": "DNK", "ES": "ESP", "FI": "FIN", "FR": "FRA",
    "GB": "GBR", "IE": "IRL", "IT": "ITA", "LU": "LUX", "NL": "NLD",
    "NO": "NOR", "PT": "PRT", "SE": "SWE",
    # Eastern Europe / CIS
    "BY": "BLR", "EE": "EST", "LT": "LTU", "LV": "LVA", "MD": "MDA",
    "RU": "RUS", "UA": "UKR",
    # Middle East / Asia / Americas (common)
    "AE": "ARE", "AU": "AUS", "BR": "BRA", "CA": "CAN", "CN": "CHN",
    "IL": "ISR", "IN": "IND", "JP": "JPN", "KR": "KOR", "MX": "MEX",
    "NZ": "NZL", "SA": "SAU", "SG": "SGP", "TR": "TUR", "US": "USA",
    "ZA": "ZAF",
}


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(
        selection_add=[("raiffeisen", "Raiffeisen RaiAccept")],
        ondelete={"raiffeisen": "set default"},
    )

    # ── Credential fields (no required_if_provider — validated below) ─
    raiffeisen_api_username = fields.Char(
        string="API Username",
        copy=False,
        groups="base.group_system",
    )
    raiffeisen_api_password = fields.Char(
        string="API Password",
        copy=False,
        groups="base.group_system",
    )
    raiffeisen_sandbox_username = fields.Char(
        string="Sandbox Username",
        copy=False,
        groups="base.group_system",
    )
    raiffeisen_sandbox_password = fields.Char(
        string="Sandbox Password",
        copy=False,
        groups="base.group_system",
    )
    raiffeisen_gateway_currency = fields.Selection(
        [("RSD", "RSD"), ("EUR", "EUR")],
        string="Gateway Currency",
        default="RSD",
        help="Currency used by the RaiAccept payment gateway for "
             "settlement. Must match your merchant account currency.",
    )
    raiffeisen_currency_rate = fields.Float(
        string="Currency Rate",
        default=1.0,
        digits=(12, 6),
        help="Exchange rate: store_amount × rate = gateway_amount. "
             "Set to 1.0 if store and gateway currencies are the same.",
    )

    # ── Webhook security (added 19.0.1.6.0) ──────────────────────────
    # Defense-in-depth for /payment/raiffeisen/webhook. Defaults are
    # safe-by-default conservative: empty IP allowlist = log-only,
    # signature mode "off" = no enforcement until merchant captures a
    # real prod webhook payload and confirms the wire format with
    # RaiAccept (the legacy Shop_Gateway_Interface_Token_eng.pdf
    # describes form-POST + RSA, but the modern RaiAccept Documentation
    # Portal serves a JSON webhook whose signature scheme has to be
    # verified against an actual payload before we hardcode the
    # canonicalization order).

    raiffeisen_webhook_allowed_ips = fields.Char(
        string="Webhook Allowed Source IPs",
        copy=False,
        groups="base.group_system",
        help="Comma-separated list of trusted source IPs from which "
             "the gateway may POST to the webhook URL. When set, "
             "requests from any other IP are rejected with HTTP 403. "
             "Leave empty to disable the IP allowlist (LOG ONLY — the "
             "remote_addr is logged but not enforced).\n\n"
             "Per the legacy Shop_Gateway_Interface doc the UPC test "
             "server posts from 195.85.198.16 and prod from "
             "195.85.198.15. Confirm the actual RaiAccept Serbia "
             "egress IPs with pos-ecommerce@raiffeisenbank.rs before "
             "hard-enforcing in production.",
    )
    raiffeisen_webhook_signature_mode = fields.Selection(
        [
            ("off", "Off (log only)"),
            ("warn", "Warn (log invalid signatures, accept anyway)"),
            ("enforce", "Enforce (reject invalid signatures with HTTP 403)"),
        ],
        string="Webhook Signature Verification",
        default="off",
        copy=False,
        groups="base.group_system",
        help="Controls whether the webhook signature is verified.\n\n"
             "off (default) — relies on the gateway re-fetch in "
             "_apply_updates to verify amount + currency authoritatively. "
             "Safe baseline.\n\n"
             "warn — verify the signature when present, log a warning "
             "on mismatch but still process. Use during the first prod "
             "week to capture real-payload mismatches without breaking.\n\n"
             "enforce — reject any webhook with missing or invalid "
             "signature. Switch to this only after capturing real prod "
             "payloads and confirming the canonicalization scheme.",
    )
    raiffeisen_webhook_server_cert_pem = fields.Text(
        string="Gateway Public Certificate (PEM)",
        copy=False,
        groups="base.group_system",
        help="The bank's public X.509 certificate (PEM format) used to "
             "verify webhook signatures. Issued by RaiAccept Onboarding "
             "with the merchant credential pack — corresponds to the "
             "`test-server.cert` (sandbox) / production server cert "
             "files in the integration ZIP. Populate this field before "
             "switching `Webhook Signature Verification` away from "
             "`off`.",
    )

    # ── State-aware credential validation ────────────────────────────

    @api.constrains("state", "code",
                    "raiffeisen_api_username", "raiffeisen_api_password",
                    "raiffeisen_sandbox_username", "raiffeisen_sandbox_password")
    def _check_raiffeisen_credentials(self):
        for provider in self.filtered(lambda p: p.code == "raiffeisen"):
            if provider.state == "enabled":
                if not provider.raiffeisen_api_username \
                        or not provider.raiffeisen_api_password:
                    raise ValidationError(
                        _("Production API credentials are required when "
                          "the Raiffeisen provider is enabled.")
                    )
            elif provider.state == "test":
                if not provider.raiffeisen_sandbox_username \
                        or not provider.raiffeisen_sandbox_password:
                    raise ValidationError(
                        _("Sandbox credentials are required when "
                          "the Raiffeisen provider is in test mode.")
                    )

    @api.constrains("raiffeisen_currency_rate")
    def _check_raiffeisen_currency_rate(self):
        for provider in self.filtered(lambda p: p.code == "raiffeisen"):
            if provider.raiffeisen_currency_rate <= 0:
                raise ValidationError(
                    _("Currency rate must be a positive number.")
                )

    # ── Feature support ──────────────────────────────────────────────

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == "raiffeisen").update({
            "support_refund": "partial",
        })

    # ── Webhook security helpers (19.0.1.6.0) ────────────────────────

    def _raiffeisen_webhook_allowed_ip_list(self):
        """Parse the comma-separated allowed-IP field into a set.

        Whitespace and empty entries are dropped. Returns an empty
        set when the field is unset (caller treats empty set as
        "log only, no enforcement").
        """
        self.ensure_one()
        raw = self.raiffeisen_webhook_allowed_ips or ""
        return {
            ip.strip() for ip in raw.split(",")
            if ip.strip()
        }

    def _raiffeisen_webhook_ip_allowed(self, remote_ip):
        """Return (allowed: bool, reason: str) for a webhook source IP.

        Returns (True, "no allowlist configured") when the merchant
        hasn't set any IPs (log-only mode). Returns (True, "matched")
        when remote_ip is in the configured set, else (False, "...").
        Caller decides what to do with the booleans — the controller
        rejects on False; the verification mode field also influences
        the final decision.
        """
        self.ensure_one()
        allowed = self._raiffeisen_webhook_allowed_ip_list()
        if not allowed:
            return (True, "no allowlist configured (log-only)")
        if remote_ip in allowed:
            return (True, "ip in allowlist")
        return (False, f"ip {remote_ip} not in allowlist")

    def _raiffeisen_verify_webhook_signature(self, payload, signature):
        """Verify a webhook payload signature against the gateway cert.

        Currently a stub returning (None, "signature scheme not yet
        implemented") because the JSON webhook signature canonicalization
        used by modern RaiAccept (Serbia branding) needs to be confirmed
        against real prod payloads — the legacy 2019 UPC documentation
        describes a form-POST + RSA scheme that does not match the
        current JSON shape.

        Roadmap for filling in this method (after prod cutover):

        1. Capture 5-10 real webhook POST bodies from prod (signed by
           the bank, served from ~195.85.198.0/24 or whatever IP range
           RaiAccept Serbia actually uses).
        2. Cross-reference with the canonicalization order specified in
           the RaiAccept Documentation Portal (the link sent in
           Raiffeisen 2026-04-24 11:56 production-approval email).
        3. Implement the verifier here using `cryptography` library:

               from cryptography.hazmat.primitives import hashes, serialization
               from cryptography.hazmat.primitives.asymmetric import padding

               cert = x509.load_pem_x509_certificate(
                   self.raiffeisen_webhook_server_cert_pem.encode()
               )
               public_key = cert.public_key()
               try:
                   public_key.verify(
                       base64.b64decode(signature),
                       canonical_payload_bytes,
                       padding.PKCS1v15(),
                       hashes.SHA1(),  # confirm with portal docs
                   )
                   return (True, "signature valid")
               except InvalidSignature:
                   return (False, "signature invalid")

        4. Add real round-trip tests with a fixture cert + payload.

        Until then, this stub returns None to signal "verification not
        attempted"; the controller logs and either accepts (off / warn
        modes) or rejects with a clear error (enforce mode).

        Returns: (None | True | False, human-readable reason).
        """
        self.ensure_one()
        if self.raiffeisen_webhook_signature_mode == "off":
            return (None, "signature mode 'off' — verification skipped")
        if not self.raiffeisen_webhook_server_cert_pem:
            return (None, "gateway public cert not configured")
        # TODO: implement real RSA verification once prod-payload
        # canonicalization is confirmed (see roadmap above).
        return (None, "signature scheme not yet implemented (stub)")

    # ── Credential helpers ───────────────────────────────────────────

    def _raiffeisen_get_credentials(self):
        """Return (username, password) based on provider state."""
        self.ensure_one()
        if self.state == "test":
            return (
                self.raiffeisen_sandbox_username or "",
                self.raiffeisen_sandbox_password or "",
            )
        return (
            self.raiffeisen_api_username or "",
            self.raiffeisen_api_password or "",
        )

    # ── Authentication ───────────────────────────────────────────────

    def _raiffeisen_authenticate(self):
        """Obtain a JWT access token via AWS Cognito USER_PASSWORD_AUTH."""
        self.ensure_one()
        username, password = self._raiffeisen_get_credentials()
        if not username or not password:
            raise ValidationError(
                _("Raiffeisen API credentials are not configured.")
            )

        payload = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "AuthParameters": {
                "USERNAME": username,
                "PASSWORD": password,
            },
            "ClientId": RAIACCEPT_AUTH_CLIENT_ID,
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": (
                "AWSCognitoIdentityProviderService.InitiateAuth"
            ),
        }

        try:
            resp = requests.post(
                RAIACCEPT_AUTH_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("AuthenticationResult", {}).get("AccessToken")
            if not token:
                raise ValidationError(
                    _("Raiffeisen authentication failed: "
                      "no access token in response.")
                )
            return token
        except requests.RequestException as exc:
            _logger.error("Raiffeisen auth error: %s", exc)
            raise ValidationError(
                _("Failed to authenticate with Raiffeisen: %s") % str(exc)
            ) from exc

    # ── API request ──────────────────────────────────────────────────

    def _raiffeisen_api_request(self, method, endpoint, payload=None):
        """Make an authenticated request to the RaiAccept API."""
        self.ensure_one()
        token = self._raiffeisen_authenticate()
        url = urljoin(RAIACCEPT_API_BASE, endpoint)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.request(
                method,
                url,
                json=payload if method == "POST" else None,
                headers=headers,
                timeout=30,
            )
            if resp.status_code >= 400:
                # Log full response body to aid debugging RaiAccept
                # validation errors. RaiAccept returns JSON with
                # field-level error details on 400 responses.
                _logger.error(
                    "Raiffeisen API %s %s -> %s\nRequest payload: %s\n"
                    "Response body: %s",
                    method, endpoint, resp.status_code,
                    payload, resp.text,
                )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except requests.RequestException as exc:
            body = ""
            if exc.response is not None:
                body = exc.response.text or ""
            _logger.error(
                "Raiffeisen API %s %s failed: %s. Body: %s",
                method, endpoint, exc, body,
            )
            # Show a truncated body in the user-facing error so the
            # admin sees the validation reason without digging into logs.
            short = body[:300] if body else str(exc)
            raise ValidationError(
                _("Raiffeisen API request failed: %s") % short
            ) from exc

    # ── Order / Checkout ─────────────────────────────────────────────

    def _raiffeisen_create_order_and_checkout(self, tx):
        """Create an order on RaiAccept and return the checkout redirect URL."""
        self.ensure_one()
        order_payload = self._raiffeisen_build_order_payload(tx)

        # Step 1: Create order entry
        order_resp = self._raiffeisen_api_request(
            "POST", "/orders", order_payload
        )
        order_id = order_resp.get("orderIdentification")
        if not order_id:
            raise ValidationError(
                _("Raiffeisen: order creation returned no "
                  "orderIdentification.")
            )
        tx.raiffeisen_order_id = order_id

        # Step 2: Create payment session (checkout)
        checkout_resp = self._raiffeisen_api_request(
            "POST", f"/orders/{order_id}/checkout", order_payload
        )
        redirect_url = checkout_resp.get("paymentRedirectURL")
        if not redirect_url:
            raise ValidationError(
                _("Raiffeisen: checkout returned no paymentRedirectURL.")
            )
        return redirect_url

    @staticmethod
    def _raiffeisen_minor_unit_factor(currency):
        """Return the multiplier that converts a major-unit amount
        into RaiAccept's minor unit for the given gateway currency.

        RaiAccept expects whole integers:
        - RSD: zero-decimal — RaiAccept stores and displays whole
          dinars. Sending 82500 shows as "82,500.00 RSD", not
          "825.00 RSD". So factor = 1.
        - EUR: two-decimal cents — factor = 100.
        """
        return 1 if (currency or "").upper() == "RSD" else 100

    def _raiffeisen_build_order_payload(self, tx):
        """Build the CreateOrderEntryRequest payload from a transaction."""
        self.ensure_one()
        partner = tx.partner_id

        gateway_currency = self.raiffeisen_gateway_currency or "RSD"

        # Amount in gateway minor units.
        # See _raiffeisen_minor_unit_factor for currency-specific rules.
        amount = tx.amount
        rate = self.raiffeisen_currency_rate or 1.0
        if rate > 0:
            amount = amount * rate
        factor = self._raiffeisen_minor_unit_factor(gateway_currency)
        amount_cents = int(round(amount * factor))

        country_a2 = partner.country_id.code or ""
        country_a3 = _COUNTRY_ISO3.get(country_a2)
        if not country_a3:
            raise ValidationError(
                _("Cannot process payment: country '%s' (%s) is not "
                  "supported by the Raiffeisen payment gateway. "
                  "Please contact support to add this country.",
                  partner.country_id.name or "N/A", country_a2)
            )

        # Strip trailing slash to avoid double-slash in callback URLs like
        # "https://adriamart.rs//payment/raiffeisen/return".
        base_url = self.get_base_url().rstrip("/")

        name_parts = (partner.name or "Customer").split()
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 \
            else first_name

        billing_address = {
            "firstName": _transliterate(first_name),
            "lastName": _transliterate(last_name),
            "addressStreet1": _transliterate(partner.street or "N/A"),
            "city": _transliterate(partner.city or "N/A"),
            "postalCode": _transliterate(partner.zip or "00000"),
            "country": country_a3,
        }
        if partner.street2:
            billing_address["addressStreet2"] = _transliterate(
                partner.street2
            )
        # RaiAccept requires state to be an ISO 3166-2 subdivision code
        # with max length 3. Odoo's res.country.state.code is typically
        # like "RS-00" or "00" — strip the country prefix and truncate.
        # If we can't produce a ≤3 char code, omit the state field
        # (RaiAccept accepts empty state).
        if partner.state_id and partner.state_id.code:
            raw_code = partner.state_id.code
            if "-" in raw_code:
                raw_code = raw_code.split("-", 1)[1]
            if raw_code and len(raw_code) <= 3:
                billing_address["state"] = raw_code

        consumer = {
            "firstName": billing_address["firstName"],
            "lastName": billing_address["lastName"],
            "email": partner.email or "noreply@example.com",
        }
        # Odoo 19 merged partner.mobile into partner.phone; use phone for both.
        if partner.phone:
            phone_digits = re.sub(r"\D", "", partner.phone)
            consumer["phone"] = phone_digits
            consumer["mobilePhone"] = phone_digits

        invoice_items = [{
            "description": _transliterate(tx.reference or "Order"),
            "numberOfItems": 1,
            "price": amount_cents,
        }]

        return {
            "billingAddress": billing_address,
            "shippingAddress": billing_address,
            "consumer": consumer,
            "invoice": {
                "amount": amount_cents,
                "currency": gateway_currency,
                "merchantOrderReference": tx.reference[:127],
                "items": invoice_items,
            },
            "urls": {
                "successUrl": (
                    f"{base_url}/payment/raiffeisen/return"
                    f"?ref={tx.reference}"
                ),
                "failUrl": (
                    f"{base_url}/payment/raiffeisen/return"
                    f"?ref={tx.reference}"
                ),
                "cancelUrl": (
                    f"{base_url}/payment/raiffeisen/return"
                    f"?ref={tx.reference}"
                ),
                "notificationUrl": (
                    f"{base_url}/payment/raiffeisen/webhook"
                ),
            },
            "paymentMethodPreference": "CARD",
        }

    def _raiffeisen_get_order_status(self, order_id):
        """Query the order status from the gateway."""
        self.ensure_one()
        return self._raiffeisen_api_request("GET", f"/orders/{order_id}")

    def _raiffeisen_refund(self, order_id, transaction_id, amount_cents,
                           currency):
        """Issue a refund via the API."""
        self.ensure_one()
        return self._raiffeisen_api_request(
            "POST",
            f"/orders/{order_id}/transactions/{transaction_id}/refund",
            {"amount": amount_cents, "currency": currency},
        )

    # ── Odoo payment provider interface ──────────────────────────────

    def _get_supported_currencies(self):
        if self.code != "raiffeisen":
            return super()._get_supported_currencies()
        return self.env["res.currency"].search([
            ("name", "in", ["RSD", "EUR"]),
        ])

    def _get_default_payment_method_codes(self):
        if self.code != "raiffeisen":
            return super()._get_default_payment_method_codes()
        return ["card"]


# ── Module-level transliteration utility ─────────────────────────────

_CYR_MAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D",
    "Ђ": "Dj", "Е": "E", "Ж": "Z", "З": "Z", "И": "I",
    "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj", "М": "M",
    "Н": "N", "Њ": "Nj", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "Ћ": "C", "У": "U", "Ф": "F",
    "Х": "H", "Ц": "C", "Ч": "C", "Џ": "Dz", "Ш": "S",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "ђ": "dj", "е": "e", "ж": "z", "з": "z", "и": "i",
    "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m",
    "н": "n", "њ": "nj", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "ћ": "c", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "c", "џ": "dz", "ш": "s",
}


def _transliterate(text, max_len=127):
    """Transliterate non-Latin characters and limit length."""
    if not text:
        return ""
    result = []
    for char in text:
        if char in _CYR_MAP:
            result.append(_CYR_MAP[char])
        else:
            nfkd = unicodedata.normalize("NFKD", char)
            ascii_char = nfkd.encode("ascii", "ignore").decode("ascii")
            result.append(ascii_char if ascii_char else "")
    return "".join(result)[:max_len]
