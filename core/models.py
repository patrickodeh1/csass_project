from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin, Group
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
from datetime import datetime, timedelta, time
import uuid

class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication"""
    
    def create_user(self, email, username, password=None, **extra_fields):
        """Create and save a regular user"""
        if not email:
            raise ValueError('Users must have an email address')
        if not username:
            raise ValueError('Users must have a username')
        
        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, username, password=None, **extra_fields):
        """Create and save a superuser"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True')
        
        return self.create_user(email, username, password, **extra_fields)

# ============================================================
# CUSTOM USER MODEL
# ============================================================

class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with email and username authentication"""
    
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    plain_text_password = models.CharField(max_length=255, blank=True, null=True)
    
    # Additional fields
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    phone_number = models.CharField(max_length=20)
    commission_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active_salesman = models.BooleanField(default=False)
    hire_date = models.DateField(null=True)
    
    # Status fields
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    
    # Timestamps
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)
    
    # Password reset
    password_reset_token = models.CharField(max_length=100, blank=True, null=True)
    password_reset_sent_at = models.DateTimeField(null=True, blank=True)
    
    # Login attempts tracking
    failed_login_attempts = models.IntegerField(default=0)
    last_failed_login = models.DateTimeField(null=True, blank=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email', 'first_name', 'last_name']
    
    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['last_name', 'first_name']
    
    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"

    def get_full_name(self):
        """Return the first_name plus the last_name, with a space in between"""
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_short_name(self):
        """Return the short name for the user"""
        return self.first_name
    
    def get_commission_rate(self):
        """Get user's commission rate or system default"""
        if self.commission_rate:
            return self.commission_rate
        return SystemConfig.get_config().default_commission_rate
    
    def has_group(self, group_name):
        """Check if user belongs to a group"""
        return self.groups.filter(name=group_name).exists()
    
    def get_roles(self):
        """Get list of user's role names"""
        return list(self.groups.values_list('name', flat=True))
    
    def reset_failed_login_attempts(self):
        """Reset failed login attempts counter"""
        self.failed_login_attempts = 0
        self.last_failed_login = None
        self.save(update_fields=['failed_login_attempts', 'last_failed_login'])
    
    def increment_failed_login(self):
        """Increment failed login attempts"""
        self.failed_login_attempts += 1
        self.last_failed_login = timezone.now()
        self.save(update_fields=['failed_login_attempts', 'last_failed_login'])
    
    def is_account_locked(self):
        """Check if account is locked due to too many failed attempts"""
        from django.conf import settings
        max_attempts = getattr(settings, 'MAX_LOGIN_ATTEMPTS', 5)
        
        if self.failed_login_attempts >= max_attempts:
            # Check if 30 minutes have passed since last failed attempt
            if self.last_failed_login:
                time_since_last_fail = timezone.now() - self.last_failed_login
                if time_since_last_fail < timedelta(minutes=30):
                    return True
                else:
                    # Auto-reset after 30 minutes
                    self.reset_failed_login_attempts()
        return False


    def get_available_slots_for_date(self, date):
        """Get available time slots for a specific date"""
        date = date.date()
        return self.available_timeslots.filter(
            date=date,
            is_active=True
        ).order_by('start_time')

        
class Client(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='clients_created')
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['phone_number']),
            models.Index(fields=['email', 'phone_number']),
        ]
    
    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"
    
    def get_booking_count(self):
        return self.bookings.exclude(status='canceled').count()

    
