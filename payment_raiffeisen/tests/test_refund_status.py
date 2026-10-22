"""Tests for refunds answered without a status (19.0.1.6.3).

On production (2026-10-21) RaiAccept answered a refund POST with a
transactionId but no status, and the refund turned SUCCESS three
seconds later. Odoo marked it failed, so the payment still looked
refundable and a second refund was sent (the gateway rejected it).
An unknown status is now pending and settled by the cron, and a refund
that would exceed the payment together with refunds in flight is never
sent. The gateway is mocked; refunds go through Odoo's `_refund`.
"""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

PROVIDER = "odoo.addons.payment_raiffeisen.models.payment_provider.PaymentProvider"


@tagged("post_install", "-at_install")
class TestRefundStatus(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["payment.provider"].create({
            "name": "Raiffeisen Test",
            "code": "raiffeisen",
            "state": "test",
            "raiffeisen_sandbox_username": "u",
            "raiffeisen_sandbox_password": "p",
        })
        cls.rsd = cls.env.ref("base.RSD")
        cls.rsd.active = True
        cls.partner = cls.env["res.partner"].create({"name": "Buyer"})
        cls.card = cls.env.ref("payment.payment_method_card")

    def _paid_tx(self, reference):
        source = self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.card.id,
            "reference": reference,
            "amount": 496.0,
            "currency_id": self.rsd.id,
            "partner_id": self.partner.id,
            "raiffeisen_order_id": "ORD-1",
            "raiffeisen_tx_id": "TX-PURCHASE",
            "raiffeisen_gateway_currency": "RSD",
            "raiffeisen_currency_rate": 1.0,
        })
        source._set_done()
        return source

    @staticmethod
    def _listed(status, tx_id="TX-REFUND"):
        return [{"transactionId": tx_id, "transactionType": "REFUND",
                 "status": status, "transactionAmount": 496.0,
                 "transactionCurrency": "RSD"}]

    def _refund(self, source, response, amount=None):
        with patch(PROVIDER + "._raiffeisen_refund", return_value=response) as post:
            refund = source._refund(amount)
        return refund, post

    def _cron(self, listing):
        with patch(PROVIDER + "._raiffeisen_get_order_status", return_value={"status": "PAID"}), \
                patch(PROVIDER + "._raiffeisen_get_order_transactions", return_value=listing):
            self.env["payment.transaction"]._cron_raiffeisen_sync_pending_refunds()

    def test_missing_status_is_pending_not_failed(self):
        refund, _post = self._refund(self._paid_tx("RF-1"), {"transactionId": "TX-REFUND"})
        self.assertEqual(refund.state, "pending",
                         "A refund the gateway is still processing must not be marked failed.")
        self.assertEqual(refund.raiffeisen_tx_id, "TX-REFUND")

    def test_cron_settles_pending_refund(self):
        refund, _post = self._refund(self._paid_tx("RF-2"), {"transactionId": "TX-REFUND"})
        self._cron(self._listed("PENDING"))
        self.assertEqual(refund.state, "pending")
        self._cron(self._listed("SUCCESS"))
        self.assertEqual(refund.state, "done")

    def test_cron_marks_rejected_refund_error(self):
        refund, _post = self._refund(self._paid_tx("RF-3"), {"transactionId": "TX-REFUND"})
        self._cron(self._listed("FAILED"))
        self.assertEqual(refund.state, "error")

    def test_explicit_status_in_response_is_used(self):
        source = self._paid_tx("RF-4")
        done, _post = self._refund(source, {"transactionId": "TX-A", "status": "SUCCESS"}, 100.0)
        failed, _post = self._refund(source, {"transactionId": "TX-B", "status": "FAILED"}, 100.0)
        self.assertEqual((done.state, failed.state), ("done", "error"))

    def test_second_refund_not_sent_while_first_in_flight(self):
        source = self._paid_tx("RF-5")
        first, _post = self._refund(source, {"transactionId": "TX-REFUND"})
        second, post = self._refund(source, {"transactionId": "TX-2", "status": "SUCCESS"})
        post.assert_not_called()
        self.assertEqual(second.state, "error")
        self.assertIn("still being processed", second.state_message)
        self.assertEqual(first.state, "pending")

    def test_partial_refunds_up_to_the_amount_are_sent(self):
        source = self._paid_tx("RF-6")
        self._refund(source, {"transactionId": "TX-1"}, 200.0)
        second, post = self._refund(source, {"transactionId": "TX-2", "status": "SUCCESS"}, 296.0)
        post.assert_called_once()
        self.assertEqual(second.state, "done")
        third, post = self._refund(source, {"transactionId": "TX-3", "status": "SUCCESS"}, 1.0)
        post.assert_not_called()
        self.assertEqual(third.state, "error")
