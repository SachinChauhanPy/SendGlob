from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from accounts.models import SMTPConfig
from accounts.utils import validate_turnstile
from unittest.mock import patch

User = get_user_model()

class AccountModelsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword123"
        )

    def test_smtp_config_encryption(self):
        """Test that SMTP app passwords are encrypted on write and decrypted on read."""
        config = SMTPConfig.objects.create(
            user=self.user,
            name="Gmail Test Connection",
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_username="testuser@gmail.com",
        )
        
        raw_pass = "abcd-efgh-ijkl-mnop"
        config.set_app_password(raw_pass)
        config.save()

        # Check password is not saved in plaintext
        self.assertNotEqual(config.encrypted_password, raw_pass)
        
        # Check decryption matches raw value
        decrypted_pass = config.get_app_password()
        self.assertEqual(decrypted_pass, raw_pass)

    def test_smtp_config_active_exclusivity(self):
        """Test that setting a configuration active deactivates all other configs for that user."""
        config1 = SMTPConfig.objects.create(
            user=self.user,
            name="SMTP Config 1",
            smtp_username="user1@gmail.com",
            is_active=True
        )
        config1.set_app_password("pass1")
        config1.save()

        config2 = SMTPConfig.objects.create(
            user=self.user,
            name="SMTP Config 2",
            smtp_username="user2@gmail.com",
            is_active=True
        )
        config2.set_app_password("pass2")
        config2.save()

        # Re-fetch config1
        config1.refresh_from_db()
        
        self.assertFalse(config1.is_active)
        self.assertTrue(config2.is_active)


class DuplicateSignupTestCase(TestCase):
    """With ACCOUNT_PREVENT_ENUMERATION disabled, signing up with an already
    registered email must show a form error and send no mail, instead of
    allauth's default fake-success plus "account already exists" email."""

    @patch("accounts.forms.validate_turnstile", return_value=True)
    def test_duplicate_email_signup_shows_error_and_sends_no_email(self, mock_turnstile):
        User.objects.create_user(
            username="existing",
            email="existing@example.com",
            password="testpassword123",
        )
        response = self.client.post(
            reverse("account_signup"),
            {
                "email": "existing@example.com",
                "password1": "another-Secret-123",
                "cf-turnstile-response": "token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already registered")
        self.assertEqual(len(mail.outbox), 0)


class TurnstileValidationTestCase(TestCase):
    @patch('requests.post')
    def test_validate_turnstile_success(self, mock_post):
        """Test successful turnstile validation."""
        # Mock requests.post response
        mock_post.return_value.json.return_value = {'success': True}
        mock_post.return_value.status_code = 200

        result = validate_turnstile("valid-token")
        self.assertTrue(result)

    @patch('requests.post')
    def test_validate_turnstile_failure(self, mock_post):
        """Test failed turnstile validation."""
        mock_post.return_value.json.return_value = {'success': False, 'error-codes': ['invalid-input-response']}
        mock_post.return_value.status_code = 200

        result = validate_turnstile("invalid-token")
        self.assertFalse(result)


class SettingsViewTestCase(TestCase):
    """B-038: account settings page to set the scheduling timezone."""

    def setUp(self):
        self.user = User.objects.create_user(email='s@t.com', password='pw12345678')
        self.client.force_login(self.user)

    def test_get_renders(self):
        resp = self.client.get(reverse('accounts:settings'))
        self.assertEqual(resp.status_code, 200)

    def test_post_updates_timezone(self):
        self.client.post(reverse('accounts:settings'), {'timezone': 'Asia/Kolkata'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, 'Asia/Kolkata')

    def test_post_invalid_timezone_rejected(self):
        self.client.post(reverse('accounts:settings'), {'timezone': 'Not/AZone'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, 'UTC')
