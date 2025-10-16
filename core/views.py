from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.core.paginator import Paginator
from datetime import datetime, timedelta
from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView, 
    PasswordResetConfirmView, PasswordResetCompleteView
)
import csv
from django.urls import reverse_lazy
from .models import (Booking, Client, PayrollPeriod, PayrollAdjustment, 
                     SystemConfig, AvailableTimeSlot, AvailabilityCycle, AuditLog, User)
from .forms import (LoginForm, BookingForm, CancelBookingForm, AudioForm,
                    PayrollAdjustmentForm, AvailableTimeSlotForm, UserForm, SystemConfigForm, CustomPasswordResetForm, CustomSetPasswordForm, CustomPasswordChangeForm)
from .decorators import group_required, admin_required, remote_agent_required
from .utils import (
    get_current_payroll_period,
    get_payroll_periods,
    send_booking_confirmation,
    send_booking_cancellation,
    check_booking_conflicts,
    send_booking_approved_notification,
    send_booking_declined_notification,
    generate_timeslots_for_cycle,
    cleanup_old_slots,
    ensure_timeslots_for_payroll_period,
    mark_past_slots_inactive,
    mark_elapsed_today_slots_inactive,
)
from django.utils.crypto import get_random_string
from calendar import monthcalendar
import logging
from datetime import datetime, date, time, timedelta 
from .models import MessageTemplate, DripCampaign, ScheduledMessage, CommunicationLog
from .decorators import admin_required
from .forms import MessageTemplateForm
from .utils import start_drip_campaign
import os

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================
# Authentication Views
# ============================================================
def login_view(request):
    if request.user.is_authenticated:
        return redirect('calendar')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        username = request.POST.get('username')
        
        # Get user by username to check lock status
        try:
            user = User.objects.get(username=username)
            
            # Check if account is locked
            if user.is_account_locked():
                messages.error(request, 'Account is locked due to too many failed login attempts. Please try again in 30 minutes.')
                return render(request, 'login.html', {'form': form})
            
        except User.DoesNotExist:
            pass  # Will be handled by form validation
        
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            
            # Reset failed login attempts on successful login
            user.reset_failed_login_attempts()
            
            # Handle remember me
            if not form.cleaned_data.get('remember_me'):
                request.session.set_expiry(0)
            
            messages.success(request, f'Welcome back, {user.get_full_name()}!')
            return redirect('calendar')
        else:
            # Increment failed login attempts
            if username:
                try:
                    user = User.objects.get(username=username)
                    user.increment_failed_login()
                    
                    # Show attempts remaining
                    from django.conf import settings
                    max_attempts = getattr(settings, 'MAX_LOGIN_ATTEMPTS', 5)
                    remaining = max_attempts - user.failed_login_attempts
                    if remaining > 0:
                        messages.warning(request, f'Invalid credentials. {remaining} attempts remaining before account is locked.')
                except User.DoesNotExist:
                    messages.error(request, 'Invalid username or password.')
    else:
        form = LoginForm()
    
    return render(request, 'login.html', {'form': form})

@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('login')

# ============================================================
# Password Reset Views
# ============================================================

class CustomPasswordResetView(PasswordResetView):
    template_name = 'password_reset.html'
    email_template_name = 'emails/password_reset_email.txt'
    subject_template_name = 'emails/password_reset_subject.txt'
    form_class = CustomPasswordResetForm  # This should be PasswordResetForm (email only)
    success_url = reverse_lazy('password_reset_done')
    html_email_template_name = 'emails/password_reset_email.html'
    def form_valid(self, form):
        messages.success(self.request, 'Password reset email has been sent. Please check your inbox.')
        return super().form_valid(form)

class CustomPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'password_reset_done.html'

class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'password_reset_confirm.html'
    form_class = CustomSetPasswordForm
    success_url = reverse_lazy('password_reset_complete')
    
    def form_valid(self, form):
        # Save the user first
        response = super().form_valid(form)
        
        # Now save plain text password
        user = form.user
        new_password = form.cleaned_data['new_password1']
        user.plain_text_password = new_password
        user.save(update_fields=['plain_text_password'])
        
        messages.success(self.request, 'Your password has been reset successfully!')
        return response

class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'password_reset_complete.html'

@login_required
def password_change_view(request):
    if request.method == 'POST':
        form = CustomPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            
            # SAVE PLAIN TEXT PASSWORD
            new_password = form.cleaned_data['new_password']
            user.plain_text_password = new_password
            user.save(update_fields=['plain_text_password'])
            
            update_session_auth_hash(request, user)  # Keep user logged in
            
            # Mark temp credential as used
            try:
                from .models import TemporaryCredential
                if hasattr(user, 'temp_credential'):
                    user.temp_credential.mark_as_used()
            except:
                pass
            
            messages.success(request, 'Password changed successfully!')
            return redirect('calendar')
        else:
            for error in form.non_field_errors():
                messages.error(request, error)
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
    else:
        form = CustomPasswordChangeForm(request.user)
    
    return render(request, 'password_change.html', {'form': form})
    
