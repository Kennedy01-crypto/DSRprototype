from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import django_fsm


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="DSRRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("patient_id", models.CharField(db_index=True, max_length=128)),
                ("request_type", models.CharField(choices=[("ACCESS", "Access"), ("PORTABILITY", "Portability"), ("RECTIFICATION", "Rectification"), ("ERASURE", "Erasure"), ("RESTRICTION", "Restriction"), ("OBJECTION", "Objection")], max_length=32)),
                ("description", models.TextField()),
                ("department", models.CharField(blank=True, max_length=128)),
                ("state", django_fsm.FSMField(choices=[("SUBMITTED", "Submitted"), ("ACCEPTED", "Accepted / Under Assessment"), ("DEPT_SEARCH", "Departmental Search"), ("PRIVACY_REVIEW", "Privacy Review"), ("LEGAL_REVIEW", "Legal / Management Review"), ("RESPONSE_PREP", "Response Preparation"), ("CLOSED", "Closed"), ("REJECTED", "Rejected"), ("ESCALATED", "Escalated")], default="SUBMITTED", max_length=32, protected=True)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("statutory_deadline", models.DateTimeField()),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("-submitted_at",)},
        ),
        migrations.CreateModel(
            name="DSRAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_id", models.CharField(max_length=128)),
                ("from_state", models.CharField(max_length=32)),
                ("to_state", models.CharField(max_length=32)),
                ("reason", models.TextField(blank=True)),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("dsr", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="audit_logs", to="dsr.dsrrequest")),
            ],
            options={"ordering": ("timestamp", "pk")},
        ),
        migrations.CreateModel(
            name="DSRDataQuarantine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("storage_reference", models.CharField(max_length=512, unique=True)),
                ("encryption_key_reference", models.CharField(max_length=256)),
                ("source_systems", models.JSONField(default=list)),
                ("payload_sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("dsr", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="quarantine", to="dsr.dsrrequest")),
            ],
            options={"verbose_name": "DSR data quarantine record"},
        ),
    ]