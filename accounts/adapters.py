from allauth.account.adapter import DefaultAccountAdapter

class CustomAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        """
        Custom user saving for allauth registration.
        """
        user = super().save_user(request, user, form, commit=False)
        # We can perform additional field mappings here if needed
        if commit:
            user.save()
        return user
