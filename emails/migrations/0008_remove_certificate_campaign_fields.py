from django.db import migrations


class Migration(migrations.Migration):
    """
    Remove all certificate-campaign fields from the emails app.

    Certificate campaign functionality has been superseded by the document
    campaign system (documents.DocumentTemplate), which supports the same
    use-case plus multi-field templates, PDF/image output selection, and
    arbitrary CSV column mapping.

    Fields removed:
        EmailCampaign.is_certificate_campaign
        EmailCampaign.certificate_template  (FK -> certificates.CertificateTemplate)
        Recipient.certificate_file
    """

    dependencies = [
        ('emails', '0007_emailcampaign_next_run_at_and_more'),
        ('certificates', '0002_certificatetemplate_deleted_at'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='emailcampaign',
            name='is_certificate_campaign',
        ),
        migrations.RemoveField(
            model_name='emailcampaign',
            name='certificate_template',
        ),
        migrations.RemoveField(
            model_name='recipient',
            name='certificate_file',
        ),
    ]