# ============================================================
# Calendar & Booking Views
# ============================================================
@login_required
def calendar_view(request):
    """Calendar view with role-based booking visibility and correct weekday alignment"""
    from datetime import date as date_class
    # Auto-inactivate past and elapsed slots for display and safety
    try:
        mark_past_slots_inactive()
        mark_elapsed_today_slots_inactive()
    except Exception:
        pass
    
    # Get filter parameters
    salesman_id = request.GET.get('salesman')
    appointment_type = request.GET.get('type')
    view_mode = request.GET.get('view', 'month')
    date_str = request.GET.get('date')
    
    # Parse date or use current
    if date_str:
        try:
            current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            current_date = timezone.now().date()
    else:
        current_date = timezone.now().date()
    
    # Calculate date range based on view mode
    if view_mode == 'month':
        start_date = current_date.replace(day=1)
        if current_date.month == 12:
            end_date = current_date.replace(year=current_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = current_date.replace(month=current_date.month + 1, day=1) - timedelta(days=1)
        
        # Calculate navigation dates
        if current_date.month == 1:
            prev_month = current_date.replace(year=current_date.year - 1, month=12, day=1)
        else:
            prev_month = current_date.replace(month=current_date.month - 1, day=1)
        
        if current_date.month == 12:
            next_month = current_date.replace(year=current_date.year + 1, month=1, day=1)
        else:
            next_month = current_date.replace(month=current_date.month + 1, day=1)
        
        # Generate calendar grid with Sunday as first day
        import calendar
        calendar.setfirstweekday(calendar.SUNDAY)  # Set Sunday as first day
        cal = calendar.monthcalendar(current_date.year, current_date.month)
        
        calendar_weeks = []
        for week in cal:
            week_data = []
            for day in week:
                if day == 0:
                    week_data.append({
                        'day': 0,
                        'is_current_month': False,
                        'available_slots': [],
                        'pending_bookings': [],
                        'confirmed_bookings': [],
                        'declined_bookings': [],
                        'appointments': [],  # For salesmen
                    })
                else:
                    day_date = date_class(current_date.year, current_date.month, day)
                    week_data.append({
                        'day': day,
                        'date': day_date,
                        'is_current_month': True,
                        'available_slots': [],
                        'pending_bookings': [],
                        'confirmed_bookings': [],
                        'declined_bookings': [],
                        'appointments': [],  # For salesmen
                    })
            calendar_weeks.append(week_data)
        
        week_days = None
        prev_date = None
        next_date = None
        
    elif view_mode == 'week':
        # Calculate week starting on Sunday
        # weekday() returns 0=Mon, 1=Tue, ..., 6=Sun
        # We need (Mon=1, Tue=2, ..., Sat=6, Sun=0) days back to get to Sunday
        days_since_sunday = (current_date.weekday() + 1) % 7
        start_date = current_date - timedelta(days=days_since_sunday)
        end_date = start_date + timedelta(days=6)
        
        calendar_weeks = None
        prev_month = None
        next_month = None
        prev_date = start_date - timedelta(days=7)
        next_date = end_date + timedelta(days=1)
        
        week_days = []
        for i in range(7):
            day_date = start_date + timedelta(days=i)
            week_days.append({
                'date': day_date,
                'available_slots': [],
                'pending_bookings': [],
                'confirmed_bookings': [],
                'declined_bookings': [],
                'appointments': [],  # For salesmen
            })
    else:  # day
        start_date = end_date = current_date
        calendar_weeks = None
        week_days = None
        prev_month = None
        next_month = None
        prev_date = current_date - timedelta(days=1)
        next_date = current_date + timedelta(days=1)
    
    # Determine user role
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()
    is_remote_agent = request.user.groups.filter(name='remote_agent').exists()
    
    # Build query for bookings based on role
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman', 'created_by', 'approved_by', 'declined_by', 'canceled_by', 'updated_by')
    
    # Exclude past confirmed/completed/no_show appointments from the calendar view
    today_date = timezone.now().date()
    bookings = bookings.exclude(
        Q(appointment_date__lt=today_date) & Q(status__in=['confirmed', 'completed', 'no_show'])
    )
    
    if is_salesman and not is_admin:
        # Salesmen see only their own bookings
        bookings = bookings.filter(salesman=request.user)
    elif is_remote_agent and not is_admin:
        # Remote agents see only bookings they created
        bookings = bookings.filter(created_by=request.user)
    elif salesman_id and is_admin:
        # Admins can filter by salesman_id
        bookings = bookings.filter(salesman_id=salesman_id)
    
    if appointment_type:
        bookings = bookings.filter(appointment_type=appointment_type)
    
    # Get available time slots (salesmen see none, admins/remote agents see based on filters)
    timeslots = AvailableTimeSlot.objects.filter(
        is_active=True,
        date__gte=start_date,
        date__lte=end_date
    ).select_related('salesman')
    inactive_timeslots = AvailableTimeSlot.objects.filter(
        is_active=False,
        date__gte=start_date,
        date__lte=end_date
    ).select_related('salesman')
    
    if is_salesman and not is_admin:
        # Salesmen do not see available time slots
        timeslots = timeslots.none()
    elif salesman_id and is_admin:
        timeslots = timeslots.filter(salesman_id=salesman_id)
        inactive_timeslots = inactive_timeslots.filter(salesman_id=salesman_id)
    
    if appointment_type:
        timeslots = timeslots.filter(appointment_type=appointment_type)
        inactive_timeslots = inactive_timeslots.filter(appointment_type=appointment_type)
    
    # Organize available slots by date
    class SlotData:
        def __init__(self, date, time, salesman, appointment_type):
            self.date = date
            self.time = time
            self.salesman = salesman
            self.appointment_type = appointment_type
    
    available_slots_dict = {}
    for slot in timeslots:
        # With slot deactivated on pending/confirmed/completed, any active slot is available
        date_key = slot.date
        if date_key not in available_slots_dict:
            available_slots_dict[date_key] = []
        slot_obj = SlotData(slot.date, slot.start_time, slot.salesman, slot.appointment_type)
        available_slots_dict[date_key].append(slot_obj)
    inactive_slots_dict = {}
    for slot in inactive_timeslots:
        date_key = slot.date
        if date_key not in inactive_slots_dict:
            inactive_slots_dict[date_key] = []
        slot_obj = SlotData(slot.date, slot.start_time, slot.salesman, slot.appointment_type)
        inactive_slots_dict[date_key].append(slot_obj)
    
    # Organize bookings by status for admins/remote agents, or as appointments for salesmen
    pending_bookings_dict = {}
    confirmed_bookings_dict = {}
    declined_bookings_dict = {}
    appointments_dict = {}  # For salesmen
    
    for booking in bookings:
        date_key = booking.appointment_date
        if is_salesman and not is_admin:
            # For salesmen, all bookings go into appointments
            if date_key not in appointments_dict:
                appointments_dict[date_key] = []
            appointments_dict[date_key].append(booking)
        else:
            # For admins/remote agents, split by status
            if booking.status == 'pending':
                if date_key not in pending_bookings_dict:
                    pending_bookings_dict[date_key] = []
                pending_bookings_dict[date_key].append(booking)
            elif booking.status in ['confirmed', 'completed']:
                if date_key not in confirmed_bookings_dict:
                    confirmed_bookings_dict[date_key] = []
                confirmed_bookings_dict[date_key].append(booking)
            elif booking.status == 'declined':
                if date_key not in declined_bookings_dict:
                    declined_bookings_dict[date_key] = []
                declined_bookings_dict[date_key].append(booking)
    
    # Attach data to calendar structure
    if view_mode == 'month':
        for week in calendar_weeks:
            for day_info in week:
                if day_info['is_current_month']:
                    day_date = day_info['date']
                    day_info['available_slots'] = available_slots_dict.get(day_date, [])
                    day_info['inactive_slots'] = inactive_slots_dict.get(day_date, [])
                    if is_salesman and not is_admin:
                        day_info['appointments'] = appointments_dict.get(day_date, [])
                    else:
                        day_info['pending_bookings'] = pending_bookings_dict.get(day_date, [])
                        day_info['confirmed_bookings'] = confirmed_bookings_dict.get(day_date, [])
                        day_info['declined_bookings'] = declined_bookings_dict.get(day_date, [])
    
    elif view_mode == 'week':
        for day_info in week_days:
            day_date = day_info['date']
            day_info['available_slots'] = available_slots_dict.get(day_date, [])
            day_info['inactive_slots'] = inactive_slots_dict.get(day_date, [])
            if is_salesman and not is_admin:
                day_info['appointments'] = appointments_dict.get(day_date, [])
            else:
                day_info['pending_bookings'] = pending_bookings_dict.get(day_date, [])
                day_info['confirmed_bookings'] = confirmed_bookings_dict.get(day_date, [])
                day_info['declined_bookings'] = declined_bookings_dict.get(day_date, [])
    
    # Day view - prepare separate lists
    day_available_slots = None
    day_inactive_slots = None
    day_pending_bookings = None
    day_confirmed_bookings = None
    day_declined_bookings = None
    day_appointments = None
    
    if view_mode == 'day':
        day_available_slots = available_slots_dict.get(current_date, [])
        day_inactive_slots = inactive_slots_dict.get(current_date, [])
        if is_salesman and not is_admin:
            day_appointments = appointments_dict.get(current_date, [])
        else:
            day_pending_bookings = pending_bookings_dict.get(current_date, [])
            day_confirmed_bookings = confirmed_bookings_dict.get(current_date, [])
            day_declined_bookings = declined_bookings_dict.get(current_date, [])
    
    
    # Get all salesmen for filter (only for admins)
    salesmen = None
    if is_admin:
        salesmen = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        )
    
    context = {
        
        'salesmen': salesmen,
        'current_date': current_date,
        'start_date': start_date,
        'end_date': end_date,
        'view_mode': view_mode,
        'selected_salesman': salesman_id,
        'selected_type': appointment_type,
        'calendar_weeks': calendar_weeks,
        'week_days': week_days,
        'prev_month': prev_month,
        'next_month': next_month,
        'prev_date': prev_date,
        'next_date': next_date,
        'day_available_slots': day_available_slots,
        'day_pending_bookings': day_pending_bookings,
        'day_confirmed_bookings': day_confirmed_bookings,
        'day_declined_bookings': day_declined_bookings,
        'day_appointments': day_appointments,
        'day_inactive_slots': day_inactive_slots,
        'is_salesman': is_salesman and not is_admin,  # Flag for template
        'is_remote_agent': is_remote_agent and not is_admin,
    }
    
    return render(request, 'calendar.html', context)
    
