from django import forms
from django.contrib.auth.models import User, Group
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field, Div, HTML
from .models import Booking, Client, Unavailability, UserProfile, PayrollAdjustment, SystemConfig
from datetime import datetime, timedelta

class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label='Email Address',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'})
    )
    remember_me = forms.BooleanField(required=False, initial=False)

class BookingForm(forms.ModelForm):
    client_first_name = forms.CharField(max_length=100, required=True)
    client_last_name = forms.CharField(max_length=100, required=True)
    client_email = forms.EmailField(required=True)
    client_phone = forms.CharField(max_length=20, required=True)
    
    class Meta:
        model = Booking
        fields = ['salesman', 'appointment_date', 'appointment_time', 'duration_minutes', 
                  'appointment_type', 'notes']
        widgets = {
            'appointment_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'appointment_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'duration_minutes': forms.Select(choices=[(30, '30 min'), (45, '45 min'), (60, '1 hour'), (90, '1.5 hours')], attrs={'class': 'form-control'}),
            'appointment_type': forms.Select(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        
        # Filter salesmen to only active salesmen
        self.fields['salesman'].queryset = User.objects.filter(
            profile__is_active_salesman=True,
            is_active=True
        )
        self.fields['salesman'].widget.attrs['class'] = 'form-control'
        
        # Pre-fill client info if editing
        if self.instance and self.instance.pk:
            self.fields['client_first_name'].initial = self.instance.client.first_name
            self.fields['client_last_name'].initial = self.instance.client.last_name
            self.fields['client_email'].initial = self.instance.client.email
            self.fields['client_phone'].initial = self.instance.client.phone_number
    
    def clean(self):
        cleaned_data = super().clean()
        salesman = cleaned_data.get('salesman')
        appointment_date = cleaned_data.get('appointment_date')
        appointment_time = cleaned_data.get('appointment_time')
        duration_minutes = cleaned_data.get('duration_minutes')
        
        if all([salesman, appointment_date, appointment_time, duration_minutes]):
            # Check for booking conflicts
            has_conflict, conflict_booking = check_booking_conflicts(
                salesman, appointment_date, appointment_time, duration_minutes,
                exclude_booking_id=self.instance.pk if self.instance.pk else None
            )
            
            if has_conflict:
                raise forms.ValidationError(
                    f"This time slot conflicts with an existing booking: {conflict_booking}"
                )
            
            # Check for unavailability
            has_unavailable, unavailable_block = check_unavailability_conflicts(
                salesman, appointment_date, appointment_time, duration_minutes
            )
            
            if has_unavailable:
                raise forms.ValidationError(
                    f"Salesman is unavailable during this time: {unavailable_block.reason}"
                )
            
            # Check booking constraints
            config = SystemConfig.get_config()
            appt_datetime = datetime.combine(appointment_date, appointment_time)
            now = datetime.now()
            
            # Check minimum advance booking
            min_advance = now + timedelta(hours=config.min_advance_booking_hours)
            if appt_datetime < min_advance:
                raise forms.ValidationError(
                    f"Bookings must be made at least {config.min_advance_booking_hours} hours in advance"
                )
            
            # Check maximum advance booking
            max_advance = now + timedelta(days=config.max_advance_booking_days)
            if appt_datetime > max_advance:
                raise forms.ValidationError(
                    f"Bookings cannot be made more than {config.max_advance_booking_days} days in advance"
                )
        
        return cleaned_data
    
    def save(self, commit=True):
        booking = super().save(commit=False)
        
        # Get or create client
        client, created = Client.objects.get_or_create(
            email=self.cleaned_data['client_email'],
            defaults={
                'first_name': self.cleaned_data['client_first_name'],
                'last_name': self.cleaned_data['client_last_name'],
                'phone_number': self.cleaned_data['client_phone'],
                'created_by': self.request.user if self.request else booking.salesman
            }
        )
        
        if not created:
            # Update existing client info
            client.first_name = self.cleaned_data['client_first_name']
            client.last_name = self.cleaned_data['client_last_name']
            client.phone_number = self.cleaned_data['client_phone']
            client.save()
        
        booking.client = client
        
        if not booking.pk:
            booking.created_by = self.request.user if self.request else booking.salesman
            booking.status = 'confirmed'
        else:
            booking.updated_by = self.request.user if self.request else booking.salesman
        
        if commit:
            booking.save()
        
        return booking

class CancelBookingForm(forms.Form):
    cancellation_reason = forms.ChoiceField(
        choices=Booking.CANCELLATION_REASONS,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    cancellation_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Additional notes...'})
    )

class UnavailabilityForm(forms.ModelForm):
    class Meta:
        model = Unavailability
        fields = ['salesman', 'start_date', 'end_date', 'start_time', 'end_time', 'reason', 'notes']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'end_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'reason': forms.Select(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.is_admin = kwargs.pop('is_admin', False)
        super().__init__(*args, **kwargs)
        
        if not self.is_admin:
            # Regular users can only manage their own availability
            self.fields['salesman'].widget = forms.HiddenInput()
        else:
            # Admins can manage anyone's availability
            self.fields['salesman'].queryset = User.objects.filter(
                profile__is_active_salesman=True,
                is_active=True
            )
            self.fields['salesman'].widget.attrs['class'] = 'form-control'
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        
        if start_date and end_date:
            if start_date > end_date:
                raise forms.ValidationError("End date must be after start date")
        
        if start_time and end_time:
            if start_time >= end_time:
                raise forms.ValidationError("End time must be after start time")
        
        # Check for conflicts with existing bookings
        if all([cleaned_data.get('salesman'), start_date, end_date, start_time, end_time]):
            salesman = cleaned_data['salesman']
            conflicts = Booking.objects.filter(
                salesman=salesman,
                status='confirmed',
                appointment_date__gte=start_date,
                appointment_date__lte=end_date,
                appointment_time__gte=start_time,
                appointment_time__lt=end_time
            )
            
            if self.instance.pk:
                conflicts = conflicts.exclude(id=self.instance.pk)
            
            if conflicts.exists():
                conflict_list = ', '.join([str(b) for b in conflicts[:3]])
                raise forms.ValidationError(
                    f"Cannot block this time - conflicts with existing bookings: {conflict_list}"
                )
        
        return cleaned_data
    
    def save(self, commit=True):
        unavailability = super().save(commit=False)
        unavailability.created_by = self.request.user if self.request else unavailability.salesman
        
        if commit:
            unavailability.save()
        
        return unavailability

class PayrollAdjustmentForm(forms.ModelForm):
    class Meta:
        model = PayrollAdjustment
        fields = ['user', 'adjustment_type', 'amount', 'reason', 'booking']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-control'}),
            'adjustment_type': forms.Select(attrs={'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
            'booking': forms.Select(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.payroll_period = kwargs.pop('payroll_period', None)
        super().__init__(*args, **kwargs)
        
        # Filter users to those with bookings in this period
        if self.payroll_period:
            self.fields['user'].queryset = User.objects.filter(
                bookings__payroll_period=self.payroll_period
            ).distinct()
            
            self.fields['booking'].queryset = Booking.objects.filter(
                payroll_period=self.payroll_period
            )
        
        self.fields['booking'].required = False

class UserForm(forms.ModelForm):
    first_name = forms.CharField(max_length=100, required=True)
    last_name = forms.CharField(max_length=100, required=True)
    email = forms.EmailField(required=True)
    employee_id = forms.CharField(max_length=20, required=True)
    phone_number = forms.CharField(max_length=20, required=True)
    commission_rate = forms.DecimalField(max_digits=10, decimal_places=2, required=False)
    is_active_salesman = forms.BooleanField(required=False)
    hire_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    roles = forms.MultipleChoiceField(
        choices=[('sales_support', 'Sales Support'), ('salesman', 'Salesman'), ('admin', 'Administrator')],
        widget=forms.CheckboxSelectMultiple,
        required=False
    )
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'is_active']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if self.instance and self.instance.pk:
            # Pre-fill profile fields
            profile = self.instance.profile
            self.fields['employee_id'].initial = profile.employee_id
            self.fields['phone_number'].initial = profile.phone_number
            self.fields['commission_rate'].initial = profile.commission_rate
            self.fields['is_active_salesman'].initial = profile.is_active_salesman
            self.fields['hire_date'].initial = profile.hire_date
            
            # Pre-fill roles
            user_groups = list(self.instance.groups.values_list('name', flat=True))
            self.fields['roles'].initial = user_groups
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        
        if commit:
            user.save()
            
            # Update profile
            profile = user.profile
            profile.employee_id = self.cleaned_data['employee_id']
            profile.phone_number = self.cleaned_data['phone_number']
            profile.commission_rate = self.cleaned_data.get('commission_rate')
            profile.is_active_salesman = self.cleaned_data.get('is_active_salesman', False)
            profile.hire_date = self.cleaned_data['hire_date']
            profile.save()
            
            # Update groups/roles
            user.groups.clear()
            for role in self.cleaned_data.get('roles', []):
                group, created = Group.objects.get_or_create(name=role)
                user.groups.add(group)
        
        return user

class SystemConfigForm(forms.ModelForm):
    class Meta:
        model = SystemConfig
        fields = ['company_name', 'timezone', 'default_commission_rate', 'buffer_time_minutes',
                  'reminder_lead_time_hours', 'max_advance_booking_days', 'min_advance_booking_hours']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'timezone': forms.TextInput(attrs={'class': 'form-control'}),
            'default_commission_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'buffer_time_minutes': forms.NumberInput(attrs={'class': 'form-control'}),
            'reminder_lead_time_hours': forms.NumberInput(attrs={'class': 'form-control'}),
            'max_advance_booking_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'min_advance_booking_hours': forms.NumberInput(attrs={'class': 'form-control'}),
        }