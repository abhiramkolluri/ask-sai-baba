# Password Reset Feature Setup Guide

## Overview
The password reset feature has been successfully implemented. This document provides setup instructions and usage information.

## Features Implemented

### Backend (Flask)
1. **POST /password/reset/request** - Request a password reset email
2. **POST /password/reset/verify** - Verify if a reset token is valid
3. **POST /password/reset/confirm** - Reset password with valid token

### Frontend (React)
1. **Reset Page** (`/password/reset`) - Request password reset
2. **NewPassword Page** (`/password/newpassword?token=xxx`) - Set new password
3. **Login Page** - Already has "Forgot password?" link

## Setup Instructions

### 1. Install Backend Dependencies

```bash
cd BE/backend
pip install -r requirements.txt
```

This will install `Flask-Mail` for sending emails.

### 2. Configure Email Settings

Update the `.env` file in `BE/backend/` with your email provider settings:

#### For Gmail (Recommended for testing):

1. **Enable 2-Step Verification** on your Google Account
2. **Generate an App Password**:
   - Go to: https://myaccount.google.com/security
   - Under "2-Step Verification", find "App passwords"
   - Generate a new app password for "Mail"
   - Copy the 16-character password

3. **Update .env file**:
```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-16-char-app-password
MAIL_DEFAULT_SENDER=your-email@gmail.com
FRONTEND_URL=http://localhost:3000
```

#### For Other Email Providers:

**SendGrid:**
```env
MAIL_SERVER=smtp.sendgrid.net
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=apikey
MAIL_PASSWORD=your-sendgrid-api-key
```

**Mailgun:**
```env
MAIL_SERVER=smtp.mailgun.org
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-mailgun-smtp-username
MAIL_PASSWORD=your-mailgun-smtp-password
```

**AWS SES:**
```env
MAIL_SERVER=email-smtp.us-east-1.amazonaws.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-ses-smtp-username
MAIL_PASSWORD=your-ses-smtp-password
```

### 3. MongoDB Collections

The system will automatically create a `password_resets` collection in MongoDB to store reset tokens. No manual setup required.

### 4. Start the Backend

```bash
cd BE/backend
python app.py
```

### 5. Start the Frontend

```bash
cd FE
npm start
```

## Usage Flow

### User Perspective:

1. **Request Reset**:
   - User clicks "Forgot password?" on login page
   - Navigates to `/password/reset`
   - Enters their email address
   - Clicks "Send Reset Link"
   - Receives email with reset link

2. **Reset Password**:
   - User clicks link in email (or copies to browser)
   - Navigates to `/password/newpassword?token=xxx`
   - System verifies token automatically
   - User enters new password (twice)
   - Clicks "Confirm Reset"
   - Redirected to login page

3. **Sign In**:
   - User logs in with new password

## Security Features

1. **Token Hashing**: Tokens are hashed before storage using SHA-256
2. **Token Expiration**: Tokens expire after 1 hour
3. **One-Time Use**: Tokens are marked as used after successful reset
4. **Email Enumeration Prevention**: Same response whether email exists or not
5. **Password Validation**: Minimum 6 characters required
6. **HTTPS Recommended**: Use HTTPS in production

## Testing

### Test the Flow:

1. Register a test user at `/signup`
2. Go to `/signin` and click "Forgot password?"
3. Enter your email
4. Check your email inbox for the reset link
5. Click the link and set a new password
6. Log in with the new password

### Test Email Locally:

You can use a service like [Mailtrap](https://mailtrap.io/) for testing emails without sending real emails:

```env
MAIL_SERVER=smtp.mailtrap.io
MAIL_PORT=2525
MAIL_USE_TLS=True
MAIL_USERNAME=your-mailtrap-username
MAIL_PASSWORD=your-mailtrap-password
```

## MongoDB Collections

### password_resets Collection Structure:
```javascript
{
  "_id": ObjectId,
  "user_email": "user@example.com",
  "token_hash": "sha256_hash_of_token",
  "created_at": ISODate,
  "expires_at": ISODate,
  "used": false,
  "used_at": ISODate (optional)
}
```

## Troubleshooting

### Email Not Sending:

1. **Check email credentials** in `.env`
2. **Verify SMTP settings** for your provider
3. **Check backend console** for error messages
4. **For Gmail**: Make sure you're using an App Password, not your regular password
5. **Check spam folder** in recipient's email

### Token Invalid/Expired:

1. Tokens expire after 1 hour
2. Tokens can only be used once
3. Request a new reset if token is invalid

### Frontend Not Connecting:

1. Check that `REACT_APP_API_URL` is set correctly in frontend `.env`
2. Default is `http://localhost:8000`
3. Verify backend is running

## Production Recommendations

1. **Use HTTPS**: Enforce HTTPS for all password operations
2. **Use Professional Email Service**: SendGrid, Mailgun, or AWS SES
3. **Set Strong Passwords**: Increase minimum password length requirement
4. **Add Rate Limiting**: Prevent abuse of reset endpoint
5. **Monitor Reset Requests**: Log and alert on suspicious activity
6. **Update FRONTEND_URL**: Set to your production domain

## API Endpoints

### POST /password/reset/request
Request a password reset email

**Request:**
```json
{
  "email": "user@example.com"
}
```

**Response (Success):**
```json
{
  "message": "If an account exists with this email, a password reset link has been sent."
}
```

### POST /password/reset/verify
Verify if a reset token is valid

**Request:**
```json
{
  "token": "reset_token_from_url"
}
```

**Response (Success):**
```json
{
  "valid": true,
  "email": "user@example.com"
}
```

**Response (Invalid):**
```json
{
  "valid": false,
  "error": "Invalid or expired reset token"
}
```

### POST /password/reset/confirm
Reset password with valid token

**Request:**
```json
{
  "token": "reset_token_from_url",
  "password": "new_password"
}
```

**Response (Success):**
```json
{
  "message": "Password has been reset successfully"
}
```

## Support

For issues or questions, check:
1. Backend console logs
2. Browser console (Developer Tools)
3. Email provider logs
4. MongoDB for stored tokens

## Files Modified/Created

### Backend:
- `BE/backend/app.py` - Added password reset endpoints and email functionality
- `BE/backend/requirements.txt` - Added Flask-Mail
- `BE/backend/.env` - Added email configuration

### Frontend:
- `FE/src/pages/password/reset/Reset.jsx` - Request password reset page
- `FE/src/pages/password/reset/NewPassword.jsx` - Set new password page
- `FE/src/components/input/MaterialInput.jsx` - Enhanced to support onChange

### Documentation:
- `BE/backend/PASSWORD_RESET_SETUP.md` - This setup guide
