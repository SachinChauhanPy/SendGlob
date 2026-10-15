from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add the signatures_purged counter to PurgeLog.

    Soft-deleted Signatures are now physically purged by retention so their
    uploaded and processed images are reclaimed from storage; the purge audit
    log tracks how many were removed per run.
    """

    dependencies = [
        ('core', '0002_remove_purgelog_certificate_templates_purged'),
    ]

    operations = [
        migrations.AddField(
            model_name='purgelog',
            name='signatures_purged',
            field=models.IntegerField(default=0),
        ),
    ]
