"""
Data migration: copy legacy certificate templates into the generalized document
model, and link existing campaigns to their new DocumentTemplate.

Non-destructive: CertificateTemplate rows and EmailCampaign.certificate_template
links are left untouched so the legacy certificate flow keeps working while both
engines coexist. ALL certificate templates are copied (including soft-deleted
ones) to preserve historical foreign-key integrity.
"""
from django.db import migrations


def copy_certificates_to_documents(apps, schema_editor):
    CertificateTemplate = apps.get_model('certificates', 'CertificateTemplate')
    DocumentTemplate = apps.get_model('documents', 'DocumentTemplate')
    DocumentTextField = apps.get_model('documents', 'DocumentTextField')
    EmailCampaign = apps.get_model('emails', 'EmailCampaign')

    cert_to_doc = {}

    for cert in CertificateTemplate.objects.all():
        doc = DocumentTemplate.objects.create(
            user_id=cert.user_id,
            name=cert.name,
            template_file=cert.image.name if cert.image else '',
            output_format='pdf',  # legacy certificates were always delivered as PDF
            deleted_at=cert.deleted_at,
        )
        # auto_now/auto_now_add override assigned timestamps on create; restore
        # the originals so historical ordering/reporting stays accurate.
        DocumentTemplate.objects.filter(pk=doc.pk).update(
            created_at=cert.created_at, updated_at=cert.updated_at,
        )

        DocumentTextField.objects.create(
            template=doc,
            field_name='name',
            label='Name',
            x_start=cert.x_start,
            x_end=cert.x_end,
            y_coordinate=cert.y_coordinate,
            font_size=cert.font_size,
            font_color=cert.font_color,
            font_file=cert.font_file.name if cert.font_file else None,
            alignment='center',
            order=0,
        )
        cert_to_doc[cert.pk] = doc.pk

    # Link campaigns to the new document template; mirror the certificate flag.
    for campaign in EmailCampaign.objects.exclude(certificate_template__isnull=True):
        doc_pk = cert_to_doc.get(campaign.certificate_template_id)
        if doc_pk:
            campaign.document_template_id = doc_pk
            campaign.is_document_campaign = campaign.is_certificate_campaign
            campaign.save(update_fields=['document_template', 'is_document_campaign'])


def reverse(apps, schema_editor):
    DocumentTemplate = apps.get_model('documents', 'DocumentTemplate')
    EmailCampaign = apps.get_model('emails', 'EmailCampaign')

    # Unlink campaigns, then remove the copied templates (cascades to fields).
    EmailCampaign.objects.exclude(document_template__isnull=True).update(
        document_template=None, is_document_campaign=False,
    )
    DocumentTemplate.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0001_initial'),
        ('emails', '0006_emailcampaign_column_mapping_and_more'),
        ('certificates', '0002_certificatetemplate_deleted_at'),
    ]

    operations = [
        migrations.RunPython(copy_certificates_to_documents, reverse),
    ]
