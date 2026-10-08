from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dsr", "0002_rename_statutory_deadline")]

    operations = [
        migrations.AddField(
            model_name="dsrrequest",
            name="assigned_to",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="dsrrequest",
            name="resolution_status",
            field=models.CharField(
                choices=[
                    ("OPEN", "Open"),
                    ("FULFILLED", "Fulfilled"),
                    ("PARTIALLY_FULFILLED", "Partially fulfilled"),
                    ("RETAINED", "Retained with rationale"),
                    ("REJECTED", "Rejected"),
                    ("WITHDRAWN", "Withdrawn"),
                ],
                default="OPEN",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="DSRCaseEvent",
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
                ("actor_id", models.CharField(max_length=128)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("ASSIGNED", "Assigned"),
                            ("TASK_CREATED", "Review task created"),
                            ("TASK_UPDATED", "Review task updated"),
                            ("DECISION_RECORDED", "Decision recorded"),
                            ("PATIENT_UPDATE_RECORDED", "Patient update recorded"),
                        ],
                        max_length=32,
                    ),
                ),
                ("summary", models.CharField(max_length=255)),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                (
                    "dsr",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="events",
                        to="dsr.dsrrequest",
                    ),
                ),
            ],
            options={"ordering": ("timestamp", "pk")},
        ),
        migrations.CreateModel(
            name="DSRReviewTask",
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
                ("title", models.CharField(max_length=160)),
                ("details", models.TextField(blank=True)),
                ("assigned_to", models.CharField(blank=True, max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "Open"),
                            ("IN_PROGRESS", "In progress"),
                            ("COMPLETED", "Completed"),
                            ("BLOCKED", "Blocked"),
                        ],
                        default="OPEN",
                        max_length=16,
                    ),
                ),
                ("created_by", models.CharField(max_length=128)),
                ("updated_by", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "dsr",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="review_tasks",
                        to="dsr.dsrrequest",
                    ),
                ),
            ],
            options={"ordering": ("status", "created_at", "pk")},
        ),
        migrations.CreateModel(
            name="DSRDecision",
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
                ("category", models.CharField(default="overall", max_length=80)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("FULFILLED", "Fulfilled"),
                            ("PARTIALLY_FULFILLED", "Partially fulfilled"),
                            ("RETAINED", "Retained with rationale"),
                            ("REJECTED", "Rejected"),
                            ("WITHDRAWN", "Withdrawn"),
                            ("FURTHER_REVIEW", "Further review required"),
                        ],
                        max_length=32,
                    ),
                ),
                ("rationale", models.TextField()),
                ("recorded_by", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dsr",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decisions",
                        to="dsr.dsrrequest",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "pk")},
        ),
        migrations.CreateModel(
            name="DSRCommunication",
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
                ("message", models.TextField()),
                ("created_by", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dsr",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="communications",
                        to="dsr.dsrrequest",
                    ),
                ),
            ],
            options={"ordering": ("created_at", "pk")},
        ),
    ]
