from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import Group
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field, Div, HTML
from .models import Booking, Client, AvailableTimeSlot, PayrollAdjustment, SystemConfig, User
from datetime import datetime, timedelta
import logging
from .utils import check_booking_conflicts
from django.db import transaction

logger = logging.getLogger(__name__)

class UserForm(forms.ModelForm):
    username = forms.CharField(
        max_length=150, 
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.'
    )
    first_name = forms.CharField(max_length=100, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=100, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={'class': 'form-control'}))
    phone_number = forms.CharField(max_length=20, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    commission_rate = forms.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        required=False, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    is_active_salesman = forms.BooleanField(required=False)
    hire_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))
    
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 
            'placeholder': 'Leave blank to keep current password'
        }),
        help_text='Leave blank if password is set programmatically.'
    )
    password_confirm = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 
            'placeholder': 'Confirm password'
        }),
        label='Confirm Password'
    )
    
    roles = forms.MultipleChoiceField(
        choices=[
            ('remote_agent', 'Remote Agent'), 
            ('salesman', 'Salesman'), 
            ('admin', 'Administrator')
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False
    )
    
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone_number',
                  'commission_rate', 'is_active_salesman', 'hire_date', 'is_active']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if self.instance and self.instance.pk:
            # Editing existing user
            self.fields['phone_number'].initial = self.instance.phone_number
            self.fields['commission_rate'].initial = self.instance.commission_rate
            self.fields['is_active_salesman'].initial = self.instance.is_active_salesman
            self.fields['hire_date'].initial = self.instance.hire_date
            user_groups = list(self.instance.groups.values_list('name', flat=True))
            self.fields['roles'].initial = user_groups
            self.fields['password'].help_text = 'Leave blank to keep current password.'
        else:
            # Creating new user
            self.fields['password'].help_text = 'Leave blank if password is set programmatically.'
    
    def clean_username(self):
        username = self.cleaned_data.get('username')
        if not self.instance.pk:
            if User.objects.filter(username=username).exists():
                raise forms.ValidationError("A user with this username already exists.")
        else:
            if User.objects.filter(username=username).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("A user with this username already exists.")
        return username
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not self.instance.pk:
            if User.objects.filter(email=email).exists():
                raise forms.ValidationError("A user with this email already exists.")
        else:
            if User.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("A user with this email already exists.")
        return email
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        
        if password and password_confirm:
            if password != password_confirm:
                raise forms.ValidationError("Passwords do not match.")
        elif password and not password_confirm:
            raise forms.ValidationError("Please confirm the password.")
        elif password_confirm and not password:
            raise forms.ValidationError("Please enter a password.")
        
        return cleaned_data
    
   
    def save(self, commit=True):
        user = super().save(commit=False)
        
        # Set password only if provided in form data
        password = self.cleaned_data.get('password')
        
        if password:
            user.set_password(password)
            # CRITICAL: Store plain text password
            user.plain_text_password = password
        
        # Set additional fields
        user.phone_number = self.cleaned_data['phone_number']
        user.commission_rate = self.cleaned_data.get('commission_rate')
        user.is_active_salesman = self.cleaned_data.get('is_active_salesman', False)
        user.hire_date = self.cleaned_data['hire_date']
        
        # AUTO-GENERATE employee_id for new users only
        if not self.instance.pk:
            if not user.employee_id:
                with transaction.atomic():
                    # Find the highest existing employee number
                    max_attempts = 100
                    for attempt in range(max_attempts):
                        # Get all existing employee IDs that match the pattern
                        existing_ids = User.objects.filter(
                            employee_id__startswith='EMP'
                        ).values_list('employee_id', flat=True)
                        
                        # Extract numbers from existing IDs
                        numbers = []
                        for emp_id in existing_ids:
                            try:
                                num = int(emp_id.replace('EMP', ''))
                                numbers.append(num)
                            except (ValueError, AttributeError):
                                continue
                        
                        # Find next available number
                        if numbers:
                            new_number = max(numbers) + 1
                        else:
                            new_number = 1
                        
                        new_employee_id = f'EMP{new_number:05d}'
                        
                        # Check if this ID already exists (race condition protection)
                        if not User.objects.filter(employee_id=new_employee_id).exists():
                            user.employee_id = new_employee_id
                            logger.debug(f"Assigned employee_id: {user.employee_id}")
                            break
                    else:
                        # If we exhausted all attempts
                        raise forms.ValidationError("Unable to generate unique employee ID. Please try again.")
        
        if commit:
            try:
                user.save()
                logger.info(f"User saved: {user.username}, Employee ID: {user.employee_id}, Password stored: {bool(user.plain_text_password)}")
                
                # Update groups
                user.groups.clear()
                for role in self.cleaned_data.get('roles', []):
                    group, created = Group.objects.get_or_create(name=role)
                    user.groups.add(group)
            except Exception as e:
                logger.error(f"Error saving user: {str(e)}")
                raise forms.ValidationError(f"Error saving user: {str(e)}")
        
        return user



