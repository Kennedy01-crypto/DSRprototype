"""Role matrix for every API endpoint.

``EXPECTED_ROLES`` is the single reviewed statement of who may call what. The
tests fail if a new API route is added without an entry, if a view's declared
roles drift from this table, or if any other role (or no identity at all)
gets past the role gate.
"""

from collections.abc import Mapping

from django.test import TestCase
from django.urls import URLPattern, reverse
from rest_framework.test import APIClient
from rest_framework.views import APIView

from dsr import urls
from dsr.permissions import RolePermission

ALL_ROLES = (
    "patient",
    "dpo",
    "admin",
    "department_contributor",
    "independent_reviewer",
)
PATIENT = frozenset({"patient"})
DPO = frozenset({"dpo"})
DPO_OR_CONTRIBUTOR = frozenset({"dpo", "department_contributor"})
DPO_OR_REVIEWER = frozenset({"dpo", "independent_reviewer"})

# URL name -> roles for every method, or {METHOD: roles}.
EXPECTED_ROLES: dict[str, frozenset[str] | dict[str, frozenset[str]]] = {
    "dsr-submit": PATIENT,
    "dsr-my-requests": PATIENT,
    "dsr-detail": PATIENT,
    "dsr-withdraw": PATIENT,
    "dsr-communications": PATIENT,
    "dsr-published-responses": PATIENT,
    "dpo-dsrs": DPO,
    "dpo-dsrs-export": DPO,
    "dpo-deadline-policies": DPO,
    "dpo-dsr-detail": DPO_OR_REVIEWER,
    "dpo-dsr-transition": DPO_OR_REVIEWER,
    "dpo-dsr-assignment": DPO,
    "dpo-dsr-jurisdiction": DPO,
    "dpo-dsr-tasks": {"GET": DPO_OR_CONTRIBUTOR, "POST": DPO},
    "dpo-dsr-task": DPO_OR_CONTRIBUTOR,
    "dpo-assigned-work": frozenset({"department_contributor"}),
    "dpo-assigned-reviews": frozenset({"independent_reviewer"}),
    "dpo-dsr-exceptions": DPO,
    "dpo-dsr-decisions": DPO,
    "dpo-dsr-source-searches": DPO,
    "dpo-dsr-source-search": DPO,
    "dpo-dsr-response-draft": DPO,
    "dpo-dsr-response-versions": DPO,
    "dpo-dsr-response-publish": DPO,
    "dpo-dsr-communications": DPO,
}
HANDLER_METHODS = ("get", "post", "put", "patch", "delete")
# Route arguments only need to be well-formed: the role gate runs before
# any object is loaded.
URL_KWARG_VALUE = 999999


def api_routes() -> list[tuple[URLPattern, type[APIView]]]:
    routes = []
    for pattern in urls.urlpatterns:
        view_class = getattr(pattern.callback, "cls", None)
        if view_class is not None and issubclass(view_class, APIView):
            routes.append((pattern, view_class))
    return routes


def expected_for(name: str, method: str) -> frozenset[str]:
    expected = EXPECTED_ROLES[name]
    if isinstance(expected, Mapping):
        return expected.get(method, frozenset())
    return expected


class RoleMatrixTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_every_api_route_is_in_the_reviewed_table(self) -> None:
        names = {pattern.name for pattern, _ in api_routes()}
        self.assertEqual(names, set(EXPECTED_ROLES))

    def test_every_api_view_uses_the_role_permission(self) -> None:
        for pattern, view_class in api_routes():
            with self.subTest(route=pattern.name):
                self.assertIn(RolePermission, view_class.permission_classes)

    def test_declared_roles_match_the_reviewed_table(self) -> None:
        for pattern, view_class in api_routes():
            declared = view_class.allowed_roles
            for method in HANDLER_METHODS:
                if not hasattr(view_class, method):
                    continue
                verb = method.upper()
                with self.subTest(route=pattern.name, method=verb):
                    actual = (
                        declared.get(verb, frozenset())
                        if isinstance(declared, Mapping)
                        else declared
                    )
                    self.assertTrue(actual, "a handler must allow at least one role")
                    self.assertEqual(frozenset(actual), expected_for(pattern.name, verb))

    def test_role_gate_blocks_and_allows_exactly_the_expected_roles(self) -> None:
        for pattern, view_class in api_routes():
            kwargs = {key: URL_KWARG_VALUE for key in pattern.pattern.converters}
            url = reverse(pattern.name, kwargs=kwargs)
            for method in HANDLER_METHODS:
                if not hasattr(view_class, method):
                    continue
                allowed = expected_for(pattern.name, method.upper())
                for role in (None, *ALL_ROLES):
                    with self.subTest(route=pattern.name, method=method, role=role):
                        self.client.credentials(
                            **(
                                {"HTTP_AUTHORIZATION": f"Bearer {role}:someone"}
                                if role
                                else {}
                            )
                        )
                        response = getattr(self.client, method)(url, {}, format="json")
                        blocked_by_role_gate = (
                            response.status_code == 403
                            and response.data.get("detail") == RolePermission.message
                        )
                        if role in allowed:
                            self.assertFalse(blocked_by_role_gate)
                        elif role is None:
                            self.assertEqual(response.status_code, 403)
                        else:
                            self.assertTrue(blocked_by_role_gate, response.status_code)

    def test_contributor_cannot_discover_cases_without_assigned_work(self) -> None:
        from dsr.models import DSRRequest

        dsr = DSRRequest.objects.create(
            patient_id="alice",
            request_type=DSRRequest.RequestType.ACCESS,
            description="Synthetic",
            response_due_at=None,
        )
        self.client.credentials(
            HTTP_AUTHORIZATION="Bearer department_contributor:someone"
        )
        response = self.client.get(f"/api/v1/dpo/dsrs/{dsr.pk}/tasks/")
        self.assertEqual(response.status_code, 404)
