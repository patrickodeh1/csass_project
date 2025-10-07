from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.models import Group
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field, Div, HTML
from .models import Booking, Client, AvailableTimeSlot, Unavailability, PayrollAdjustment, SystemConfig, User
from datetime import datetime, timedelta
import logging
from .utils import check_booking_conflicts, check_unavailability_conflicts
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
            ('sales_support', 'Sales Support'), 
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
                logger.info(f"User saved: {user.username}, Employee ID: {user.employee_id}")
                
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
    client_first_name = forms.CharField(max_length=100, required=True)
    client_last_name = forms.CharField(max_length=100, required=True)
    client_email = forms.EmailField(required=True)
    client_phone = forms.CharField(max_length=20, required=True)
    zoom_link = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://zoom.us/j/1234567890 or https://meet.google.com/xxx-xxxx-xxx'
        }),
        help_text='Paste your Zoom/Google Meet link here (only for Zoom appointments)'
    )
    
    class Meta:
        model = Booking
        fields = ['salesman', 'appointment_date', 'appointment_time', 'duration_minutes', 
                  'appointment_type', 'zoom_link', 'notes']
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
    
    def clean(self):
        cleaned_data = super().clean()
        salesman = cleaned_data.get('salesman')
        appointment_date = cleaned_data.get('appointment_date')
        appointment_time = cleaned_data.get('appointment_time')
        appointment_type = cleaned_data.get('appointment_type')
        duration_minutes = cleaned_data.get('duration_minutes')
        zoom_link = cleaned_data.get('zoom_link')

        # Validate Zoom link for Zoom appointments
        if appointment_type == 'zoom' and not zoom_link:
            raise forms.ValidationError("Zoom link is required for Zoom appointments")
        
        if all([salesman, appointment_date, appointment_time]):
            # CHECK 1: Verify appointment time is within available time slots
            available_slots = salesman.get_available_slots_for_date(appointment_date)
            
            if not available_slots.exists():
                raise forms.ValidationError(
                    f"{salesman.get_full_name()} has no available time slots on {appointment_date.strftime('%A')}s. "
                    f"Please contact an administrator to set up availability."
                )
            
            # Check if appointment time falls within any available slot
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
                    f"Selected time ({appointment_time.strftime('%I:%M %p')}) is not within available slots. "
                    f"{salesman.get_full_name()}'s available times on {appointment_date.strftime('%A')}s: {available_times}"
                )
            
            # Verify entire appointment (including duration) fits within the slot
            if valid_slot and duration_minutes:
                appointment_end_time = (
                    datetime.combine(appointment_date, appointment_time) + 
                    timedelta(minutes=duration_minutes)
                ).time()
                
                if appointment_end_time > valid_slot.end_time:
                    raise forms.ValidationError(
                        f"Appointment duration extends beyond available time slot. "
                        f"Slot ends at {valid_slot.end_time.strftime('%I:%M %p')}, "
                        f"but your appointment would end at {appointment_end_time.strftime('%I:%M %p')}. "
                        f"Please choose an earlier time or shorter duration."
                    )
            
            # CHECK 2: Check for booking conflicts with other appointments
            if duration_minutes:
                has_conflict, conflict_booking = check_booking_conflicts(
                    salesman, appointment_date, appointment_time, duration_minutes,
                    exclude_booking_id=self.instance.pk if self.instance.pk else None
                )
                
                if has_conflict:
                    raise forms.ValidationError(
                        f"This time slot conflicts with an existing booking: "
                        f"{conflict_booking.client.get_full_name()} at {conflict_booking.appointment_time.strftime('%I:%M %p')}"
                    )
            
            # CHECK 3: Check for unavailability blocks
            if duration_minutes:
                has_unavailable, unavailable_block = check_unavailability_conflicts(
                    salesman, appointment_date, appointment_time, duration_minutes
                )
                
                if has_unavailable:
                    raise forms.ValidationError(
                        f"Salesman is unavailable during this time. "
                        f"Reason: {unavailable_block.get_reason_display()}"
                    )
            
            # CHECK 4: Check booking time constraints
            if duration_minutes:
                config = SystemConfig.get_config()
                appt_datetime = datetime.combine(appointment_date, appointment_time)
                now = datetime.now()
                
                # Check minimum advance booking
                min_advance = now + timedelta(hours=config.min_advance_booking_hours)
                if appt_datetime < min_advance:
                    raise forms.ValidationError(
                        f"Bookings must be made at least {config.min_advance_booking_hours} hours in advance. "
                        f"Earliest available time: {min_advance.strftime('%B %d, %Y at %I:%M %p')}"
                    )
                
                # Check maximum advance booking
                max_advance = now + timedelta(days=config.max_advance_booking_days)
                if appt_datetime > max_advance:
                    raise forms.ValidationError(
                        f"Bookings cannot be made more than {config.max_advance_booking_days} days in advance. "
                        f"Latest bookable date: {max_advance.strftime('%B %d, %Y')}"
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
                is_active_salesman=True,
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


class AvailableTimeSlotForm(forms.ModelForm):
    class Meta:
        model = AvailableTimeSlot
        fields = ['salesman', 'day_of_week', 'start_time', 'end_time', 'is_active']
        widgets = {
            'salesman': forms.Select(attrs={'class': 'form-control'}),
            'day_of_week': forms.Select(attrs={'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'end_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['salesman'].queryset = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        )
    
    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        
        if start_time and end_time:
            if start_time >= end_time:
                raise forms.ValidationError("End time must be after start time")
        
        return cleaned_data