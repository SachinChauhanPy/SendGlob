from django.conf import settings
from django.db import models


class ContactMessage(models.Model):
    """A message submitted through the public contact form.

    Stored first, notified second: the operator email is best-effort and its
    failure never loses the message. Surfaced in the ops console (Messages
    section) and in Django admin. Plain ``models.Model``: inbound correspondence
    is operational history, kept whole like the audit log.
    """

    STATUS_NEW = 'new'
    STATUS_READ = 'read'
    STATUS_RESOLVED = 'resolved'
    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_READ, 'Read'),
        (STATUS_RESOLVED, 'Resolved'),
    ]

    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    # Set when the sender was logged in; SET_NULL keeps the message if the
    # account is later removed.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='contact_messages')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES,
                              default=STATUS_NEW)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='handled_messages')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"{self.email}: {self.subject or self.message[:40]}"
