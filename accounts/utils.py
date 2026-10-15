import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def validate_turnstile(token, remote_ip=None):
    """
    Validate Cloudflare Turnstile CAPTCHA token.
    Returns True if valid, False otherwise.
    """
    if not token:
        logger.warning("Turnstile validation failed: Token is empty.")
        return False
        
    secret_key = settings.TURNSTILE_SECRET_KEY
    if not secret_key:
        logger.error("Turnstile validation failed: TURNSTILE_SECRET_KEY is not configured.")
        return False
        
    payload = {
        'secret': secret_key,
        'response': token
    }
    if remote_ip:
        payload['remoteip'] = remote_ip
        
    try:
        response = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data=payload,
            timeout=5
        )
        result = response.json()
        success = result.get('success', False)
        if not success:
            logger.warning(f"Turnstile verification failed. Errors: {result.get('error-codes', [])}")
        return success
    except Exception as e:
        logger.error(f"Error during Turnstile validation: {e}")
        return False
