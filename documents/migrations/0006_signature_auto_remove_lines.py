from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0005_documentsignaturefield_signature'),
    ]

    operations = [
        migrations.AddField(
            model_name='signature',
            name='auto_remove_lines',
            field=models.BooleanField(
                default=False,
                help_text='Subtract notebook/ruled lines and margins during processing.',
            ),
        ),
    ]
