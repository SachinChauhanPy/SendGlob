from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('emails', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailtemplate',
            name='layout',
            field=models.CharField(
                choices=[
                    ('simple', 'Simple / Transactional'),
                    ('announcement', 'Announcement'),
                    ('newsletter', 'Newsletter'),
                    ('promotion', 'Promotion / Offer'),
                    ('event', 'Event Invite'),
                    ('custom', 'Custom HTML'),
                ],
                default='simple',
                help_text='Design layout used to render this template.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='brand_color',
            field=models.CharField(
                default='#0078D4',
                help_text='Primary brand color (hex) used by the design.',
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='logo_url',
            field=models.URLField(blank=True, help_text='Optional logo image URL shown in the design.'),
        ),
        migrations.AddField(
            model_name='emailtemplate',
            name='content',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Structured content fields (heading, body, button, etc.) for the builder.',
            ),
        ),
        migrations.AlterField(
            model_name='emailtemplate',
            name='body_html',
            field=models.TextField(
                help_text='Rendered HTML of the email. Supports variables like {{ name }}, {{ email }}, etc.'
            ),
        ),
    ]
