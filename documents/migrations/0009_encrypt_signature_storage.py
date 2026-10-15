import core.storage
import documents.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0008_protect_signature_filenames'),
    ]

    operations = [
        migrations.AlterField(
            model_name='signature',
            name='original_image',
            field=models.ImageField(
                help_text='The uploaded signature photo or scan.',
                storage=core.storage.encrypted_media_storage,
                upload_to=documents.models.signature_original_upload_to,
            ),
        ),
        migrations.AlterField(
            model_name='signature',
            name='processed_image',
            field=models.ImageField(
                blank=True, null=True,
                help_text='Background-removed transparent PNG (generated automatically).',
                storage=core.storage.encrypted_media_storage,
                upload_to=documents.models.signature_processed_upload_to,
            ),
        ),
    ]
