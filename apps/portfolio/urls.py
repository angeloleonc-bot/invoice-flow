from django.urls import path

from . import views

app_name = "portfolio"

urlpatterns = [
    path("documents/", views.documents_list, name="documents_list"),
    path("customers/", views.customers_list, name="customers_list"),
    path("assignment/unassigned/", views.unassigned_documents, name="unassigned_documents"),
    path("assignment/workloads/", views.assignment_workloads, name="assignment_workloads"),
    path("customers/<int:customer_id>/", views.customer_detail, name="customer_detail"),
]
