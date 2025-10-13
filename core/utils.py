from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta, time
from .models import SystemConfig, Booking, PayrollPeriod, AvailableTimeSlot, AvailabilityCycle, User


def get_current_payroll_period():
    """Get current payroll period (Friday to Thursday)"""
    today = datetime.now().date()
    # Calculate days since last Friday (weekday 4)
    days_since_friday = (today.weekday() - 4) % 7
    period_start = today - timedelta(days=days_since_friday)
    period_end = period_start + timedelta(days=6)
    
    return {
        'start': datetime.combine(period_start, time.min),
        'end': datetime.combine(period_end, time.max),
        'start_date': period_start,
        'end_date': period_end
    }

def get_payroll_periods(weeks=3):
    """Get list of recent payroll periods"""
    periods = []
    current = get_current_payroll_period()
    
    for i in range(weeks):
        start = current['start_date'] - timedelta(weeks=i)
        end = start + timedelta(days=6)
        
        # Check if period exists in DB
        period = PayrollPeriod.objects.filter(start_date=start, end_date=end).first()
        
        periods.append({
            'start_date': start,
            'end_date': end,
            'label': f"Week of {start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}",
            'is_finalized': period.status == 'finalized' if period else False,
            'period_obj': period
        })
    
    return periods

def send_booking_confirmation(booking, to_client=True, to_salesman=True):
    """Send booking confirmation email"""
    config = SystemConfig.get_config()
    
    context = {
        'booking': booking,
        'company_name': config.company_name,
    }
    
    if to_client:
        subject = f"Appointment Confirmed with {booking.salesman.get_full_name()}"
        html_message = render_to_string('emails/booking_confirmation_client.html', context)
        plain_message = strip_tags(html_message)
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.client.email],
            html_message=html_message,
            fail_silently=False,
        )
    
    if to_salesman:
        subject = f"New Appointment: {booking.client.get_full_name()} on {booking.appointment_date}"
        html_message = render_to_string('emails/booking_confirmation_salesman.html', context)
        plain_message = strip_tags(html_message)
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.salesman.email],
            html_message=html_message,
            fail_silently=False,
        )

def send_booking_reminder(booking):
    """Send appointment reminder"""
    config = SystemConfig.get_config()
    
    context = {
        'booking': booking,
        'company_name': config.company_name,
    }
    
    subject = f"Reminder: Appointment Tomorrow at {booking.appointment_time.strftime('%I:%M %p')}"
    
    # Send to client
    html_message = render_to_string('emails/booking_reminder.html', context)
    plain_message = strip_tags(html_message)
    
    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[booking.client.email, booking.salesman.email],
        html_message=html_message,
        fail_silently=False,
    )

def send_booking_cancellation(booking):
    """Send cancellation notification"""
    config = SystemConfig.get_config()
    
    context = {
        'booking': booking,
        'company_name': config.company_name,
    }
    
    subject = f"Appointment Canceled: {booking.appointment_date}"
    html_message = render_to_string('emails/booking_cancellation.html', context)
    plain_message = strip_tags(html_message)
    
    send_mail(
        subject=subject,
        message=plain_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[booking.client.email, booking.salesman.email],
        html_message=html_message,
        fail_silently=False,
    )

def check_booking_conflicts(salesman, appointment_date, appointment_time, duration_minutes, exclude_booking_id=None):
    """Check for booking conflicts including buffer time"""
    config = SystemConfig.get_config()
    
    # Calculate time range including buffer
    start_dt = datetime.combine(appointment_date, appointment_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes + config.buffer_time_minutes)
    
    # Check for overlapping bookings
    conflicts = Booking.objects.filter(
        salesman=salesman,
        appointment_date=appointment_date,
        status__in=['confirmed', 'completed']
    ).exclude(id=exclude_booking_id)
    
    for booking in conflicts:
        booking_start = datetime.combine(booking.appointment_date, booking.appointment_time)
        booking_end = booking_start + timedelta(minutes=booking.duration_minutes + config.buffer_time_minutes)
        
        # Check for overlap
        if start_dt < booking_end and end_dt > booking_start:
            return True, booking
    
    return False, None


