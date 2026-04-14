import logging
from urllib.parse import urlparse, parse_qsl, urlunparse

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# RaiAccept status → Odoo transaction state
_STATUS_MAP = {
    "PENDING": "pending",
    "SUCCESS": "done",
    "PAID": "done",
    "FAILED": "error",
    "CANCELED": "cancel",
    "ABANDONED": "error",
}


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    raiffeisen_order_id = fields.Char(
        string="RaiAccept Order ID",
        readonly=True,
    )
    raiffeisen_tx_id = fields.Char(
        string="RaiAccept Transaction ID",
        readonly=True,
    )
    raiffeisen_gateway_currency = fields.Char(
        string="Gateway Currency (snapshot)",
        readonly=True,
        help="Currency used at checkout time, snapshotted from provider.",
    )
    raiffeisen_currency_rate = fields.Float(
        string="Currency Rate (snapshot)",
        readonly=True,
        digits=(12, 6),
        help="Exchange rate at checkout time, snapshotted from provider.",
    )

    # ── FIX #1: Use _get_specific_rendering_values (not processing) ──

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return Raiffeisen-specific rendering values.

        Creates the RaiAccept order and returns the redirect URL.
        This is the correct hook for redirect-based providers in Odoo 19.

        Note: self.ensure_one() from `_get_processing_values`
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "raiffeisen":
            return res

        try:
            # Snapshot provider config at checkout time
            provider = self.provider_id
            self.raiffeisen_gateway_currency = (
                provider.raiffeisen_gateway_currency or "RSD"
            )
            self.raiffeisen_currency_rate = (
                provider.raiffeisen_currency_rate or 1.0
            )

            redirect_url = provider._raiffeisen_create_order_and_checkout(self)
        except ValidationError as error:
            self._set_error(str(error))
            return {}

        # The redirect_form template uses <form method="get">, which
        # causes browsers to strip any query string on the action URL
        # and rebuild it from the form's hidden inputs. Split the
        # RaiAccept redirect URL so the query params are passed as
        # url_params (they become <input type="hidden"> fields) and
        # the action URL is just the path.
        parsed = urlparse(redirect_url)
        api_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
        )
        url_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        return {"api_url": api_url, "url_params": url_params}

    # ── Reference extraction ─────────────────────────────────────────

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Extract the transaction reference from payment data."""
        if provider_code != "raiffeisen":
            return super()._extract_reference(provider_code, payment_data)

        # Return redirect: ref in query params
        ref = payment_data.get("ref")
        if ref:
            return ref

        # Webhook: resolve by transactionId FIRST (critical for refunds,
        # since refund child tx shares the same orderIdentification/
        # merchantOrderReference as the parent payment tx)
        tx_id = payment_data.get("transactionId")
        if tx_id:
            tx = self.search(
                [("raiffeisen_tx_id", "=", tx_id)], limit=1
            )
            if tx:
                return tx.reference

        # Then try merchantOrderReference (unique per payment)
        ref = payment_data.get("merchantOrderReference")
        if ref:
            return ref

        # Fallback: look up by gateway orderIdentification
        order_id = payment_data.get("orderIdentification")
        if order_id:
            tx = self.search(
                [("raiffeisen_order_id", "=", order_id)], limit=1
            )
            if tx:
                return tx.reference

        return super()._extract_reference(provider_code, payment_data)

    # ── FIX #3: Amount validation — return None to skip base check ───

    def _extract_amount_data(self, payment_data):
        """Override of payment to skip base amount validation.

        Return None → Odoo skips amount validation. We do authoritative
        amount verification in _apply_updates by querying the gateway.
        """
        if self.provider_code != "raiffeisen":
            return super()._extract_amount_data(payment_data)
        return None

    # ── Apply gateway status updates ─────────────────────────────────

    def _apply_updates(self, payment_data):
        """Process the RaiAccept payment response and update state.

        FIX #3 continued: We verify amount/currency against the
        gateway response here, since we skipped base amount validation.
        """
        super()._apply_updates(payment_data)
        if self.provider_code != "raiffeisen":
            return

        gateway_status = "PENDING"
        if not self.raiffeisen_order_id:
            self._set_pending()
            return

        # Refund child transactions: handle async status updates
        # without re-querying the parent order's invoice amount
        is_refund = bool(self.source_transaction_id)

        # Query the gateway authoritatively
        try:
            order_data = self.provider_id._raiffeisen_get_order_status(
                self.raiffeisen_order_id
            )

            if is_refund:
                # For refund callbacks, find our specific transaction
                # by raiffeisen_tx_id in the order's transaction list
                txs = order_data.get("transactions", [])
                refund_tx = next(
                    (t for t in txs
                     if t.get("transactionId") == self.raiffeisen_tx_id),
                    None,
                )
                if refund_tx:
                    gateway_status = refund_tx.get("status", "PENDING")
                    # Verify refund amount matches what we requested
                    gw_refund_amt = refund_tx.get("amount")
                    if gw_refund_amt is not None:
                        rate = self.raiffeisen_currency_rate or 1.0
                        expected = int(
                            round(abs(self.amount) * rate * 100)
                        )
                        if abs(gw_refund_amt - expected) > 1:
                            self._set_error(
                                state_message=_(
                                    "Refund amount mismatch: gateway "
                                    "returned %s, expected %s "
                                    "(in minor units).",
                                    gw_refund_amt, expected,
                                )
                            )
                            return
                else:
                    # Our tx not in list yet; stay pending
                    gateway_status = "PENDING"
            else:
                gateway_status = order_data.get("status", "PENDING")

                # Extract transaction ID — prefer successful PURCHASE
                txs = order_data.get("transactions", [])
                if txs and not self.raiffeisen_tx_id:
                    # First: successful PURCHASE
                    purchase_tx = next(
                        (t for t in txs
                         if t.get("transactionType") == "PURCHASE"
                         and t.get("status") in ("SUCCESS", "PAID")),
                        None,
                    )
                    # Fallback: any PURCHASE, then first tx
                    if not purchase_tx:
                        purchase_tx = next(
                            (t for t in txs
                             if t.get("transactionType") == "PURCHASE"),
                            txs[0],
                        )
                    self.raiffeisen_tx_id = purchase_tx.get(
                        "transactionId"
                    )

                # Authoritative amount/currency verification
                # using snapshotted values from checkout time
                invoice = order_data.get("invoice", {})
                gw_amount_cents = invoice.get("amount")
                gw_currency = invoice.get("currency")
                if gw_amount_cents is not None and gw_currency:
                    rate = self.raiffeisen_currency_rate or 1.0
                    expected_cents = int(
                        round(self.amount * rate * 100)
                    )
                    expected_currency = (
                        self.raiffeisen_gateway_currency or "RSD"
                    )
                    if gw_currency != expected_currency:
                        self._set_error(
                            state_message=_(
                                "Currency mismatch: gateway returned "
                                "%s, expected %s.",
                                gw_currency, expected_currency,
                            )
                        )
                        return
                    # Allow 1 cent tolerance for rounding
                    if abs(gw_amount_cents - expected_cents) > 1:
                        self._set_error(
                            state_message=_(
                                "Amount mismatch: gateway returned "
                                "%s, expected %s (in minor units).",
                                gw_amount_cents, expected_cents,
                            )
                        )
                        return

        except Exception:
            _logger.warning(
                "Raiffeisen: could not query order %s status",
                self.raiffeisen_order_id,
                exc_info=True,
            )
            # Do NOT fall back to unverified notification data.
            self._set_pending()
            return

        # Set provider reference for backend display
        # Refund children use their unique tx_id for traceability
        if is_refund:
            self.provider_reference = self.raiffeisen_tx_id or ""
        else:
            self.provider_reference = (
                self.raiffeisen_order_id or self.raiffeisen_tx_id or ""
            )

        # Map gateway status to Odoo state
        odoo_state = _STATUS_MAP.get(gateway_status, "pending")

        if odoo_state == "done":
            self._set_done()
        elif odoo_state == "cancel":
            self._set_canceled(
                state_message=_(
                    "Payment was canceled on Raiffeisen gateway."
                )
            )
        elif odoo_state == "error":
            self._set_error(
                state_message=_(
                    "Payment failed on Raiffeisen gateway (%s).",
                    gateway_status,
                )
            )
        else:
            self._set_pending()

    # ── FIX #4: Refund — close the state machine via _process ────────

    def _send_refund_request(self):
        """Override of payment to send a refund request to RaiAccept.

        In Odoo 19, this is called on the CHILD refund transaction.
        Gateway IDs come from the source (parent) transaction.
        After API call, we process the response to close the state machine.
        """
        if self.provider_code != "raiffeisen":
            return super()._send_refund_request()

        source_tx = self.source_transaction_id
        if not source_tx:
            raise ValidationError(
                _("Cannot refund: no source transaction found.")
            )

        order_id = source_tx.raiffeisen_order_id
        tx_id = source_tx.raiffeisen_tx_id
        if not order_id or not tx_id:
            raise ValidationError(
                _("Cannot refund: missing Raiffeisen order or "
                  "transaction ID on the original payment.")
            )

        # Use snapshotted rate/currency from the source transaction
        rate = source_tx.raiffeisen_currency_rate or 1.0
        currency = source_tx.raiffeisen_gateway_currency or "RSD"
        # self.amount is negative for refunds; use abs
        amount_cents = int(round(abs(self.amount) * rate * 100))

        # Send refund to RaiAccept
        provider = self.provider_id
        resp = provider._raiffeisen_refund(
            order_id, tx_id, amount_cents, currency
        )

        # Store IDs
        refund_tx_id = resp.get("transactionId", "")
        self.raiffeisen_order_id = order_id
        self.raiffeisen_tx_id = refund_tx_id
        self.raiffeisen_gateway_currency = currency
        self.raiffeisen_currency_rate = rate
        self.provider_reference = refund_tx_id

        # Verify refund amount from gateway response
        gw_refund_amt = resp.get("amount")
        if gw_refund_amt is not None and abs(gw_refund_amt - amount_cents) > 1:
            self._set_error(
                state_message=_(
                    "Refund amount mismatch: gateway returned %s, "
                    "expected %s (in minor units).",
                    gw_refund_amt, amount_cents,
                )
            )
            return

        # Close state machine based on actual gateway response
        refund_status = resp.get("status", "")
        if refund_status in ("SUCCESS", "PAID"):
            self._set_done()
        elif refund_status == "PENDING":
            self._set_pending()
        else:
            self._set_error(
                state_message=_(
                    "Refund failed on Raiffeisen gateway (%s).",
                    refund_status,
                )
            )
