from __future__ import annotations

from typing import Any, Callable

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import PatientPortalJWTAuthentication, SimulatedPrincipal
from .models import DSRRequest
from .serializers import DSRStatusSerializer, DSRSubmitSerializer, DSRTransitionSerializer


class PatientEndpoint(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def _principal(self, request: Request) -> SimulatedPrincipal:
        if not isinstance(request.user, SimulatedPrincipal) or request.user.role != "patient":
            raise PermissionDenied("A patient portal identity is required")
        return request.user


class SubmitDSRView(PatientEndpoint):
    def post(self, request: Request) -> Response:
        principal = self._principal(request)
        serializer = DSRSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            dsr = serializer.save(patient_id=principal.patient_id)
            dsr.accept_submission(actor_id=principal.patient_id, reason="Authenticated portal intake")
            dsr.save(update_fields=("state", "updated_at"))
        return Response(DSRStatusSerializer(dsr).data, status=status.HTTP_201_CREATED)


class MyRequestsView(PatientEndpoint):
    def get(self, request: Request) -> Response:
        principal = self._principal(request)
        requests = DSRRequest.objects.filter(patient_id=principal.patient_id)
        return Response(DSRStatusSerializer(requests, many=True).data)


class DPOPermission:
    @staticmethod
    def check(request: Request) -> None:
        if not isinstance(request.user, SimulatedPrincipal) or request.user.role != "dpo":
            raise PermissionDenied("DPO role is required")


class DSRConsoleView(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def get(self, request: Request) -> Response:
        DPOPermission.check(request)
        requests = DSRRequest.objects.all()
        data = DSRStatusSerializer(requests, many=True).data
        heatmap: dict[str, int] = {}
        for dsr in requests:
            heatmap[dsr.department or "Unassigned"] = heatmap.get(dsr.department or "Unassigned", 0) + 1
        return Response({"requests": data, "departmental_heatmap": heatmap})


class DSRTransitionView(APIView):
    authentication_classes = (PatientPortalJWTAuthentication,)

    def post(self, request: Request, pk: int) -> Response:
        DPOPermission.check(request)
        serializer = DSRTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dsr = DSRRequest.objects.get(pk=pk)
        action: Callable[..., None] = getattr(dsr, serializer.validated_data["action"])
        action(actor_id=request.user.patient_id, reason=serializer.validated_data["reason"])
        dsr.save(update_fields=("state", "closed_at", "updated_at"))
        return Response(DSRStatusSerializer(dsr).data)