@login_required
def booking_create(request):
    # Extract initial data from URL params (needed for both GET and POST)
    initial = {}
    
    # Store key parameters needed to find the AvailableTimeSlot
    slot_salesman_id = request.GET.get('salesman')
    slot_date_str = request.GET.get('date')
    start_time_str = request.GET.get('start_time')
    #end_time_str = request.GET.get('end_time')
    slot_type = request.GET.get('type')
    
    # --- Time and Duration Calculation (Existing Logic) ---
    if slot_date_str:
        initial['appointment_date'] = slot_date_str

    if start_time_str:
        try:
            # Convert time strings to datetime objects
            t1 = datetime.strptime(start_time_str, '%H:%M')
            
            # Pass a proper time object to the form
            initial['appointment_time'] = t1.time()

            # All appointments are 15 minutes duration
            initial['duration_minutes'] = 15

        except (ValueError, TypeError):
            # Fallback to default duration if calculation fails
            initial['duration_minutes'] = 15
            if request.method == 'GET':
                 messages.error(request, "Could not determine appointment duration from the selected slot. Please check the duration.")
    else:
        # Fallback if no time range is provided
        initial['duration_minutes'] = 15

    if slot_salesman_id:
        initial['salesman'] = slot_salesman_id
    if slot_type:
        initial['appointment_type'] = slot_type

    # Auto-fill zoom link for zoom appointments
    if initial.get('appointment_type') == 'zoom':
        try:
            config = SystemConfig.get_config()
            if config and config.zoom_link:
                initial['zoom_link'] = config.zoom_link
        except SystemConfig.DoesNotExist:
            pass
    
    if request.method == 'POST':
        # Pass initial data to POST form so template can access it on validation errors
        form = BookingForm(request.POST, request.FILES, initial=initial, request=request)
        if form.is_valid():
            
            # 1. Validate present/future and 30-minute alignment
            appt_date = form.cleaned_data.get('appointment_date')
            appt_time = form.cleaned_data.get('appointment_time')
            now = timezone.localtime()
            if appt_date < now.date() or (appt_date == now.date() and appt_time <= now.time()):
                messages.error(request, 'Cannot book a slot in the past or elapsed today.')
                return render(request, 'booking_form.html', {'form': form, 'title': 'New Booking'})
            if appt_time and appt_time.minute not in (0, 30):
                messages.error(request, 'Appointment time must align to the 30-minute slot boundary.')
                return render(request, 'booking_form.html', {'form': form, 'title': 'New Booking'})

            # 2. Find the ACTIVE AvailableTimeSlot
            available_slot = None
            if (form.cleaned_data.get('salesman') and 
                form.cleaned_data.get('appointment_date') and 
                form.cleaned_data.get('appointment_time') and 
                form.cleaned_data.get('appointment_type')):
                try:
                    available_slot = AvailableTimeSlot.objects.get(
                        salesman=form.cleaned_data['salesman'],
                        date=form.cleaned_data['appointment_date'],
                        start_time=form.cleaned_data['appointment_time'],
                        appointment_type=form.cleaned_data['appointment_type'],
                        is_active=True
                    )
                except AvailableTimeSlot.DoesNotExist:
                    messages.error(request, "The selected time slot is no longer active or available.")
                    return render(request, 'booking_form.html', {'form': form, 'title': 'New Booking'})
            
            # 3. Save the Booking object (commit=False)
            booking = form.save(commit=False)
            
            # 4. Link the slot
            if available_slot:
                booking.available_slot = available_slot
            
            # Set system fields and final save
            booking.created_by = request.user
            booking.save() # The custom save method handles deactivating the slot for pending/confirmed
            
            # 4. --- Handle Notifications (Existing Logic) ---
            is_remote_agent = request.user.groups.filter(name='remote_agent').exists()
            
            if is_remote_agent:
                # Remote agent - needs approval (status remains 'pending')
                messages.warning(
                    request, 
                    f'Booking submitted successfully! Status: Pending Admin Approval. '
                    f'You will receive an email once the booking is reviewed.'
                )
            else:
                # Admin/Staff - auto-confirmed (status is set to 'confirmed' by default or form)
                # Slot is deactivated by Booking.save() because status='confirmed'
                try:
                    send_booking_confirmation(booking)
                    messages.success(
                        request, 
                        'Booking created and confirmed! Confirmation emails sent to all parties.'
                    )
                except Exception as e:
                    messages.warning(request, f'Booking created but email failed: {str(e)}')
            
            return redirect('calendar')
    else:
        # GET request - create form with initial data
        form = BookingForm(initial=initial, request=request)
    
    return render(request, 'booking_form.html', {'form': form, 'title': 'New Booking'})
