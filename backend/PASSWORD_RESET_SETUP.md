# Password Reset — Email Setup

The password-reset flow (`/password/reset/request|verify|confirm`) sends an email via Flask-Mail. Reset tokens are SHA-256 hashed, expire after 1 hour, and are single-use; they're stored in the Weaviate `PasswordResetToken` collection. Endpoint contracts are listed in the main [README](README.md).

This doc only covers the SMTP configuration, which is the non-obvious part. Set the `MAIL_*` vars in `.env`.

## Gmail (handy for testing)

Requires an **App Password**, not your account password: enable 2-Step Verification, then create one at https://myaccount.google.com/security → App passwords → Mail.

```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-16-char-app-password
MAIL_DEFAULT_SENDER=your-email@gmail.com
```

## Other providers

```env
# SendGrid
MAIL_SERVER=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USERNAME=apikey
MAIL_PASSWORD=your-sendgrid-api-key

# Mailgun
MAIL_SERVER=smtp.mailgun.org
MAIL_PORT=587
MAIL_USERNAME=your-mailgun-smtp-username
MAIL_PASSWORD=your-mailgun-smtp-password

# AWS SES
MAIL_SERVER=email-smtp.us-east-1.amazonaws.com
MAIL_PORT=587
MAIL_USERNAME=your-ses-smtp-username
MAIL_PASSWORD=your-ses-smtp-password

# Mailtrap (catch emails in testing, nothing is delivered)
MAIL_SERVER=smtp.mailtrap.io
MAIL_PORT=2525
MAIL_USERNAME=your-mailtrap-username
MAIL_PASSWORD=your-mailtrap-password
```

All providers above use `MAIL_USE_TLS=True`. The reset link's domain comes from `FRONTEND_URL` (or `FRONTEND_PASSWORD_RESET_URL` if set) — make sure it points to the right environment.

## If email isn't sending

- Gmail: confirm you're using an App Password, not the account password.
- Check the backend console for the SMTP error, and the recipient's spam folder.
- Verify `MAIL_USERNAME`/`MAIL_PASSWORD` are set — `send_reset_email` no-ops if they're missing.
