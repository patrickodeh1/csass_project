from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum, Count
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.core.paginator import Paginator
from datetime import datetime, timedelta
import csv
from .models import (Booking, Client, Unavailability, PayrollPeriod, PayrollAdjustment, 
                     SystemConfig, AuditLog, UserProfile, CompanyHoliday, User)
from .forms import (LoginForm, BookingForm, CancelBookingForm, UnavailabilityForm,
                    PayrollAdjustmentForm, UserForm, SystemConfigForm)
from .decorators import group_required, admin_required
from .utils import (get_current_payroll_period, get_payroll_periods, send_booking_confirmation,
                    send_booking_cancellation, check_booking_conflicts, check_unavailability_conflicts)

# ============================================================
# Authentication Views
# ============================================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect('calendar')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            
            # Handle remember me
            if not form.cleaned_data.get('remember_me'):
                request.session.set_expiry(0)
            
            messages.success(request, f'Welcome back, {user.get_full_name()}!')
            return redirect('calendar')
    else:
        form = LoginForm()
    
    return render(request, 'login.html', {'form': form})

@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been logged out successfully.')
    return redirect('login')

# ============================================================
# Calendar & Booking Views
# ============================================================

@login_required
def calendar_view(request):
    # Get filter parameters
    salesman_id = request.GET.get('salesman')
    appointment_type = request.GET.get('type')
    view_mode = request.GET.get('view', 'month')
    date_str = request.GET.get('date')
    
    # Parse date or use current
    if date_str:
        try:
            current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            current_date = timezone.now().date()
    else:
        current_date = timezone.now().date()
    
    # Calculate date range based on view mode
    if view_mode == 'month':
        # First day of month to last day of month
        start_date = current_date.replace(day=1)
        if current_date.month == 12:
            end_date = current_date.replace(year=current_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = current_date.replace(month=current_date.month + 1, day=1) - timedelta(days=1)
    elif view_mode == 'week':
        # Monday to Sunday
        start_date = current_date - timedelta(days=current_date.weekday())
        end_date = start_date + timedelta(days=6)
    else:  # day
        start_date = end_date = current_date
    
    # Build query
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman', 'salesman__profile')
    
    if salesman_id:
        bookings = bookings.filter(salesman_id=salesman_id)
    
    if appointment_type:
        bookings = bookings.filter(appointment_type=appointment_type)
    
    # Get unavailability blocks
    unavailability_blocks = Unavailability.objects.filter(
        start_date__lte=end_date,
        end_date__gte=start_date
    ).select_related('salesman')
    
    if salesman_id:
        unavailability_blocks = unavailability_blocks.filter(salesman_id=salesman_id)
    
    # Get holidays
    holidays = CompanyHoliday.objects.filter(
        date__gte=start_date,
        date__lte=end_date
    )
    
    # Get all salesmen for filter
    salesmen = User.objects.filter(
        profile__is_active_salesman=True,
        is_active=True
    ).select_related('profile')
    
    context = {
        'bookings': bookings,
        'unavailability_blocks': unavailability_blocks,
        'holidays': holidays,
        'salesmen': salesmen,
        'current_date': current_date,
        'start_date': start_date,
        'end_date': end_date,
        'view_mode': view_mode,
        'selected_salesman': salesman_id,
        'selected_type': appointment_type,
    }
    
    return render(request, 'calendar.html', context)

@login_required
def booking_create(request):
    if request.method == 'POST':
        form = BookingForm(request.POST, request=request)
        if form.is_valid():
            booking = form.save()
            
            # Send confirmation emails
            try:
                send_booking_confirmation(booking)
                messages.success(request, 'Booking created successfully! Confirmation emails sent.')
            except Exception as e:
                messages.warning(request, f'Booking created but email failed: {str(e)}')
            
            return redirect('calendar')
    else:
        # Pre-fill date and time from URL params
        initial = {}
        if request.GET.get('date'):
            initial['appointment_date'] = request.GET.get('date')
        if request.GET.get('time'):
            initial['appointment_time'] = request.GET.get('time')
        if request.GET.get('salesman'):
            initial['salesman'] = request.GET.get('salesman')
        
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
    
    # Check permissions
    if not request.user.is_staff:
        if booking.salesman != request.user and booking.created_by != request.user:
            return HttpResponseForbidden("You don't have permission to edit this booking.")
    
    if request.method == 'POST':
        form = BookingForm(request.POST, instance=booking, request=request)
        if form.is_valid():
            booking = form.save()
            messages.success(request, 'Booking updated successfully!')
            return redirect('booking_detail', pk=pk)
    else:
        form = BookingForm(instance=booking, request=request)
    
    return render(request, 'booking_form.html', {'form': form, 'title': 'Edit Booking', 'booking': booking})

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
    
    # Check permissions
    if not request.user.is_staff:
        if booking.salesman != request.user and booking.created_by != request.user:
            return HttpResponseForbidden("You don't have permission to cancel this booking.")
    
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

# ============================================================
# Commission Views
# ============================================================

@login_required
@group_required('sales_support', 'salesman', 'admin')
def commissions_view(request):
    # Get week parameter or use current week
    week_offset = int(request.GET.get('week', 0))
    
    current_period = get_current_payroll_period()
    start_date = current_period['start_date'] - timedelta(weeks=week_offset)
    end_date = start_date + timedelta(days=6)
    
    # For non-admin users, only show their own commissions
    if request.user.is_staff:
        user_filter = request.GET.get('user')
        if user_filter:
            bookings = Booking.objects.filter(salesman_id=user_filter)
        else:
            bookings = Booking.objects.all()
    else:
        bookings = Booking.objects.filter(salesman=request.user)
    
    # Filter by date range
    bookings = bookings.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman').order_by('-appointment_date', '-appointment_time')
    
    # Calculate totals
    confirmed_bookings = bookings.filter(status__in=['confirmed', 'completed'])
    total_commission = sum(b.commission_amount for b in confirmed_bookings)
    total_bookings = confirmed_bookings.count()
    
    # Check if period is finalized
    payroll_period = PayrollPeriod.objects.filter(
        start_date=start_date,
        end_date=end_date
    ).first()
    
    # Get available weeks for dropdown
    available_weeks = get_payroll_periods(12)
    
    # Get all users for admin filter
    users = None
    if request.user.is_staff:
        users = User.objects.filter(
            profile__is_active_salesman=True,
            is_active=True
        ).select_related('profile')
    
    context = {
        'bookings': bookings,
        'total_commission': total_commission,
        'total_bookings': total_bookings,
        'start_date': start_date,
        'end_date': end_date,
        'week_offset': week_offset,
        'payroll_period': payroll_period,
        'available_weeks': available_weeks,
        'users': users,
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
            salesman = get_object_or_404(User, pk=salesman_id, profile__is_active_salesman=True)
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
            profile__is_active_salesman=True,
            is_active=True
        ).select_related('profile')
    
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
    
    # Get all bookings in this period
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman', 'salesman__profile')
    
    # Calculate commissions by user
    user_commissions = {}
    for booking in bookings:
        user_id = booking.salesman.id
        if user_id not in user_commissions:
            user_commissions[user_id] = {
                'user': booking.salesman,
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
    
    context = {
        'payroll_period': payroll_period,
        'user_commissions': user_commissions.values(),
        'adjustments': adjustments,
        'start_date': start_date,
        'end_date': end_date,
        'available_periods': available_periods,
        'can_finalize': payroll_period.status == 'pending',
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
    
    # Get bookings
    bookings = Booking.objects.filter(
        appointment_date__gte=start_date,
        appointment_date__lte=end_date
    ).select_related('client', 'salesman', 'salesman__profile').order_by('salesman', 'appointment_date')
    
    # Write booking rows
    for booking in bookings:
        commission = booking.commission_amount if booking.counts_for_commission() else 0
        
        writer.writerow([
            booking.salesman.profile.employee_id,
            booking.salesman.get_full_name(),
            booking.salesman.email,
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
    
    # Calculate totals by user
    user_totals = {}
    for booking in bookings:
        if booking.counts_for_commission():
            user_id = booking.salesman.id
            if user_id not in user_totals:
                user_totals[user_id] = {
                    'employee_id': booking.salesman.profile.employee_id,
                    'name': booking.salesman.get_full_name(),
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
                    adj.user.profile.employee_id,
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
    users = User.objects.all().select_related('profile').order_by('last_name', 'first_name')
    
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
            user = form.save(commit=False)
            # Generate temporary password
            temp_password = User.objects.make_random_password()
            user.set_password(temp_password)
            user = form.save()
            
            # TODO: Send email with temporary password
            messages.success(request, f'User created successfully! Temporary password: {temp_password}')
            return redirect('users')
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
            form.save()
            messages.success(request, 'User updated successfully!')
            return redirect('users')
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
    
    if request.method == 'POST':
        form = SystemConfigForm(request.POST, instance=config)
        if form.is_valid():
            config = form.save(commit=False)
            config.updated_by = request.user
            config.save()
            messages.success(request, 'System settings updated successfully!')
            return redirect('settings')
    else:
        form = SystemConfigForm(instance=config)
    
    context = {
        'form': form,
        'config': config,
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