@login_required
def booking_detail(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    
    # Check if user can view this booking
    if not request.user.is_staff:
        if booking.salesman != request.user and booking.created_by != request.user:
            return HttpResponseForbidden("You don't have permission to view this booking.")
    
    return render(request, 'booking_detail.html', {'booking': booking})

@login_required
def booking_edit(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    
    # Check if booking can be edited
    if not booking.is_editable():
        messages.error(request, 'This booking cannot be edited (locked or in the past).')
        return redirect('booking_detail', pk=pk)
    
    # Check permissions: Only admins can edit. Remote agents and salesmen cannot edit.
    if not request.user.is_staff:
        return HttpResponseForbidden("Only administrators can edit bookings.")
    
    if request.method == 'POST':
        form = BookingForm(request.POST, request.FILES, instance=booking, request=request)
        if form.is_valid():
            booking = form.save()
            messages.success(request, 'Booking updated successfully!')
            return redirect('booking_detail', pk=pk)
    else:
        form = BookingForm(instance=booking, request=request)
    
    return render(request, 'booking_form.html', {'form': form, 'title': 'Edit Booking', 'booking': booking})

@login_required
def pending_bookings_view(request):
    """View to see pending bookings - Admin sees all, Salesman sees only theirs"""
    status_filter = request.GET.get('status', 'pending')
    
    # Determine user role
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()
    
    # Check if user has permission
    if not (is_admin or is_salesman):
        messages.error(request, "You don't have permission to view pending bookings.")
        return redirect('calendar')
    
    bookings = Booking.objects.select_related('client', 'salesman', 'created_by', 'approved_by', 'declined_by', 'canceled_by', 'updated_by')
    
    # Filter based on user role
    if is_salesman and not is_admin:
        # Salesmen only see bookings assigned to them
        bookings = bookings.filter(salesman=request.user)
    
    # Apply status filter
    if status_filter == 'pending':
        bookings = bookings.filter(status='pending')
    elif status_filter == 'declined':
        bookings = bookings.filter(status='declined')
    elif status_filter == 'all':
        bookings = bookings.filter(status__in=['pending', 'declined', 'confirmed'])

    bookings = bookings.order_by('-created_at')

    # Pagination
    paginator = Paginator(bookings, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get counts based on user role
    if is_salesman and not is_admin:
        pending_count = Booking.objects.filter(status='pending', salesman=request.user).count()
        declined_count = Booking.objects.filter(status='declined', salesman=request.user).count()
    else:
        pending_count = Booking.objects.filter(status='pending').count()
        declined_count = Booking.objects.filter(status='declined').count()
    
    context = {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'declined_count': declined_count,
        'is_salesman': is_salesman and not is_admin,
        'is_admin': is_admin,
    }

    return render(request, 'pending_bookings.html', context)

@login_required
@group_required('salesman')
def salesman_pending_bookings_view(request):
    """Salesman view to see their own pending bookings requiring approval"""
    status_filter = request.GET.get('status', 'pending')
    
    # Only show bookings for this salesman
    bookings = Booking.objects.filter(
        salesman=request.user
    ).select_related('client', 'salesman', 'created_by', 'approved_by', 'declined_by', 'canceled_by', 'updated_by')
    
    if status_filter == 'pending':
        bookings = bookings.filter(status='pending')
    elif status_filter == 'declined':
        bookings = bookings.filter(status='declined')
    elif status_filter == 'all':
        bookings = bookings.filter(status__in=['pending', 'declined', 'confirmed'])
    
    bookings = bookings.order_by('-created_at')
    
    # Pagination
    paginator = Paginator(bookings, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get counts for this salesman only
    pending_count = Booking.objects.filter(salesman=request.user, status='pending').count()
    declined_count = Booking.objects.filter(salesman=request.user, status='declined').count()
    
    context = {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'declined_count': declined_count,
    }
    
    return render(request, 'salesman_pending_bookings.html', context)


# Salesman-specific booking approval (redirects back to salesman list)
@login_required
@group_required('salesman')
def salesman_booking_approve(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    is_salesman = request.user.groups.filter(name='salesman').exists()

    if not (is_salesman and booking.salesman == request.user):
        messages.error(request, "You don't have permission to approve this booking.")
        return redirect('salesman_pending_bookings')

    if not booking.can_be_approved():
        messages.error(request, 'This booking cannot be approved.')
        return redirect('salesman_pending_bookings')

    if request.method == 'POST':
        booking.status = 'confirmed'
        booking.approved_at = timezone.now()
        booking.approved_by = request.user
        booking.save()

        try:
            send_booking_confirmation(booking)
        except Exception as e:
            logger.warning(f"Failed to send booking confirmation: {str(e)}")

        try:
            send_booking_approved_notification(booking)
        except Exception as e:
            logger.warning(f"Failed to send approval notification: {str(e)}")

        messages.success(
            request,
            f'✓ Booking approved for {booking.client.get_full_name()} with {booking.salesman.get_full_name()}. '
            f'Confirmation emails sent to all parties.'
        )

        from .signals import create_audit_log
        create_audit_log(
            user=request.user,
            action='update',
            entity_type='Booking',
            entity_id=booking.id,
            changes={'status': 'confirmed', 'approved_by': request.user.get_full_name()},
            request=request
        )

        return redirect('salesman_pending_bookings')

    return render(request, 'salesman_booking_approve.html', {'booking': booking})


# Salesman-specific booking decline (redirects back to salesman list)
@login_required
@group_required('salesman')
def salesman_booking_decline(request, pk):
    booking = get_object_or_404(Booking, pk=pk)

    is_salesman = request.user.groups.filter(name='salesman').exists()

    if not (is_salesman and booking.salesman == request.user):
        messages.error(request, "You don't have permission to decline this booking.")
        return redirect('salesman_pending_bookings')

    if not booking.can_be_declined():
        messages.error(request, 'This booking cannot be declined.')
        return redirect('salesman_pending_bookings')

    if request.method == 'POST':
        decline_reason = request.POST.get('decline_reason', '').strip()
        if not decline_reason:
            messages.error(request, 'Please provide a reason for declining.')
            return render(request, 'booking_decline.html', {'booking': booking})

        booking.status = 'declined'
        booking.declined_at = timezone.now()
        booking.declined_by = request.user
        booking.decline_reason = decline_reason
        booking.save()

        try:
            send_booking_declined_notification(booking)
        except Exception as e:
            logger.warning(f"Failed to send decline notification: {str(e)}")

        messages.success(
            request,
            f'✗ Booking declined for {booking.client.get_full_name()} with {booking.salesman.get_full_name()}. '
            f'Notification sent to {booking.created_by.get_full_name()}.'
        )

        from .signals import create_audit_log
        create_audit_log(
            user=request.user,
            action='update',
            entity_type='Booking',
            entity_id=booking.id,
            changes={
                'status': 'declined',
                'declined_by': request.user.get_full_name(),
                'decline_reason': decline_reason,
            },
            request=request,
        )

        return redirect('salesman_pending_bookings')

    return render(request, 'salesman_booking_decline.html', {'booking': booking})

@login_required
def booking_approve(request, pk):
    """Approve a pending booking - Admin or assigned Salesman"""
    booking = get_object_or_404(Booking, pk=pk)

    # Check permissions
    is_admin = request.user.is_staff

    if not is_admin:
        messages.error(request, "You don't have permission to approve this booking.")
        return redirect('pending_bookings')

    if not booking.can_be_approved():
        messages.error(request, 'This booking cannot be approved.')
        return redirect('pending_bookings')

    # Instantiate the form
    if request.method == 'POST':
        form = AudioForm(request.POST, request.FILES, instance=booking, request=request)
        if form.is_valid():
            # Save audio_file update
            form.save(commit=False)

            # Update booking status
            booking.status = 'confirmed'
            booking.approved_at = timezone.now()
            booking.approved_by = request.user
            booking.save()

            # Send confirmation emails
            try:
                send_booking_confirmation(booking)
            except Exception as e:
                logger.warning(f"Failed to send booking confirmation: {str(e)}")

            # Send approval notification
            try:
                send_booking_approved_notification(booking)
            except Exception as e:
                logger.warning(f"Failed to send approval notification: {str(e)}")

            messages.success(
                request,
                f'✓ Booking approved for {booking.client.get_full_name()} with {booking.salesman.get_full_name()}. '
                f'Confirmation emails sent to all parties.'
            )

            # Log the approval
            from .signals import create_audit_log
            create_audit_log(
                user=request.user,
                action='update',
                entity_type='Booking',
                entity_id=booking.id,
                changes={'status': 'confirmed', 'approved_by': request.user.get_full_name()},
                request=request
            )

            return redirect('pending_bookings')
    else:
        form = AudioForm(instance=booking, request=request)

    return render(request, 'booking_approve.html', {'booking': booking, 'form': form})

@login_required
def booking_cancel(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    
    # Check if booking can be canceled
    if booking.status not in ['confirmed', 'completed']:
        messages.error(request, 'This booking has already been canceled.')
        return redirect('booking_detail', pk=pk)
    
    if booking.is_locked:
        messages.error(request, 'This booking is locked (payroll finalized). Contact admin for adjustments.')
        return redirect('booking_detail', pk=pk)
    
    # Check permissions: Only admins can cancel. Remote agents and salesmen cannot cancel.
    if not request.user.is_staff:
        return HttpResponseForbidden("Only administrators can cancel bookings.")
    
    if request.method == 'POST':
        form = CancelBookingForm(request.POST)
        if form.is_valid():
            booking.status = 'canceled'
            booking.cancellation_reason = form.cleaned_data['cancellation_reason']
            booking.cancellation_notes = form.cleaned_data['cancellation_notes']
            booking.canceled_at = timezone.now()
            booking.canceled_by = request.user
            booking.save()
            
            # Send cancellation emails
            try:
                send_booking_cancellation(booking)
                messages.success(request, 'Booking canceled successfully! Notifications sent.')
            except Exception as e:
                messages.warning(request, f'Booking canceled but email failed: {str(e)}')
            
            return redirect('calendar')
    else:
        form = CancelBookingForm()
    
    return render(request, 'booking_cancel.html', {'form': form, 'booking': booking})

@login_required
def booking_decline(request, pk):
    """Decline a pending booking - Admin or assigned Salesman"""
    booking = get_object_or_404(Booking, pk=pk)
    
    # Check permissions
    is_admin = request.user.is_staff
    
    # Only admin or the assigned salesman can decline
    if not is_admin:
        messages.error(request, "You don't have permission to decline this booking.")
        return redirect('pending_bookings')
    
    if not booking.can_be_declined():
        messages.error(request, 'This booking cannot be declined.')
        return redirect('pending_bookings')

    if request.method == 'POST':
        decline_reason = request.POST.get('decline_reason', '').strip()

        if not decline_reason:
            messages.error(request, 'Please provide a reason for declining.')
            return render(request, 'booking_decline.html', {'booking': booking})

        booking.status = 'declined'
        booking.declined_at = timezone.now()
        booking.declined_by = request.user
        booking.decline_reason = decline_reason
        booking.save()

        # Send decline notification to remote agent who created it
        try:
            send_booking_declined_notification(booking)
        except Exception as e:
            logger.warning(f"Failed to send decline notification: {str(e)}")

        messages.success(
            request,
            f'✗ Booking declined for {booking.client.get_full_name()} with {booking.salesman.get_full_name()}. '
            f'Notification sent to {booking.created_by.get_full_name()}.'
        )

        # Log the decline
        from .signals import create_audit_log
        create_audit_log(
            user=request.user,
            action='update',
            entity_type='Booking',
            entity_id=booking.id,
            changes={
                'status': 'declined',
                'declined_by': request.user.get_full_name(),
                'decline_reason': decline_reason
            },
            request=request
        )

        return redirect('pending_bookings')

    return render(request, 'booking_decline.html', {'booking': booking})


@login_required
def booking_mark_attended(request, pk):
    """Mark a confirmed booking as attended (completed). Start AD drip campaign."""
    booking = get_object_or_404(Booking, pk=pk)
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()

    if not (is_admin or (is_salesman and booking.salesman == request.user)):
        return HttpResponseForbidden("You don't have permission to update attendance for this booking.")

    if booking.status != 'confirmed':
        messages.error(request, 'Only confirmed bookings can be marked as attended.')
        return redirect('booking_detail', pk=pk)

    if booking.appointment_date > timezone.now().date():
        messages.error(request, 'You can only mark attendance on or after the appointment date.')
        return redirect('booking_detail', pk=pk)

    booking.status = 'completed'
    booking.save()
    
    # Start AD (Attended) drip campaign
    try:
        campaign = start_drip_campaign(booking, 'attended')
        if campaign:
            messages.success(request, 'Booking marked as Attended (Completed). AD drip campaign started.')
        else:
            messages.success(request, 'Booking marked as Attended (Completed).')
    except Exception as e:
        messages.warning(request, f'Booking marked as Attended but drip campaign failed: {str(e)}')
    
    return redirect('past_appointments')


@login_required
def booking_mark_dna(request, pk):
    """Mark a confirmed booking as Did Not Attend (no_show). Start DNA drip campaign."""
    booking = get_object_or_404(Booking, pk=pk)
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()

    if not (is_admin or (is_salesman and booking.salesman == request.user)):
        return HttpResponseForbidden("You don't have permission to update attendance for this booking.")

    if booking.status != 'confirmed':
        messages.error(request, 'Only confirmed bookings can be marked as DNA.')
        return redirect('booking_detail', pk=pk)

    if booking.appointment_date > timezone.now().date():
        messages.error(request, 'You can only mark attendance on or after the appointment date.')
        return redirect('booking_detail', pk=pk)

    booking.status = 'no_show'
    booking.save()
    
    # Start DNA (Did Not Attend) drip campaign
    try:
        campaign = start_drip_campaign(booking, 'did_not_attend')
        if campaign:
            messages.success(request, 'Booking marked as Did Not Attend (DNA). DNA drip campaign started.')
        else:
            messages.success(request, 'Booking marked as Did Not Attend (DNA).')
    except Exception as e:
        messages.warning(request, f'Booking marked as DNA but drip campaign failed: {str(e)}')
    
    return redirect('past_appointments')
    """Edit existing message template"""
    template = get_object_or_404(MessageTemplate, pk=pk)
    
    if request.method == 'POST':
        form = MessageTemplateForm(request.POST, instance=template)
        if form.is_valid():
            template = form.save()
            messages.success(request, f'Message template "{template.get_message_type_display()}" updated successfully!')
            return redirect('settings')
    else:
        form = MessageTemplateForm(instance=template)
    
    return render(request, 'message_template_form.html', {
        'form': form, 
        'title': 'Edit Message Template',
        'template': template
    })



@login_required
@admin_required
def past_appointments_view(request):
    """Admin page to view past appointments and manage AD/DNA."""
    today_date = timezone.now().date()
    status_filter = request.GET.get('status')
    salesman_id = request.GET.get('salesman')

    qs = Booking.objects.filter(
        appointment_date__lt=today_date,
        status__in=['confirmed', 'completed', 'no_show']
    ).select_related('client', 'salesman').order_by('-appointment_date', '-appointment_time')

    if status_filter in ['confirmed', 'completed', 'no_show']:
        qs = qs.filter(status=status_filter)
    if salesman_id:
        qs = qs.filter(salesman_id=salesman_id)

    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    salesmen = User.objects.filter(is_active_salesman=True, is_active=True).order_by('first_name', 'last_name')

    context = {
        'page_obj': page_obj,
        'salesmen': salesmen,
        'status_filter': status_filter,
        'selected_salesman': salesman_id,
    }
    return render(request, 'past_appointments.html', context)

@login_required
def pending_bookings_count_api(request):
    """API endpoint for pending bookings count (for badge in navbar)"""
    # Admin sees all, salesman sees only theirs
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()
    
    if is_salesman and not is_admin:
        count = Booking.objects.filter(status='pending', salesman=request.user).count()
    else:
        count = Booking.objects.filter(status='pending').count()
    
    return JsonResponse({'count': count})

@login_required
@group_required('salesman')
def salesman_pending_bookings_count_api(request):
    """API endpoint for salesman pending bookings count (for badge in navbar)"""
    count = Booking.objects.filter(salesman=request.user, status='pending').count()
    return JsonResponse({'count': count})

# ============================================================
# Commission Views
# ============================================================

# Update the commissions_view function in views.py

@login_required
@group_required('remote_agent')  # Only remote agents can access
def commissions_view(request):
    """Remote agents view their own commissions - RESTRICTED TO REMOTE AGENTS ONLY"""
    
    # Double-check user is remote agent (security)
    if not request.user.groups.filter(name='remote_agent').exists():
        messages.error(request, "You don't have permission to view commissions.")
        return redirect('calendar')
    
    week_offset = int(request.GET.get('week', 0))
    
    current_period = get_current_payroll_period()
    start_date = current_period['start_date'] - timedelta(weeks=week_offset)
    end_date = start_date + timedelta(days=6)
    
    # Only show bookings created by this remote agent
    all_bookings = Booking.objects.filter(
        created_by=request.user,
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman').order_by('-appointment_date', '-appointment_time')
    
    # Separate bookings by status - EVALUATE QUERYSETS EXPLICITLY
    confirmed_bookings = list(all_bookings.filter(status__in=['confirmed', 'completed']))
    pending_bookings = list(all_bookings.filter(status='pending'))
    declined_bookings = list(all_bookings.filter(status='declined'))
    
    # Calculate totals for confirmed/completed (these count toward commission)
    total_commission = sum(b.commission_amount for b in confirmed_bookings)
    total_bookings = len(confirmed_bookings)
    
    # Calculate totals for pending (these don't count yet but should be visible)
    pending_count = len(pending_bookings)
    pending_commission = sum(b.commission_amount for b in pending_bookings)
    
    # Count declined bookings
    declined_count = len(declined_bookings)
    
    # Check if period is finalized
    payroll_period = PayrollPeriod.objects.filter(
        start_date=start_date,
        end_date=end_date
    ).first()
    
    available_weeks = get_payroll_periods(3)
    
    context = {
        'bookings': all_bookings,  # All bookings to display
        'confirmed_bookings': confirmed_bookings,  # Explicitly pass these too
        'pending_bookings': pending_bookings,      # Explicitly pass pending
        'declined_bookings': declined_bookings,    # Explicitly pass declined
        'total_commission': total_commission,
        'total_bookings': total_bookings,
        'pending_count': pending_count,
        'pending_commission': pending_commission,
        'declined_count': declined_count,
        'start_date': start_date,
        'end_date': end_date,
        'week_offset': week_offset,
        'payroll_period': payroll_period,
        'available_weeks': available_weeks,
    }
    
    return render(request, 'commissions.html', context)

# ============================================================
# Availability Views
# ============================================================

@login_required
@group_required('salesman', 'admin')
def availability_view(request):
    # Determine if user is admin
    is_admin = request.user.is_staff
    
    # Get salesman parameter (admin only)
    if is_admin:
        salesman_id = request.GET.get('salesman')
        if salesman_id:
            salesman = get_object_or_404(User, pk=salesman_id, is_active_salesman=True)
        else:
            salesman = request.user
    else:
        salesman = request.user
    
    # Get unavailability blocks
    blocks = Unavailability.objects.filter(
        salesman=salesman,
        end_date__gte=timezone.now().date()
    ).order_by('start_date', 'start_time')
    
    # Get all salesmen for admin dropdown
    salesmen = None
    if is_admin:
        salesmen = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        )
    
    context = {
        'blocks': blocks,
        'salesman': salesman,
        'salesmen': salesmen,
        'is_admin': is_admin,
    }
    
    return render(request, 'availability.html', context)

@login_required
@group_required('salesman', 'admin')
def availability_create(request):
    is_admin = request.user.is_staff
    
    if request.method == 'POST':
        form = UnavailabilityForm(request.POST, request=request, is_admin=is_admin)
        if form.is_valid():
            unavailability = form.save()
            messages.success(request, 'Unavailability block created successfully!')
            return redirect('availability')
    else:
        initial = {}
        if not is_admin:
            initial['salesman'] = request.user
        elif request.GET.get('salesman'):
            initial['salesman'] = request.GET.get('salesman')
        
        form = UnavailabilityForm(initial=initial, request=request, is_admin=is_admin)
    
    return render(request, 'availability_form.html', {'form': form, 'title': 'Add Unavailability'})

@login_required
@group_required('salesman', 'admin')
def availability_edit(request, pk):
    block = get_object_or_404(Unavailability, pk=pk)
    is_admin = request.user.is_staff
    
    # Check permissions
    if not is_admin and block.salesman != request.user:
        return HttpResponseForbidden("You don't have permission to edit this block.")
    
    if request.method == 'POST':
        form = UnavailabilityForm(request.POST, instance=block, request=request, is_admin=is_admin)
        if form.is_valid():
            form.save()
            messages.success(request, 'Unavailability block updated successfully!')
            return redirect('availability')
    else:
        form = UnavailabilityForm(instance=block, request=request, is_admin=is_admin)
    
    return render(request, 'availability_form.html', {'form': form, 'title': 'Edit Unavailability', 'block': block})

@login_required
@group_required('salesman', 'admin')
def availability_delete(request, pk):
    block = get_object_or_404(Unavailability, pk=pk)
    is_admin = request.user.is_staff
    
    # Check permissions
    if not is_admin and block.salesman != request.user:
        return HttpResponseForbidden("You don't have permission to delete this block.")
    
    if request.method == 'POST':
        block.delete()
        messages.success(request, 'Unavailability block deleted successfully!')
        return redirect('availability')
    
    return render(request, 'availability_delete.html', {'block': block})

# ============================================================
# Payroll Views (Admin Only)
# ============================================================

@login_required
@admin_required
def payroll_view(request):
    # Get week parameter or use current week
    week_param = request.GET.get('week')
    
    if week_param:
        try:
            parts = week_param.split('_')
            start_date = datetime.strptime(parts[0], '%Y-%m-%d').date()
            end_date = datetime.strptime(parts[1], '%Y-%m-%d').date()
        except:
            current = get_current_payroll_period()
            start_date = current['start_date']
            end_date = current['end_date']
    else:
        current = get_current_payroll_period()
        start_date = current['start_date']
        end_date = current['end_date']
    
    # Get or create payroll period
    payroll_period, created = PayrollPeriod.objects.get_or_create(
        start_date=start_date,
        end_date=end_date
    )
    
    # Get all bookings in this period CREATED BY REMOTE AGENTS only
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date,
        created_by__groups__name='remote_agent'
    ).select_related('client', 'salesman', 'created_by')
    
    # Calculate commissions by remote agent (created_by)
    user_commissions = {}
    for booking in bookings:
        user_id = booking.created_by.id
        if user_id not in user_commissions:
            user_commissions[user_id] = {
                'user': booking.created_by,
                'bookings': [],
                'total': 0,
                'count': 0
            }
        
        user_commissions[user_id]['bookings'].append(booking)
        
        if booking.counts_for_commission():
            user_commissions[user_id]['total'] += booking.commission_amount
            user_commissions[user_id]['count'] += 1
    
    # Get adjustments for this period
    adjustments = PayrollAdjustment.objects.filter(
        payroll_period=payroll_period
    ).select_related('user', 'booking', 'created_by')
    
    # Apply adjustments to user totals
    for adjustment in adjustments:
        user_id = adjustment.user.id
        if user_id in user_commissions:
            user_commissions[user_id]['total'] += adjustment.amount
    
    # Get available periods
    available_periods = get_payroll_periods(12)
    
    # Calculate summary totals
    user_commissions_list = list(user_commissions.values())
    total_commission = sum(uc['total'] for uc in user_commissions_list)
    total_bookings = sum(uc['count'] for uc in user_commissions_list)
    
    context = {
        'payroll_period': payroll_period,
        'user_commissions': user_commissions_list,
        'adjustments': adjustments,
        'start_date': start_date,
        'end_date': end_date,
        'available_periods': available_periods,
        'can_finalize': payroll_period.status == 'pending',
        'total_commission': total_commission,
        'total_bookings': total_bookings,
    }
    
    return render(request, 'payroll.html', context)

@login_required
@admin_required
def payroll_finalize(request, pk):
    payroll_period = get_object_or_404(PayrollPeriod, pk=pk)
    
    if payroll_period.status == 'finalized':
        messages.error(request, 'This payroll period has already been finalized.')
        return redirect('payroll')
    
    if request.method == 'POST':
        # Finalize the period
        payroll_period.status = 'finalized'
        payroll_period.finalized_at = timezone.now()
        payroll_period.finalized_by = request.user
        payroll_period.save()
        
        # Lock all bookings in this period
        Booking.objects.filter(
            appointment_date__gte=payroll_period.start_date,
            appointment_date__lte=payroll_period.end_date
        ).update(
            is_locked=True,
            payroll_period=payroll_period
        )
        
        messages.success(request, f'Payroll period finalized successfully! {payroll_period.get_week_label()}')
        return redirect('payroll')
    
    # Calculate summary for confirmation
    bookings = Booking.objects.filter(
        appointment_date__gte=payroll_period.start_date,
        appointment_date__lte=payroll_period.end_date,
        status__in=['confirmed', 'completed']
    )
    
    total_commission = sum(b.commission_amount for b in bookings)
    total_bookings = bookings.count()
    affected_users = bookings.values('salesman').distinct().count()
    
    context = {
        'payroll_period': payroll_period,
        'total_commission': total_commission,
        'total_bookings': total_bookings,
        'affected_users': affected_users,
    }
    
    return render(request, 'payroll_finalize.html', context)

@login_required
@admin_required
def payroll_export(request):
    # Get week parameter
    week_param = request.GET.get('week')
    
    if week_param:
        try:
            parts = week_param.split('_')
            start_date = datetime.strptime(parts[0], '%Y-%m-%d').date()
            end_date = datetime.strptime(parts[1], '%Y-%m-%d').date()
        except:
            current = get_current_payroll_period()
            start_date = current['start_date']
            end_date = current['end_date']
    else:
        current = get_current_payroll_period()
        start_date = current['start_date']
        end_date = current['end_date']
    
    # Get payroll period
    payroll_period = PayrollPeriod.objects.filter(
        start_date=start_date,
        end_date=end_date
    ).first()
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="payroll_{start_date}_{end_date}.csv"'
    
    writer = csv.writer(response)
    
    # Write header
    writer.writerow([
        'Employee ID', 'Employee Name', 'Email', 'Client Name', 
        'Appointment Date', 'Appointment Type', 'Status', 
        'Commission Amount', 'Notes'
    ])
    
    # Get bookings created by remote agents only
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date,
        created_by__groups__name='remote_agent'
    ).select_related('client', 'salesman', 'created_by').order_by('created_by', 'appointment_date')
    
    # Write booking rows
    for booking in bookings:
        commission = booking.commission_amount if booking.counts_for_commission() else 0
        
        writer.writerow([
            booking.created_by.employee_id,
            booking.created_by.get_full_name(),
            booking.created_by.email,
            booking.client.get_full_name() if hasattr(booking.client, 'get_full_name') else f"{booking.client.first_name} {booking.client.last_name}",
            booking.appointment_date,
            booking.get_appointment_type_display(),
            booking.get_status_display(),
            commission,
            booking.notes
        ])
    
    # Add summary section
    writer.writerow([])
    writer.writerow(['SUMMARY'])
    writer.writerow([])
    
    # Calculate totals by remote agent (created_by)
    user_totals = {}
    for booking in bookings:
        if booking.counts_for_commission():
            user_id = booking.created_by.id
            if user_id not in user_totals:
                user_totals[user_id] = {
                    'employee_id': booking.created_by.employee_id,
                    'name': booking.created_by.get_full_name(),
                    'total': 0,
                    'count': 0
                }
            user_totals[user_id]['total'] += booking.commission_amount
            user_totals[user_id]['count'] += 1
    
    # Write summary rows
    writer.writerow(['Employee ID', 'Employee Name', 'Total Bookings', 'Total Commission'])
    for user_data in user_totals.values():
        writer.writerow([
            user_data['employee_id'],
            user_data['name'],
            user_data['count'],
            user_data['total']
        ])
    
    # Add adjustments if any
    if payroll_period:
        adjustments = PayrollAdjustment.objects.filter(payroll_period=payroll_period)
        if adjustments.exists():
            writer.writerow([])
            writer.writerow(['ADJUSTMENTS'])
            writer.writerow(['Employee ID', 'Employee Name', 'Type', 'Amount', 'Reason'])
            
            for adj in adjustments:
                writer.writerow([
                    adj.user.employee_id,
                    adj.user.get_full_name(),
                    adj.get_adjustment_type_display(),
                    adj.amount,
                    adj.reason
                ])
    
    return response

@login_required
@admin_required
def payroll_adjustment_create(request):
    week_param = request.GET.get('week')
    
    if week_param:
        try:
            parts = week_param.split('_')
            start_date = datetime.strptime(parts[0], '%Y-%m-%d').date()
            end_date = datetime.strptime(parts[1], '%Y-%m-%d').date()
            payroll_period = PayrollPeriod.objects.get(start_date=start_date, end_date=end_date)
        except:
            messages.error(request, 'Invalid payroll period.')
            return redirect('payroll')
    else:
        messages.error(request, 'Payroll period not specified.')
        return redirect('payroll')
    
    if request.method == 'POST':
        form = PayrollAdjustmentForm(request.POST, payroll_period=payroll_period)
        if form.is_valid():
            adjustment = form.save(commit=False)
            adjustment.payroll_period = payroll_period
            adjustment.created_by = request.user
            adjustment.save()
            
            messages.success(request, 'Payroll adjustment created successfully!')
            return redirect('payroll' + f'?week={week_param}')
    else:
        form = PayrollAdjustmentForm(payroll_period=payroll_period)
    
    context = {
        'form': form,
        'payroll_period': payroll_period,
    }
    
    return render(request, 'payroll_adjustment_form.html', context)

# ============================================================
# User Management Views (Admin Only)
# ============================================================

@login_required
@admin_required
def users_view(request):
    users = User.objects.all().order_by('last_name', 'first_name')
    
    # Filter options
    role_filter = request.GET.get('role')
    status_filter = request.GET.get('status')
    
    if role_filter:
        users = users.filter(groups__name=role_filter)
    
    if status_filter == 'active':
        users = users.filter(is_active=True)
    elif status_filter == 'inactive':
        users = users.filter(is_active=False)
    
    context = {
        'users': users,
        'role_filter': role_filter,
        'status_filter': status_filter,
    }
    
    return render(request, 'users.html', context)

@login_required
@admin_required
def user_create(request):
    if request.method == 'POST':
        form = UserForm(request.POST)
        if form.is_valid():
            try:
                # Check if password was provided in form
                password_from_form = request.POST.get('password')
                
                if password_from_form:
                    # Password provided - form will handle it
                    user = form.save()
                    messages.success(request, 'User created successfully!')
                else:
                    # No password provided - generate temp password
                    user = form.save(commit=False)  # Don't save yet
                    
                    temp_password = get_random_string(length=12)
                    user.set_password(temp_password)
                    user.plain_text_password = temp_password  # SAVE PLAIN TEXT
                    
                    # Now save with the password
                    user.save()
                    
                    # Handle groups (since we used commit=False)
                    user.groups.clear()
                    for role in form.cleaned_data.get('roles', []):
                        from django.contrib.auth.models import Group
                        group, created = Group.objects.get_or_create(name=role)
                        user.groups.add(group)
                    
                    logger.info(f"User created: {user.username}, Employee ID: {user.employee_id}, Temp Password: {temp_password}")
                    messages.success(
                        request, 
                        f'User created successfully! Temporary password: {temp_password} '
                        f'(Please save this and share securely with the user)'
                    )
                
                return redirect('users')
            except Exception as e:
                logger.error(f"Error creating user: {str(e)}")
                messages.error(request, f'Error creating user: {str(e)}')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = UserForm()
    
    return render(request, 'user_form.html', {'form': form, 'title': 'Create User'})

    
@login_required
@admin_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    
    if request.method == 'POST':
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            password_from_form = request.POST.get('password')
            
            user = form.save()
            
            # If password was provided, it's already saved by form
            # Just show a message
            if password_from_form:
                messages.success(request, f'User updated successfully! New password: {password_from_form}')
            else:
                messages.success(request, 'User updated successfully!')
            
            return redirect('users')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = UserForm(instance=user)
    
    return render(request, 'user_form.html', {'form': form, 'title': 'Edit User', 'user': user})

@login_required
@admin_required
def user_deactivate(request, pk):
    user = get_object_or_404(User, pk=pk)
    
    if request.method == 'POST':
        user.is_active = False
        user.save()
        messages.success(request, f'User {user.get_full_name()} deactivated successfully!')
        return redirect('users')
    
    return render(request, 'user_deactivate.html', {'user': user})

# ============================================================
# System Settings Views (Admin Only)
# ============================================================

@login_required
@admin_required
def settings_view(request):
    config = SystemConfig.get_config()
    message_templates = MessageTemplate.objects.all().order_by('message_type')
    
    # Check if email/SMS are configured via environment variables
    email_configured = bool(os.getenv('SENDGRID_API_KEY') or os.getenv('EMAIL_HOST_PASSWORD'))
    sms_configured = bool(os.getenv('TWILIO_ACCOUNT_SID') and os.getenv('TWILIO_AUTH_TOKEN') and os.getenv('TWILIO_FROM_NUMBER'))
    sms_enabled = os.getenv('SMS_ENABLED', 'false').lower() in ('true', '1', 'yes')
    
    if request.method == 'POST':
        if 'save_general' in request.POST:
            form = SystemConfigForm(request.POST, instance=config)
            if form.is_valid():
                config = form.save(commit=False)
                config.updated_by = request.user
                config.save()
                messages.success(request, 'General settings updated successfully!')
                return redirect('settings')
    else:
        form = SystemConfigForm(instance=config)
    
    context = {
        'form': form,
        'config': config,
        'message_templates': message_templates,
        'email_configured': email_configured,
        'sms_configured': sms_configured,
        'sms_enabled': sms_enabled,
    }
    
    return render(request, 'settings.html', context)

# ============================================================
# Audit Log Views (Admin Only)
# ============================================================

@login_required
@admin_required
def audit_log_view(request):
    logs = AuditLog.objects.all().select_related('user').order_by('-timestamp')
    
    # Filters
    user_filter = request.GET.get('user')
    action_filter = request.GET.get('action')
    entity_filter = request.GET.get('entity')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if user_filter:
        logs = logs.filter(user_id=user_filter)
    
    if action_filter:
        logs = logs.filter(action=action_filter)
    
    if entity_filter:
        logs = logs.filter(entity_type=entity_filter)
    
    if date_from:
        logs = logs.filter(timestamp__gte=date_from)
    
    if date_to:
        logs = logs.filter(timestamp__lte=date_to)
    
    # Pagination
    paginator = Paginator(logs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get unique users and entity types for filters
    users = User.objects.filter(auditlog__isnull=False).distinct()
    entity_types = AuditLog.objects.values_list('entity_type', flat=True).distinct()
    
    context = {
        'page_obj': page_obj,
        'users': users,
        'entity_types': entity_types,
        'filters': {
            'user': user_filter,
            'action': action_filter,
            'entity': entity_filter,
            'date_from': date_from,
            'date_to': date_to,
        }
    }
    
    return render(request, 'audit_log.html', context)

# ============================================================
@login_required
def timeslots_view(request):
    """Main availability dashboard view using 2-week cycles with automatic generation and pagination."""
    is_admin = request.user.is_staff

    # Auto-inactivate past and elapsed slots
    try:
        mark_past_slots_inactive()
        mark_elapsed_today_slots_inactive()
    except Exception:
        pass

    # Ensure there's an active cycle
    selected_cycle_id = request.GET.get('cycle')
    selected_salesman_id = request.GET.get('salesman') if is_admin else None
    cycles = AvailabilityCycle.objects.all().order_by('-start_date')
    cycle = AvailabilityCycle.objects.filter(id=selected_cycle_id).first() or AvailabilityCycle.get_current_cycle()

    # Filters
    selected_day = request.GET.get('day')
    appointment_type = request.GET.get('type')

    # Base queryset: slots for the selected cycle
    slots = AvailableTimeSlot.objects.filter(cycle=cycle)

    if selected_day:
        slots = slots.filter(date=selected_day)
    if appointment_type:
        slots = slots.filter(appointment_type=appointment_type)
    if selected_salesman_id:
        slots = slots.filter(salesman_id=selected_salesman_id)

    if not is_admin:
        slots = slots.filter(salesman=request.user)

    # Order with active slots first
    slots = slots.select_related('salesman', 'created_by').order_by('-is_active', 'date', 'start_time', 'salesman')

    # Handle admin actions
    if request.method == 'POST' and is_admin:
        if 'cleanup_slots' in request.POST:
            count = cleanup_old_slots()
            messages.info(request, f'Marked {count} old unbooked slots as inactive.')
            return redirect('timeslots')
        elif 'delete_cycle' in request.POST:
            if cycle:
                cycle.delete()
                messages.success(request, 'Current 2-week cycle deleted. A new cycle will be created automatically.')
            return redirect('timeslots')

    # PAGINATION - Show 50 slots per page
    paginator = Paginator(slots, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    salesmen = None
    if is_admin:
        salesmen = User.objects.filter(is_active_salesman=True, is_active=True).order_by('first_name', 'last_name')

    context = {
        'page_obj': page_obj,  # Use page_obj instead of timeslots
        'cycles': cycles,
        'selected_cycle': cycle,
        'selected_day': selected_day,
        'selected_type': appointment_type,
        'selected_salesman': selected_salesman_id,
        'salesmen': salesmen,
        'is_admin': is_admin,
    }
    return render(request, 'timeslots.html', context)


# ============================================================
# NEW: Day Detail View for Calendar
# ============================================================

@login_required
def calendar_day_detail(request, date_str):
    """Show all slots and bookings for a specific day"""
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Invalid date format.')
        return redirect('calendar')
    
    is_admin = request.user.is_staff
    is_salesman = request.user.groups.filter(name='salesman').exists()
    is_remote_agent = request.user.groups.filter(name='remote_agent').exists()
    
    # Get filters
    salesman_id = request.GET.get('salesman')
    appointment_type = request.GET.get('type')
    
    # Get available slots
    slots = AvailableTimeSlot.objects.filter(
        is_active=True,
        date=selected_date
    ).select_related('salesman')
    
    if is_salesman and not is_admin:
        slots = slots.none()  # Salesmen don't see available slots
    elif salesman_id and is_admin:
        slots = slots.filter(salesman_id=salesman_id)
    
    if appointment_type:
        slots = slots.filter(appointment_type=appointment_type)
    
    slots = slots.order_by('start_time', 'salesman')
    
    # Get bookings
    bookings = Booking.objects.filter(
        appointment_date=selected_date
    ).select_related('client', 'salesman', 'created_by')
    
    # Filter bookings by user role
    if is_salesman and not is_admin:
        bookings = bookings.filter(salesman=request.user)
    elif is_remote_agent and not is_admin:
        bookings = bookings.filter(created_by=request.user)
    elif salesman_id and is_admin:
        bookings = bookings.filter(salesman_id=salesman_id)
    
    if appointment_type:
        bookings = bookings.filter(appointment_type=appointment_type)
    
    bookings = bookings.order_by('appointment_time')
    
    # Separate bookings by status
    pending_bookings = list(bookings.filter(status='pending'))
    confirmed_bookings = list(bookings.filter(status__in=['confirmed', 'completed']))
    declined_bookings = list(bookings.filter(status='declined'))
    
    # Get salesmen for filter (admin only)
    salesmen = None
    if is_admin:
        salesmen = User.objects.filter(
            is_active_salesman=True,
            is_active=True
        ).order_by('first_name', 'last_name')
    
    context = {
        'selected_date': selected_date,
        'available_slots': slots,
        'pending_bookings': pending_bookings,
        'confirmed_bookings': confirmed_bookings,
        'declined_bookings': declined_bookings,
        'salesmen': salesmen,
        'selected_salesman': salesman_id,
        'selected_type': appointment_type,
        'is_admin': is_admin,
        'is_salesman': is_salesman and not is_admin,
        'is_remote_agent': is_remote_agent and not is_admin,
    }
    
    return render(request, 'calendar_day_detail.html', context)




@login_required
@group_required('salesman', 'admin')
def timeslot_create(request):
    """Create new time slot - Admin can create for anyone, Salesman for themselves"""
    is_admin = request.user.is_staff
    
    if request.method == 'POST':
        form = AvailableTimeSlotForm(request.POST, is_admin=is_admin, current_user=request.user)
        if form.is_valid():
            timeslot = form.save(commit=False)
            timeslot.created_by = request.user
            
            # If not admin, force salesman to be current user
            if not is_admin:
                timeslot.salesman = request.user
            
            timeslot.save()
            messages.success(request, 'Time slot created successfully!')
            return redirect('timeslots')
    else:
        # Pre-fill salesman field for non-admin users
        initial = {}
        if not is_admin:
            initial['salesman'] = request.user
        elif request.GET.get('salesman'):
            initial['salesman'] = request.GET.get('salesman')
        
        form = AvailableTimeSlotForm(initial=initial, is_admin=is_admin, current_user=request.user)
    
    return render(request, 'timeslot_form.html', {
        'form': form, 
        'title': 'Create Time Slot',
        'is_admin': is_admin
    })


@login_required
@group_required('salesman', 'admin')
def timeslot_edit(request, pk):
    """Edit existing time slot - Admin can edit any, Salesman only their own"""
    timeslot = get_object_or_404(AvailableTimeSlot, pk=pk)
    is_admin = request.user.is_staff
    
    # Check permissions - salesmen can only edit their own slots
    if not is_admin and timeslot.salesman != request.user:
        messages.error(request, "You don't have permission to edit this time slot.")
        return redirect('timeslots')
    
    if request.method == 'POST':
        form = AvailableTimeSlotForm(request.POST, instance=timeslot, is_admin=is_admin, current_user=request.user)
        if form.is_valid():
            timeslot = form.save()
            
            # Prevent salesman from changing the salesman field
            if not is_admin and timeslot.salesman != request.user:
                timeslot.salesman = request.user
                timeslot.save()
            
            messages.success(request, 'Time slot updated successfully!')
            return redirect('timeslots')
    else:
        form = AvailableTimeSlotForm(instance=timeslot, is_admin=is_admin, current_user=request.user)
    
    return render(request, 'timeslot_form.html', {
        'form': form, 
        'title': 'Edit Time Slot', 
        'timeslot': timeslot,
        'is_admin': is_admin
    })


@login_required
@group_required('salesman', 'admin')
def timeslot_delete(request, pk):
    """Delete time slot - Admin can delete any, Salesman only their own"""
    timeslot = get_object_or_404(AvailableTimeSlot, pk=pk)
    is_admin = request.user.is_staff
    
    # Check permissions
    if not is_admin and timeslot.salesman != request.user:
        messages.error(request, "You don't have permission to delete this time slot.")
        return redirect('timeslots')
    
    if request.method == 'POST':
        timeslot.delete()
        messages.success(request, 'Time slot deleted successfully!')
        return redirect('timeslots')
    
    return render(request, 'timeslot_delete.html', {
        'timeslot': timeslot,
        'is_admin': is_admin
    })

@login_required
@admin_required
def message_templates_view(request):
    """View all message templates"""
    templates = MessageTemplate.objects.all().order_by('message_type')
    
    context = {
        'message_templates': templates,
    }
    return render(request, 'message_templates.html', context)


@login_required
@admin_required
def message_template_create(request):
    """Create new message template"""
    if request.method == 'POST':
        form = MessageTemplateForm(request.POST)
        if form.is_valid():
            template = form.save()
            messages.success(request, f'Message template "{template.get_message_type_display()}" created successfully!')
            return redirect('settings')
    else:
        form = MessageTemplateForm()
    
    return render(request, 'message_template_form.html', {'form': form, 'title': 'Create Message Template'})


@login_required
@admin_required
def message_template_edit(request, pk):
    """Edit existing message template"""
    template = get_object_or_404(MessageTemplate, pk=pk)
    
    if request.method == 'POST':
        form = MessageTemplateForm(request.POST, instance=template)
        if form.is_valid():
            template = form.save()
            messages.success(request, f'Message template "{template.get_message_type_display()}" updated successfully!')
            return redirect('settings')
    else:
        form = MessageTemplateForm(instance=template)
    
    return render(request, 'message_template_form.html', {
        'form': form, 
        'title': 'Edit Message Template',
        'template': template
    })


@login_required
@admin_required
def message_template_delete(request, pk):
    """Delete message template"""
    template = get_object_or_404(MessageTemplate, pk=pk)
    
    if request.method == 'POST':
        template_name = template.get_message_type_display()
        template.delete()
        messages.success(request, f'Message template "{template_name}" deleted successfully!')
        return redirect('settings')
    
    return render(request, 'message_template_delete.html', {'template': template})


@login_required
@admin_required
def drip_campaigns_view(request):
    """View all drip campaigns with filtering"""
    campaigns = DripCampaign.objects.all().select_related('booking__client', 'booking__salesman').order_by('-started_at')
    
    # Filters
    campaign_type = request.GET.get('type')
    status = request.GET.get('status')
    
    if campaign_type:
        campaigns = campaigns.filter(campaign_type=campaign_type)
    
    if status == 'active':
        campaigns = campaigns.filter(is_active=True, is_stopped=False)
    elif status == 'stopped':
        campaigns = campaigns.filter(is_stopped=True)
    elif status == 'completed':
        # Campaigns where all scheduled messages are sent/failed/canceled
        campaigns = campaigns.filter(is_active=True).exclude(
            scheduled_messages__status='pending'
        )
    
    # Pagination
    paginator = Paginator(campaigns, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'campaign_type': campaign_type,
        'status': status,
    }
    return render(request, 'drip_campaigns.html', context)


@login_required
@admin_required
def drip_campaign_stop(request, pk):
    """Stop a drip campaign"""
    campaign = get_object_or_404(DripCampaign, pk=pk)
    
    if request.method == 'POST':
        campaign.stop_campaign(request.user)
        messages.success(request, f'Drip campaign stopped for {campaign.booking.client.get_full_name()}')
        return redirect('drip_campaigns')
    
    return render(request, 'drip_campaign_stop.html', {'campaign': campaign})


@login_required
@admin_required
def drip_campaign_resume(request, pk):
    """Resume a stopped drip campaign"""
    campaign = get_object_or_404(DripCampaign, pk=pk)
    
    if request.method == 'POST':
        if campaign.is_stopped:
            campaign.is_active = True
            campaign.is_stopped = False
            campaign.save()
            
            # Reactivate pending messages
            campaign.scheduled_messages.filter(status='canceled').update(status='pending')
            
            messages.success(request, f'Drip campaign resumed for {campaign.booking.client.get_full_name()}')
        else:
            messages.warning(request, 'Campaign is not stopped')
        
        return redirect('drip_campaigns')
    
    return render(request, 'drip_campaign_resume.html', {'campaign': campaign})


@login_required
@admin_required
def communication_logs_view(request):
    """View all communication logs (emails + SMS)"""
    logs = CommunicationLog.objects.all().order_by('-sent_at')
    
    # Filters
    comm_type = request.GET.get('type')
    status = request.GET.get('status')
    
    if comm_type:
        logs = logs.filter(communication_type=comm_type)
    
    if status:
        logs = logs.filter(status=status)
    
    # Pagination
    paginator = Paginator(logs, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'comm_type': comm_type,
        'status': status,
    }
    return render(request, 'communication_logs.html', context)