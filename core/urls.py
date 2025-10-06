from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Authentication
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Password Reset
    path('password-reset/', views.CustomPasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', views.CustomPasswordResetDoneView.as_view(), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', views.CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password-reset-complete/', views.CustomPasswordResetCompleteView.as_view(), name='password_reset_complete'),
    
    # Password Change
    path('password-change/', views.password_change_view, name='password_change'),
    
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
    
    # Payroll (admiin)
    path('admiin/payroll/', views.payroll_view, name='payroll'),
    path('admiin/payroll/<int:pk>/finalize/', views.payroll_finalize, name='payroll_finalize'),
    path('admiin/payroll/export/', views.payroll_export, name='payroll_export'),
    path('admiin/payroll/adjustment/new/', views.payroll_adjustment_create, name='payroll_adjustment_create'),
    
    # Users (admiin)
    path('admiin/users/', views.users_view, name='users'),
    path('admiin/users/new/', views.user_create, name='user_create'),
    path('admiin/users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('admiin/users/<int:pk>/deactivate/', views.user_deactivate, name='user_deactivate'),
    
    # Settings (admiin)
    path('admiin/settings/', views.settings_view, name='settings'),
    
    # Audit Log (admiin)
    path('admiin/audit-log/', views.audit_log_view, name='audit_log'),
]