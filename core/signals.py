from django.db.models.signals import post_save, post_delete, pre_save
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, Booking, Unavailability, PayrollPeriod, AuditLog, SystemConfig
import json

def get_client_ip(request):
    """Get client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

def create_audit_log(user, action, entity_type, entity_id, changes, request=None):
    """Create audit log entry"""
    ip_address = get_client_ip(request) if request else None
    user_agent = request.META.get('HTTP_USER_AGENT', '') if request else ''
    
    AuditLog.objects.create(
        user=user,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=changes,
        ip_address=ip_address,
        user_agent=user_agent
    )

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically create UserProfile when User is created"""
    if created and not hasattr(instance, 'profile'):
        UserProfile.objects.create(
            user=instance,
            employee_id=f"EMP{instance.id:05d}",
            phone_number='',
            hire_date=timezone.now().date()
        )

@receiver(post_save, sender=Booking)
def log_booking_changes(sender, instance, created, **kwargs):
    """Log booking creates/updates"""
    action = 'create' if created else 'update'
    changes = {
        'client': str(instance.client),
        'salesman': instance.salesman.get_full_name(),
        'date': str(instance.appointment_date),
        'time': str(instance.appointment_time),
        'type': instance.appointment_type,
        'status': instance.status,
    }
    
    create_audit_log(
        user=instance.created_by if created else instance.updated_by,
        action=action,
        entity_type='Booking',
        entity_id=instance.id,
        changes=changes
    )

@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """Log user login"""
    create_audit_log(
        user=user,
        action='login',
        entity_type='User',
        entity_id=user.id,
        changes={'username': user.username},
        request=request
    )

@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    """Log user logout"""
    if user:
        create_audit_log(
            user=user,
            action='logout',
            entity_type='User',
            entity_id=user.id,
            changes={'username': user.username},
            request=request
        )