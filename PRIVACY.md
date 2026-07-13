# Privacy Policy — NYC Fiscal Policy Digest

This policy covers the SMS program "NYC Fiscal Policy Digest"
(nyc-budget-updator), a personal automated weekly news digest.

## What we collect

The only personal data this program holds is what each recipient provides
directly when they ask to be enrolled: their **first name** and **mobile phone
number**.

## How it is used

Names and phone numbers are used solely to deliver the weekly digest text
message via Twilio, the program's SMS provider. They are not used for any
other purpose.

## What we do NOT do

**No mobile information will be shared with third parties or affiliates for
marketing or promotional purposes. Text messaging originator opt-in data and
consent will not be shared with, or sold to, any third party.** The recipient
list is never published, sold, rented, or used for advertising.

## Storage

The recipient list is stored as an encrypted secret in the program's private
CI configuration (GitHub Actions secrets). It is deliberately excluded from
this public code repository. Phone numbers are transmitted only to Twilio at
send time, as required to deliver each message.

## Opt-out and deletion

Reply **STOP** to any message to unsubscribe, or contact the operator
directly. On request, a recipient's name and number are deleted from the
recipient list entirely.

## Contact

Sami Ellougani — sami.ellougani1@gmail.com, or open an issue at
<https://github.com/samiellougani/nyc-budget-updator/issues>.
