#!/usr/bin/env python3
"""
Environment validation script for Ask Sai Baba Backend
Ensures all required environment variables are present before starting the application
"""

import os
import sys
import configparser
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def check_env_var(var_name, required=True, fallback_file=None, fallback_section=None, fallback_key=None):
    """Check if environment variable exists, with optional fallback to config file"""
    value = os.getenv(var_name)

    if not value and fallback_file:
        try:
            config = configparser.ConfigParser()
            config.read(fallback_file)
            value = config[fallback_section][fallback_key]
            print(f"✅ {var_name} loaded from {fallback_file}")
        except Exception as e:
            print(f"⚠️  Could not read {var_name} from {fallback_file}: {e}")

    if value:
        # Mask sensitive values for display
        display_value = value[:10] + "..." if len(value) > 10 else value
        print(f"✅ {var_name}: {display_value}")
        return True
    elif required:
        print(f"❌ MISSING REQUIRED: {var_name}")
        return False
    else:
        print(f"⚠️  OPTIONAL MISSING: {var_name}")
        return True

def main():
    print("🔍 Validating environment variables...")
    print("=" * 50)

    all_good = True

    # MongoDB validation removed.

    # Check OpenAI API key with fallback to openai.ini
    if not check_env_var("OPENAI_API_KEY", required=True,
                        fallback_file="openai.ini",
                        fallback_section="OpenAI",
                        fallback_key="api_key"):
        all_good = False
        print("   💡 Set OPENAI_API_KEY environment variable or add to openai.ini")

    # Check Weaviate configuration
    if not check_env_var("WEAVIATE_URL", required=True):
        all_good = False
        print("   💡 Set WEAVIATE_URL to your Weaviate instance URL")

    if not check_env_var("WEAVIATE_API_KEY", required=False):
        print("   💡 WEAVIATE_API_KEY is optional for local Weaviate instances")

    # Check optional configuration
    check_env_var("LANGSMITH_TRACING", required=False)
    check_env_var("LANGSMITH_API_KEY", required=False)
    check_env_var("GOOGLE_CLIENT_ID", required=False)
    check_env_var("GOOGLE_CLIENT_SECRET", required=False)
    check_env_var("GOOGLE_REDIRECT_URI", required=False)
    check_env_var("BACKEND_BASE_URL", required=False)
    check_env_var("FRONTEND_SIGNIN_URL", required=False)
    check_env_var("JWT_SECRET_KEY", required=False)
    check_env_var("MAIL_SERVER", required=False)
    check_env_var("MAIL_PORT", required=False)
    check_env_var("MAIL_USE_TLS", required=False)
    check_env_var("MAIL_USERNAME", required=False)
    check_env_var("MAIL_PASSWORD", required=False)
    check_env_var("MAIL_DEFAULT_SENDER", required=False)
    check_env_var("FRONTEND_PASSWORD_RESET_URL", required=False)

    print("=" * 50)

    if all_good:
        print("✅ All required environment variables are configured!")
        return 0
    else:
        print("❌ Some required environment variables are missing!")
        print("\n📋 To fix this:")
        print("1. Update your .env file with missing variables")
        print("2. Or set environment variables directly")
        print("3. For OpenAI, you can also use openai.ini file")
        return 1

if __name__ == "__main__":
    sys.exit(main())