class Booking(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('completed', 'Completed'),
        ('canceled', 'Canceled'),
        ('no_show', 'No Show'),
        ('declined', 'Declined'),
    ]
    
    TYPE_CHOICES = [
        ('zoom', 'Zoom'),
        ('in_person', 'In-Person'),
    ]
    
    CANCELLATION_REASONS = [
        ('client_request', 'Client Requested Cancellation'),
        ('no_show', 'Client No-Show'),
        ('salesman_unavailable', 'Salesman Unavailable'),
        ('duplicate', 'Duplicate Booking'),
        ('other', 'Other'),
    ]
    business_name = models.CharField(max_length=200, help_text="business name")
    business_owner = models.CharField(max_length=100, help_text="business owner's full name")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='bookings')
    salesman = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bookings')
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    duration_minutes = models.IntegerField(default=60)
    appointment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    zoom_link = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    cancellation_reason = models.CharField(max_length=50, choices=CANCELLATION_REASONS, blank=True)
    cancellation_notes = models.TextField(blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings_canceled')
    is_locked = models.BooleanField(default=False)
    payroll_period = models.ForeignKey('PayrollPeriod', on_delete=models.PROTECT, null=True, blank=True, related_name='bookings')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bookings_created')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings_updated')
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings_approved')
    declined_at = models.DateTimeField(null=True, blank=True)
    declined_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='bookings_declined')
    decline_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['appointment_date']),
            models.Index(fields=['salesman']),
            models.Index(fields=['status']),
            models.Index(fields=['salesman', 'appointment_date', 'status']),
            models.Index(fields=['payroll_period']),
        ]
        unique_together = ['salesman', 'appointment_date', 'appointment_time']
        ordering = ['appointment_date', 'appointment_time']
    
    def __str__(self):
        return f"{self.client} with {self.salesman.get_full_name()} on {self.appointment_date}"
    
    def counts_for_commission(self):
        """Check if booking counts for commission - must be confirmed or completed"""
        return self.status in ['confirmed', 'completed']
    
    def can_be_approved(self):
        """Check if booking can be approved"""
        return self.status == 'pending' and not self.is_in_past()
    
    def can_be_declined(self):
        """Check if booking can be declined"""
        return self.status == 'pending'

    def get_end_time(self):
        """Calculate end time based on duration"""
        dt = datetime.combine(self.appointment_date, self.appointment_time)
        end_dt = dt + timedelta(minutes=self.duration_minutes)
        return end_dt.time()
    
    def get_buffer_end_time(self):
        """Calculate when buffer period ends"""
        config = SystemConfig.get_config()
        dt = datetime.combine(self.appointment_date, self.appointment_time)
        buffer_dt = dt + timedelta(minutes=self.duration_minutes + config.buffer_time_minutes)
        return buffer_dt.time()
    
    def is_editable(self):
        """Check if booking can be edited"""
        if self.is_locked:
            return False
        if self.is_in_past():
            return False
        return True
    
    def is_in_past(self):
        """Check if appointment has passed"""
        appt_datetime = datetime.combine(self.appointment_date, self.appointment_time)
        return timezone.make_aware(appt_datetime) < timezone.now()
    
    def counts_for_commission(self):
        """Check if booking counts for commission"""
        return self.status in ['confirmed', 'completed']
    
    def save(self, *args, **kwargs):
        # Set commission amount if not set
        if not self.commission_amount:
            if self.created_by and self.created_by.groups.filter(name='remote_agent').exists():
                # Remote agents: $30 for Zoom, $50 for In-Person
                if self.appointment_type == 'zoom':
                    self.commission_amount = Decimal('30.00')
                else:  # in_person
                    self.commission_amount = Decimal('50.00')
            else:
                self.commission_amount = Decimal('0.00') 
        
        super().save(*args, **kwargs)


    def conflicts_with_booking(self):
        """Check if this unavailability conflicts with any confirmed bookings"""
        return Booking.objects.filter(
            salesman=self.salesman,
            status='confirmed',
            appointment_date__gte=self.start_date,
            appointment_date__lte=self.end_date,
            appointment_time__gte=self.start_time,
            appointment_time__lt=self.end_time
        ).exists()

