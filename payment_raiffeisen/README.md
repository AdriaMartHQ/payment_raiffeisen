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

**Customer payments**: Customers from any country worldwide can pay using Visa, Mastercard, Maestro, Diners and other card networks. Billing address mapping covers 50+ countries.

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

## License

OPL-1 (Odoo Proprietary License v1.0)

## Support

- GitHub Issues: https://github.com/AdriaMartHQ/payment_raiffeisen/issues
- Email: balkan@adriamart.com
