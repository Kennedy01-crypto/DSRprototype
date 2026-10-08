from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dsr", "0004_dsrcommunication_direction_dsrrequest_escalated_from_and_more")]

    operations = [
        migrations.AlterField(
            model_name="dsrcaseevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("ASSIGNED", "Assigned"),
                    ("TASK_CREATED", "Review task created"),
                    ("TASK_UPDATED", "Review task updated"),
                    ("DECISION_RECORDED", "Decision recorded"),
                    ("PATIENT_UPDATE_RECORDED", "Patient update recorded"),
                    ("PATIENT_RESPONSE_RECORDED", "Patient response recorded"),
                    ("CASES_EXPORTED", "Case included in a DPO export"),
                    ("SOURCE_SEARCH_RECORDED", "Source search recorded"),
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="DSRSourceSearch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source_system",
                    models.CharField(
                        choices=[
                            ("PARAS", "PARAS (mock source)"),
                            ("CIMS", "CIMS (mock source)"),
                            ("ERPS", "ERPS (mock source)"),
                            ("OTHER", "Other mock source"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("COMPLETE", "Complete"),
                            ("BLOCKED", "Blocked"),
                        ],
                        default="PENDING",
                        max_length=16,
                    ),
                ),
                ("summary", models.TextField(blank=True)),
                ("evidence_reference", models.CharField(blank=True, max_length=256)),
                ("created_by", models.CharField(max_length=128)),
                ("updated_by", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dsr",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_searches",
                        to="dsr.dsrrequest",
                    ),
                ),
            ],
            options={"ordering": ("source_system", "pk")},
        ),
        migrations.AddConstraint(
            model_name="dsrsourcesearch",
            constraint=models.UniqueConstraint(
                fields=("dsr", "source_system"),
                name="unique_dsr_source_search",
            ),
        ),
    ]
