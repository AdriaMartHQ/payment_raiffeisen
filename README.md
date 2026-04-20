# Odoo 19 Payment Provider: Raiffeisen RaiAccept

[![Odoo Apps](https://img.shields.io/badge/Odoo%20Apps-Available-875A7B)](https://apps.odoo.com/apps/modules/19.0/payment_raiffeisen)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Odoo Version](https://img.shields.io/badge/Odoo-19.0-875A7B)](https://www.odoo.com/documentation/19.0/)

Open-source Odoo 19 payment provider module for **Raiffeisen Bank RaiAccept** gateway. Accepts Visa, Mastercard, and DinaCard payments through Raiffeisen's hosted checkout in Serbia and the wider SEE/CEE region.

## Install

- **Odoo Apps Store** (recommended): [apps.odoo.com/apps/modules/19.0/payment_raiffeisen](https://apps.odoo.com/apps/modules/19.0/payment_raiffeisen)
- **From source**: clone this repo and copy `payment_raiffeisen/` into your Odoo addons path.

## Highlights

- Full RaiAccept API integration (order creation, hosted checkout redirect, status query)
- Settlement in **RSD** or **EUR**, with built-in currency conversion
- Partial and full refunds from the Odoo backend
- Production + sandbox credentials separated
- Serbian Cyrillic-to-Latin transliteration for addresses
- Webhook processing with authoritative gateway-side amount / currency verification
- Billing address mapping for 50+ countries

Supported markets: Serbia, Austria, Croatia, Bosnia & Herzegovina, Kosovo, Albania, Romania, Hungary, Czech Republic, Slovakia, Ukraine (all Raiffeisen business accounts).

## Repository layout

```
payment_raiffeisen/        The actual Odoo module (copy this into your addons path)
  ├── README.md            Detailed docs, changelog, supported cards/countries
  ├── __manifest__.py      Module manifest
  ├── controllers/         Webhook + redirect handlers
  ├── models/              payment.provider / payment.transaction overrides
  ├── views/               Provider config + redirect form
  ├── data/                Provider data record
  └── static/description/  Apps Store assets
```

Full documentation is in [`payment_raiffeisen/README.md`](payment_raiffeisen/README.md).

## Documentation

- RaiAccept API reference, test cards, onboarding: [docs.raiaccept.com](https://docs.raiaccept.com/index.html)
- Module README: [`payment_raiffeisen/README.md`](payment_raiffeisen/README.md)

## License

LGPL-3 — see [`payment_raiffeisen/LICENSE`](payment_raiffeisen/LICENSE).

## Maintainer

Built and maintained by **Adria Mart d.o.o.** (Belgrade, Serbia) — [adriamart.rs](https://adriamart.rs) — during our pre-launch technical preparation. Pull requests and issues welcome.

- Blog: [Open-Source Odoo 19 Payment Provider: Raiffeisen RaiAccept](https://adriamart.rs/blog/resources-3/8)
- Email: balkan@adriamart.rs
- GitHub Issues: [issues](https://github.com/AdriaMartHQ/payment_raiffeisen/issues)
