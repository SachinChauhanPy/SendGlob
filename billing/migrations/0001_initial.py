# Reset to an empty initial migration. The full billing schema is generated in a
# later migration in this app; this app's tables are created fresh on migrate.
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = []
