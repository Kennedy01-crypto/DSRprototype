from django.urls import path

from .views import DSRConsoleView, DSRTransitionView, MyRequestsView, SubmitDSRView

urlpatterns = [
    path("api/v1/dsrs/submit/", SubmitDSRView.as_view(), name="dsr-submit"),
    path("api/v1/dsrs/my-requests/", MyRequestsView.as_view(), name="dsr-my-requests"),
    path("api/v1/dpo/dsrs/", DSRConsoleView.as_view(), name="dpo-dsrs"),
    path("api/v1/dpo/dsrs/<int:pk>/transition/", DSRTransitionView.as_view(), name="dpo-dsr-transition"),
]