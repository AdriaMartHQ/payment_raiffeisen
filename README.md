# Payment Provider: Raiffeisen RaiAccept

Odoo 19 payment provider module for **Raiffeisen Bank RaiAccept** gateway.

Accept online card payments (Visa, Mastercard, Maestro, Diners) via Raiffeisen Bank's hosted payment page. Designed for merchants in Serbia and Southeast Europe.

## Features

- Full RaiAccept API integration (order creation, checkout redirect, status query)
- Settlement in **RSD** or **EUR**
- Built-in currency conversion with configurable exchange rate
- Partial and full refunds from Odoo backend
- Separate production and sandbox credentials
- Serbian Cyrillic-to-Latin transliteration for addresses
- Secure webhook processing with authoritative gateway verification
- Comprehensive country code mapping (50+ countries)

## Supported Countries (Merchant)

This module works for merchants with a Raiffeisen Bank business account in the following RaiAccept-connected markets:

| Country | Bank | Currency |
|---------|------|----------|
| Serbia | Raiffeisen banka a.d. Beograd | RSD |
| Austria | Raiffeisen Bank International | EUR |
| Croatia | Raiffeisenbank Austria d.d. Zagreb | EUR |
| Bosnia & Herzegovina | Raiffeisen Bank d.d. BiH | EUR |
| Kosovo | Raiffeisen Bank Kosovo | EUR |
| Albania | Raiffeisen Bank Albania | EUR |
| Romania | Raiffeisen Bank Romania | EUR |
| Hungary | Raiffeisen Bank Zrt. | EUR |
| Czech Republic | Raiffeisenbank a.s. | EUR |
| Slovakia | Tatra banka (RBI group) | EUR |
| Ukraine | Raiffeisen Bank Ukraine | EUR |

**Customer payments**: Customers from any country worldwide can pay using Visa, Mastercard, Maestro, Diners, DinaCard and other card networks. Billing address mapping covers 50+ countries.

## Documentation

Official RaiAccept API reference, test card numbers, and integration guides:

- **https://docs.raiaccept.com/index.html** — RaiAccept docs (merchant
  onboarding, API specs, webhook details)

## Requirements

- **Odoo 19.0** (Community or Enterprise)
- A **Raiffeisen Bank RaiAccept** merchant account
- RaiAccept API credentials (username + password)

## Installation

1. Copy this module to your Odoo addons path
2. Update the apps list: **Settings > Apps > Update Apps List**
3. Install **Payment Provider: Raiffeisen RaiAccept**

## Configuration

1. Go to **Invoicing > Configuration > Payment Providers**
2. Open **Raiffeisen RaiAccept**
3. Enter credentials:
   - **Test mode**: Sandbox Username + Password
   - **Production**: API Username + Password
4. Set **Gateway Currency** (RSD or EUR) and **Currency Rate** if your store currency differs
5. Enable the provider

## Technical Details

- Uses AWS Cognito `USER_PASSWORD_AUTH` for API authentication
- Redirect-based payment flow via `_get_specific_rendering_values`
- Authoritative amount/currency verification against gateway response
- Transaction ID selection prefers successful PURCHASE transactions
- Refund callbacks routed by `transactionId` for correct child tx resolution

## Changelog

### 19.0.1.4.0 — 2026-04-14

Documentation and branding.

- Replace the plugin icon with the official **Raiffeisen Giebelkreuz**
  brand mark (yellow square with the crossed horse-head gable cross),
  cropped and centered on a clean 128×128 square.
- Add **DinaCard** to the supported brands list throughout the
  Odoo Apps Store description (`static/description/index.html`).
- Add a **Documentation** section linking to the official RaiAccept
  API reference at https://docs.raiaccept.com/index.html, both in
  README and in the Apps Store description.

### 19.0.1.3.0 — 2026-04-14

Brand display polish.

**Fixes**

- Link the provider to both the primary `Card` method **and** each
  accepted brand (Visa, Mastercard, Maestro, Diners, DinaCard).
  Linking only the primary hid the brand icons at checkout; linking
  only the brands caused Odoo's `_get_compatible_payment_methods`
  to drop the Card method with "no supported provider available".
  Both are required.