class PayrollPeriod(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('finalized', 'Finalized'),
    ]
    
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='payrolls_finalized')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['start_date']),
            models.Index(fields=['status']),
        ]
        unique_together = ['start_date', 'end_date']
        ordering = ['-start_date']
    
    def __str__(self):
        return f"Week of {self.start_date.strftime('%b %d')} - {self.end_date.strftime('%b %d, %Y')}"
    
    def get_week_label(self):
        return self.__str__()
    
    def calculate_commissions(self):
        """Calculate total commissions for all users in this period"""
        bookings = self.bookings.filter(
            status__in=['confirmed', 'completed']
        ).values('salesman').annotate(
            total=models.Sum('commission_amount'),
            count=models.Count('id')
        )
        
        return {b['salesman']: {'total': b['total'], 'count': b['count']} for b in bookings}

class PayrollAdjustment(models.Model):
    ADJUSTMENT_TYPES = [
        ('bonus', 'Bonus'),
        ('penalty', 'Penalty'),
        ('correction', 'Correction'),
        ('cancellation_after_finalized', 'Cancellation After Finalized'),
    ]
    
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name='adjustments')
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='payroll_adjustments')
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, null=True, blank=True, related_name='adjustments')
    adjustment_type = models.CharField(max_length=50, choices=ADJUSTMENT_TYPES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='adjustments_created')
    
    class Meta:
        indexes = [
            models.Index(fields=['payroll_period']),
            models.Index(fields=['user']),
        ]
    
    def __str__(self):
        return f"{self.adjustment_type} - {self.user.get_full_name()} - ${self.amount}"

class CompanyHoliday(models.Model):
    name = models.CharField(max_length=100)
    date = models.DateField()
    is_recurring_annually = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['date']),
        ]
        ordering = ['date']
    
    def __str__(self):
        return f"{self.name} - {self.date}"

class SystemConfig(models.Model):
    # Singleton pattern - only one record with id=1
    company_name = models.CharField(max_length=200, default='Revenue Acceleration Unit')
    timezone = models.CharField(max_length=50, default='UTC')
    default_commission_rate_in_person = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('50.00'))
    default_commission_rate_zoom = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('30.00'))
    buffer_time_minutes = models.IntegerField(default=30)
    zoom_link = models.URLField(
        blank=True,
        default='us04web.zoom.us/j/77703295752?pwd=n8xdNGWmJa7mFnn1JlFwUw0C0jXNH0.1'
        )
    reminder_lead_time_hours = models.IntegerField(default=24)
    max_advance_booking_days = models.IntegerField(default=90)
    min_advance_booking_hours = models.IntegerField(default=2)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    
    @classmethod
    def get_config(cls):
        """Get or create the singleton config"""
        config, created = cls.objects.get_or_create(id=1)
        return config
    
    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"System Configuration - {self.company_name}"

class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('finalize', 'Finalize'),
        ('adjust', 'Adjust'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    entity_type = models.CharField(max_length=50)
    entity_id = models.IntegerField()
    changes = models.JSONField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['user']),
            models.Index(fields=['entity_type']),
            models.Index(fields=['entity_type', 'entity_id']),
        ]
        ordering = ['-timestamp']
    
    def __str__(self):
        user_str = self.user.get_full_name() if self.user else 'System'
        return f"{user_str} - {self.action} {self.entity_type} ({self.timestamp})"


class AvailableTimeSlot(models.Model):
    """Admin-defined time slots for bookings"""
    APPOINTMENT_TYPE_CHOICES = [
        ('zoom', 'Zoom'),
        ('in_person', 'In-Person'),
    ]
    
    salesman = models.ForeignKey(User, on_delete=models.CASCADE, related_name='available_timeslots')
    date = models.DateField(null=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    appointment_type = models.CharField(
        max_length=20, 
        choices=APPOINTMENT_TYPE_CHOICES,
        help_text="Type of appointments allowed in this slot"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='timeslots_created')
    
    class Meta:
        ordering = ['salesman', 'date', 'start_time']
        indexes = [
            models.Index(fields=['salesman', 'date', 'appointment_type', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.salesman.get_full_name()} - {self.date.strftime('%b %d, %Y')} {self.start_time}-{self.end_time} ({self.get_appointment_type_display()})"
    
    def is_time_in_slot(self, time_obj):
        """Check if a given time falls within this slot"""
        return self.start_time <= time_obj < self.end_time