def send_booking_declined_notification(booking):
    """
    Send email notification when booking is declined by admin
    """
    # Email to remote agent who created the booking
    if booking.created_by.groups.filter(name='remote_agent').exists():
        subject = f'Booking Declined - {booking.client.get_full_name()}'
        
        context = {
            'booking': booking,
            'agent': booking.created_by,
            'admin': booking.declined_by,
        }
        
        message = render_to_string('emails/booking_declined.txt', context)
        html_message = render_to_string('emails/booking_declined.html', context)
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.created_by.email],
            html_message=html_message,
            fail_silently=False,
        )


def send_booking_approved_notification(booking):
    """
    Send email notification when booking is approved by admin
    Notify the remote agent that their booking was approved
    """
    if booking.created_by.groups.filter(name='remote_agent').exists():
        subject = f'Booking Approved - {booking.client.get_full_name()}'
        
        context = {
            'booking': booking,
            'agent': booking.created_by,
            'admin': booking.approved_by,
        }
        
        message = render_to_string('emails/booking_approved.txt', context)
        html_message = render_to_string('emails/booking_approved.html', context)
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.created_by.email],
            html_message=html_message,
            fail_silently=False,
        )


def generate_timeslots_for_cycle():
    """
    Generate timeslots automatically for each active salesman within the active 2-week cycle.
    - Only Monday to Friday
    - From 9:00 AM to 7:00 PM (30-minute intervals)
    - For both appointment types: zoom and in_person
    """
    cycle = AvailabilityCycle.get_current_cycle()

    start_date, end_date = cycle.start_date, cycle.end_date
    salesmen = User.objects.filter(is_active_salesman=True, is_active=True)

    for salesman in salesmen:
        current_date = start_date
        while current_date <= end_date:
            # Weekday check: 0=Mon ... 6=Sun -> only Mon-Fri
            if current_date.weekday() < 5:
                start = time(9, 0)
                end = time(19, 0)
                current_dt = datetime.combine(current_date, start)

                while current_dt.time() < end:
                    for appt_type in ['zoom', 'in_person']:
                        # unique_together ensures we won't duplicate existing slots
                        AvailableTimeSlot.objects.get_or_create(
                            salesman=salesman,
                            date=current_date,
                            start_time=current_dt.time(),
                            appointment_type=appt_type,
                            defaults={'cycle': cycle, 'created_by': salesman}
                        )
                    current_dt += timedelta(minutes=30)
            # Next day
            current_date += timedelta(days=1)

    return cycle


def ensure_timeslots_for_payroll_period(start_date, end_date, created_by=None):
    """
    Ensure timeslots exist for each active salesman within the given payroll period.
    Mon–Fri, 9:00–19:00, 30min intervals, both zoom and in_person.
    Idempotent via get_or_create.
    """
    salesmen = User.objects.filter(is_active_salesman=True, is_active=True)
    for salesman in salesmen:
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5:  # Mon-Fri
                start = time(9, 0)
                end = time(19, 0)
                current_dt = datetime.combine(current_date, start)
                while current_dt.time() < end:
                    for appt_type in ['zoom', 'in_person']:
                        AvailableTimeSlot.objects.get_or_create(
                            salesman=salesman,
                            date=current_date,
                            start_time=current_dt.time(),
                            appointment_type=appt_type,
                            defaults={'created_by': (created_by or salesman)}
                        )
                    current_dt += timedelta(minutes=30)
            current_date += timedelta(days=1)


def cleanup_old_slots(weeks=2):
    """Delete unused slots older than N weeks."""
    cutoff = timezone.now().date() - timedelta(weeks=weeks)
    old_slots = AvailableTimeSlot.objects.filter(is_booked=False, date__lt=cutoff)
    count = old_slots.count()
    old_slots.delete()
    return count