- Pre-pad the DinaCard PNG onto a transparent 3:2 canvas (318×214)
  so Odoo's Image-field resize produces a 64×43 icon, matching the
  dimensions of Visa/Mastercard/Maestro/Diners. Previously the
  DinaCard icon was rendered ~25% shorter than its neighbours and
  the text was unreadable.

### 19.0.1.2.0 — 2026-04-14

Payment brand coverage update.

**Improvements**

- Declare explicit brand list on the Raiffeisen provider: **Visa,
  Mastercard, Maestro, Diners, DinaCard**. Previously the provider
  only linked to the generic `Card` method, so the Odoo brand pills
  on checkout/config views were empty.
- Ship **DinaCard** (Serbian national card scheme, operated by NBS)
  as a brand record. Odoo core does not include DinaCard by default;
  this module now creates it as a child of the `Card` method with a
  bundled logo asset under `static/src/img/dinacard.png`. Merchants
  on Raiffeisen RaiAccept in Serbia can now show a DinaCard pill
  alongside Visa/Mastercard at checkout.

### 19.0.1.1.0 — 2026-04-14

First production-tested release. This version has been validated end-to-end
on a live Odoo 19 e-commerce site with real sandbox transactions.

**Bug fixes**

- Fix `AttributeError: 'res.partner' object has no attribute 'mobile'`.
  Odoo 19 merged `partner.mobile` into `partner.phone`; the module now
  uses `phone` for both the `phone` and `mobilePhone` fields in the
  RaiAccept consumer payload.
- Fix HTTP 400 Bad Request on `/orders` caused by oversized `state`
  field. RaiAccept validates `billingAddress.state` / `shippingAddress.state`
  to 0–3 characters. The module now reads `res.country.state.code`
  (e.g. `RS-00`), strips the country prefix, and omits the field entirely
  if a short code cannot be produced.
- Fix amount being inflated 100× for RSD orders. RaiAccept treats RSD
  as a zero-decimal currency — sending 82500 was being displayed as
  "82,500.00 RSD" instead of "825.00 RSD". A new
  `_raiffeisen_minor_unit_factor(currency)` helper returns 1 for RSD
  and 100 for EUR, applied in all three places where the module
  computes minor units (order payload, status verification, refund).
- Fix customers landing on a blank RaiAccept "unexpected error" page.
  The redirect form uses `method="get"`, which caused browsers to drop
  the query string on the RaiAccept session URL (`?token=...&session=...`).
  The module now parses the redirect URL with `urlparse`, passes the
  path as `api_url` and the query parameters as hidden inputs via
  `url_params`, so the GET form submission reconstructs the full URL.
- Fix double-slash in callback URLs (`https://site.rs//payment/...`)
  by stripping the trailing slash from `get_base_url()`.

**Improvements**

- On HTTP 4xx/5xx responses, the module now logs the full outgoing
  request payload and the RaiAccept response body, and surfaces a
  truncated copy of the response body in the user-facing `ValidationError`
  so merchants can diagnose validation failures without digging into
  `journalctl`.
- Provider icon (`image_128`) and Apps Store assets updated to the
  official Raiffeisen "Giebelkreuz" brand mark and a clean Raiffeisen
  Visa card artwork.
- `available_country_ids` on the provider record now defaults to a
  curated list of 15 SEE/CEE + core EU markets, so RaiAccept only
  appears at checkout for customers with shipping addresses in those
  countries.

### 19.0.1.0.0 — 2026-04-13

Initial release. Full RaiAccept API integration, RSD/EUR settlement,
partial & full refunds, Cyrillic transliteration, webhook with
authoritative amount/currency verification, sandbox/production
credential split.

## License

OPL-1 (Odoo Proprietary License v1.0)

## Support

- GitHub Issues: https://github.com/AdriaMartHQ/payment_raiffeisen/issues
- Email: balkan@adriamart.com
