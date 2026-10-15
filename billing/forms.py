"""Billing profile forms.

The billing page edits two records at once: the user's identity fields (shown on
receipts and used as the pricing region) and their BillingProfile address. Two
ModelForms keep validation with the models instead of hand-parsing POST data.
"""
import re

from django import forms
from django.contrib.auth import get_user_model

from .models import BillingProfile

_INPUT = {'class': 'form-control bg-body text-body border-secondary'}


class BillingIdentityForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ['full_name', 'organization', 'phone', 'country']
        labels = {'country': 'Country (ISO code)'}
        widgets = {f: forms.TextInput(attrs=_INPUT) for f in fields}

    def clean_country(self):
        country = (self.cleaned_data.get('country') or '').strip().upper()
        if country and not re.fullmatch(r'[A-Z]{2}', country):
            raise forms.ValidationError('Use a two-letter ISO country code, e.g. IN or US.')
        return country

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone and not re.fullmatch(r'\+?[0-9 ()-]{6,20}', phone):
            raise forms.ValidationError('Enter a valid phone number.')
        return phone


class BillingAddressForm(forms.ModelForm):
    class Meta:
        model = BillingProfile
        fields = ['billing_name', 'tax_id', 'address_line1', 'address_line2',
                  'city', 'state', 'postal_code']
        labels = {'tax_id': 'Tax ID (optional)'}
        widgets = {f: forms.TextInput(attrs=_INPUT) for f in fields}