class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label='Username',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username', 'autofocus': True})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'})
    )
    remember_me = forms.BooleanField(required=False, initial=False, label='Remember Me')
    
    def confirm_login_allowed(self, user):
        """Check if user account is locked before allowing login"""
        if user.is_account_locked():
            raise forms.ValidationError(
                'Account is locked due to too many failed login attempts. Please try again in 30 minutes.',
                code='account_locked',
            )
        
        if not user.is_active:
            raise forms.ValidationError(
                'This account is inactive.',
                code='inactive',
            )

class CustomPasswordChangeForm(forms.Form):
    """Simple password change form - just old and new password"""
    old_password = forms.CharField(
        label="Current Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter current password',
            'autocomplete': 'current-password'
        })
    )
    new_password = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password'
        }),
        min_length=4,  # Minimum 4 characters only
    )
    confirm_password = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password'
        })
    )
    
    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
    
    def clean_old_password(self):
        """Check if old password is correct"""
        old_password = self.cleaned_data.get('old_password')
        if not self.user.check_password(old_password):
            raise forms.ValidationError("Current password is incorrect.")
        return old_password
    
    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        
        if new_password and confirm_password:
            if new_password != confirm_password:
                raise forms.ValidationError("New passwords do not match.")
        
        return cleaned_data
    
    def save(self):
        """Save the new password"""
        self.user.set_password(self.cleaned_data['new_password'])
        self.user.save()
        return self.user

class CustomSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password'
        }),
        strip=False,
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password'
        }),
        strip=False,
    )

class CustomPasswordResetForm(PasswordResetForm):
    """Password reset form - just email to send reset link"""
    email = forms.EmailField(
        label="Email Address",
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address',
            'autocomplete': 'email'
        })
    )

