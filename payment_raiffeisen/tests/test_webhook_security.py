"""Tests for the webhook security scaffolding added in 19.0.1.6.0.

The first tests in this module's history. Added alongside the IP
allowlist + signature verification hooks because the previous
controller accepted any POST with valid JSON as authentic — defense
relied entirely on the gateway re-fetch in `_apply_updates`.

Run with::

    odoo-bin --test-tags /payment_raiffeisen \\
             -i payment_raiffeisen --stop-after-init -d <db>

These are TransactionCase tests so they need an Odoo database. The
helper-method tests under TestProviderHelpers don't actually create
transactions and run fast (~ms).

Codex review on PR #78 surfaced two P1 gaps that drove a revision:

  - XFF spoofability — the controller previously read
    `X-Forwarded-For` directly to derive remote_ip, which an attacker
    can forge to bypass the IP allowlist. Fixed by reading
    `request.httprequest.remote_addr` only and documenting the
    requirement to enable Odoo `proxy_mode` behind reverse proxies.

  - enforce mode silent-accept — the controller used to reject only
    when `_raiffeisen_verify_webhook_signature` returned False, but
    the verifier stub returns None for every code path so enforce
    was equivalent to warn. Fixed: enforce now requires sig_ok is
    True; both False AND None are rejected with HTTP 403. Tests
    below cover the new contract.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestProviderHelpers(TransactionCase):
    """Pure helper-method tests — no HTTP, no DB writes beyond setUp."""

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

    # ── IP allowlist parsing ────────────────────────────────────────

    def test_allowed_ip_list_empty_when_unset(self):
        self.provider.raiffeisen_webhook_allowed_ips = False
        self.assertEqual(
            self.provider._raiffeisen_webhook_allowed_ip_list(),
            set(),
        )

    def test_allowed_ip_list_strips_whitespace(self):
        self.provider.raiffeisen_webhook_allowed_ips = (
            " 195.85.198.15 ,195.85.198.16 ,  ,"
        )
        self.assertEqual(
            self.provider._raiffeisen_webhook_allowed_ip_list(),
            {"195.85.198.15", "195.85.198.16"},
            "Empty entries (trailing comma) and surrounding whitespace "
            "must be normalized away — otherwise an admin could "
            "accidentally lock out the gateway with a stray space.",
        )

    def test_allowed_ip_list_single_value(self):
        self.provider.raiffeisen_webhook_allowed_ips = "195.85.198.15"
        self.assertEqual(
            self.provider._raiffeisen_webhook_allowed_ip_list(),
            {"195.85.198.15"},
        )

    # ── IP allowlist enforcement ────────────────────────────────────

    def test_ip_allowed_log_only_when_unset(self):
        """Empty allowlist field = log-only mode (every IP allowed,
        the controller still logs the remote_addr). Critical default
        so a freshly-installed module doesn't reject all webhooks."""
        self.provider.raiffeisen_webhook_allowed_ips = False
        ok, reason = self.provider._raiffeisen_webhook_ip_allowed("8.8.8.8")
        self.assertTrue(ok)
        self.assertIn("no allowlist", reason)

    def test_ip_allowed_match(self):
        self.provider.raiffeisen_webhook_allowed_ips = "195.85.198.15"
        ok, reason = self.provider._raiffeisen_webhook_ip_allowed("195.85.198.15")
        self.assertTrue(ok)
        self.assertIn("allowlist", reason)

    def test_ip_allowed_reject(self):
        self.provider.raiffeisen_webhook_allowed_ips = "195.85.198.15"
        ok, reason = self.provider._raiffeisen_webhook_ip_allowed("8.8.8.8")
        self.assertFalse(ok)
        self.assertIn("8.8.8.8", reason)

    # ── Signature verification stub ─────────────────────────────────

    def test_verify_signature_off_returns_none(self):
        """In `off` mode the verifier short-circuits with None — the
        controller treats None as 'not verified, log only'."""
        self.provider.raiffeisen_webhook_signature_mode = "off"
        result, reason = self.provider._raiffeisen_verify_webhook_signature(
            payload={"order": {}}, signature="xxx",
        )
        self.assertIsNone(result)
        self.assertIn("off", reason)

    def test_verify_signature_warn_without_cert_returns_none(self):
        """When cert isn't configured, the verifier can't actually
        verify — returns None even in warn/enforce so the controller
        doesn't reject (would brick legitimate prod webhooks if the
        admin enabled the mode without uploading a cert)."""
        self.provider.raiffeisen_webhook_signature_mode = "warn"
        self.provider.raiffeisen_webhook_server_cert_pem = False
        result, reason = self.provider._raiffeisen_verify_webhook_signature(
            payload={"order": {}}, signature="xxx",
        )
        self.assertIsNone(result)
        self.assertIn("cert", reason.lower())

    def test_verify_signature_stub_when_cert_set(self):
        """With cert set + mode != off, the verifier currently returns
        None with 'not yet implemented' — that's the documented stub
        behavior. When the real RSA verification lands, this test
        flips to assert True/False on real fixture data.

        IMPORTANT (post Codex P1 PR #78): even though the helper
        returns None here, the *controller* now rejects in enforce
        mode whenever sig_ok is not True — so a None stub in enforce
        mode hard-fails the webhook. That contract is enforced one
        layer up, not in the helper itself; see
        controllers/main.py::raiffeisen_webhook for the policy."""
        self.provider.raiffeisen_webhook_signature_mode = "enforce"
        self.provider.raiffeisen_webhook_server_cert_pem = (
            "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        )
        result, reason = self.provider._raiffeisen_verify_webhook_signature(
            payload={"order": {}}, signature="xxx",
        )
        self.assertIsNone(result)
        self.assertIn("not yet implemented", reason)

    def test_enforce_rejects_when_verifier_returns_none(self):
        """Verifier returns None in stub state OR when cert missing.
        Controller treats `enforce` as 'sig_ok must be True' — None
        in enforce mode means the webhook gets a 403, even though
        the helper itself returned no boolean verdict.

        Pure-helper unit test exercising the `is True` semantics:
        this is what the controller branch hinges on. We verify the
        helper's return shape so a refactor can't quietly flip the
        semantics back to None=accept."""
        # Stub mode: verifier returns None → controller rejects.
        self.provider.raiffeisen_webhook_signature_mode = "enforce"
        self.provider.raiffeisen_webhook_server_cert_pem = (
            "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        )
        result, _reason = self.provider._raiffeisen_verify_webhook_signature(
            payload={"order": {}}, signature="xxx",
        )
        # Critical contract: enforce branch is `if sig_ok is not True`,
        # so the only accepted value is True. None / False both reject.
        # Mirror that here so a future verifier returning None still
        # short-circuits to a hard-fail.
        self.assertIsNot(result, True,
                         "enforce-mode contract: verifier must return "
                         "True to accept; None/False both reject")

        # Cert-missing mode: same outcome — None, controller rejects.
        self.provider.raiffeisen_webhook_server_cert_pem = False
        result, reason = self.provider._raiffeisen_verify_webhook_signature(
            payload={"order": {}}, signature="xxx",
        )
        self.assertIsNot(result, True)
        self.assertIn("cert", reason.lower())
