import emails.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('emails', '0010_remove_emailcampaign_recurrence_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recipient',
            name='document_file',
            field=models.FileField(
                blank=True, null=True,
                upload_to=emails.models.generated_document_upload_to,
            ),
        ),
    ]
