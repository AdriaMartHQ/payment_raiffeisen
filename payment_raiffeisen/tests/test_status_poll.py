"""Tests for the status poll of unfinished card payments (19.0.1.6.4).

RaiAccept did not POST the webhook for the first production payment
(2026-10-21); only the customer's return arrived. A customer closing the
tab after 3-D Secure would have left a charged card on a draft
transaction. The cron now asks the gateway, settles the payment through
`_process` and post-processes it once. The gateway is mocked; nothing
goes out.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from psycopg2.errors import SerializationFailure

from odoo import fields
from odoo.tests import TransactionCase, tagged

PROVIDER = "odoo.addons.payment_raiffeisen.models.payment_provider.PaymentProvider"
COMMIT = "odoo.addons.base.models.ir_cron.IrCron._commit_progress"

# Shape of the real production payloads (values made up).
PURCHASE = {"transactionId": "TX-PURCHASE", "transactionType": "PURCHASE",
            "status": "SUCCESS", "transactionAmount": 496,
            "transactionCurrency": "RSD"}


def order(status):
    return {"status": status, "invoice": {"amount": 496, "currency": "RSD"}}


class _Gateway:
    """Patch the RaiAccept order queries and count `_post_process` calls."""

    def __init__(self, env, status=None, error=None):
        self.status, self.error = status, error
        self.model_cls = type(env["payment.transaction"])
        self.post_processed = []

    def __enter__(self):
        get = {"side_effect": self.error} if self.error else {"return_value": order(self.status)}
        self.get_order = patch(PROVIDER + "._raiffeisen_get_order_status", **get)
        self.get_txs = patch(PROVIDER + "._raiffeisen_get_order_transactions",
                             return_value=[PURCHASE])
        original = self.model_cls._post_process
        calls = self.post_processed

        def counting(tx_self):
            calls.extend(tx_self.ids)
            return original(tx_self)

        self.post = patch.object(self.model_cls, "_post_process", counting)
        self.order_mock = self.get_order.__enter__()
        self.get_txs.__enter__()
        self.post.__enter__()
        return self

    def __exit__(self, *exc):
        self.post.__exit__(*exc)
        self.get_txs.__exit__(*exc)
        self.get_order.__exit__(*exc)


@tagged("post_install", "-at_install")
class TestStatusPoll(TransactionCase):

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
        # Commits are not allowed inside a test; the poll commits per payment.
        patcher = patch(COMMIT, return_value=float("inf"))
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def _tx(self, reference, age=timedelta(minutes=10), **values):
        tx = self.env["payment.transaction"].create({
            "provider_id": self.provider.id,
            "payment_method_id": self.card.id,
            "reference": reference,
            "amount": 496.0,
            "currency_id": self.rsd.id,
            "partner_id": self.partner.id,
            "operation": "online_redirect",
            "raiffeisen_order_id": "ORD-" + reference,
            "raiffeisen_gateway_currency": "RSD",
            "raiffeisen_currency_rate": 1.0,
            **values,
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE payment_transaction SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - age, tx.id],
        )
        tx.invalidate_recordset()
        return tx

    def _cron(self, status=None, error=None):
        with _Gateway(self.env, status, error) as gateway:
            self.env["payment.transaction"]._cron_raiffeisen_sync_pending_payments()
        return gateway

    def test_success_is_settled_and_post_processed_once(self):
        tx = self._tx("POLL-1")
        gateway = self._cron("PAID")
        self.assertEqual(tx.state, "done")
        self.assertTrue(tx.is_post_processed)
        self.assertEqual(tx.raiffeisen_tx_id, "TX-PURCHASE",
                         "The purchase id is needed for refunds.")
        self.assertEqual(gateway.post_processed, [tx.id])
        again = self._cron("PAID")
        again.order_mock.assert_not_called()
        self.assertEqual(again.post_processed, [])

    def test_pending_or_unknown_status_changes_nothing(self):
        draft = self._tx("POLL-2")
        pending = self._tx("POLL-3")
        pending._set_pending()
        pending.is_post_processed = True
        for status in ("PENDING", "SOMETHING_NEW", ""):
            gateway = self._cron(status)
            self.assertEqual((draft.state, pending.state), ("draft", "pending"),
                             f"Status {status!r} is not final.")
            self.assertFalse(draft.raiffeisen_tx_id)
            self.assertEqual(gateway.post_processed, [])
        self.assertEqual(gateway.order_mock.call_count, 2, "Asked again on the next run.")

    def test_failed_query_changes_nothing(self):
        tx = self._tx("POLL-4")
        gateway = self._cron(error=Exception("gateway down"))
        self.assertEqual(tx.state, "draft",
                         "A draft must not turn pending: Odoo would mail the customer.")
        self.assertEqual(gateway.post_processed, [])

    def test_failed_and_canceled_payments(self):
        failed = self._tx("POLL-5")
        self._cron("FAILED")
        self.assertEqual(failed.state, "error")
        canceled = self._tx("POLL-6")
        self._cron("CANCELED")
        self.assertEqual(canceled.state, "cancel")

    def test_settled_payment_not_asked_again(self):
        tx = self._tx("POLL-7")
        tx._set_done()
        tx.is_post_processed = True
        gateway = self._cron("PAID")
        gateway.order_mock.assert_not_called()
        self.assertEqual(gateway.post_processed, [])

    def test_out_of_window_and_other_transactions_ignored(self):
        too_old = self._tx("POLL-8", age=timedelta(hours=49))
        too_new = self._tx("POLL-9", age=timedelta(minutes=2))
        no_order = self._tx("POLL-10", raiffeisen_order_id=False)
        paid = self._tx("POLL-11")
        paid._set_done()
        refund = paid._create_child_transaction(paid.amount, is_refund=True)
        refund.raiffeisen_order_id = paid.raiffeisen_order_id
        gateway = self._cron("PAID")
        gateway.order_mock.assert_not_called()
        self.assertEqual(
            (too_old | too_new | no_order | refund).mapped("state"),
            ["draft", "draft", "draft", "draft"],
        )

    def test_one_failing_payment_does_not_stop_the_others(self):
        first = self._tx("POLL-12")
        second = self._tx("POLL-13")
        with patch.object(type(first), "_process", autospec=True,
                          side_effect=[Exception("boom"), first.browse()]) as process:
            self._cron("PAID")
        self.assertEqual(process.call_count, 2)
        self.assertEqual((first.state, second.state), ("draft", "draft"))


@tagged("post_install", "-at_install")
class TestStatusPollConcurrency(TransactionCase):
    """The webhook and the cron settling one payment at the same time.

    Runs on separate, committed cursors so PostgreSQL's REPEATABLE READ
    conflict detection is real. The test cursor itself writes nothing.
    """

    def setUp(self):
        super().setUp()
        self.ref = "POLL-CONCURRENT-" + uuid.uuid4().hex[:8]
        with self.registry.cursor() as cr:
            env = self.env(cr=cr)
            provider = env["payment.provider"].create({
                "name": "Raiffeisen " + self.ref,
                "code": "raiffeisen",
                "state": "test",
                "raiffeisen_sandbox_username": "u",
                "raiffeisen_sandbox_password": "p",
            })
            partner = env["res.partner"].create({"name": "Concurrent Buyer"})
            tx = env["payment.transaction"].create({
                "provider_id": provider.id,
                "payment_method_id": env.ref("payment.payment_method_card").id,
                "reference": self.ref,
                "amount": 496.0,
                "currency_id": env.company.currency_id.id,
                "partner_id": partner.id,
                "operation": "online_redirect",
                "raiffeisen_order_id": "ORD-" + self.ref,
                "raiffeisen_gateway_currency": "RSD",
                "raiffeisen_currency_rate": 1.0,
            })
            self.tx_id, self.partner_id, self.provider_id = tx.id, partner.id, provider.id
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        # Best effort: with accounting installed, post-processing posts a
        # payment that cannot simply be deleted. The reference is unique
        # per run, so leftovers do not break the next run.
        with self.registry.cursor() as cr:
            env = self.env(cr=cr)
            try:
                with cr.savepoint():
                    env["payment.transaction"].browse(self.tx_id).unlink()
                    env["res.partner"].browse(self.partner_id).unlink()
                    env["payment.provider"].browse(self.provider_id).state = "disabled"
                    env["payment.provider"].browse(self.provider_id).unlink()
            except Exception:
                with cr.savepoint():
                    env["payment.provider"].browse(self.provider_id).state = "disabled"

    def _env(self, cr):
        return self.env(cr=cr)

    def _state(self):
        with self.registry.cursor() as cr:
            cr.execute(
                "SELECT state, is_post_processed FROM payment_transaction WHERE id = %s",
                [self.tx_id],
            )
            return cr.fetchone()

    def _webhook(self, env):
        env["payment.transaction"].sudo()._process(
            "raiffeisen", {"orderIdentification": "ORD-" + self.ref,
                           "merchantOrderReference": self.ref,
                           "status": "PAID"},
        )
        env.flush_all()

    def test_webhook_first_then_cron_with_stale_snapshot(self):
        with _Gateway(self.env, "PAID") as gateway, \
                self.registry.cursor() as cron_cr, self.registry.cursor() as hook_cr:
            cron_env = self._env(cron_cr)
            # The cron's snapshot starts before the webhook commits.
            cron_tx = cron_env["payment.transaction"].browse(self.tx_id)
            self.assertEqual(cron_tx.state, "draft")
            self._webhook(self._env(hook_cr))
            hook_cr.commit()
            self.assertEqual(cron_tx._raiffeisen_poll_status(), "",
                             "The cron must back off from a payment the webhook settled.")
            cron_cr.rollback()
        self.assertEqual(self._state(), ("done", False),
                         "Post-processing is left to the /payment/status poll or Odoo's cron.")
        self.assertEqual(gateway.post_processed, [])

    def test_cron_first_then_webhook_is_replayed_as_no_op(self):
        with _Gateway(self.env, "PAID") as gateway, \
                self.registry.cursor() as cron_cr, self.registry.cursor() as hook_cr:
            hook_env = self._env(hook_cr)
            # The webhook's snapshot starts before the cron commits.
            self.assertEqual(
                hook_env["payment.transaction"].browse(self.tx_id).state, "draft")
            cron_env = self._env(cron_cr)
            self.assertEqual(
                cron_env["payment.transaction"].browse(self.tx_id)._raiffeisen_poll_status(),
                "done",
            )
            cron_cr.commit()
            with self.assertRaises(SerializationFailure):
                self._webhook(hook_env)
            hook_cr.rollback()
        self.assertEqual(gateway.post_processed, [self.tx_id])
        self.assertEqual(self._state(), ("done", True))
        # Odoo replays the request on a fresh snapshot: nothing left to do.
        with _Gateway(self.env, "PAID") as replay, self.registry.cursor() as cr:
            self._webhook(self._env(cr))
        self.assertEqual(self._state(), ("done", True))
        self.assertEqual(replay.post_processed, [])

    def test_post_processing_claimed_by_another_flow_is_skipped(self):
        with _Gateway(self.env, "PAID") as gateway:
            with self.registry.cursor() as cr:
                self._env(cr)["payment.transaction"].browse(self.tx_id)._set_done()
            with self.registry.cursor() as cron_cr, self.registry.cursor() as poll_cr:
                cron_tx = self._env(cron_cr)["payment.transaction"].browse(self.tx_id)
                self.assertFalse(cron_tx.is_post_processed)  # snapshot taken
                # The customer's /payment/status poll post-processes it meanwhile.
                self._env(poll_cr)["payment.transaction"].browse(self.tx_id)._post_process()
                poll_cr.commit()
                cron_tx._raiffeisen_post_process_once()
                cron_cr.rollback()
        self.assertEqual(gateway.post_processed, [self.tx_id], "Post-processed once, by the poll.")
        self.assertEqual(self._state(), ("done", True))
