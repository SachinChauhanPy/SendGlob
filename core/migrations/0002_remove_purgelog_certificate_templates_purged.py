from django.db import migrations


class Migration(migrations.Migration):
    """
    Remove the certificate_templates_purged counter from PurgeLog.

    The certificates app has been deprecated in favour of the documents app.
    The purge task no longer tracks certificate template deletions.
    """

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='purgelog',
            name='certificate_templates_purged',
        ),
    ]
