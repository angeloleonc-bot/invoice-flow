from django.urls import path

from . import views
from . import statement_views

app_name = "portfolio"

urlpatterns = [
    path("documents/", views.documents_list, name="documents_list"),
    path("customers/", views.customers_list, name="customers_list"),
    path("assignment/unassigned/", views.unassigned_documents, name="unassigned_documents"),
    path("assignment/workloads/", views.assignment_workloads, name="assignment_workloads"),
    path("customers/<int:customer_id>/", views.customer_detail, name="customer_detail"),
    path(
        "customers/<int:customer_id>/statement/form/",
        statement_views.customer_statement_form,
        name="customer_statement_form",
    ),
    path(
        "customers/<int:customer_id>/statement/preview/",
        statement_views.customer_statement_create_preview,
        name="customer_statement_create_preview",
    ),
    path(
        "statements/<uuid:public_id>/",
        statement_views.customer_statement_preview,
        name="customer_statement_preview",
    ),
    path(
        "statements/<uuid:public_id>/pdf/",
        statement_views.customer_statement_pdf,
        name="customer_statement_pdf",
    ),
    path(
        "statements/<uuid:public_id>/xlsx/",
        statement_views.customer_statement_xlsx,
        name="customer_statement_xlsx",
    ),
    path(
        "statements/<uuid:public_id>/send/",
        statement_views.customer_statement_send,
        name="customer_statement_send",
    ),

    path("assignment/assign/",views.assign_documents,name="assign_documents",),
]
