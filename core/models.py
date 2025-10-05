from django.db import models
from django.contrib.auth.models import User, Group
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
from datetime import datetime, timedelta, time
import uuid

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    employee_id = models.CharField(max_length=20, unique=True)
    phone_number = models.CharField(max_length=20)
    commission_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active_salesman = models.BooleanField(default=False)
    hire_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.get_full_name()} ({self.employee_id})"
    
    def get_commission_rate(self):
        """Get user's commission rate or system default"""
        if self.commission_rate:
            return self.commission_rate
        return SystemConfig.get_config().default_commission_rate

class Client(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, unique=True)
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
    
    def get_booking_count(self):
        return self.bookings.exclude(status='canceled').count()

class Booking(models.Model):
    STATUS_CHOICES = [
        ('confirmed', 'Confirmed'),
        ('completed', 'Completed'),
        ('canceled', 'Canceled'),
        ('no_show', 'No Show'),
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
    
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='bookings')
    salesman = models.ForeignKey(User, on_delete=models.PROTECT, related_name='bookings')
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    duration_minutes = models.IntegerField(default=60)
    appointment_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
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
        # Generate Zoom link if needed
        if self.appointment_type == 'zoom' and not self.zoom_link:
            self.zoom_link = f"https://zoom.us/j/{uuid.uuid4().hex[:10]}"
        
        # Set commission amount if not set
        if not self.commission_amount:
            self.commission_amount = self.salesman.profile.get_commission_rate()
        
        super().save(*args, **kwargs)

class Unavailability(models.Model):
    REASON_CHOICES = [
        ('vacation', 'Vacation'),
        ('sick', 'Sick Leave'),
        ('training', 'Training'),
        ('personal', 'Personal Appointment'),
        ('company_holiday', 'Company Holiday'),
        ('other', 'Other'),
    ]
    
    salesman = models.ForeignKey(User, on_delete=models.CASCADE, related_name='unavailability_blocks')
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    reason = models.CharField(max_length=50, choices=REASON_CHOICES)
    notes = models.TextField(blank=True)
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.JSONField(null=True, blank=True)
    parent_block = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='occurrences')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='unavailability_created')
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['salesman']),
            models.Index(fields=['start_date']),
            models.Index(fields=['salesman', 'start_date', 'end_date']),
        ]
        ordering = ['start_date', 'start_time']
    
    def __str__(self):
        return f"{self.salesman.get_full_name()} - {self.reason} ({self.start_date})"
    
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
    default_commission_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('50.00'))
    buffer_time_minutes = models.IntegerField(default=30)
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
