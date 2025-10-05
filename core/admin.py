from django.contrib import admin
from .models import (UserProfile, Client, Booking, Unavailability, 
                     PayrollPeriod, PayrollAdjustment, CompanyHoliday, 
                     SystemConfig, AuditLog)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'employee_id', 'phone_number', 'is_active_salesman', 'hire_date']
    list_filter = ['is_active_salesman', 'hire_date']
    search_fields = ['user__first_name', 'user__last_name', 'employee_id', 'phone_number']

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'email', 'phone_number', 'created_at']
    search_fields = ['first_name', 'last_name', 'email', 'phone_number']
    readonly_fields = ['created_at', 'updated_at']

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['client', 'salesman', 'appointment_date', 'appointment_time', 
                    'appointment_type', 'status', 'commission_amount']
    list_filter = ['status', 'appointment_type', 'appointment_date', 'is_locked']
    search_fields = ['client__first_name', 'client__last_name', 'salesman__first_name', 
                     'salesman__last_name']
    readonly_fields = ['created_at', 'updated_at', 'canceled_at']
    date_hierarchy = 'appointment_date'

@admin.register(Unavailability)
class UnavailabilityAdmin(admin.ModelAdmin):
    list_display = ['salesman', 'start_date', 'end_date', 'start_time', 'end_time', 'reason']
    list_filter = ['reason', 'start_date']
    search_fields = ['salesman__first_name', 'salesman__last_name']

@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ['start_date', 'end_date', 'status', 'finalized_by', 'finalized_at']
    list_filter = ['status', 'start_date']
    readonly_fields = ['finalized_at', 'created_at']

@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(admin.ModelAdmin):
    list_display = ['payroll_period', 'user', 'adjustment_type', 'amount', 'created_by', 'created_at']
    list_filter = ['adjustment_type', 'payroll_period']
    search_fields = ['user__first_name', 'user__last_name', 'reason']

@admin.register(CompanyHoliday)
class CompanyHolidayAdmin(admin.ModelAdmin):
    list_display = ['name', 'date', 'is_recurring_annually']
    list_filter = ['is_recurring_annually', 'date']
    ordering = ['date']

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'default_commission_rate', 'buffer_time_minutes', 
                    'timezone', 'updated_at']
    readonly_fields = ['updated_at']

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['timestamp', 'user', 'action', 'entity_type', 'entity_id']
    list_filter = ['action', 'entity_type', 'timestamp']
    search_fields = ['user__first_name', 'user__last_name', 'entity_type']
    readonly_fields = ['timestamp']
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
