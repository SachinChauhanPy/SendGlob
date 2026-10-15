from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0006_signature_auto_remove_lines'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='signature',
            name='auto_remove_lines',
        ),
    ]
