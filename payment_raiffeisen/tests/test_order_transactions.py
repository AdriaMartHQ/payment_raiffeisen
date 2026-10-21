"""Tests for reading the gateway transaction id (19.0.1.6.2).

RaiAccept's GET /orders/{id} carries no "transactions" list (checked on
sandbox and production, 2026-10-21), so `raiffeisen_tx_id` was never
stored and refunds failed with "missing Raiffeisen order or transaction
ID". The transactions now come from GET /orders/{id}/transactions when
the order payload has none. The gateway is mocked; nothing goes out.
"""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

PROVIDER = "odoo.addons.payment_raiffeisen.models.payment_provider.PaymentProvider"


@tagged("post_install", "-at_install")
class TestOrderTransactions(TransactionCase):

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

    def _tx(self, reference):
        return self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.card.id,
            "reference": reference,
            "amount": 496.0,
            "currency_id": self.rsd.id,
            "partner_id": self.partner.id,
            "raiffeisen_order_id": "ORD-1",
            "raiffeisen_gateway_currency": "RSD",
            "raiffeisen_currency_rate": 1.0,
        })

    # Shape of the real production payloads (values made up).
    ORDER = {"status": "PAID", "invoice": {"amount": 496, "currency": "RSD"}}
    PURCHASE = {"transactionId": "TX-PURCHASE", "transactionType": "PURCHASE",
                "status": "SUCCESS", "transactionAmount": 496,
                "transactionCurrency": "RSD"}

    def test_tx_id_fetched_when_order_payload_has_no_transactions(self):
        tx = self._tx("RTEST-1")
        with patch(PROVIDER + "._raiffeisen_get_order_status", return_value=dict(self.ORDER)), \
                patch(PROVIDER + "._raiffeisen_get_order_transactions",
                      return_value=[self.PURCHASE]) as get_txs:
            tx._apply_updates({"reference": tx.reference})
        get_txs.assert_called_once_with("ORD-1")
        self.assertEqual(tx.raiffeisen_tx_id, "TX-PURCHASE",
                         "Without the purchase transactionId a refund cannot be sent.")
        self.assertEqual(tx.state, "done")

    def test_transactions_in_order_payload_are_used_without_extra_call(self):
        tx = self._tx("RTEST-2")
        order = dict(self.ORDER, transactions=[self.PURCHASE])
        with patch(PROVIDER + "._raiffeisen_get_order_status", return_value=order), \
                patch(PROVIDER + "._raiffeisen_get_order_transactions") as get_txs:
            tx._apply_updates({"reference": tx.reference})
        get_txs.assert_not_called()
        self.assertEqual(tx.raiffeisen_tx_id, "TX-PURCHASE")

    def test_paid_order_stays_done_when_transaction_list_fails(self):
        tx = self._tx("RTEST-3")
        with patch(PROVIDER + "._raiffeisen_get_order_status", return_value=dict(self.ORDER)), \
                patch(PROVIDER + "._raiffeisen_get_order_transactions",
                      side_effect=Exception("gateway down")):
            tx._apply_updates({"reference": tx.reference})
        self.assertEqual(tx.state, "done",
                         "The order query says PAID; a failing transaction list must not undo that.")
        self.assertFalse(tx.raiffeisen_tx_id)

    def test_refund_of_old_payment_fetches_missing_purchase_id(self):
        source = self._tx("RTEST-4")  # paid before 19.0.1.6.2: no tx id stored
        source.state = "done"
        refund = source._create_child_transaction(source.amount, is_refund=True)
        with patch(PROVIDER + "._raiffeisen_get_order_transactions",
                   return_value=[self.PURCHASE]), \
                patch(PROVIDER + "._raiffeisen_refund",
                      return_value={"transactionId": "TX-REFUND", "status": "SUCCESS"}) as send:
            refund._send_refund_request()
        send.assert_called_once_with("ORD-1", "TX-PURCHASE", 496, "RSD")
        self.assertEqual(source.raiffeisen_tx_id, "TX-PURCHASE")
        self.assertEqual(refund.raiffeisen_tx_id, "TX-REFUND")
        self.assertEqual(refund.state, "done")

    def test_get_order_transactions_accepts_list_or_wrapped_list(self):
        with patch(PROVIDER + "._raiffeisen_api_request", return_value=[self.PURCHASE]) as api:
            self.assertEqual(self.provider._raiffeisen_get_order_transactions("ORD-1"), [self.PURCHASE])
        api.assert_called_once_with("GET", "/orders/ORD-1/transactions")
        with patch(PROVIDER + "._raiffeisen_api_request", return_value={"transactions": [self.PURCHASE]}):
            self.assertEqual(self.provider._raiffeisen_get_order_transactions("ORD-1"), [self.PURCHASE])
        with patch(PROVIDER + "._raiffeisen_api_request", return_value={"unexpected": 1}):
            self.assertEqual(self.provider._raiffeisen_get_order_transactions("ORD-1"), [])
