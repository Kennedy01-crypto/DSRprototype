from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("dsr", "0001_initial")]

    operations = [
        migrations.RenameField(
            model_name="dsrrequest",
            old_name="statutory_deadline",
            new_name="response_due_at",
        ),
    ]
