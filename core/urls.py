from django.urls import path
from . import views

urlpatterns = [
    # Authentication
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Calendar & Bookings
    path('calendar/', views.calendar_view, name='calendar'),
    path('booking/new/', views.booking_create, name='booking_create'),
    path('booking/<int:pk>/', views.booking_detail, name='booking_detail'),
    path('booking/<int:pk>/edit/', views.booking_edit, name='booking_edit'),
    path('booking/<int:pk>/cancel/', views.booking_cancel, name='booking_cancel'),
    
    # Commissions
    path('commissions/', views.commissions_view, name='commissions'),
    
    # Availability
    path('availability/', views.availability_view, name='availability'),
    path('availability/new/', views.availability_create, name='availability_create'),
    path('availability/<int:pk>/edit/', views.availability_edit, name='availability_edit'),
    path('availability/<int:pk>/delete/', views.availability_delete, name='availability_delete'),
    
    # Payroll (Admin)
    path('admin/payroll/', views.payroll_view, name='payroll'),
    path('admin/payroll/<int:pk>/finalize/', views.payroll_finalize, name='payroll_finalize'),
    path('admin/payroll/export/', views.payroll_export, name='payroll_export'),
    path('admin/payroll/adjustment/new/', views.payroll_adjustment_create, name='payroll_adjustment_create'),
    
    # Users (Admin)
    path('admin/users/', views.users_view, name='users'),
    path('admin/users/new/', views.user_create, name='user_create'),
    path('admin/users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('admin/users/<int:pk>/deactivate/', views.user_deactivate, name='user_deactivate'),
    
    # Settings (Admin)
    path('admin/settings/', views.settings_view, name='settings'),
    
    # Audit Log (Admin)
    path('admin/audit-log/', views.audit_log_view, name='audit_log'),
]