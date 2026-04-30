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

        Defense-in-depth (added 19.0.1.6.0 — Codex review on PR #76):

          1. Source IP allowlist — reject HTTP 403 when remote_addr is
             not in the merchant's `raiffeisen_webhook_allowed_ips`
             config. Bypassed when the field is empty (log-only mode
             so a misconfigured allowlist can't accidentally lock the
             merchant out of receiving webhooks during onboarding).

          2. Signature verification — invoked when
             `raiffeisen_webhook_signature_mode` is `warn` or
             `enforce`. The verifier itself is a stub today (the JSON
             canonicalization scheme needs to be confirmed against
             real prod payloads); see
             `_raiffeisen_verify_webhook_signature` docstring for the
             roadmap. `enforce` mode rejects when the verifier returns
             False; `warn` mode logs a warning but proceeds.

          3. Authoritative re-fetch — `_apply_updates` queries the
             gateway directly for amount + currency, so even an
             unsigned (or replayed) webhook cannot mark an order paid
             unless the gateway itself confirms the transaction. This
             is the primary protection today and stays in place
             regardless of the IP / signature checks above.

        The two new checks are deliberately additive and conservative
        by default — out of the box the controller behaves exactly as
        in 19.0.1.5.0 (no rejections, just structured audit logs)
        until the merchant configures the allowlist / cert.
        """
        # ----- Source IP audit -----
        remote_ip = (
            request.httprequest.headers.get("X-Forwarded-For", "")
            .split(",")[0]
            .strip()
            or request.httprequest.remote_addr
            or "?"
        )

        # ----- Provider lookup (for security config) -----
        # We can't call _process before knowing the provider, but the
        # provider record is needed for IP / signature checks. Use the
        # first enabled raiffeisen provider; if multi-provider setups
        # become a real scenario, the gateway notify URL itself can be
        # made provider-specific (per /payment/raiffeisen/<id>/webhook).
        provider = request.env["payment.provider"].sudo().search(
            [("code", "=", "raiffeisen")],
            order="state desc, id asc",
            limit=1,
        )
        if not provider:
            _logger.warning(
                "Raiffeisen webhook: no raiffeisen provider configured "
                "(remote_ip=%s)", remote_ip,
            )
            return request.make_json_response(
                {"error": "provider not configured"}, status=503,
            )

        # ----- IP allowlist enforcement -----
        ip_ok, ip_reason = provider._raiffeisen_webhook_ip_allowed(remote_ip)
        if not ip_ok:
            _logger.warning(
                "Raiffeisen webhook REJECTED: %s (remote_ip=%s)",
                ip_reason, remote_ip,
            )
            return request.make_json_response(
                {"error": "source ip not allowed"}, status=403,
            )

        # ----- Parse JSON body -----
        try:
            payload = json.loads(request.httprequest.data or b"{}")
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            _logger.warning(
                "Raiffeisen webhook: invalid JSON payload (remote_ip=%s)",
                remote_ip,
            )
            return request.make_json_response(
                {"error": "invalid payload"}, status=400
            )

        if not isinstance(payload, dict):
            _logger.warning(
                "Raiffeisen webhook: payload is not a JSON object "
                "(remote_ip=%s)", remote_ip,
            )
            return request.make_json_response(
                {"error": "expected JSON object"}, status=400
            )

        # Validate nested structures are dicts
        order_data = payload.get("order", {})
        tx_data = payload.get("transaction", {})
        if not isinstance(order_data, dict) or not isinstance(tx_data, dict):
            _logger.warning(
                "Raiffeisen webhook: order/transaction not dicts "
                "(remote_ip=%s)", remote_ip,
            )
            return request.make_json_response(
                {"error": "malformed payload structure"}, status=400
            )

        # ----- Signature verification (mode-driven) -----
        sig = (
            payload.get("signature")
            or payload.get("Signature")
            or tx_data.get("signature")
            or ""
        )
        sig_ok, sig_reason = provider._raiffeisen_verify_webhook_signature(
            payload, sig,
        )
        mode = provider.raiffeisen_webhook_signature_mode
        if sig_ok is False and mode == "enforce":
            _logger.warning(
                "Raiffeisen webhook REJECTED: signature %s "
                "(remote_ip=%s, order=%s)",
                sig_reason, remote_ip,
                order_data.get("orderIdentification", "?"),
            )
            return request.make_json_response(
                {"error": "invalid signature"}, status=403,
            )
        if sig_ok is False and mode == "warn":
            _logger.warning(
                "Raiffeisen webhook signature mismatch (warn-only): %s "
                "(remote_ip=%s, order=%s)",
                sig_reason, remote_ip,
                order_data.get("orderIdentification", "?"),
            )
        # sig_ok is None when the verifier is the stub or the mode is
        # "off"; we still log it so prod operators can audit the gap.

        # Log only non-PII identifiers — no customer data
        _logger.info(
            "Raiffeisen webhook ACCEPTED: order=%s status=%s txType=%s "
            "remote_ip=%s ip_check=%s sig_check=%s",
            order_data.get("orderIdentification", "?"),
            order_data.get("status", "?"),
            tx_data.get("transactionType", "?"),
            remote_ip,
            ip_reason,
            sig_reason,
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
