#!/bin/bash

# Setup script for Password Reset Feature
# This script helps configure the password reset feature

echo "=========================================="
echo "Password Reset Feature Setup"
echo "=========================================="
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Please create a .env file first."
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "✅ Dependencies installed successfully"
else
    echo "❌ Failed to install dependencies"
    exit 1
fi

echo ""
echo "=========================================="
echo "Email Configuration"
echo "=========================================="
echo ""
echo "Please configure your email settings in the .env file:"
echo ""
echo "For Gmail (recommended for testing):"
echo "  1. Enable 2-Step Verification on your Google Account"
echo "  2. Generate an App Password at: https://myaccount.google.com/security"
echo "  3. Update .env with:"
echo "     MAIL_USERNAME=your-email@gmail.com"
echo "     MAIL_PASSWORD=your-16-char-app-password"
echo ""
echo "For production, consider using:"
echo "  - SendGrid (https://sendgrid.com)"
echo "  - Mailgun (https://mailgun.com)"
echo "  - AWS SES (https://aws.amazon.com/ses)"
echo ""

# Check if email settings are configured
if grep -q "your-email@gmail.com" .env; then
    echo "⚠️  WARNING: Email settings need to be configured in .env"
    echo "   Please update MAIL_USERNAME and MAIL_PASSWORD"
else
    echo "✅ Email settings appear to be configured"
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Configure email settings in .env (if not done)"
echo "2. Start the backend: python app.py"
echo "3. Test the password reset flow"
echo ""
echo "For detailed instructions, see PASSWORD_RESET_SETUP.md"
echo ""
