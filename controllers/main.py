import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class RaiffeisenController(http.Controller):
    _return_url = "/payment/raiffeisen/return"
    _webhook_url = "/payment/raiffeisen/webhook"

    @http.route(
        _return_url,
        type="http",
        auth="public",
        methods=["GET", "POST"],
        csrf=False,
        save_session=False,
    )
    def raiffeisen_return(self, **data):
        """Handle customer return from RaiAccept payment page.

        After payment, the gateway redirects the customer back here
        via successUrl / failUrl / cancelUrl.
        """
        _logger.info(
            "Raiffeisen return with ref=%s", data.get("ref", "N/A")
        )

        # Use Odoo 19's standard _process flow
        request.env["payment.transaction"].sudo()._process(
            "raiffeisen", data
        )

        return request.redirect("/payment/status")

    @http.route(
        _webhook_url,
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def raiffeisen_webhook(self, **data):
        """Handle asynchronous webhook notifications from RaiAccept.

        The gateway sends order/transaction status updates here after
        payment processing completes.
        """
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            _logger.warning("Raiffeisen webhook: invalid JSON payload")
            return request.make_json_response(
                {"error": "invalid payload"}, status=400
            )

        if not isinstance(payload, dict):
            _logger.warning("Raiffeisen webhook: payload is not a JSON object")
            return request.make_json_response(
                {"error": "expected JSON object"}, status=400
            )

        # Validate nested structures are dicts
        order_data = payload.get("order", {})
        tx_data = payload.get("transaction", {})
        if not isinstance(order_data, dict) or not isinstance(tx_data, dict):
            _logger.warning("Raiffeisen webhook: order/transaction not dicts")
            return request.make_json_response(
                {"error": "malformed payload structure"}, status=400
            )

        # Log only non-PII identifiers — no customer data
        _logger.info(
            "Raiffeisen webhook: order=%s status=%s txType=%s",
            order_data.get("orderIdentification", "?"),
            order_data.get("status", "?"),
            tx_data.get("transactionType", "?"),
        )

        notification_data = {
            "orderIdentification": order_data.get(
                "orderIdentification"
            ),
            "merchantOrderReference": order_data.get(
                "merchantOrderReference"
            ),
            "status": order_data.get("status", ""),
            "transactionId": tx_data.get("transactionId"),
            "transactionType": tx_data.get("transactionType"),
        }

        try:
            request.env["payment.transaction"].sudo()._process(
                "raiffeisen", notification_data
            )
        except Exception:
            _logger.exception("Raiffeisen webhook processing failed")
            return request.make_json_response(
                {"error": "processing failed"}, status=500
            )

        # Acknowledge receipt — RaiAccept expects HTTP 200
        return ""
