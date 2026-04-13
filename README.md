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

- GitHub Issues: https://github.com/daihuo/payment_raiffeisen/issues
- Email: support@daihuo.tech