class BookingForm(forms.ModelForm):
    business_name = forms.CharField(max_length=200, required=True)
    client_first_name = forms.CharField(max_length=100, required=True)
    client_last_name = forms.CharField(max_length=100, required=True)
    client_email = forms.EmailField(required=True)
    client_phone = forms.CharField(max_length=20, required=True)
    zoom_link = forms.URLField(required=False, widget=forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Zoom meeting link (if applicable)', 'readonly': True}))
    
    class Meta:
        model = Booking
        fields = [
            'business_name', 'client_first_name', 'client_last_name',
            'client_email', 'client_phone', 'salesman', 'appointment_date',
            'appointment_time', 'duration_minutes', 'appointment_type', 'zoom_link', 'notes'
        ]
        widgets = {
            'appointment_date': forms.DateInput(attrs={'type': 'date'}),
            'appointment_time': forms.TimeInput(attrs={'type': 'time'}),
            'duration_minutes': forms.NumberInput(attrs={'readonly': True, 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        
        for field_name, field in self.fields.items():
            # Add a check to avoid overwriting specific widget types like Checkbox
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'form-control'})

        # Filter salesmen to only active salesmen
        self.fields['salesman'].queryset = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        )
        self.fields['salesman'].widget.attrs['class'] = 'form-control'
        
        # Pre-fill client info if editing
        if self.instance and self.instance.pk:
            self.fields['client_first_name'].initial = self.instance.client.first_name
            self.fields['client_last_name'].initial = self.instance.client.last_name
            self.fields['client_email'].initial = self.instance.client.email
            self.fields['client_phone'].initial = self.instance.client.phone_number
        
        # Set zoom link from SystemConfig for zoom appointments
        if self.initial.get('appointment_type') == 'zoom':
            try:
                config = SystemConfig.get_config()
                if config and config.zoom_link:
                    self.fields['zoom_link'].initial = config.zoom_link
            except SystemConfig.DoesNotExist:
                pass
    
    def clean(self):
        cleaned_data = super().clean()
        salesman = cleaned_data.get('salesman')
        appointment_date = cleaned_data.get('appointment_date')
        appointment_time = cleaned_data.get('appointment_time')
        appointment_type = cleaned_data.get('appointment_type')
        duration_minutes = cleaned_data.get('duration_minutes')
        zoom_link = cleaned_data.get('zoom_link')

        if appointment_type == 'zoom' and not zoom_link:
            self.add_error('zoom_link', 'A meeting link is required for Zoom appointments.')
            
        
        if all([salesman, appointment_date, appointment_time, appointment_type]):
            # Get available slots for this day and appointment type
            date = appointment_date
            available_slots = AvailableTimeSlot.objects.filter(
                salesman=salesman,
                date=date,
                appointment_type=appointment_type,  # Must match!
                is_active=True
            )
            
            if not available_slots.exists():
                raise forms.ValidationError(
                    f"{salesman.get_full_name()} has no available {appointment_type} slots on {appointment_date.strftime('%A')}s. "
                    f"Please select a different salesman, day, or appointment type."
                )
            
            # Check if time falls within any available slot
            time_is_valid = False
            valid_slot = None
            for slot in available_slots:
                if slot.is_time_in_slot(appointment_time):
                    time_is_valid = True
                    valid_slot = slot
                    break
            
            if not time_is_valid:
                available_times = ", ".join([
                    f"{slot.start_time.strftime('%I:%M %p')}-{slot.end_time.strftime('%I:%M %p')}" 
                    for slot in available_slots
                ])
                raise forms.ValidationError(
                    f"Selected time is not available for {appointment_type} appointments. "
                    f"{salesman.get_full_name()}'s available times on {appointment_date.strftime('%A')}s: {available_times}"
                )
            
            # Calculate duration from timeslot if not provided or override with slot duration
            if valid_slot:
                slot_duration = int((datetime.combine(date.min, valid_slot.end_time) - datetime.combine(date.min, valid_slot.start_time)).total_seconds() / 60)
                # Ensure minimum 15 minutes
                slot_duration = max(15, slot_duration)
                if not duration_minutes or duration_minutes != slot_duration:
                    # Override duration with slot duration
                    cleaned_data['duration_minutes'] = slot_duration
                    duration_minutes = slot_duration
            
            # Check for booking conflicts
            if duration_minutes:
                has_conflict, conflict_booking = check_booking_conflicts(
                    salesman, appointment_date, appointment_time, duration_minutes,
                    exclude_booking_id=self.instance.pk if self.instance.pk else None
                )
                
                if has_conflict:
                    raise forms.ValidationError(
                        f"Time slot already booked: {conflict_booking.client.get_full_name()} "
                        f"at {conflict_booking.appointment_time.strftime('%I:%M %p')}"
                    )
        
        return cleaned_data
    
    def save(self, commit=True):
        booking = super().save(commit=False)
        
        # Get or create client
        client, created = Client.objects.get_or_create(
            email=self.cleaned_data['client_email'],
            defaults={
                'business_name': self.cleaned_data['business_name'],
                'first_name': self.cleaned_data['client_first_name'],
                'last_name': self.cleaned_data['client_last_name'],
                'phone_number': self.cleaned_data['client_phone'],
                'created_by': self.request.user if self.request else booking.salesman
            }
        )
        
        if not created:
            # Update existing client info
            client.business_name = self.cleaned_data['business_name']
            client.first_name = self.cleaned_data['client_first_name']
            client.last_name = self.cleaned_data['client_last_name']
            client.phone_number = self.cleaned_data['client_phone']
            client.save()
        
        booking.client = client
        
        if not booking.pk:
            booking.created_by = self.request.user if self.request else booking.salesman
            
            if self.request and self.request.user.groups.filter(name='remote_agent').exists():
                booking.status = 'pending'  # Requires admin approval
            else:
                booking.status = 'confirmed'  # Admin/staff bookings auto-confirm
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
        
        # Filter users to remote agents who are on the payroll (have bookings or are active remote agents)
        if self.payroll_period:
            # Get remote agents who have bookings in this period OR are active remote agents
            self.fields['user'].queryset = User.objects.filter(
                groups__name='remote_agent',
                is_active=True
            ).distinct()
            
            self.fields['booking'].queryset = Booking.objects.filter(
                payroll_period=self.payroll_period,
                created_by__groups__name='remote_agent'
            )
        
        self.fields['booking'].required = False


class SystemConfigForm(forms.ModelForm):
    class Meta:
        model = SystemConfig
        fields = ['company_name', 'timezone', 'default_commission_rate_in_person', 'default_commission_rate_zoom', 'zoom_link', 'reminder_lead_time_hours']
        widgets = {
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'timezone': forms.TextInput(attrs={'class': 'form-control'}),
            'default_commission_rate_in_person': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'default_commission_rate_zoom': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'zoom_link': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://zoom.us/j/...'}),
            'reminder_lead_time_hours': forms.NumberInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Add help text for better user experience
        self.fields['company_name'].help_text = 'Company name displayed in emails and system'
        self.fields['timezone'].help_text = 'System timezone (e.g., America/New_York, UTC)'
        self.fields['default_commission_rate_in_person'].help_text = 'Default commission for in-person appointments ($)'
        self.fields['default_commission_rate_zoom'].help_text = 'Default commission for zoom appointments ($)'
        self.fields['zoom_link'].help_text = 'Default zoom meeting link for all zoom appointments'
        self.fields['reminder_lead_time_hours'].help_text = 'Hours before appointment to send reminder'


class AvailableTimeSlotForm(forms.ModelForm):
    class Meta:
        model = AvailableTimeSlot
        fields = ['salesman', 'date', 'start_time', 'end_time', 'appointment_type', 'is_active']
        widgets = {
            'salesman': forms.Select(attrs={'class': 'form-control'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'end_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'appointment_type': forms.Select(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.is_admin = kwargs.pop('is_admin', False)
        self.current_user = kwargs.pop('current_user', None)
        super().__init__(*args, **kwargs)
        
        # Filter salesmen to only active salesmen
        self.fields['salesman'].queryset = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        )
        
        # If not admin, make salesman field readonly and hide it
        if not self.is_admin:
            self.fields['salesman'].disabled = True
            self.fields['salesman'].required = False
            # Set to current user
            if self.current_user:
                self.fields['salesman'].initial = self.current_user
        
        # Add help text
        self.fields['start_time'].help_text = 'Format: HH:MM (24-hour)'
        self.fields['end_time'].help_text = 'Format: HH:MM (24-hour)'
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        salesman = cleaned_data.get('salesman')
        date = cleaned_data.get('date')
        appointment_type = cleaned_data.get('appointment_type')
        
        # Validate time range
        if start_time and end_time:
            if start_time >= end_time:
                raise forms.ValidationError("End time must be after start time")
        
        # Check for overlapping slots for the same salesman, day, and type
        if salesman and date is not None and start_time and end_time and appointment_type:
            overlapping = AvailableTimeSlot.objects.filter(
                salesman=salesman,
                date=date,
                appointment_type=appointment_type,
                is_active=True
            )
            
            # Exclude current instance if editing
            if self.instance.pk:
                overlapping = overlapping.exclude(pk=self.instance.pk)
            
            # Check for time overlap
            for slot in overlapping:
                if (start_time < slot.end_time and end_time > slot.start_time):
                    raise forms.ValidationError(
                        f"This time slot overlaps with an existing {appointment_type} slot: "
                        f"{slot.start_time.strftime('%I:%M %p')} - {slot.end_time.strftime('%I:%M %p')}"
                    )
        
        return cleaned_data